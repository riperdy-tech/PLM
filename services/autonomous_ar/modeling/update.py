# services/autonomous_ar/modeling/update.py
"""§74/§75: idempotent, version-guarded posterior updates from stored observations.

Order of refusals: already applied (skip) → no posterior row (fail with a log row) →
unmeasured `normalized_reward` (quarantine; R11: P9 owns the number, P2 never invents it) →
attribution below policy floor (quarantine) → otherwise one conjugate update, one log row.
The partial unique index on `applied` is the backstop if two workers race anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from plm_contracts import ObservationWindow, Tri

from ..config import ARSettings
from ..domain.enums import ObservationMaturity, UpdateStatus
from ..domain.policy import PolicyConfig
from ..domain.posterior import UpdateRecord
from ..domain.reward import RewardObservation
from ..modeling.decay import recency_weight
from ..modeling.reward_model import NormalNormalRewardModel, RewardModel
from ..storage import repositories

__all__ = ["UpdateSummary", "apply_pending_updates"]


@dataclass(frozen=True, slots=True)
class UpdateSummary:
    applied: int
    skipped_duplicate: int
    quarantined: int
    failed: int


def _aware(value: datetime) -> datetime:
    """SQLite drops `tzinfo` on read-back; stored instants are aware-UTC (P2.3 convention)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _stored_reward(row: Any) -> Tri[float]:
    """`normalized_reward_state` keeps absent / explicit zero / not-ready apart (P2.3);
    `known` — a measured zero included — is the only state an update may use."""
    state = row["normalized_reward_state"]
    if state == "known":
        return Tri.known(float(row["normalized_reward"]))
    if state == "not_ready":
        return Tri.not_ready()
    return Tri.unknown()


def _row_to_observation(row: Any) -> RewardObservation:
    return RewardObservation(
        experiment_id=row["experiment_id"], arm_id=row["arm_id"],
        content_id=row["content_id"],
        observation_window=ObservationWindow(row["observation_window"]),
        maturity=ObservationMaturity(row["maturity"]), observed_at=_aware(row["observed_at"]),
        raw_metrics={name: _tri_from_row(value)
                     for name, value in (row["raw_metrics_json"] or {}).items()},
        normalized_reward=_stored_reward(row),
        reward_version=row["reward_version"],
        attribution_confidence=float(row["attribution_confidence"]),
    )


def _tri_from_row(value: Any) -> Tri[float]:
    """Stored Tri dicts round-trip; a bare number can only be `known` (P2.3's write path)."""
    if isinstance(value, dict):
        state = value.get("state")
        if state == "known":
            return Tri.known(float(value["value"]))
        return Tri.not_ready() if state == "not_ready" else Tri.unknown()
    return Tri.known(float(value))


def apply_pending_updates(session: Any, *, settings: ARSettings, now: datetime,
                          policy: PolicyConfig,
                          model: RewardModel | None = None) -> UpdateSummary:
    """Apply every due observation at most once (§74); deterministic order (§73)."""
    resolved_model = model or NormalNormalRewardModel(
        observation_variance=settings.observation_variance,
        variance_floor=settings.posterior_variance_floor,
    )
    applied = skipped = quarantined = failed = 0
    for row in repositories.load_pending_observations(session, now=now):
        observation_id = str(row["id"])
        observation = _row_to_observation(row)
        if repositories.load_posterior_update(session, observation_id) is not None:
            skipped += 1
            continue
        posterior = repositories.load_posterior(session, observation.arm_id)
        if posterior is None:
            failed += 1
            repositories.insert_posterior_update(
                session,
                UpdateRecord(reward_observation_id=observation_id, arm_id=observation.arm_id,
                             version_before=0, version_after=0, status=UpdateStatus.FAILED,
                             effective_weight=0.0, updated_at=now),
                brand_id=row["brand_id"],
            )
            continue
        if not observation.normalized_reward.is_known:
            quarantined += 1
            repositories.insert_posterior_update(
                session,
                UpdateRecord(reward_observation_id=observation_id, arm_id=observation.arm_id,
                             version_before=posterior.update_version,
                             version_after=posterior.update_version,
                             status=UpdateStatus.QUARANTINED, effective_weight=0.0,
                             updated_at=now),
                brand_id=row["brand_id"],
            )
            continue
        if observation.attribution_confidence < policy.min_attribution_confidence:
            quarantined += 1
            repositories.insert_posterior_update(
                session,
                UpdateRecord(reward_observation_id=observation_id, arm_id=observation.arm_id,
                             version_before=posterior.update_version,
                             version_after=posterior.update_version,
                             status=UpdateStatus.QUARANTINED, effective_weight=0.0,
                             updated_at=now),
                brand_id=row["brand_id"],
            )
            continue
        if policy.nonstationarity.enabled:
            weight = recency_weight(observation.observed_at, now=now,
                                    half_life_days=policy.nonstationarity.decay_half_life_days)
        else:
            weight = 1.0
        if weight <= 0.0:
            skipped += 1
            continue
        updated = resolved_model.update(posterior, observation, effective_weight=weight,
                                        received_at=now)
        repositories.upsert_posterior(session, updated, brand_id=row["brand_id"],
                                      expected_version=posterior.update_version)
        repositories.insert_posterior_update(
            session,
            UpdateRecord(reward_observation_id=observation_id, arm_id=observation.arm_id,
                         version_before=posterior.update_version,
                         version_after=updated.update_version, status=UpdateStatus.APPLIED,
                         effective_weight=weight, updated_at=now),
            brand_id=row["brand_id"],
        )
        repositories.increment_result_counts(session, arm_id=observation.arm_id,
                                             maturity=observation.maturity, now=now)
        applied += 1
    return UpdateSummary(applied=applied, skipped_duplicate=skipped, quarantined=quarantined,
                         failed=failed)
