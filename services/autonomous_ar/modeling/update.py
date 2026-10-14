# services/autonomous_ar/modeling/update.py
"""§74/§75 with ADR-0031: each stored result is classified once; one outcome per
(experiment, content) moves the posterior.

Order of decisions for a pending observation:

1. already settled (applied, provisional, off-horizon, duplicate) → skip, visibly;
2. no posterior row → `failed` (retried);
3. window is not the policy's `outcome_horizon` → `off_horizon` (history, not evidence);
4. maturity is `provisional` → `provisional` (§18: a partial window is never final);
5. unmeasured `normalized_reward` or attribution below the floor → `quarantined` (retried;
   R11: P9 owns the number, P2 never invents it);
6. another outcome of the same (experiment, content) at the horizon already applied →
   `skipped_duplicate`;
7. otherwise `applied`. The posterior row is then recomputed from the prior and every
   applied outcome as of `now` (`modeling.evidence`), never folded in sequentially.

The partial unique index on `applied` is the backstop if two workers race anyway.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from plm_contracts import ObservationWindow, Tri

from ..config import ARSettings
from ..domain.enums import ObservationMaturity, UpdateStatus
from ..domain.policy import PolicyConfig
from ..domain.posterior import UpdateRecord
from ..domain.reward import RewardObservation
from ..modeling.evidence import aware, posterior_as_of
from ..modeling.reward_model import NormalNormalRewardModel, RewardModel
from ..observability.metrics import record_posterior_updates
from ..storage import repositories

__all__ = ["UpdateSummary", "apply_pending_updates"]


@dataclass(frozen=True, slots=True)
class UpdateSummary:
    applied: int
    skipped_duplicate: int
    """Observations already settled by an earlier pass, plus duplicate outcomes."""
    quarantined: int
    failed: int
    provisional: int = 0
    off_horizon: int = 0


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
        maturity=ObservationMaturity(row["maturity"]), observed_at=aware(row["observed_at"]),
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


def _classify(session: Any, observation: RewardObservation,
              policy: PolicyConfig) -> UpdateStatus:
    if observation.observation_window is not policy.outcome_horizon:
        return UpdateStatus.OFF_HORIZON
    if observation.maturity is ObservationMaturity.PROVISIONAL:
        return UpdateStatus.PROVISIONAL
    if not observation.normalized_reward.is_known:
        return UpdateStatus.QUARANTINED
    if observation.attribution_confidence < policy.min_attribution_confidence:
        return UpdateStatus.QUARANTINED
    if repositories.has_applied_outcome(
        session, experiment_id=observation.experiment_id,
        content_id=observation.content_id, window=observation.observation_window.value,
    ):
        return UpdateStatus.SKIPPED_DUPLICATE
    return UpdateStatus.APPLIED


def apply_pending_updates(session: Any, *, settings: ARSettings, now: datetime,
                          policy: PolicyConfig,
                          model: RewardModel | None = None) -> UpdateSummary:
    """Classify every due observation at most once (§74); deterministic order (§73)."""
    resolved_model = model or NormalNormalRewardModel(
        observation_variance=settings.observation_variance,
        variance_floor=settings.posterior_variance_floor,
    )
    counts = dict.fromkeys(UpdateStatus, 0)
    already_settled = 0
    for row in repositories.load_pending_observations(session, now=now):
        observation_id = str(row["id"])
        if repositories.load_terminal_update(session, observation_id) is not None:
            already_settled += 1
            continue
        observation = _row_to_observation(row)
        posterior = repositories.load_posterior(session, observation.arm_id)
        if posterior is None:
            counts[UpdateStatus.FAILED] += 1
            repositories.insert_posterior_update(
                session,
                UpdateRecord(reward_observation_id=observation_id, arm_id=observation.arm_id,
                             version_before=0, version_after=0, status=UpdateStatus.FAILED,
                             effective_weight=0.0, updated_at=now),
                brand_id=row["brand_id"],
            )
            continue
        status = _classify(session, observation, policy)
        counts[status] += 1
        if status is not UpdateStatus.APPLIED:
            repositories.insert_posterior_update(
                session,
                UpdateRecord(reward_observation_id=observation_id, arm_id=observation.arm_id,
                             version_before=posterior.update_version,
                             version_after=posterior.update_version, status=status,
                             effective_weight=0.0, updated_at=now),
                brand_id=row["brand_id"],
            )
            if status is UpdateStatus.PROVISIONAL:
                repositories.increment_result_counts(session, arm_id=observation.arm_id,
                                                     maturity=observation.maturity, now=now)
            continue
        repositories.insert_posterior_update(
            session,
            UpdateRecord(reward_observation_id=observation_id, arm_id=observation.arm_id,
                         version_before=posterior.update_version,
                         version_after=posterior.update_version + 1,
                         status=UpdateStatus.APPLIED, effective_weight=1.0, updated_at=now),
            brand_id=row["brand_id"],
        )
        recomputed = posterior_as_of(session, posterior, model=resolved_model, policy=policy,
                                     as_of=now)
        repositories.upsert_posterior(
            session, replace(recomputed, update_version=posterior.update_version + 1),
            brand_id=row["brand_id"], expected_version=posterior.update_version,
        )
        repositories.increment_result_counts(session, arm_id=observation.arm_id,
                                             maturity=observation.maturity, now=now)
    summary = UpdateSummary(
        applied=counts[UpdateStatus.APPLIED],
        skipped_duplicate=already_settled + counts[UpdateStatus.SKIPPED_DUPLICATE],
        quarantined=counts[UpdateStatus.QUARANTINED], failed=counts[UpdateStatus.FAILED],
        provisional=counts[UpdateStatus.PROVISIONAL],
        off_horizon=counts[UpdateStatus.OFF_HORIZON],
    )
    record_posterior_updates(summary)  # §87: one count per applied/failed/quarantined
    return summary
