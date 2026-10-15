"""The six PART XVII views over live data (R5). Nothing here is a literal or a sample.

Data source per view (``db:<schema>`` = a read-only query, ``api:<phase>`` = the phase's
HTTP API with the service token):

1. **Command Center**: revenue, views and streams from P9's per-entity economics
   (``api:analytics`` ``/analytics/economics/{entity}``; entities enumerated from
   ``db:analytics``), subscribers from P9's video metrics (``/analytics/metrics/videos``,
   latest ``lifetime`` snapshot per video), unattributed statement revenue from
   ``db:analytics``, catalog size from ``db:youtube`` / ``db:distribution``, best/worst
   arms by posterior from ``db:ar``.
2. **Market Map**: ``api:market`` ``/market/v1/opportunities`` (C1); a component the
   evidence does not support is ``null`` on the wire and shown as unknown.
3. **Production Queue**: ``db:ops`` ``pipeline_runs`` / ``pipeline_transitions`` and the
   exception cases a HELD run waits on.
4. **Experiment Board**: arms and posteriors from ``db:ar``; experiments from
   ``api:experiments`` ``/experiments/v1/experiments``.
5. **Economics**: P9's per-entity economics grouped by track, channel and genre.
   Amounts stay ``Money`` (exact decimal + currency). Within an entity P9 already
   applies its ledger semantics (latest snapshot of one window, incremental statement
   lines per period); across entities the dashboard adds only distinct entities in the
   same currency, never snapshots of one entity. Estimated revenue is kept apart from
   realized revenue and never enters net or ROI.
6. **Exceptions**: ``db:ops`` open holds through :class:`ExceptionQueue` only.

Money amounts are strings; an unknown value is ``null`` with a count of the entities it
is unknown for. A source that cannot answer makes its panel ``unavailable``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final

import sqlalchemy as sa
from plm_contracts.enums import ObservationWindow
from plm_contracts.measurement import MeasurementKindError, MetricRecord, latest_snapshot
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..clock import Clock, SystemClock
from ..exceptions import (
    ExceptionCase,
    ExceptionQueue,
    exception_cases_table,
)
from ..pipeline.states import LIFECYCLE, TERMINAL_STATES, PipelineState
from ..pipeline.store import pipeline_runs, pipeline_transitions
from ..temporal import Phase
from .actions import ReviewerConfig, allowed_actions, pipeline_link
from .sources import (
    LIVE_DATA_SOURCE,
    PhaseApis,
    ReadOnlyDatabase,
    SourceUnavailable,
    error_code,
    panel_ok,
    panel_unavailable,
)

__all__ = ["VIEW_NAMES", "LiveDashboard"]

VIEW_NAMES: Final[tuple[str, ...]] = (
    "command-center", "market-map", "production-queue", "experiment-board", "economics",
    "exceptions",
)

REWARD_LABEL: Final[str] = (
    "posterior of P2's normalized proxy reward (not expected economic value)")
LIVE_ARM_EXCLUDED: Final[frozenset[str]] = frozenset({"retired", "archived"})
ACTIVE_EXPERIMENT_STATUSES: Final[frozenset[str]] = frozenset(
    {"draft", "validated", "materialized", "executing", "quarantined"})
STATE_ORDER: Final[tuple[str, ...]] = (
    *(s.value for s in LIFECYCLE), PipelineState.HELD.value, PipelineState.REJECTED.value,
    PipelineState.FAILED.value, PipelineState.CANCELLED.value,
)
ARM_LIMIT: Final[int] = 3
RECENT_TERMINAL_RUNS: Final[int] = 25
ACTIVE_RUN_LIMIT: Final[int] = 200
MARKET_PAGES: Final[int] = 5
MARKET_PAGE_SIZE: Final[int] = 200
ENTITY_LIMIT: Final[int] = 500

# Read-only views of other phases' tables (packages never import services).
_ar_state = sa.table(
    "ar_content_arm_state", sa.column("arm_id"), sa.column("brand_id"), sa.column("status"),
    sa.column("selection_count"), sa.column("mature_result_count"),
    sa.column("provisional_result_count"), sa.column("last_selected_at"), schema="ar")
_ar_posterior = sa.table(
    "ar_arm_posterior", sa.column("arm_id"), sa.column("model_type"),
    sa.column("model_version"), sa.column("prior_mean"), sa.column("prior_variance"),
    sa.column("posterior_mean"), sa.column("posterior_variance"),
    sa.column("observation_count"), sa.column("effective_observation_count"),
    sa.column("last_update_at"), schema="ar")
_video_metrics = sa.table("video_metrics", sa.column("video_id"), sa.column("channel_id"),
                          schema="analytics")
_track_metrics = sa.table("track_metrics", sa.column("track_id"), schema="analytics")
_cost_items = sa.table("cost_items", sa.column("entity_id"), sa.column("entity_type"),
                       schema="analytics")
_statements = sa.table(
    "revenue_statements", sa.column("currency"), sa.column("unattributed_revenue_amount"),
    sa.column("unattributed_row_count"), schema="analytics")
_publications = sa.table("publication_records", sa.column("youtube_video_id"),
                         sa.column("content_arm_id"), schema="youtube")
_releases = sa.table("releases", sa.column("release_id"), sa.column("status"),
                     sa.column("content_arm_id"), schema="distribution")
_tracks = sa.table("tracks", sa.column("track_id"), sa.column("release_id"),
                   schema="distribution")
_market_arms = sa.table("market_arms", sa.column("arm_id"),
                        sa.column("taxonomy_json", sa.JSON()), schema="market")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.isoformat()
    return str(value)


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


# --------------------------------------------------------------------------- money


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _money(value: Any) -> tuple[Decimal, str] | None:
    """A ``{"amount", "currency"}`` dict as an exact pair, or ``None``."""
    if not isinstance(value, Mapping):
        return None
    amount = _decimal(value.get("amount"))
    currency = value.get("currency")
    if amount is None or not isinstance(currency, str) or not currency:
        return None
    return amount, currency


def _money_list(totals: Mapping[str, Decimal]) -> list[dict[str, str]] | None:
    """Per-currency totals; ``None`` when nothing was known (not reported ≠ zero)."""
    if not totals:
        return None
    return [{"amount": str(amount), "currency": currency}
            for currency, amount in sorted(totals.items())]


@dataclass(frozen=True, slots=True)
class _Known:
    """A sum over the entities that reported a value, with how many did not."""

    totals: dict[str, Decimal]
    known: int
    unknown: int

    def as_money(self) -> dict[str, Any]:
        return {"value": _money_list(self.totals), "entities_known": self.known,
                "entities_unknown": self.unknown}


def _sum_money(values: Iterable[Any]) -> _Known:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    known = unknown = 0
    for value in values:
        pair = _money(value)
        if pair is None:
            unknown += 1
            continue
        totals[pair[1]] += pair[0]
        known += 1
    return _Known(dict(totals), known, unknown)


def _sum_count(values: Sequence[int | None]) -> dict[str, Any]:
    """A count over distinct entities; ``None`` when no entity measured it."""
    known = [int(v) for v in values if v is not None]
    return {"value": sum(known) if known else None, "entities_known": len(known),
            "entities_unknown": len(values) - len(known)}


# ----------------------------------------------------------------------- economics


@dataclass(frozen=True, slots=True)
class _Entity:
    entity_id: str
    entity_type: str  # "video" | "track"
    channel_id: str | None


class LiveDashboard:
    """Builds the six views; every method returns a JSON-ready dict labelled ``live``."""

    def __init__(
        self,
        database: ReadOnlyDatabase,
        apis: PhaseApis,
        *,
        reviewers: ReviewerConfig | None,
        clock: Clock | None = None,
        queue: ExceptionQueue | None = None,
    ) -> None:
        self._db = database
        self._apis = apis
        self._reviewers = reviewers
        self._clock = clock if clock is not None else SystemClock()
        self._queue = queue if queue is not None else ExceptionQueue()

    # -- envelope -------------------------------------------------------------------------

    def _view(self, name: str, panels: Mapping[str, Any]) -> dict[str, Any]:
        return {"data_source": LIVE_DATA_SOURCE, "view": name,
                "generated_at": self._clock.now().isoformat(), "panels": dict(panels)}

    @staticmethod
    def _panel(source: str, build: Callable[[], Any]) -> dict[str, Any]:
        try:
            return panel_ok(source, build())
        except SourceUnavailable as exc:
            return panel_unavailable(exc.source, exc.reason)

    def view(self, name: str) -> dict[str, Any]:
        builders: dict[str, Callable[[], dict[str, Any]]] = {
            "command-center": self.command_center, "market-map": self.market_map,
            "production-queue": self.production_queue,
            "experiment-board": self.experiment_board, "economics": self.economics,
            "exceptions": self.exceptions,
        }
        return builders[name]()

    # -- 1. command center ----------------------------------------------------------------

    def command_center(self) -> dict[str, Any]:
        economics: list[dict[str, Any]] | SourceUnavailable
        try:
            economics = self._entity_economics()
        except SourceUnavailable as exc:
            economics = exc

        def revenue() -> dict[str, Any]:
            rows = self._require(economics)
            videos = [r for r in rows if r["entity_type"] == "video"]
            tracks = [r for r in rows if r["entity_type"] == "track"]
            unattributed = self._db.read("db:analytics", self._unattributed_revenue)
            return {
                "realized": _sum_money(r.get("realized_revenue") for r in tracks).as_money(),
                "estimated": _sum_money(r.get("estimated_revenue") for r in videos).as_money(),
                "unattributed_realized": unattributed,
                "blocked_entities": [
                    {"entity_id": r["entity_id"], "code": r["code"]}
                    for r in rows if r["status"] != "ok"],
                "notes": [
                    "realized = distributor statement lines attributed to a track (P9)",
                    "estimated = latest lifetime YouTube estimate per video; never booked",
                    "unattributed = statement lines naming no known track",
                ],
            }

        def audience() -> dict[str, Any]:
            rows = self._require(economics)
            videos = [r for r in rows if r["entity_type"] == "video" and r["status"] == "ok"]
            tracks = [r for r in rows if r["entity_type"] == "track" and r["status"] == "ok"]
            return {
                "views": _sum_count([r.get("views") for r in videos]),
                "streams": _sum_count([r.get("streams") for r in tracks]),
                "subscribers_net": self._subscribers([r["entity_id"] for r in videos]),
                "window": ObservationWindow.LIFETIME.value,
            }

        return self._view("command_center", {
            "revenue": self._panel("api:analytics", revenue),
            "audience": self._panel("api:analytics", audience),
            "catalog": self._panel("db:youtube+distribution", self._catalog),
            "arms": self._panel("db:ar", self._best_worst_arms),
        })

    @staticmethod
    def _require(value: list[dict[str, Any]] | SourceUnavailable) -> list[dict[str, Any]]:
        if isinstance(value, SourceUnavailable):
            raise value
        return value

    @staticmethod
    def _unattributed_revenue(session: Session) -> dict[str, Any]:
        rows = session.execute(sa.select(
            _statements.c.currency, _statements.c.unattributed_revenue_amount,
            _statements.c.unattributed_row_count)).all()
        totals: dict[str, Decimal] = defaultdict(Decimal)
        undeclared = 0
        for row in rows:
            amount = _decimal(row.unattributed_revenue_amount)
            if amount is None or amount == 0:
                continue
            if row.currency is None:
                undeclared += 1  # no declared currency: never summed, never assumed
                continue
            totals[row.currency] += amount
        return {"value": _money_list(totals),
                "statements": len(rows), "statements_without_currency": undeclared}

    def _subscribers(self, video_ids: Sequence[str]) -> dict[str, Any]:
        """Net subscribers: the latest lifetime snapshot per video, summed over videos."""
        total = 0
        known = unknown = 0
        for video_id in video_ids:
            rows = self._apis.get_json(Phase.ANALYTICS, f"/analytics/metrics/videos/{video_id}")
            gained = self._latest(rows, video_id, "subscribers_gained")
            lost = self._latest(rows, video_id, "subscribers_lost")
            if gained is None or lost is None:
                unknown += 1
                continue
            total += gained - lost
            known += 1
        return {"value": total if known else None, "entities_known": known,
                "entities_unknown": unknown}

    @staticmethod
    def _latest(rows: Any, video_id: str, metric: str) -> int | None:
        records: list[MetricRecord] = []
        values: dict[str, int] = {}
        for row in rows if isinstance(rows, list) else []:
            value = row.get(metric) if isinstance(row, dict) else None
            if value is None or row.get("status", "valid") != "valid":
                continue
            try:
                records.append(MetricRecord(
                    record_id=str(row["id"]), kind=row["observation_kind"], entity_id=video_id,
                    metric=metric, value=Decimal(int(value)),
                    as_of=datetime.fromisoformat(str(row["observed_at"])),
                    window=row.get("window")))
            except (KeyError, ValueError, ValidationError):
                continue
            values[str(row["id"])] = int(value)
        try:
            chosen = latest_snapshot(records, window=ObservationWindow.LIFETIME)
        except MeasurementKindError:
            return None
        return None if chosen is None else values[chosen.record_id]

    def _catalog(self) -> dict[str, Any]:
        def query(session: Session) -> dict[str, Any]:
            videos = session.execute(sa.select(sa.func.count()).select_from(_publications)
                                     ).scalar_one()
            tracks = session.execute(sa.select(sa.func.count()).select_from(_tracks)
                                     ).scalar_one()
            by_status = session.execute(
                sa.select(_releases.c.status, sa.func.count()).group_by(_releases.c.status)
                .order_by(_releases.c.status)).all()
            return {"published_videos": int(videos), "distributed_tracks": int(tracks),
                    "releases_by_status": {row[0]: int(row[1]) for row in by_status}}

        return self._db.read("db:youtube+distribution", query)

    def _arms(self, session: Session) -> list[dict[str, Any]]:
        rows = session.execute(
            sa.select(_ar_state.c.arm_id, _ar_state.c.brand_id, _ar_state.c.status,
                      _ar_state.c.selection_count, _ar_state.c.mature_result_count,
                      _ar_state.c.provisional_result_count, _ar_state.c.last_selected_at,
                      _ar_posterior.c.model_type, _ar_posterior.c.prior_mean,
                      _ar_posterior.c.prior_variance, _ar_posterior.c.posterior_mean,
                      _ar_posterior.c.posterior_variance, _ar_posterior.c.observation_count,
                      _ar_posterior.c.effective_observation_count,
                      _ar_posterior.c.last_update_at)
            .select_from(_ar_state.outerjoin(
                _ar_posterior, _ar_posterior.c.arm_id == _ar_state.c.arm_id))
            .order_by(_ar_state.c.arm_id)).mappings().all()
        arms = []
        for row in rows:
            has_posterior = row["posterior_mean"] is not None
            arms.append({
                "arm_id": row["arm_id"], "brand_id": row["brand_id"], "status": row["status"],
                "selection_count": row["selection_count"],
                "mature_result_count": row["mature_result_count"],
                "provisional_result_count": row["provisional_result_count"],
                "last_selected_at": _iso(row["last_selected_at"]),
                "posterior": None if not has_posterior else {
                    "model_type": row["model_type"],
                    "prior_mean": _float(row["prior_mean"]),
                    "prior_variance": _float(row["prior_variance"]),
                    "mean": _float(row["posterior_mean"]),
                    "variance": _float(row["posterior_variance"]),
                    "observation_count": int(row["observation_count"]),
                    "effective_observation_count": _float(row["effective_observation_count"]),
                    "last_update_at": _iso(row["last_update_at"]),
                },
            })
        return arms

    def _best_worst_arms(self) -> dict[str, Any]:
        arms = self._db.read("db:ar", self._arms)
        observed = [a for a in arms if a["posterior"] is not None
                    and a["posterior"]["observation_count"] > 0]
        ranked = sorted(observed, key=lambda a: (-a["posterior"]["mean"], a["arm_id"]))
        worst = sorted(observed, key=lambda a: (a["posterior"]["mean"], a["arm_id"]))
        return {
            "best": ranked[:ARM_LIMIT], "worst": worst[:ARM_LIMIT],
            "arms_total": len(arms), "arms_with_observations": len(observed),
            "arms_prior_only": sum(1 for a in arms if a["posterior"] is not None
                                   and a["posterior"]["observation_count"] == 0),
            "arms_without_posterior": sum(1 for a in arms if a["posterior"] is None),
            "reward_label": REWARD_LABEL,
        }

    # -- 2. market map --------------------------------------------------------------------

    def market_map(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            items: list[dict[str, Any]] = []
            cursor: str | None = None
            as_of: str | None = None
            score_version: str | None = None
            for _ in range(MARKET_PAGES):
                params: dict[str, Any] = {"limit": MARKET_PAGE_SIZE}
                if cursor is not None:
                    params["cursor"] = cursor
                page = self._apis.get_json(Phase.MARKET, "/market/v1/opportunities", params)
                as_of = as_of or page.get("as_of")
                score_version = score_version or page.get("score_version")
                items.extend(self._opportunity(item) for item in page.get("items", []))
                cursor = page.get("next_cursor")
                if not cursor:
                    break
            return {"items": items, "as_of": as_of, "score_version": score_version,
                    "truncated": bool(cursor),
                    "axes": {"x": "competition", "y": "demand", "size": "growth"}}

        return self._view("market_map", {"opportunities": self._panel("api:market", build)})

    @staticmethod
    def _opportunity(item: Mapping[str, Any]) -> dict[str, Any]:
        components = item.get("components") or {}
        taxonomy = item.get("taxonomy") or {}
        evidence = item.get("evidence") or {}
        shown = ("demand", "competition", "growth")
        return {
            "arm_id": item.get("arm_id"),
            "genre": taxonomy.get("genre"), "subgenre": taxonomy.get("subgenre"),
            "score": item.get("market_opportunity_score"),
            "confidence": item.get("confidence"), "as_of": item.get("as_of"),
            "demand": components.get("demand"), "competition": components.get("competition"),
            "growth": components.get("growth"),
            "unknown_components": [name for name in shown if components.get(name) is None],
            "observation_count": evidence.get("observation_count"),
            "source_count": evidence.get("source_count"),
        }

    # -- 3. production queue --------------------------------------------------------------

    def production_queue(self) -> dict[str, Any]:
        return self._view("production_queue",
                          {"runs": self._panel("db:ops", lambda: self._db.read(
                              "db:ops", self._runs))})

    def _runs(self, session: Session) -> dict[str, Any]:
        counts: dict[str, int] = {
            str(state): int(count) for state, count in session.execute(
                sa.select(pipeline_runs.c.state, sa.func.count())
                .group_by(pipeline_runs.c.state)).all()}
        terminal = [s.value for s in TERMINAL_STATES]
        active_rows = session.execute(
            sa.select(pipeline_runs).where(pipeline_runs.c.state.not_in(terminal),
                                           pipeline_runs.c.completed_at.is_(None))
            .order_by(pipeline_runs.c.updated_at.desc(), pipeline_runs.c.workflow_id)
            .limit(ACTIVE_RUN_LIMIT)
        ).mappings().all()
        recent_rows = session.execute(
            sa.select(pipeline_runs).where(sa.or_(pipeline_runs.c.state.in_(terminal),
                                                  pipeline_runs.c.completed_at.is_not(None)))
            .order_by(pipeline_runs.c.updated_at.desc(), pipeline_runs.c.workflow_id)
            .limit(RECENT_TERMINAL_RUNS)
        ).mappings().all()
        return {
            "by_state": [{"state": s, "count": int(counts.get(s, 0))} for s in STATE_ORDER
                         if counts.get(s)],
            "unknown_states": sorted(s for s in counts if s not in STATE_ORDER),
            "active": [self._run(session, row) for row in active_rows],
            "active_truncated": len(active_rows) == ACTIVE_RUN_LIMIT,
            "recent_finished": [self._run(session, row) for row in recent_rows],
            "total": int(sum(counts.values())),
        }

    def _run(self, session: Session, row: sa.RowMapping) -> dict[str, Any]:
        last = session.execute(
            sa.select(pipeline_transitions.c.step, pipeline_transitions.c.occurred_at,
                      pipeline_transitions.c.error_code)
            .where(pipeline_transitions.c.workflow_id == row["workflow_id"])
            .order_by(pipeline_transitions.c.sequence.desc()).limit(1)
        ).first()
        run: dict[str, Any] = {
            "workflow_id": row["workflow_id"], "directive_id": row["directive_id"],
            "brand_id": row["brand_id"], "state": row["state"], "held_from": row["held_from"],
            "last_sequence": row["last_sequence"], "last_step": None if last is None else last.step,
            "last_error_code": row["last_error_code"],
            "last_error_detail": row["last_error_detail"],
            "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
            "completed_at": _iso(row["completed_at"]),
            "blocked": row["state"] in (PipelineState.HELD.value, PipelineState.FAILED.value),
            "holds": [], "awaiting_signal": False,
        }
        if row["state"] == PipelineState.HELD.value:
            holds = self._holds_of(session, row["workflow_id"])
            run["holds"] = holds
            run["awaiting_signal"] = bool(holds) and all(h["status"] != "OPEN" for h in holds)
        return run

    def _holds_of(self, session: Session, workflow_id: str) -> list[dict[str, Any]]:
        """The cases of the run's latest hold episode (one ``subject_ref``)."""
        raised = session.execute(
            sa.select(exception_cases_table.c.id, exception_cases_table.c.subject_ref)
            .where(exception_cases_table.c.raised_by == f"pipeline:{workflow_id}")
            .order_by(exception_cases_table.c.raised_at.desc(), exception_cases_table.c.id)
        ).all()
        if not raised:
            return []
        subject = raised[0].subject_ref
        holds = []
        for row in raised:
            if row.subject_ref != subject:
                continue
            case = self._queue.require(session, row.id)
            holds.append({"case_id": case.id, "reason_code": case.reason_code,
                          "hold_class": case.hold_class.value, "status": case.status.value,
                          "reviewer_role": case.reviewer_role,
                          "required_evidence": list(case.required_evidence)})
        return holds

    # -- 4. experiment board --------------------------------------------------------------

    def experiment_board(self) -> dict[str, Any]:
        def arms() -> dict[str, Any]:
            rows = self._db.read("db:ar", self._arms)
            live = [a for a in rows if a["status"] not in LIVE_ARM_EXCLUDED]
            return {"arms": live, "excluded_statuses": sorted(LIVE_ARM_EXCLUDED),
                    "arms_excluded": len(rows) - len(live), "reward_label": REWARD_LABEL}

        def experiments() -> dict[str, Any]:
            body = self._apis.get_json(Phase.EXPERIMENTS, "/experiments/v1/experiments",
                                       {"limit": 500})
            items = [self._experiment(e) for e in body.get("experiments", [])]
            active = [e for e in items if e["status"] in ACTIVE_EXPERIMENT_STATUSES]
            by_status: dict[str, int] = defaultdict(int)
            for item in items:
                by_status[str(item["status"])] += 1
            return {"active": active, "by_status": dict(sorted(by_status.items())),
                    "listed": len(items), "active_statuses": sorted(ACTIVE_EXPERIMENT_STATUSES)}

        return self._view("experiment_board", {
            "arms": self._panel("db:ar", arms),
            "experiments": self._panel("api:experiments", experiments),
        })

    @staticmethod
    def _experiment(item: Mapping[str, Any]) -> dict[str, Any]:
        plan = item.get("measurement_plan") or {}
        return {
            "experiment_id": item.get("experiment_id"),
            "content_arm_id": item.get("content_arm_id"),
            "production_directive_id": item.get("production_directive_id"),
            "experiment_type": item.get("experiment_type"), "status": item.get("status"),
            "treatments": len(item.get("treatments") or []),
            "budget_limit": item.get("budget_limit"), "created_at": item.get("created_at"),
            "outcome_horizon": plan.get("outcome_horizon") if isinstance(plan, dict) else None,
        }

    # -- 5. economics ---------------------------------------------------------------------

    def economics(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            rows = self._entity_economics()
            ok = [r for r in rows if r["status"] == "ok"]
            return {
                "by_track": [r for r in ok if r["entity_type"] == "track"],
                "by_video": [r for r in ok if r["entity_type"] == "video"],
                "by_channel": self._group(
                    [r for r in ok if r["entity_type"] == "video"], "channel_id"),
                "by_genre": self._group(ok, "genre"),
                "blocked": [r for r in rows if r["status"] != "ok"],
                "reporting_currencies": sorted({r["reporting_currency"] for r in ok
                                                if r.get("reporting_currency")}),
                "entities_total": len(rows),
                "notes": [
                    ("per entity: P9's economics (lifetime window; statement lines summed "
                     "per period)"),
                    ("groups add distinct entities in one currency; estimated revenue never "
                     "enters net or ROI"),
                ],
            }

        return self._view("economics", {"economics": self._panel("api:analytics", build)})

    def _entities(self, session: Session) -> list[_Entity]:
        entities: dict[tuple[str, str], _Entity] = {}
        for video_id, channel_id in session.execute(
                sa.select(_video_metrics.c.video_id, _video_metrics.c.channel_id).distinct()
                .order_by(_video_metrics.c.video_id)).all():
            key = (video_id, "video")
            if key not in entities:
                entities[key] = _Entity(video_id, "video", channel_id)
        for (track_id,) in session.execute(
                sa.select(_track_metrics.c.track_id).distinct()
                .where(_track_metrics.c.track_id.is_not(None))
                .order_by(_track_metrics.c.track_id)).all():
            entities.setdefault((track_id, "track"), _Entity(track_id, "track", None))
        for entity_id, entity_type in session.execute(
                sa.select(_cost_items.c.entity_id, _cost_items.c.entity_type).distinct()
                .where(_cost_items.c.entity_type.in_(("video", "track")))).all():
            entities.setdefault((entity_id, entity_type), _Entity(entity_id, entity_type, None))
        return sorted(entities.values(), key=lambda e: (e.entity_type, e.entity_id))[:ENTITY_LIMIT]

    def _genres(self, entities: Sequence[_Entity]) -> dict[tuple[str, str], tuple[str | None,
                                                                                    str | None]]:
        """(arm_id, genre) per entity from stored lineage; unknown when not linked."""
        def query(session: Session) -> dict[tuple[str, str], tuple[str | None, str | None]]:
            video_arms: dict[str, str] = {
                str(video): str(arm) for video, arm in session.execute(
                    sa.select(_publications.c.youtube_video_id, _publications.c.content_arm_id)
                ).all()}
            track_arms: dict[str, str] = {
                str(track): str(arm) for track, arm in session.execute(
                    sa.select(_tracks.c.track_id, _releases.c.content_arm_id)
                    .select_from(_tracks.join(
                        _releases, _releases.c.release_id == _tracks.c.release_id))
                ).all()}
            genres: dict[str, str | None] = {}
            for arm_id, taxonomy in session.execute(
                    sa.select(_market_arms.c.arm_id, _market_arms.c.taxonomy_json)).all():
                value = taxonomy.get("genre") if isinstance(taxonomy, dict) else None
                genres[arm_id] = None if value in (None, "", "none") else str(value)
            result: dict[tuple[str, str], tuple[str | None, str | None]] = {}
            for entity in entities:
                arms = video_arms if entity.entity_type == "video" else track_arms
                arm = arms.get(entity.entity_id)
                result[(entity.entity_id, entity.entity_type)] = (
                    arm, None if arm is None else genres.get(arm))
            return result

        try:
            return self._db.read("db:youtube+distribution+market", query)
        except SourceUnavailable:
            return {}

    def _entity_economics(self) -> list[dict[str, Any]]:
        entities = self._db.read("db:analytics", self._entities)
        lineage = self._genres(entities)
        rows = []
        for entity in entities:
            response = self._apis.get(
                Phase.ANALYTICS, f"/analytics/economics/{entity.entity_id}",
                {"entity_type": entity.entity_type, "window": ObservationWindow.LIFETIME.value})
            arm_id, genre = lineage.get((entity.entity_id, entity.entity_type), (None, None))
            base = {"entity_id": entity.entity_id, "entity_type": entity.entity_type,
                    "channel_id": entity.channel_id, "arm_id": arm_id, "genre": genre}
            if response.status_code // 100 != 2:
                code, message = error_code(response)
                if response.status_code in (401, 403, 503) or response.status_code >= 500:
                    raise SourceUnavailable(PhaseApis.source(Phase.ANALYTICS),
                                            f"/analytics/economics: HTTP "
                                            f"{response.status_code} {code}: {message}")
                rows.append({**base, "status": "blocked", "code": code, "message": message})
                continue
            body = response.json()
            rows.append({
                **base, "status": "ok", "code": None,
                "reporting_currency": body.get("reporting_currency"),
                "window": body.get("window"),
                "realized_revenue": body.get("realized_revenue"),
                "estimated_revenue": body.get("estimated_revenue"),
                "allocated_cost": body.get("allocated_cost"),
                "net_contribution": body.get("net_contribution"),
                "net_contribution_blocked_reason": body.get("net_contribution_blocked_reason"),
                "roi": body.get("roi"), "views": body.get("views"),
                "streams": body.get("streams"),
            })
        return rows

    @staticmethod
    def _group(rows: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
        groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[str(row.get(key)) if row.get(key) is not None else "unknown"].append(row)
        result = []
        for name, members in sorted(groups.items()):
            realized = _sum_money(m.get("realized_revenue") for m in members)
            estimated = _sum_money(m.get("estimated_revenue") for m in members)
            cost = _sum_money(m.get("allocated_cost") for m in members)
            net = _sum_money(m.get("net_contribution") for m in members)
            net_value = net.totals if net.unknown == 0 else {}
            roi: str | None = None
            if (net.unknown == 0 and cost.unknown == 0 and len(net_value) == 1
                    and set(net_value) == set(cost.totals)):
                (currency,) = net_value
                if cost.totals[currency] != 0:
                    roi = str((net_value[currency] / cost.totals[currency]).quantize(
                        Decimal("0.0001")))
            result.append({
                key: name, "entities": len(members),
                "realized_revenue": realized.as_money(),
                "estimated_revenue": estimated.as_money(),
                "allocated_cost": cost.as_money(),
                "net_contribution": {"value": _money_list(net_value),
                                     "entities_blocked": net.unknown},
                "roi": roi,
                "views": _sum_count([m.get("views") for m in members
                                     if m["entity_type"] == "video"]),
                "streams": _sum_count([m.get("streams") for m in members
                                       if m["entity_type"] == "track"]),
            })
        return result

    # -- 6. exceptions --------------------------------------------------------------------

    def exceptions(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            return self._db.read("db:ops", self._open_holds)

        return self._view("exceptions", {
            "open": self._panel("db:ops", build),
            "reviewers": panel_ok("config", {
                "configured": self._reviewers is not None,
                "source": None if self._reviewers is None else self._reviewers.source,
                "reviewers": [] if self._reviewers is None else self._reviewers.describe(),
            }),
        })

    def _open_holds(self, session: Session) -> dict[str, Any]:
        cases = self._queue.list_open(session)
        run_states = self._run_states(session, cases)
        return {"items": [self.describe_case(c, run_states) for c in cases],
                "count": len(cases)}

    @staticmethod
    def _run_states(session: Session, cases: Sequence[ExceptionCase]) -> dict[str, str]:
        ids = sorted({link.workflow_id for c in cases if (link := pipeline_link(c))})
        if not ids:
            return {}
        return {str(workflow_id): str(state) for workflow_id, state in session.execute(
            sa.select(pipeline_runs.c.workflow_id, pipeline_runs.c.state)
            .where(pipeline_runs.c.workflow_id.in_(ids))).all()}

    def describe_case(self, case: ExceptionCase,
                      run_states: Mapping[str, str] | None = None) -> dict[str, Any]:
        rule = self._queue.policy.reason_codes.get(case.reason_code)
        link = pipeline_link(case)
        hard = case.hold_class.value == "HARD_STOP"
        return {
            "case_id": case.id, "origin_phase": case.origin_phase,
            "subject_ref": case.subject_ref, "reason_code": case.reason_code,
            "severity": case.severity.value, "hold_class": case.hold_class.value,
            "override_permitted": case.override_permitted,
            "required_evidence": list(case.required_evidence),
            "reviewer_role": case.reviewer_role, "policy_version": case.policy_version,
            "policy_mapped": case.policy_mapped, "status": case.status.value,
            "raised_by": case.raised_by, "raised_at": _iso(case.raised_at),
            "details": dict(case.details),
            "description": None if rule is None else rule.description,
            "category": None if rule is None else rule.category,
            "allowed_actions": allowed_actions(case),
            "approve_available": "resume" in allowed_actions(case),
            "resolution_path": (
                "external process only: record its outcome with the required evidence"
                if hard else ("reviewer with the role and every evidence item"
                              if case.override_permitted else "policy permits no override")),
            "pipeline": None if link is None else {
                "workflow_id": link.workflow_id, "run_id": link.run_id, "step": link.step,
                "episode": link.episode,
                "run_state": (run_states or {}).get(link.workflow_id),
            },
        }

    def exception_detail(self, case_id: str) -> dict[str, Any] | None:
        def query(session: Session) -> dict[str, Any] | None:
            case = self._queue.get(session, case_id)
            if case is None:
                return None
            history = [
                {"seq": e.seq, "event_type": e.event_type.value,
                 "decision_code": e.decision_code, "actor": e.actor, "role": e.role,
                 "evidence_keys": sorted(e.evidence), "notes": e.notes,
                 "occurred_at": _iso(e.occurred_at)}
                for e in self._queue.history(session, case.id)]
            return {**self.describe_case(case, self._run_states(session, [case])),
                    "closed_by": case.closed_by, "closed_at": _iso(case.closed_at),
                    "resolution": None if case.resolution is None else case.resolution.value,
                    "history": history}

        detail = self._db.read("db:ops", query)
        return None if detail is None else {"data_source": LIVE_DATA_SOURCE, **detail}
