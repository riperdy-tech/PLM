# services/market_intelligence/cli.py
"""Implementations behind the `plm market …` commands (declared in `plm_platform.cli`).

`run_daily` is the one orchestration entry point shared by the workflow activities,
the CLI and the acceptance test — there is no second path through the pipeline.

The daily run is also where §27's **daily quota ceiling** is enforced. The connector's
`QuotaBudget` is per-run; a day of replays could therefore overspend the provider's
allowance. The scheduler's ledger (`collection.scheduler`) records what each run spent
(`cost_api_quota_units`) and either refuses the run (day spent: nothing is collected,
the queries are rescheduled) or builds the connector with `min(per-run, day remaining)`
— an attempt that would cross the ceiling raises before the HTTP call, so the run
degrades to `partial` instead of overspending.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .collection.collectors import CollectionRunSummary, collect_query
from .collection.rate_limit import QuotaBudget, QuotaExceededError, RateLimiter, RequestBudget
from .collection.scheduler import (
    daily_quota_state,
    due_queries,
    mark_run,
    mark_scheduled,
    reset_schedule,
    run_quota_budget,
)
from .config import MarketSettings, load_market_settings
from .connectors.youtube import BASE_URL, YouTubeCredentials, YouTubeDataAPIConnector
from .domain.entities import CollectionRunCounters
from .domain.enums import (
    CollectionRunStatus,
    ConnectorErrorClass,
    ConnectorType,
    ProviderName,
    TaxonomyDimension,
)
from .domain.value_objects import fingerprint
from .features.arm_features import compute_arm_features
from .scoring.opportunity import persist_scores, score_all_arms
from .storage import repositories
from .storage.models import observations, raw_observations
from .taxonomy.arms import generate_candidate_arms
from .taxonomy.classifier import ClassificationInput, RuleBasedClassifier, classify_observation
from .taxonomy.ontology import profile_from_dimensions
from .taxonomy.validators import (
    ClassificationDraft,
    ClassificationSchemaError,
    classification_input_fingerprint,
)

__all__ = [
    "run_collect", "run_daily", "run_daily_step", "run_report", "run_score", "serve", "smoke_live",
]

SessionFactory = Callable[[], Any]
ConnectorFactory = Callable[[MarketSettings, int], YouTubeDataAPIConnector]

_HTTP_TIMEOUT_SECONDS = 30.0


def run_daily(now: datetime | None = None, *, session_factory: SessionFactory | None = None,
              connector: YouTubeDataAPIConnector | None = None,
              connector_factory: ConnectorFactory | None = None,
              settings: MarketSettings | None = None) -> dict:
    """One daily cycle in-process: seeds → discovery → classification → features → scores.

    This is the single orchestration entry point: the Temporal activities call
    `run_daily_step`, the CLI calls this, and the acceptance test calls this.
    """
    resolved = settings if settings is not None else load_market_settings()
    moment = now if now is not None else datetime.now(UTC)
    steps: dict[str, Any] = {}
    with _session(session_factory) as session:
        steps["seed_refresh"] = _install_seeds(session, source_id=_ensure_source(session))
        session.commit()
    with _session(session_factory) as session:
        steps["discovery"] = _run_due_collection(session, now=moment, settings=resolved,
                                                 connector=connector,
                                                 connector_factory=connector_factory)
        session.commit()
    with _session(session_factory) as session:
        steps["classification"] = _classify_pending(session, now=moment, settings=resolved)
        session.commit()
    with _session(session_factory) as session:
        steps["scores"] = _recompute(session, now=moment, settings=resolved)
        session.commit()
    steps["status"] = "partial" if any(
        isinstance(value, dict) and value.get("status") == "partial"
        for value in steps.values()
    ) else "succeeded"
    return steps


def run_daily_step(job: str, *, payload: dict, session_factory: SessionFactory | None = None,
                   connector: YouTubeDataAPIConnector | None = None,
                   connector_factory: ConnectorFactory | None = None,
                   settings: MarketSettings | None = None) -> dict:
    """One activity's worth of work; the Temporal layer calls this, nothing else does."""
    from plm_platform.correlation import correlation_scope
    from plm_platform.observability import run_scope

    correlation_id = payload.get("correlation_id")
    now = _parse_now(payload.get("now"))
    resolved = settings if settings is not None else _settings()
    with (
        correlation_scope(correlation_id),
        run_scope(f"p1-{job}-{now.date().isoformat()}"),
        _session(session_factory) as session,
    ):
        result = _execute_step(job, session, payload=payload, now=now, settings=resolved,
                               connector=connector, connector_factory=connector_factory)
        session.commit()
    return result


def run_collect(*, refresh: bool = False, limit: int = 25) -> dict:
    """`plm market collect`: one operator-triggered collection over due queries."""
    settings = _settings()
    now = datetime.now(UTC)
    trigger = "market.youtube_refresh" if refresh else "market.youtube_discovery"
    with _session() as session:
        result = _run_due_collection(session, now=now, settings=settings, trigger=trigger,
                                     limit=limit)
        session.commit()
    return result


def run_score() -> dict:
    """`plm market score`: recompute arms, features and opportunity scores from storage."""
    settings = _settings()
    with _session() as session:
        result = _recompute(session, now=datetime.now(UTC), settings=settings)
        session.commit()
    return result


def run_report(as_of: str | None = None) -> str:
    """Write the §31 report — implemented by P1.12's `reports` module."""
    try:
        from .reports import write_report
    except ImportError as exc:  # pragma: no cover - until P1.12 lands
        raise SystemExit(
            "market report is not implemented until PHASE-P1.12 (the reports module)"
        ) from exc
    return write_report(as_of=as_of)


def serve(host: str = "127.0.0.1", port: int = 8081) -> None:
    """Serve the `/market/v1` API (R15) with uvicorn over the platform database."""
    import uvicorn

    from .api.app import create_app

    settings = _settings()
    app = create_app(session_factory=_session_factory_for_api(), settings=settings)
    uvicorn.run(app, host=host, port=port)


def smoke_live() -> str:
    """§40's live smoke test; blocked (never a silent pass) until credentials exist.

    P1.14 adds the live run itself; with no credential configured the acceptance is
    *blocked*, which is the honest state of a fresh checkout.
    """
    from plm_platform.config import load_settings

    platform = load_settings()
    if platform.youtube_api_key is None:
        return ("blocked: pending credentials (YOUTUBE_API_KEY, YOUTUBE_OAUTH_CLIENT_ID, "
                "YOUTUBE_OAUTH_CLIENT_SECRET)")
    return "blocked: the live smoke run is not implemented until PHASE-P1.14"


# ---------------------------------------------------------------------------
# step dispatch (shared by `run_daily_step`)


def _execute_step(job: str, session: Session, *, payload: dict, now: datetime,
                  settings: MarketSettings, connector: YouTubeDataAPIConnector | None = None,
                  connector_factory: ConnectorFactory | None = None) -> dict:
    if job == "market.seed_refresh":
        source_id = _ensure_source(session)
        return {"job": job, "new_queries": _install_seeds(session, source_id=source_id)}
    if job == "market.youtube_discovery":
        return _run_due_collection(session, now=now, settings=settings, connector=connector,
                                   connector_factory=connector_factory)
    if job == "market.youtube_refresh":
        return _run_due_collection(session, now=now, settings=settings, connector=connector,
                                   connector_factory=connector_factory,
                                   query_ids=payload.get("query_ids"), trigger=job)
    if job == "market.taxonomy_classification":
        return _classify_pending(session, now=now, settings=settings,
                                 full=bool(payload.get("full")))
    if job in {"market.feature_recompute", "market.opportunity_scoring"}:
        return _recompute(session, now=now, settings=settings)
    if job == "market.reschedule":
        due = due_queries(session, now=now, settings=settings)
        return {"job": job, "scheduled": mark_scheduled(session, due, now=now, settings=settings)}
    if job == "market.stale_data_cleanup":
        return _stale_inventory(session, now=now, settings=settings)
    return {"job": job, "status": "unknown job"}


# ---------------------------------------------------------------------------
# collection (the daily quota ceiling lives here)


def _run_due_collection(session: Session, *, now: datetime, settings: MarketSettings,
                        connector: YouTubeDataAPIConnector | None = None,
                        connector_factory: ConnectorFactory | None = None,
                        query_ids: list[str] | None = None, trigger: str = "market.youtube_discovery",
                        limit: int | None = None) -> dict:
    """Collect every due query within the day's remaining quota; never overdraw it."""
    due = due_queries(session, now=now, settings=settings)
    if query_ids is not None:
        wanted = {str(query_id) for query_id in query_ids}
        due = [state for state in due if state.query_id in wanted]
    if limit is not None:
        due = due[:limit]
    if not due:
        return {"job": trigger, "queries": 0}
    day = daily_quota_state(session, now=now, settings=settings)
    budget = run_quota_budget(day, per_run_limit=settings.quota_units_per_run)
    if budget is None:
        # The day's ceiling is reached: refuse the run, keep the priority order intact
        # by rescheduling instead of retrying (§27).
        for state in due:
            reset_schedule(session, state.query_id, now=now, settings=settings,
                           reason="DAILY_QUOTA_EXHAUSTED")
        return {"job": trigger, "queries": len(due), "quota_exhausted": True,
                "reason": "DAILY_QUOTA_EXHAUSTED", "remaining_units": day.remaining_units}
    active = connector if connector is not None else _build_connector(
        settings, units=budget.limit_units, connector_factory=connector_factory)
    source_id = _ensure_source(session)
    run_id = repositories.start_collection_run(
        session, source_id=source_id, query_id=None, software_version=settings.score_version,
        started_at=now,
    )

    summaries: list[CollectionRunSummary] = []
    try:
        for state in due:
            summaries.append(collect_query(
                session, connector=active, source_id=source_id, query_id=state.query_id,
                query_text=state.query_text, run_id=run_id, settings=settings, now=now,
            ))
    except QuotaExceededError:
        # The connector refused an attempt that would have crossed the run's budget
        # (per-run or the day's remainder): stop low-priority work, reschedule (§27).
        repositories.add_run_cost(session, run_id, api_quota_units=active.quota_units_used)
        for state in due:
            reset_schedule(session, state.query_id, now=now, settings=settings,
                           reason="QUOTA_EXCEEDED")
        repositories.finish_collection_run(
            session, run_id, status=CollectionRunStatus.PARTIAL,
            counters=_counters_from_db(session, run_id, requests=active.request_count),
            error_summary={"QUOTA_EXCEEDED": len(due)}, finished_at=datetime.now(UTC),
        )
        return {"job": trigger, "queries": len(due), "run_id": run_id, "quota_exhausted": True,
                "status": CollectionRunStatus.PARTIAL.value}
    repositories.add_run_cost(session, run_id, api_quota_units=active.quota_units_used)
    status = (CollectionRunStatus.PARTIAL
              if any(summary.status is not CollectionRunStatus.SUCCEEDED for summary in summaries)
              else CollectionRunStatus.SUCCEEDED)
    repositories.finish_collection_run(
        session, run_id, status=status,
        counters=_merge(summaries, requests=active.request_count),
        finished_at=datetime.now(UTC),
    )
    mark_scheduled(session, due, now=now, settings=settings)
    for state in due:
        mark_run(session, state.query_id, at=now)
    return {"job": trigger, "queries": len(due), "run_id": run_id, "status": status.value}


def _build_connector(settings: MarketSettings, *, units: int,
                     connector_factory: ConnectorFactory | None) -> YouTubeDataAPIConnector:
    if connector_factory is not None:
        return connector_factory(settings, units)
    return _production_connector(settings, units)


def _production_connector(settings: MarketSettings, quota_units: int) -> YouTubeDataAPIConnector:
    """The run's connector — real credentials, or CI's fixture-backed mock, never both."""
    from plm_platform.config import Env, load_settings

    platform = settings.platform if settings.platform is not None else load_settings()
    if platform.env is Env.CI:
        return _mock_connector(settings, quota_units, platform=platform)
    credentials = YouTubeCredentials(api_key=platform.youtube_api_key)
    if credentials.api_key is None:
        raise RuntimeError(
            "no YouTube credential configured: set YOUTUBE_API_KEY in the environment or "
            ".env before collecting (ENV=ci uses the fixture-backed mock instead); this run "
            "refuses rather than collecting nothing silently"
        )
    return YouTubeDataAPIConnector(
        client=httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS), credentials=credentials,
        quota=QuotaBudget(quota_units),
        request_budget=RequestBudget(settings.max_requests_per_run),
        limiter=RateLimiter(settings.requests_per_second),
    )


def _mock_connector(settings: MarketSettings, quota_units: int, *,
                    platform: Any) -> YouTubeDataAPIConnector:
    """CI only: the fixture-backed transport, so the pipeline runs with no credentials."""
    from plm_platform.config import Env, Secret

    if platform.env is not Env.CI:
        raise RuntimeError(
            "the fixture-backed connector is for ENV=ci only; configure YOUTUBE_API_KEY for "
            "a real run — a mock must never masquerade as a provider"
        )
    from tests.support.http import MockHTTP

    from .collection.seeds import load_bootstrap_seeds

    fixtures = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "market" / "youtube"
    search = json.loads((fixtures / "search_lofi_p1.json").read_text(encoding="utf-8"))
    videos = json.loads((fixtures / "videos_lofi_batch.json").read_text(encoding="utf-8"))
    mock = MockHTTP(repeat_last=True)
    for _ in load_bootstrap_seeds():  # one search + one hydration per bootstrap query
        mock.enqueue_json(search).enqueue_json(videos)
    return YouTubeDataAPIConnector(
        client=mock.client(base_url=BASE_URL), credentials=YouTubeCredentials(
            api_key=Secret("ci-mock")),
        quota=QuotaBudget(quota_units),
        request_budget=RequestBudget(settings.max_requests_per_run),
    )


def _merge(summaries: list[CollectionRunSummary], *, requests: int) -> CollectionRunCounters:
    """One run's counters across its queries; any loss makes the run `partial`."""
    errors = sum(len(summary.error_classes) for summary in summaries)
    rate_limits = sum(
        1 for summary in summaries for error in summary.error_classes
        if error == ConnectorErrorClass.RATE_LIMIT.value
    )
    status = (CollectionRunStatus.PARTIAL
              if any(summary.status is not CollectionRunStatus.SUCCEEDED for summary in summaries)
              else CollectionRunStatus.SUCCEEDED)
    return CollectionRunCounters(
        request_count=requests, success_count=max(0, requests - errors), error_count=errors,
        rate_limit_count=rate_limits,
        raw_observation_count=sum(summary.raw_count for summary in summaries),
        normalized_observation_count=sum(summary.normalized_count for summary in summaries),
        quarantined_count=sum(summary.quarantined_count for summary in summaries),
        status=status,
    )


def _counters_from_db(session: Session, run_id: str, *, requests: int) -> CollectionRunCounters:
    """What an interrupted run already stored (raw, normalized, quarantined rows).

    Attempt counts come from the connector (`requests`); the database records the
    records that landed, not the attempts, so this reads them back rather than
    guessing from the summaries that never completed.
    """
    raw = int(session.execute(
        select(func.count()).select_from(raw_observations)
        .where(raw_observations.c.collection_run_id == run_id)
    ).scalar_one())
    normalized = int(session.execute(
        select(func.count()).select_from(observations)
        .join(raw_observations, observations.c.raw_observation_id == raw_observations.c.id)
        .where(raw_observations.c.collection_run_id == run_id)
    ).scalar_one())
    return CollectionRunCounters(
        request_count=requests, success_count=0, error_count=1, rate_limit_count=0,
        raw_observation_count=raw, normalized_observation_count=normalized,
        quarantined_count=repositories.count_quarantined(session, run_id),
        status=CollectionRunStatus.PARTIAL,
    )


# ---------------------------------------------------------------------------
# classification and scoring


def _classify_pending(session: Session, *, now: datetime, settings: MarketSettings,
                      full: bool = False) -> dict:
    """Classify observations whose content has no classification row yet (§15/§28)."""
    window = None if full else now - timedelta(hours=settings.freshness_normal_hours)
    rows = repositories.observation_classification_inputs(session, since=window)
    classifier = RuleBasedClassifier()
    classified = cached = unclassifiable = 0
    for row in rows:
        title, description = row["text_title"], row["text_description"]
        content_fingerprint = classification_input_fingerprint(title, description)
        if repositories.load_classification(
            session, content_fingerprint=content_fingerprint,
            taxonomy_version=settings.taxonomy_version,
        ) is not None:
            cached += 1
            continue
        try:
            classify_observation(
                session, entity_id=str(row["entity_id"]), observation_id=str(row["id"]),
                text=ClassificationInput(title=title, description=description),
                classifier=classifier, taxonomy_version=settings.taxonomy_version,
            )
        except ClassificationSchemaError:
            # The text carries no classifiable term; §15 refuses an empty draft, and a
            # failed draft is counted rather than silently turned into a classification.
            unclassifiable += 1
            continue
        classified += 1
    return {"job": "market.taxonomy_classification", "classified": classified,
            "cached": cached, "unclassifiable": unclassifiable}


def _recompute(session: Session, *, now: datetime, settings: MarketSettings) -> dict:
    """Arms from stored classifications, then features and scores — the §16–§20 chain."""
    rows = repositories.latest_classifications_for(
        session, taxonomy_version=settings.taxonomy_version
    )
    drafts = [_classification_draft(row) for row in rows]
    observation_ids: dict[str, tuple[str, ...]] = {}
    for row in rows:
        if row["observation_id"] is None:
            continue
        current = observation_ids.get(row["content_fingerprint"], ())
        observation_ids[row["content_fingerprint"]] = (*current, str(row["observation_id"]))
    arms = generate_candidate_arms(
        drafts, min_support=settings.min_arm_support, observation_ids_by_content=observation_ids
    )
    arm_ids: dict[str, str] = {}
    for arm in arms:
        stored_id, _ = repositories.upsert_arm(session, arm, brand_id=settings.default_brand_id)
        arm_ids[arm.fingerprint] = stored_id
    by_observation_id = {
        repositories.deterministic_id(
            "observation", observation.entity_id, observation.source_id,
            observation.observed_at.isoformat(),
        ): observation
        for observation in repositories.load_observations(session)
    }
    grouped: dict[str, list[Any]] = {}
    for arm in arms:
        group = [by_observation_id[oid] for oid in arm.source_observation_ids
                 if oid in by_observation_id]
        if group:
            grouped[arm.fingerprint] = group
    confidence = _taxonomy_confidence(drafts)
    features = compute_arm_features(
        grouped, as_of=now, settings=settings, arm_ids=arm_ids,
        profiles={arm.fingerprint: arm.profile for arm in arms},
        taxonomy_confidence={fp: confidence.get(fp, 0.0) for fp in grouped},
    )
    scores = score_all_arms(features, as_of=now, settings=settings)
    written = persist_scores(session, scores, computed_at=now)
    return {"job": "market.opportunity_scoring", "arms": len(arms), "scores": written}


def _classification_draft(row: Any) -> ClassificationDraft:
    """A stored classification row → the draft shape `generate_candidate_arms` groups."""
    dimensions = {
        TaxonomyDimension(key): tuple(values)
        for key, values in (row["dimensions_json"] or {}).items()
    }
    return ClassificationDraft(
        dimensions=dimensions, confidence=float(row["confidence"]),
        unmapped_terms=tuple(row["unmapped_terms"] or ()),
        schema_version=row["classification_schema_version"],
        content_fingerprint=row["content_fingerprint"],
    )


def _taxonomy_confidence(drafts: list[ClassificationDraft]) -> dict[str, float]:
    """Mean classification confidence per arm fingerprint — the confidence input (§19)."""
    buckets: dict[str, list[float]] = {}
    for draft in drafts:
        arm_fingerprint = fingerprint(profile_from_dimensions(draft.dimensions))
        buckets.setdefault(arm_fingerprint, []).append(draft.confidence)
    return {key: sum(values) / len(values) for key, values in buckets.items()}


def _stale_inventory(session: Session, *, now: datetime, settings: MarketSettings) -> dict:
    """§26's stale-data cleanup as an inventory: P1 never deletes evidence (§3.2).

    Raw observations are immutable and quarantined records are the audit trail, so the
    weekly step reports how much stored evidence has aged past the stale window (and
    how old the oldest row is) instead of dropping rows an operator still needs.
    """
    cutoff = now - timedelta(hours=settings.freshness_stale_hours)
    total = int(session.execute(
        select(func.count()).select_from(observations)
    ).scalar_one())
    stale = int(session.execute(
        select(func.count()).select_from(observations)
        .where(observations.c.observed_at < cutoff)
    ).scalar_one())
    oldest = session.execute(select(func.min(observations.c.observed_at))).scalar_one()
    return {"job": "market.stale_data_cleanup", "observations": total, "stale": stale,
            "oldest_observed_at": None if oldest is None else str(oldest)}


# ---------------------------------------------------------------------------
# small helpers


def _settings() -> MarketSettings:
    return load_market_settings()


def _session(session_factory: SessionFactory | None = None,
             ) -> AbstractContextManager[Session]:
    from plm_platform.db import scoped_session

    if session_factory is None:
        return scoped_session("market")
    return scoped_session("market", factory=session_factory)


def _session_factory_for_api() -> SessionFactory:
    def factory() -> AbstractContextManager[Session]:
        return _session()

    return factory


def _parse_now(value: Any) -> datetime:
    return datetime.now(UTC) if value is None else datetime.fromisoformat(str(value))


def _ensure_source(session: Session) -> str:
    return repositories.upsert_source(
        session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API,
        base_identifier="https://www.googleapis.com/youtube/v3",
        configuration_version="p1-sources-v1",
    )


def _install_seeds(session: Session, *, source_id: str) -> int:
    from .collection.seeds import install_bootstrap_seeds

    return install_bootstrap_seeds(session, source_id=source_id, query_version="p1-queries-v1")
