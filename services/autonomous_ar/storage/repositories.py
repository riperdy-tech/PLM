# services/autonomous_ar/storage/repositories.py
"""Row-level helpers for the `ar` schema. Ids derive from stable identity; no uuid4.

Semantics that carry rules (not convenience):
* policies are immutable per version (§92);
* feature snapshots are insert-only — a decision's market context is never recomputed
  from current P1 values (no leakage, Review Focus 6);
* posterior writes are guarded by `update_version` (§75);
* reward observations are idempotent on `(experiment_id, observation_window)` (§73);
* `applied` posterior updates are unique per reward observation (§74).

Every insert path is idempotent on its natural key (the deterministic id itself):
replaying a scenario returns the stored row instead of raising a unique violation —
the defect P1 hit when deterministic ids met a naive `INSERT` twice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.arm import ContentArm
from ..domain.directive import ProductionDirective
from ..domain.enums import (
    AllocationCycleStatus,
    ArmLifecycleStatus,
    DirectiveStatus,
    ObservationMaturity,
)
from ..domain.policy import PolicyConfig
from ..domain.posterior import PosteriorState, UpdateRecord
from ..domain.reward import RewardObservation
from .ids import deterministic_id
from .models import (
    ar_allocation_cycle,
    ar_allocation_decision,
    ar_allocation_policy,
    ar_arm_exposure,
    ar_arm_feature_snapshot,
    ar_arm_posterior,
    ar_arm_similarity,
    ar_baseline_config,
    ar_channel_state,
    ar_content_arm_state,
    ar_experiment_result,
    ar_manual_override,
    ar_posterior_update_log,
    ar_production_directive,
    ar_reward_observation,
)

__all__ = [
    "PolicyImmutableError", "PosteriorVersionConflict", "activate_policy",
    "finish_cycle", "get_channel_state", "increment_result_counts", "insert_cycle",
    "insert_decision", "insert_directive", "insert_experiment_result", "insert_exposure",
    "insert_feature_snapshot", "insert_override", "insert_policy",
    "insert_posterior_update", "insert_reward_observation", "is_in_cooldown_state",
    "latest_observation_instant", "list_queued_directives", "load_active_overrides",
    "load_active_policy", "load_all_latest_feature_snapshots", "load_arm_state",
    "load_arm_states", "load_baselines", "load_cycle", "load_decisions",
    "load_directives_for_cycle", "load_exposure", "load_exposure_score",
    "load_feature_snapshot", "load_latest_feature_snapshot", "load_pending_observations",
    "load_policy", "load_posterior", "load_posterior_update", "load_similarity",
    "record_selection", "replace_baselines", "replace_similarity", "set_arm_status",
    "set_channel_state", "upsert_arm_state", "upsert_posterior",
]


class PolicyImmutableError(RuntimeError):
    """A policy version already exists with different content (§92)."""


class PosteriorVersionConflict(RuntimeError):
    """Someone else advanced the arm's posterior first (§75)."""


def _aware(value: datetime) -> datetime:
    """SQLite drops `tzinfo` on read-back; the stored instants are aware-UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# --- policies -----------------------------------------------------------------------

def _policy_row(config: PolicyConfig, *, brand_id: str | None,
                created_at: datetime) -> dict[str, Any]:
    return {
        "id": deterministic_id("policy", brand_id or "global", config.version),
        "brand_id": brand_id, "name": config.name, "version": config.version,
        "exploit_fraction": config.exploit_fraction,
        "adjacent_explore_fraction": config.adjacent_explore_fraction,
        "radical_explore_fraction": config.radical_explore_fraction,
        "baseline_fraction": config.baseline_fraction,
        "max_exact_arm_share": config.max_exact_arm_share,
        "max_cluster_share": config.max_cluster_share,
        "max_genre_share": config.max_genre_share,
        "max_use_case_share": config.max_use_case_share,
        "max_language_share": config.max_language_share,
        "fatigue_config_json": config.fatigue.model_dump(),
        "nonstationarity_config_json": config.nonstationarity.model_dump(),
        "retirement_config_json": config.retirement.model_dump(),
        "created_at": created_at, "active": False,
    }


def _policy_from_row(row: sa.RowMapping) -> PolicyConfig:
    return PolicyConfig(
        name=row["name"], version=row["version"],
        exploit_fraction=row["exploit_fraction"],
        adjacent_explore_fraction=row["adjacent_explore_fraction"],
        radical_explore_fraction=row["radical_explore_fraction"],
        baseline_fraction=row["baseline_fraction"],
        max_exact_arm_share=row["max_exact_arm_share"],
        max_cluster_share=row["max_cluster_share"], max_genre_share=row["max_genre_share"],
        max_use_case_share=row["max_use_case_share"],
        max_language_share=row["max_language_share"],
        fatigue=row["fatigue_config_json"], nonstationarity=row["nonstationarity_config_json"],
        retirement=row["retirement_config_json"],
    )


def insert_policy(session: Session, config: PolicyConfig, *, brand_id: str | None,
                  created_at: datetime) -> str:
    """Insert once per version; replaying the same version returns the existing id."""
    existing = session.execute(
        sa.select(ar_allocation_policy).where(ar_allocation_policy.c.version == config.version)
    ).mappings().one_or_none()
    if existing is not None:
        if _policy_from_row(existing) != config:
            raise PolicyImmutableError(
                f"policy {config.version!r} already exists with different content: create a new "
                "version and activate it, never mutate a used policy (§92)"
            )
        return str(existing["id"])
    row = _policy_row(config, brand_id=brand_id, created_at=created_at)
    try:
        session.execute(sa.insert(ar_allocation_policy).values(**row))
    except IntegrityError:
        session.rollback()
        return str(session.execute(
            sa.select(ar_allocation_policy.c.id)
            .where(ar_allocation_policy.c.version == config.version)
        ).scalar_one())
    return str(row["id"])


def activate_policy(session: Session, version: str) -> None:
    exists = session.execute(
        sa.select(ar_allocation_policy.c.id)
        .where(ar_allocation_policy.c.version == version)
    ).scalar_one_or_none()
    if exists is None:
        raise ValueError(f"policy {version!r} does not exist: activate a stored version only")
    session.execute(sa.update(ar_allocation_policy).values(active=False))
    session.execute(
        sa.update(ar_allocation_policy).where(ar_allocation_policy.c.version == version)
        .values(active=True)
    )


def load_policy(session: Session, version: str) -> PolicyConfig | None:
    row = session.execute(
        sa.select(ar_allocation_policy).where(ar_allocation_policy.c.version == version)
    ).mappings().one_or_none()
    return None if row is None else _policy_from_row(row)


def load_active_policy(session: Session) -> PolicyConfig | None:
    """The policy row `activate_policy` marked active, or `None` when none is (§92).

    Callers that find `None` refuse the operation — the update worker never runs on code
    defaults, and activation is the operator's explicit act.
    """
    row = session.execute(
        sa.select(ar_allocation_policy).where(ar_allocation_policy.c.active.is_(True))
        .order_by(ar_allocation_policy.c.version)
    ).mappings().first()
    return None if row is None else _policy_from_row(row)


# --- arm state ----------------------------------------------------------------------

def upsert_arm_state(session: Session, *, brand_id: str, arm_id: str,
                     now: datetime) -> None:
    """Create the arm's P2 state row on first sighting; never touches P1's arm."""
    exists = session.execute(
        sa.select(ar_content_arm_state.c.id)
        .where(ar_content_arm_state.c.arm_id == arm_id)
    ).scalar_one_or_none()
    if exists is None:
        session.execute(sa.insert(ar_content_arm_state).values(
            id=deterministic_id("arm_state", arm_id), brand_id=brand_id, arm_id=arm_id,
            status=ArmLifecycleStatus.CANDIDATE.value, last_updated_at=now,
        ))
    else:
        session.execute(
            sa.update(ar_content_arm_state)
            .where(ar_content_arm_state.c.arm_id == arm_id)
            .values(last_updated_at=now)
        )


def set_arm_status(session: Session, *, arm_id: str, status: ArmLifecycleStatus,
                   now: datetime, reason: str | None = None,
                   cooldown_until: datetime | None = None) -> None:
    session.execute(
        sa.update(ar_content_arm_state)
        .where(ar_content_arm_state.c.arm_id == arm_id)
        .values(status=status.value, retirement_reason=reason, cooldown_until=cooldown_until,
                last_updated_at=now, state_version=ar_content_arm_state.c.state_version + 1)
    )


def record_selection(session: Session, *, arm_id: str, now: datetime,
                     cooldown_until: datetime | None = None,
                     recent_exposure_score: float | None = None) -> None:
    values: dict[str, Any] = {
        "selection_count": ar_content_arm_state.c.selection_count + 1,
        "last_selected_at": now,
        "first_selected_at": sa.func.coalesce(ar_content_arm_state.c.first_selected_at, now),
        "status": ArmLifecycleStatus.ACTIVE.value,
        "cooldown_until": cooldown_until,
        "last_updated_at": now,
        "state_version": ar_content_arm_state.c.state_version + 1,
    }
    if recent_exposure_score is not None:  # P2.10 passes the refreshed §26 score
        values["recent_exposure_score"] = recent_exposure_score
    session.execute(
        sa.update(ar_content_arm_state).where(ar_content_arm_state.c.arm_id == arm_id)
        .values(**values)
    )


def load_arm_states(session: Session) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(
        sa.select(ar_content_arm_state).order_by(ar_content_arm_state.c.arm_id)
    ).mappings()]


def load_arm_state(session: Session, arm_id: str) -> dict[str, Any] | None:
    row = session.execute(
        sa.select(ar_content_arm_state)
        .where(ar_content_arm_state.c.arm_id == arm_id)
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def load_exposure_score(session: Session, arm_id: str) -> float:
    """The decayed §26 exposure score carried on the arm's state row (`0.0` when new)."""
    value = session.execute(
        sa.select(ar_content_arm_state.c.recent_exposure_score)
        .where(ar_content_arm_state.c.arm_id == arm_id)
    ).scalar_one_or_none()
    return float(value) if value is not None else 0.0


def is_in_cooldown_state(session: Session, *, arm_id: str, now: datetime) -> bool:
    """§9: a stored `cooldown_until` in the future keeps the arm out of this cycle."""
    until = session.execute(
        sa.select(ar_content_arm_state.c.cooldown_until)
        .where(ar_content_arm_state.c.arm_id == arm_id)
    ).scalar_one_or_none()
    return until is not None and _aware(until) > now


# --- feature snapshots (insert-only: no leakage) ------------------------------------

def insert_feature_snapshot(session: Session, *, brand_id: str, arm: ContentArm,
                            captured_at: datetime, feature_version: str) -> str:
    snapshot_id = deterministic_id("feature_snapshot", arm.arm_id, captured_at.isoformat())
    exists = session.execute(
        sa.select(ar_arm_feature_snapshot.c.id)
        .where(ar_arm_feature_snapshot.c.id == snapshot_id)
    ).scalar_one_or_none()
    if exists is not None:
        return snapshot_id
    session.execute(sa.insert(ar_arm_feature_snapshot).values(
        id=snapshot_id, brand_id=brand_id, arm_id=arm.arm_id, captured_at=captured_at,
        p1_score=arm.p1_score, p1_confidence=arm.p1_confidence,
        demand_score=arm.component_scores["demand"],
        growth_score=arm.component_scores["growth"],
        competition_score=arm.component_scores["competition"],
        cross_platform_score=arm.component_scores["cross_platform"],
        production_fit_score=arm.component_scores["production_fit"],
        freshness_seconds=arm.freshness_seconds,
        # The dimensions ride inside `feature_json` so §102's degraded path can rebuild
        # the arm without reading P1 (P2.10; the row is the arm's last known state).
        feature_json={
            "components": dict(arm.component_scores),
            "dimensions": asdict(arm.dimensions),
            "observation_count": arm.observation_count,
            "source_count": arm.source_count,
            "score_version": arm.score_version,
        },
        feature_version=feature_version,
    ))
    return snapshot_id


def load_feature_snapshot(session: Session, snapshot_id: str) -> dict[str, Any] | None:
    row = session.execute(
        sa.select(ar_arm_feature_snapshot)
        .where(ar_arm_feature_snapshot.c.id == snapshot_id)
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def load_latest_feature_snapshot(session: Session, arm_id: str) -> dict[str, Any] | None:
    """The arm's most recent snapshot — §39's stored score for reopen decisions."""
    row = session.execute(
        sa.select(ar_arm_feature_snapshot)
        .where(ar_arm_feature_snapshot.c.arm_id == arm_id)
        .order_by(ar_arm_feature_snapshot.c.captured_at.desc())
    ).mappings().first()
    return None if row is None else dict(row)


def load_all_latest_feature_snapshots(session: Session, *, brand_id: str,
                                      ) -> list[dict[str, Any]]:
    """One row per arm: its newest snapshot for the brand (§102's stored rebuild)."""
    newest = (
        sa.select(ar_arm_feature_snapshot.c.arm_id,
                  sa.func.max(ar_arm_feature_snapshot.c.captured_at).label("captured_at"))
        .where(ar_arm_feature_snapshot.c.brand_id == brand_id)
        .group_by(ar_arm_feature_snapshot.c.arm_id)
        .subquery()
    )
    return [dict(row) for row in session.execute(
        sa.select(ar_arm_feature_snapshot)
        .join(newest, sa.and_(
            ar_arm_feature_snapshot.c.arm_id == newest.c.arm_id,
            ar_arm_feature_snapshot.c.captured_at == newest.c.captured_at))
        .order_by(ar_arm_feature_snapshot.c.arm_id)
    ).mappings()]


# --- posteriors ---------------------------------------------------------------------

def upsert_posterior(session: Session, state: PosteriorState, *, brand_id: str,
                     expected_version: int) -> None:
    """Insert at version 0; later writes must match the stored `update_version` (§75)."""
    existing = load_posterior(session, state.arm_id)
    values = {
        "brand_id": brand_id, "arm_id": state.arm_id, "model_type": state.model_type,
        "model_version": state.model_version, "prior_mean": state.prior_mean,
        "prior_variance": state.prior_variance, "posterior_mean": state.posterior_mean,
        "posterior_variance": state.posterior_variance,
        "observation_count": state.observation_count,
        "effective_observation_count": state.effective_observation_count,
        "last_update_at": state.last_update_at, "update_version": state.update_version,
        "context_state_json": dict(state.context_state_json),
    }
    if existing is None:
        session.execute(sa.insert(ar_arm_posterior).values(
            id=deterministic_id("posterior", state.arm_id), **values,
        ))
        return
    if existing.update_version != expected_version:
        raise PosteriorVersionConflict(
            f"posterior for {state.arm_id!r} is at version {existing.update_version}, "
            f"writer holds {expected_version}: reload and retry (§75)"
        )
    # RETURNING tells us atomically whether the versioned row was still ours to write
    written = session.execute(
        sa.update(ar_arm_posterior)
        .where(ar_arm_posterior.c.arm_id == state.arm_id,
               ar_arm_posterior.c.update_version == expected_version)
        .values(**values)
        .returning(ar_arm_posterior.c.update_version)
    ).scalar_one_or_none()
    if written is None:
        raise PosteriorVersionConflict(
            f"posterior for {state.arm_id!r} was advanced concurrently (§75)"
        )


def load_posterior(session: Session, arm_id: str) -> PosteriorState | None:
    row = session.execute(
        sa.select(ar_arm_posterior).where(ar_arm_posterior.c.arm_id == arm_id)
    ).mappings().one_or_none()
    if row is None:
        return None
    return PosteriorState(
        arm_id=row["arm_id"], model_type=row["model_type"], model_version=row["model_version"],
        prior_mean=row["prior_mean"], prior_variance=row["prior_variance"],
        posterior_mean=row["posterior_mean"], posterior_variance=row["posterior_variance"],
        observation_count=row["observation_count"],
        effective_observation_count=row["effective_observation_count"],
        update_version=row["update_version"], last_update_at=_aware(row["last_update_at"]),
        context_state_json=row["context_state_json"] or {},
    )


# --- cycles, decisions, directives --------------------------------------------------

def insert_cycle(session: Session, *, cycle_id: str, brand_id: str, started_at: datetime,
                 status: AllocationCycleStatus, slot_count: int, budget: float,
                 policy_id: str, p1_snapshot_time: datetime, activation_state: str,
                 random_seed: int) -> bool:
    """False when the cycle already exists — a replay is idempotent, not a second cycle."""
    existing = session.execute(
        sa.select(ar_allocation_cycle.c.id).where(ar_allocation_cycle.c.id == cycle_id)
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.execute(sa.insert(ar_allocation_cycle).values(
        id=cycle_id, brand_id=brand_id, started_at=started_at, status=status.value,
        slot_count=slot_count, budget=budget, policy_id=policy_id,
        p1_snapshot_time=p1_snapshot_time, activation_state=activation_state,
        random_seed=random_seed, warning_codes_json=[],
    ))
    return True


def finish_cycle(session: Session, *, cycle_id: str, status: AllocationCycleStatus,
                 finished_at: datetime, decision_count: int, selected_count: int,
                 rejected_count: int, warning_codes: Sequence[str]) -> None:
    session.execute(sa.update(ar_allocation_cycle)
                    .where(ar_allocation_cycle.c.id == cycle_id)
                    .values(status=status.value, finished_at=finished_at,
                            decision_count=decision_count, selected_count=selected_count,
                            rejected_count=rejected_count,
                            warning_count=len(warning_codes),
                            warning_codes_json=list(warning_codes)))


def load_cycle(session: Session, cycle_id: str) -> dict[str, Any] | None:
    row = session.execute(
        sa.select(ar_allocation_cycle).where(ar_allocation_cycle.c.id == cycle_id)
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def insert_decision(session: Session, *, cycle_id: str, arm_id: str,
                    allocation_type: str, sampled_reward: float,
                    posterior_expected_reward: float, selection_priority: float,
                    rank_before_constraints: int | None, rank_after_constraints: int | None,
                    constraint_adjustments: Mapping[str, Any], reason_codes: Sequence[str],
                    policy_version: str, model_version: str, feature_version: str,
                    normalization_version: str, p1_score_version: str | None,
                    feature_snapshot_id: str, created_at: datetime) -> str:
    decision_id = deterministic_id("decision", cycle_id, arm_id)
    exists = session.execute(
        sa.select(ar_allocation_decision.c.id)
        .where(ar_allocation_decision.c.id == decision_id)
    ).scalar_one_or_none()
    if exists is not None:
        return decision_id
    session.execute(sa.insert(ar_allocation_decision).values(
        id=decision_id, cycle_id=cycle_id, arm_id=arm_id, allocation_type=allocation_type,
        sampled_reward=sampled_reward, posterior_expected_reward=posterior_expected_reward,
        selection_priority=selection_priority,
        rank_before_constraints=rank_before_constraints,
        rank_after_constraints=rank_after_constraints,
        constraint_adjustments_json=dict(constraint_adjustments),
        reason_codes=list(reason_codes), policy_version=policy_version,
        model_version=model_version, feature_version=feature_version,
        normalization_version=normalization_version, p1_score_version=p1_score_version,
        feature_snapshot_id=feature_snapshot_id, created_at=created_at,
    ))
    return decision_id


def load_decisions(session: Session, cycle_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(
        sa.select(ar_allocation_decision)
        .where(ar_allocation_decision.c.cycle_id == cycle_id)
        .order_by(ar_allocation_decision.c.rank_after_constraints.nullslast(),
                  ar_allocation_decision.c.arm_id)
    ).mappings()]


def insert_directive(session: Session, directive: ProductionDirective, *, brand_id: str,
                     status: DirectiveStatus, created_at: datetime) -> str:
    exists = session.execute(
        sa.select(ar_production_directive.c.id)
        .where(ar_production_directive.c.id == directive.directive_id)
    ).scalar_one_or_none()
    if exists is not None:
        return directive.directive_id
    session.execute(sa.insert(ar_production_directive).values(
        id=directive.directive_id, decision_id=directive.decision_id,
        arm_id=directive.arm_id, quantity=directive.quantity,
        brief_json=directive.brief.model_dump(mode="json"), priority=directive.priority,
        status=status.value, expires_at=directive.expires_at, created_at=created_at,
        allocation_type=directive.allocation_type.value,
        expected_value=directive.expected_value, uncertainty=directive.uncertainty,
        budget_limit=directive.budget_limit,
        market_context_json=dict(directive.market_context),
        selection_reason_json=list(directive.selection_reason),
        directive_version=directive.directive_version,
        policy_version=directive.policy_version,
        exploration_fraction=directive.exploration_fraction,
        value_model=directive.value_model,
    ))
    return directive.directive_id


def list_queued_directives(session: Session, *, brand_id: str,
                           activation_states: Sequence[str]) -> list[dict[str, Any]]:
    """The actionable surface. Only cycles whose activation state is in `activation_states`
    may be served (ADR-0024: SHADOW/DRY_RUN directives stay auditable but not actionable)."""
    return [dict(row) for row in session.execute(
        sa.select(ar_production_directive)
        .join(ar_allocation_decision,
              ar_allocation_decision.c.id == ar_production_directive.c.decision_id)
        .join(ar_allocation_cycle,
              ar_allocation_cycle.c.id == ar_allocation_decision.c.cycle_id)
        .where(ar_allocation_cycle.c.brand_id == brand_id,
               ar_allocation_cycle.c.activation_state.in_(list(activation_states)),
               ar_production_directive.c.status == DirectiveStatus.QUEUED.value)
        .order_by(ar_production_directive.c.priority, ar_production_directive.c.id)
    ).mappings()]


def load_directive(session: Session, directive_id: str) -> dict[str, Any] | None:
    row = session.execute(
        sa.select(ar_production_directive)
        .where(ar_production_directive.c.id == directive_id)
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def load_directives_for_cycle(session: Session, cycle_id: str) -> list[dict[str, Any]]:
    """The cycle's directives in priority order — the replay read-back path."""
    return [dict(row) for row in session.execute(
        sa.select(ar_production_directive)
        .join(ar_allocation_decision,
              ar_allocation_decision.c.id == ar_production_directive.c.decision_id)
        .where(ar_allocation_decision.c.cycle_id == cycle_id)
        .order_by(ar_production_directive.c.priority, ar_production_directive.c.id)
    ).mappings()]


# --- reward observations and updates ------------------------------------------------

def _reward_value(tri: Any) -> float | None:
    """The float column: a known value (zero included), NULL for unknown/not_ready."""
    return tri.unwrap() if tri.is_known else None


def insert_reward_observation(session: Session, observation: RewardObservation, *,
                              brand_id: str, received_at: datetime) -> tuple[str, bool]:
    """Idempotent on `(experiment_id, observation_window)` (§73). Returns (id, stored?)."""
    observation_id = deterministic_id("reward_observation", observation.key)
    existing = session.execute(
        sa.select(ar_reward_observation.c.id)
        .where(ar_reward_observation.c.id == observation_id)
    ).scalar_one_or_none()
    if existing is not None:
        return observation_id, False
    try:
        session.execute(sa.insert(ar_reward_observation).values(
            id=observation_id, brand_id=brand_id, experiment_id=observation.experiment_id,
            arm_id=observation.arm_id, content_id=observation.content_id,
            observation_window=observation.observation_window.value,
            maturity=observation.maturity.value, observed_at=observation.observed_at,
            raw_metrics_json={name: tri.to_json_value()
                              for name, tri in observation.raw_metrics.items()},
            normalized_reward=_reward_value(observation.normalized_reward),
            normalized_reward_state=observation.normalized_reward.state.value,
            reward_version=observation.reward_version,
            attribution_confidence=observation.attribution_confidence,
            quality_status=(observation.quality_status.value
                            if observation.quality_status is not None else None),
            economic_terms_json={name: tri.to_json_value()
                                 for name, tri in observation.economic_terms.items()},
            received_at=received_at,
        ))
    except IntegrityError:
        session.rollback()
        return observation_id, False
    return observation_id, True


def insert_experiment_result(session: Session, observation: RewardObservation, *,
                             brand_id: str) -> str:
    result_id = deterministic_id("experiment_result", observation.key)
    exists = session.execute(
        sa.select(ar_experiment_result.c.id).where(ar_experiment_result.c.id == result_id)
    ).scalar_one_or_none()
    if exists is not None:
        return result_id
    session.execute(sa.insert(ar_experiment_result).values(
        id=result_id, brand_id=brand_id, experiment_id=observation.experiment_id,
        arm_id=observation.arm_id, content_id=observation.content_id,
        observation_window=observation.observation_window.value,
        status=observation.maturity.value, observed_at=observation.observed_at,
        raw_metrics_json={name: tri.to_json_value()
                          for name, tri in observation.raw_metrics.items()},
        normalized_reward=_reward_value(observation.normalized_reward),
        normalized_reward_state=observation.normalized_reward.state.value,
        reward_version=observation.reward_version,
        attribution_confidence=observation.attribution_confidence, source_ids=[],
    ))
    return result_id


def latest_observation_instant(session: Session) -> datetime | None:
    """The newest stored `observed_at`, or `None` when nothing is stored.

    The API's update worker uses this as its default `now`: applying "everything stored"
    is deterministic and replay-stable, where a wall-clock read would not be (§67).
    """
    value = session.execute(
        sa.select(sa.func.max(ar_reward_observation.c.observed_at))
    ).scalar_one_or_none()
    return None if value is None else _aware(value)


def load_pending_observations(session: Session, *, now: datetime) -> list[dict[str, Any]]:
    """Every observation due at/before `now`, deterministic order — no future data (§23).

    Applied observations are deliberately included: §74's retry must consult the persisted
    update log and be *visible* as a skip (`skipped_duplicate`), not silently filtered out
    by this query. The worker (`modeling.update`) owns the "already applied" check.
    """
    return [dict(row) for row in session.execute(
        sa.select(ar_reward_observation)
        .where(ar_reward_observation.c.observed_at <= now)
        .order_by(ar_reward_observation.c.observed_at, ar_reward_observation.c.id)
    ).mappings()]


def insert_posterior_update(session: Session, record: UpdateRecord, *, brand_id: str) -> None:
    update_id = deterministic_id("posterior_update", record.reward_observation_id,
                                 record.status.value, str(record.version_after))
    exists = session.execute(
        sa.select(ar_posterior_update_log.c.id)
        .where(ar_posterior_update_log.c.id == update_id)
    ).scalar_one_or_none()
    if exists is not None:
        return
    session.execute(sa.insert(ar_posterior_update_log).values(
        id=update_id,
        brand_id=brand_id, reward_observation_id=record.reward_observation_id,
        arm_id=record.arm_id, posterior_version_before=record.version_before,
        posterior_version_after=record.version_after, update_status=record.status.value,
        effective_weight=record.effective_weight, updated_at=record.updated_at,
    ))


def load_posterior_update(session: Session, reward_observation_id: str) -> dict[str, Any] | None:
    row = session.execute(
        sa.select(ar_posterior_update_log)
        .where(ar_posterior_update_log.c.reward_observation_id == reward_observation_id,
               ar_posterior_update_log.c.update_status == "applied")
    ).mappings().one_or_none()
    return None if row is None else dict(row)


def increment_result_counts(session: Session, *, arm_id: str,
                            maturity: ObservationMaturity, now: datetime) -> None:
    """§38 evidence counter: an applied update increments the arm's result tally."""
    column = (ar_content_arm_state.c.mature_result_count
              if maturity is ObservationMaturity.MATURE
              else ar_content_arm_state.c.provisional_result_count)
    session.execute(sa.update(ar_content_arm_state)
                    .where(ar_content_arm_state.c.arm_id == arm_id)
                    .values({column.name: column + 1, "last_updated_at": now}))


# --- similarity, exposure, baselines, overrides, channel state ----------------------

def replace_similarity(session: Session, *, brand_id: str, pairs: Sequence[tuple[str, str, float]],
                       similarity_method: str, feature_version: str,
                       computed_at: datetime) -> int:
    """Upsert similarity pairs (canonical `a < b`); returns the count stored."""
    stored = 0
    for arm_a, arm_b, score in pairs:
        first, second = sorted((arm_a, arm_b))
        pair_id = deterministic_id("similarity", first, second)
        exists = session.execute(
            sa.select(ar_arm_similarity.c.id).where(ar_arm_similarity.c.id == pair_id)
        ).scalar_one_or_none()
        if exists is None:
            session.execute(sa.insert(ar_arm_similarity).values(
                id=pair_id, brand_id=brand_id, arm_id_a=first, arm_id_b=second,
                similarity_score=score, similarity_method=similarity_method,
                feature_version=feature_version, computed_at=computed_at,
            ))
        stored += 1
    return stored


def load_similarity(session: Session) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(
        sa.select(ar_arm_similarity).order_by(ar_arm_similarity.c.arm_id_a,
                                              ar_arm_similarity.c.arm_id_b)
    ).mappings()]


def insert_exposure(session: Session, *, brand_id: str, arm_id: str, window_start: datetime,
                    window_end: datetime, selection_count: int, content_count: int,
                    estimated_audience_exposure: float | None, exposure_score: float,
                    calculation_version: str) -> None:
    exposure_id = deterministic_id("exposure", arm_id, window_start.isoformat(),
                                   window_end.isoformat())
    exists = session.execute(
        sa.select(ar_arm_exposure.c.id).where(ar_arm_exposure.c.id == exposure_id)
    ).scalar_one_or_none()
    if exists is not None:
        return
    session.execute(sa.insert(ar_arm_exposure).values(
        id=exposure_id,
        brand_id=brand_id, arm_id=arm_id, window_start=window_start, window_end=window_end,
        selection_count=selection_count, content_count=content_count,
        estimated_audience_exposure=estimated_audience_exposure,
        exposure_score=exposure_score, calculation_version=calculation_version,
    ))


def load_exposure(session: Session, *, arm_id: str) -> list[dict[str, Any]]:
    return [dict(row) for row in session.execute(
        sa.select(ar_arm_exposure).where(ar_arm_exposure.c.arm_id == arm_id)
        .order_by(ar_arm_exposure.c.window_start)
    ).mappings()]


def replace_baselines(session: Session, *, brand_id: str, arm_ids: Sequence[str],
                      now: datetime, config_version: str) -> None:
    session.execute(sa.update(ar_baseline_config)
                    .where(ar_baseline_config.c.brand_id == brand_id)
                    .values(enabled=False, removed_at=now))
    for arm_id in arm_ids:
        existing = session.execute(
            sa.select(ar_baseline_config.c.id)
            .where(ar_baseline_config.c.brand_id == brand_id,
                   ar_baseline_config.c.arm_id == arm_id)
        ).scalar_one_or_none()
        if existing is None:
            session.execute(sa.insert(ar_baseline_config).values(
                id=deterministic_id("baseline", brand_id, arm_id), brand_id=brand_id,
                arm_id=arm_id, enabled=True, added_at=now, config_version=config_version,
            ))
        else:
            session.execute(sa.update(ar_baseline_config)
                            .where(ar_baseline_config.c.id == existing)
                            .values(enabled=True, removed_at=None, added_at=now))


def load_baselines(session: Session, *, brand_id: str) -> tuple[str, ...]:
    return tuple(session.execute(
        sa.select(ar_baseline_config.c.arm_id)
        .where(ar_baseline_config.c.brand_id == brand_id,
               ar_baseline_config.c.enabled.is_(True))
        .order_by(ar_baseline_config.c.arm_id)
    ).scalars())


def insert_override(session: Session, *, brand_id: str, target_type: str, target_id: str,
                    action: str, reason: str, created_by: str, created_at: datetime,
                    expires_at: datetime | None) -> str:
    override_id = deterministic_id("override", brand_id, target_id, created_at.isoformat())
    exists = session.execute(
        sa.select(ar_manual_override.c.id).where(ar_manual_override.c.id == override_id)
    ).scalar_one_or_none()
    if exists is not None:
        return override_id
    session.execute(sa.insert(ar_manual_override).values(
        id=override_id, brand_id=brand_id, target_type=target_type, target_id=target_id,
        action=action, reason=reason, created_by=created_by, created_at=created_at,
        expires_at=expires_at, active=True,
    ))
    return override_id


def load_active_overrides(session: Session, *, brand_id: str,
                          now: datetime) -> list[dict[str, Any]]:
    """§108: an expired override is not an override."""
    return [dict(row) for row in session.execute(
        sa.select(ar_manual_override)
        .where(ar_manual_override.c.brand_id == brand_id,
               ar_manual_override.c.active.is_(True),
               sa.or_(ar_manual_override.c.expires_at.is_(None),
                      ar_manual_override.c.expires_at > now))
        .order_by(ar_manual_override.c.created_at)
    ).mappings()]


def get_channel_state(session: Session, *, brand_id: str,
                      default: str) -> str:
    value = session.execute(
        sa.select(ar_channel_state.c.activation_state)
        .where(ar_channel_state.c.brand_id == brand_id)
    ).scalar_one_or_none()
    return default if value is None else str(value)


def set_channel_state(session: Session, *, brand_id: str, activation_state: str,
                      now: datetime, reason: str, updated_by: str) -> None:
    existing = session.execute(
        sa.select(ar_channel_state.c.id).where(ar_channel_state.c.brand_id == brand_id)
    ).scalar_one_or_none()
    if existing is None:
        session.execute(sa.insert(ar_channel_state).values(
            id=deterministic_id("channel_state", brand_id), brand_id=brand_id,
            activation_state=activation_state, updated_at=now, reason=reason,
            updated_by=updated_by,
        ))
    else:
        session.execute(sa.update(ar_channel_state)
                        .where(ar_channel_state.c.id == existing)
                        .values(activation_state=activation_state, updated_at=now,
                                reason=reason, updated_by=updated_by))
