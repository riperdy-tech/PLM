"""Measurement, reward and learning over HTTP (R5): P7/P8 sources into P9, P9 to P2.

* ``evaluate_outcome`` measures every published unit at the run's ``requested_at``
  (``workflow.now()`` once the horizon elapsed) through P9's normal ingest paths:

  1. the configured FX rates (``POST /analytics/fx-rates``; replays are no-ops);
  2. P8's Seam C7 leg-1 record (``POST /analytics/ingest/releases``), linking each ISRC
     to the lineage;
  3. the video's analytics for the horizon window from P7's source
     (``GET /youtube/videos/{id}/analytics``) into ``POST /analytics/ingest/youtube``,
     labelled ``synthetic`` when the source is a mock;
  4. the release's monthly statements up to ``requested_at`` from P8
     (``GET /distribution/releases/{id}/statements``), each into
     ``POST /analytics/ingest/distributor``, received at ``requested_at``;
  5. the unit's costs, from the ledger entries of the writes the phases' guards funded
     (the ``costs`` input, read by the ``write_costs`` platform activity), one
     incremental record per (category, month incurred) into
     ``POST /analytics/ingest/cost``.
     A write whose cost is unknown (still reserved, unreported) refuses the step: an
     unknown cost is never booked as zero;
  6. ``POST /analytics/evaluation/measure``: P9 computes the outcome from what it stored
     and enqueues the reward in its outbox. The pipeline passes no amount.

* ``dispatch_reward``: ``POST /analytics/rewards/dispatch`` runs one P9 outbox pass and
  reports each fact's delivery status. ``DELIVERED`` for all completes the step; pending
  or uncertain is :class:`~.phase_client.StepNotReady` (retried); failed refuses.
* ``learn``: ``POST /ar/update?as_of=`` applies the stored rewards to P2's posterior,
  then ``POST /ar/cycles`` runs the next allocation cycle at the same instant with the
  configured cycle parameters and a seed derived from the run (``stable_seed``).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Final

from ..clock import stable_seed
from ..temporal import Phase, ValidationFailure
from .http_base import HttpStepsBase, instant
from .phase_client import StepNotReady, request_key, require
from .types import StepRequest, StepResult

__all__ = ["COST_CATEGORY", "OutcomeSteps"]

COST_CATEGORY: Final[dict[str, str]] = {
    "music.generate": "third_party_api",
    "youtube.upload": "third_party_api",
    "youtube.thumbnail": "third_party_api",
    "youtube.playlist": "third_party_api",
    "distribution.submit": "distribution",
}
"""P9's cost category of each guarded capability (``analytics.domain.enums.CostCategory``)."""

_METRICS: Final[tuple[str, ...]] = (
    "views", "watch_time_minutes", "average_view_duration_seconds",
    "average_percentage_viewed", "subscribers_gained", "subscribers_lost",
)


def _unit_costs(costs: list[dict[str, Any]], *, media_package_id: str,
                p3_package_id: str | None) -> list[dict[str, Any]]:
    mine = []
    for cost in costs:
        context = cost.get("context") or {}
        if context.get("media_package_id") == media_package_id or (
                p3_package_id is not None and context.get("p3_package_id") == p3_package_id):
            mine.append(cost)
    return mine


def _cost_records(costs: list[dict[str, Any]], *, unit: str,
                  fallback_at: str) -> list[dict[str, Any]]:
    """One incremental cost record per (category, calendar month incurred) for one unit.

    A simulated write has no settlement instant; it is dated ``fallback_at`` (the
    publication). Two currencies in one (category, month) cannot share P9's cost key
    and are refused rather than converted here."""
    if not any(c.get("status") != "not_performed" for c in costs):
        raise ValidationFailure(f"{unit}: no external write was reported for this unit, so "
                                "its cost is unknown")
    totals: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    currencies: dict[tuple[str, str], str] = {}
    latest: dict[tuple[str, str], str] = {}
    sources: dict[tuple[str, str], list[str]] = defaultdict(list)
    for cost in costs:
        if cost.get("status") == "not_performed":
            continue  # the guard denied it or recorded intent only: nothing was written
        capability = str(cost.get("capability"))
        category = COST_CATEGORY.get(capability)
        if category is None:
            raise ValidationFailure(f"{unit}: no P9 cost category for capability "
                                    f"{capability!r}")
        actual = cost.get("actual")
        if not isinstance(actual, dict):
            raise ValidationFailure(
                f"cost_unknown: {capability} write {cost.get('idempotency_key')} is "
                f"{cost.get('status')}; an unknown cost is never booked as zero")
        try:
            amount = Decimal(str(actual["amount"]))
        except (KeyError, InvalidOperation):
            raise ValidationFailure(f"{unit}: unreadable cost {actual!r}") from None
        settled = str(cost.get("settled_at") or fallback_at)
        group = (category, instant(settled, f"{unit} cost").strftime("%Y-%m"))
        currency = str(actual["currency"])
        if currencies.setdefault(group, currency) != currency:
            raise ValidationFailure(f"{unit}: {group} costs are in two currencies")
        totals[group] += amount
        latest[group] = max(latest.get(group, settled), settled)
        sources[group].append(f"{capability}={cost.get('basis')}")
    return [
        {"entity_id": unit, "entity_type": "video", "category": category,
         "amount": str(totals[(category, month)]), "currency": currencies[(category, month)],
         "period": month,
         "description": ("pipeline external writes: "
                         + ", ".join(sorted(sources[(category, month)])))[:255],
         "incurred_at": latest[(category, month)]}
        for category, month in sorted(totals)
    ]


class OutcomeSteps(HttpStepsBase):
    # -- evaluate_outcome ------------------------------------------------------------------

    async def evaluate_outcome(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._evaluate_outcome, request)

    def _evaluate_outcome(self, request: StepRequest) -> StepResult:
        key = request.idempotency_key
        inputs = request.inputs
        window = str(require(inputs, "outcome_horizon", "evaluate_outcome inputs"))
        publications = require(require(inputs, "upload", "evaluate_outcome inputs"),
                               "publications", "upload outputs")
        releases = require(require(inputs, "submit_distribution", "evaluate_outcome inputs"),
                           "releases", "submit_distribution outputs")
        costs = inputs.get("costs")
        if not isinstance(costs, list):
            raise ValidationFailure("evaluate_outcome inputs: 'costs' (the ledger's view of "
                                    "the run's writes) is missing")
        by_package = {r["media_package_id"]: r for r in releases if isinstance(r, dict)}
        observed_at = request.requested_at.isoformat()
        p7, p8, p9 = (self._client(Phase.YOUTUBE), self._client(Phase.DISTRIBUTION),
                      self._client(Phase.ANALYTICS))
        if self._config.fx_rates:
            p9.post("/analytics/fx-rates", idempotency_key=request_key(key, "fx-rates"),
                    body={"rates": [rate.wire() for rate in self._config.fx_rates]})
        rewards: list[dict[str, Any]] = []
        for publication in publications:
            media_package_id = str(require(publication, "media_package_id", "publication"))
            video_id = str(require(publication, "youtube_video_id", "publication"))
            published_at = instant(require(publication, "published_at", "publication"),
                                   f"{video_id} published_at")
            if published_at > request.requested_at:
                raise ValidationFailure(f"{video_id} was published after the measurement")
            release = by_package.get(media_package_id)
            if release is None:
                raise ValidationFailure(f"no release was submitted for {media_package_id}")
            release_id = str(require(release, "release_id", "release"))
            p9.post("/analytics/ingest/releases",
                    idempotency_key=request_key(key, "link-release", release_id),
                    body={"record": require(release, "distribution_record", "release")})

            analytics = p7.get(f"/youtube/videos/{video_id}/analytics", idempotency_key=key,
                               params={"window": window, "as_of": observed_at})
            metrics = require(analytics, "metrics", f"P7 analytics of {video_id}")
            revenue = metrics.get("estimated_revenue")
            snapshot: dict[str, Any] = {
                "video_id": video_id,
                "channel_id": str(require(publication, "channel_id", "publication")),
                "date_str": request.requested_at.date().isoformat(), "window": window,
                "observed_at": observed_at,
                "source": ("synthetic" if analytics.get("provider_mode") == "mock"
                           else "youtube_analytics"),
                **{name: metrics.get(name) for name in _METRICS},
            }
            if isinstance(revenue, dict):
                snapshot["estimated_revenue"] = str(require(revenue, "amount", "revenue"))
                snapshot["revenue_currency"] = str(require(revenue, "currency", "revenue"))
            p9.post("/analytics/ingest/youtube",
                    idempotency_key=request_key(key, "ingest-youtube", video_id), body=snapshot)

            reported = p8.get(f"/distribution/releases/{release_id}/statements",
                              idempotency_key=key, params={"as_of": observed_at})
            provider = require(reported, "provider", "P8 statements")
            for statement in require(reported, "statements", "P8 statements"):
                period = str(require(statement, "period", "P8 statement"))
                p9.post("/analytics/ingest/distributor",
                        idempotency_key=request_key(key, "ingest-statement",
                                                    f"{release_id}.{period}"),
                        body={"provider": provider, "period": period,
                              "currency": require(statement, "currency", "P8 statement"),
                              "raw_content": require(statement, "raw_content",
                                                     "P8 statement"),
                              "source_reference": require(statement, "source_reference",
                                                          "P8 statement"),
                              "rows": require(statement, "rows", "P8 statement"),
                              "received_at": observed_at})

            unit_costs = _unit_costs(costs, media_package_id=media_package_id,
                                     p3_package_id=publication.get("p3_package_id"))
            for record in _cost_records(unit_costs, unit=video_id,
                                        fallback_at=published_at.isoformat()):
                p9.post("/analytics/ingest/cost",
                        idempotency_key=request_key(
                            key, "ingest-cost",
                            f"{video_id}.{record['category']}.{record['period']}"),
                        body=record)

            reward = p9.post(
                "/analytics/evaluation/measure",
                idempotency_key=request_key(key, "measure", video_id),
                body={"publication": require(publication, "publication_event", "publication"),
                      "isrcs": require(release, "isrcs", "release"), "window": window,
                      "observed_at": observed_at, "closed_by_source": False,
                      "quality_status": None})
            rewards.append({
                "experiment_id": str(require(reward, "experiment_id", "P9 reward")),
                "content_id": str(require(reward, "content_id", "P9 reward")),
                "observation_window": str(require(reward, "observation_window", "P9 reward")),
                "status": str(require(reward, "status", "P9 reward")),
                "arm_id": str(require(reward, "arm_id", "P9 reward")),
                "normalized_reward": reward.get("normalized_reward"),
                "reward_version": reward.get("reward_version"),
                "measurement_source": snapshot["source"],
            })
        return StepResult(
            outputs={"rewards": rewards, "observed_at": observed_at, "window": window},
            provider_ids={f"reward.{n}": f"{r['experiment_id']}|{r['content_id']}"
                          for n, r in enumerate(rewards)},
        )

    # -- dispatch_reward -------------------------------------------------------------------

    async def dispatch_reward(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._dispatch_reward, request)

    def _dispatch_reward(self, request: StepRequest) -> StepResult:
        evaluated = require(request.inputs, "evaluate_outcome", "dispatch_reward inputs")
        rewards = require(evaluated, "rewards", "evaluate_outcome outputs")
        if not isinstance(rewards, list) or not rewards:
            raise ValidationFailure("evaluate_outcome outputs: no reward to dispatch")
        facts = [{"experiment_id": r["experiment_id"], "content_id": r["content_id"],
                  "observation_window": r["observation_window"], "status": r["status"]}
                 for r in rewards]
        answer = self._client(Phase.ANALYTICS).post(
            "/analytics/rewards/dispatch",
            idempotency_key=request_key(request.idempotency_key, "dispatch"),
            body={"facts": facts})
        reported = require(answer, "facts", "P9 dispatch")
        statuses = [str(f.get("delivery_status")) for f in reported if isinstance(f, dict)]
        if len(statuses) != len(facts):
            raise ValidationFailure(f"P9 reported {len(statuses)} of {len(facts)} facts")
        failed = [s for s in statuses if s not in ("delivered", "pending", "uncertain")]
        if failed:
            raise ValidationFailure(f"reward delivery refused or never queued: {statuses}")
        if any(s != "delivered" for s in statuses):
            raise StepNotReady(f"rewards not yet acknowledged by P2: {statuses}",
                               code="reward_not_delivered")
        return StepResult(
            outputs={"delivery_status": "DELIVERED", "facts": reported,
                     "report": answer.get("report")},
            provider_ids={f"reward.{n}": f"{f['experiment_id']}|{f['content_id']}"
                          for n, f in enumerate(facts)},
        )

    # -- learn: P2 applies the reward, then allocates again --------------------------------

    async def learn(self, request: StepRequest) -> StepResult:
        return await asyncio.to_thread(self._learn, request)

    def _learn(self, request: StepRequest) -> StepResult:
        cycle = self._config.require_learning(request.step)
        dispatched = require(request.inputs, "dispatch_reward", "learn inputs")
        if dispatched.get("delivery_status") != "DELIVERED":
            raise ValidationFailure("learn: the reward is not delivered to P2")
        key = request.idempotency_key
        as_of = request.requested_at.isoformat()
        p2 = self._client(Phase.AR)
        update = p2.post("/ar/update", idempotency_key=request_key(key, "update"),
                         params={"as_of": as_of})
        summary = p2.post("/ar/cycles", idempotency_key=request_key(key, "cycle"), body={
            "brand_id": request.brand_id, "slot_count": cycle.slot_count,
            "budget": cycle.budget, "as_of": as_of, "policy_version": cycle.policy_version,
            "activation_state": cycle.activation_state,
            "random_seed": stable_seed("pipeline.learn", request.workflow_id),
        })
        cycle_id = str(require(summary, "cycle_id", "P2 cycle"))
        return StepResult(
            outputs={"update": update, "next_cycle": summary, "as_of": as_of},
            provider_ids={"next_cycle_id": cycle_id},
        )
