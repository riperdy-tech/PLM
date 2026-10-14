"""Generation planner and batch scheduling (PHASE-P4 §8, §9, §65)."""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain.enums import PlanStatus
from ..domain.ids import plan_id, variant_id
from ..domain.models import CreativeVariant, GenerationPlan
from ..ingestion.p3_adapter import AdaptedTrackSpec

__all__ = ["plan_generation"]


def plan_generation(
    track_spec: AdaptedTrackSpec,
    *,
    policy_version: str = "p4-v1",
    created_at: datetime | None = None,
) -> tuple[GenerationPlan, list[CreativeVariant]]:
    """Construct a batch GenerationPlan and creative variants from an adapted TrackSpec."""
    now = created_at or datetime.now(UTC)
    pid = plan_id(track_spec.track_spec_id, track_spec.experiment_id, policy_version)

    candidate_target = max(1, track_spec.candidate_target)
    # Plan at least candidate_target attempts + 1 retry headroom, capped at 10
    max_attempts = min(max(candidate_target + 1, candidate_target * 2), 10)

    plan = GenerationPlan(
        plan_id=pid,
        track_spec_id=track_spec.track_spec_id,
        experiment_id=track_spec.experiment_id,
        lineage=track_spec.lineage,
        track_spec_wire=track_spec.raw_spec,
        candidate_target=candidate_target,
        max_attempts=max_attempts,
        budget_limit=track_spec.budget_limit,
        credit_limit=float(max_attempts * 2),
        policy_version=policy_version,
        status=PlanStatus.PLANNED,
        created_at=now,
    )

    variants: list[CreativeVariant] = []

    # 1. Primary baseline variant (v1)
    v1_id = variant_id(pid, "v1")
    variants.append(
        CreativeVariant(
            variant_id=v1_id,
            generation_plan_id=pid,
            variant_code="v1",
            parent_variant_id=None,
            changed_dimensions={},
            change_reason="baseline_spec",
            prompt_delta={},
            created_at=now,
        )
    )

    # 2. Exploratory variant (v2) if target > 1
    if candidate_target > 1:
        v2_id = variant_id(pid, "v2")
        variants.append(
            CreativeVariant(
                variant_id=v2_id,
                generation_plan_id=pid,
                variant_code="v2",
                parent_variant_id=v1_id,
                changed_dimensions={"bpm": track_spec.bpm + 4},
                change_reason="tempo_and_texture_variation",
                prompt_delta={
                    "bpm_offset": 4,
                    "additional_tags": ["smooth_chords", "rich_harmonies"],
                },
                created_at=now,
            )
        )

    # 3. Third variant (v3) if target > 2
    if candidate_target > 2:
        v3_id = variant_id(pid, "v3")
        variants.append(
            CreativeVariant(
                variant_id=v3_id,
                generation_plan_id=pid,
                variant_code="v3",
                parent_variant_id=v1_id,
                changed_dimensions={"bpm": track_spec.bpm - 4},
                change_reason="downtempo_subtle_variation",
                prompt_delta={
                    "bpm_offset": -4,
                    "additional_tags": ["warm_low_end", "tape_warmth"],
                },
                created_at=now,
            )
        )

    return plan, variants
