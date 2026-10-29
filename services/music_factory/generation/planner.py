"""Generation planner and batch scheduling (PHASE-P4 §8, §9, §65)."""

from __future__ import annotations

from datetime import datetime

from ..domain.enums import PlanStatus
from ..domain.ids import plan_id, variant_id
from ..domain.models import CreativeVariant, GenerationPlan
from ..ingestion.p3_adapter import AdaptedTrackSpec

__all__ = ["MAX_PLAN_ATTEMPTS", "GenerationPlanCapacityError", "plan_generation"]

MAX_PLAN_ATTEMPTS = 10


class GenerationPlanCapacityError(ValueError):
    """The requested candidate count exceeds the P4 bounded generation capacity."""


def _variant_emphasis(spec: AdaptedTrackSpec, *, dimension: str) -> str:
    """Contrast an interpretation, not P3's controlled genre/BPM/structure.

    Bind the direction to declared P3 arrangement facts when available; do not
    invent a lead instrument or reuse a fixed genre-specific production recipe.
    """
    latitude = spec.raw_spec.get("creative_latitude")
    arrangement = latitude.get("track_arrangement") if isinstance(latitude, dict) else None
    direction = arrangement if isinstance(arrangement, dict) else {}
    if dimension == "rhythmic_space":
        rhythm = direction.get("rhythmic_feel")
        melody = direction.get("melodic_motion")
        base = (
            f"Within the declared groove ({rhythm}), " if isinstance(rhythm, str) and rhythm.strip()
            else "Within the declared rhythmic feel, "
        )
        if isinstance(melody, str) and melody.strip():
            base += f"leave space around the declared melodic motion ({melody}); "
        else:
            base += "leave intentional space between the existing motifs; "
        return (
            base + "contrast short phrases with rests and responsive accents, "
            "without replacing the supplied hook, instrumentation, structure, "
            "harmonic key or vocal mode."
        )
    arc = direction.get("arrangement_arc")
    lead = direction.get("lead_instrument")
    base = (
        f"Develop the declared arrangement arc ({arc}) with "
        if isinstance(arc, str) and arc.strip()
        else "Develop the declared arrangement progression with "
    )
    if isinstance(lead, str) and lead.strip():
        base += f"foreground/background contrast around {lead}; "
    else:
        base += "foreground/background contrast within the existing instrument palette; "
    return (
        base + "vary phrase density and dynamics across sections, preserving "
        "the supplied groove, hook, structure, BPM, harmonic key and vocal mode."
    )


def plan_generation(
    track_spec: AdaptedTrackSpec,
    *,
    policy_version: str = "p4-v2",
    created_at: datetime,
) -> tuple[GenerationPlan, list[CreativeVariant]]:
    """Construct a batch GenerationPlan and creative variants from an adapted TrackSpec."""
    now = created_at
    pid = plan_id(track_spec.track_spec_id, track_spec.experiment_id, policy_version)

    candidate_target = track_spec.candidate_target
    if candidate_target > MAX_PLAN_ATTEMPTS:
        # A plan cannot produce more successful candidates than its hard
        # attempt cap. Refuse rather than create a permanently short plan.
        raise GenerationPlanCapacityError(
            f"candidate_target {candidate_target} exceeds P4 maximum {MAX_PLAN_ATTEMPTS}"
        )
    # Keep retry headroom without exceeding the hard external-call ceiling.
    max_attempts = min(
        max(candidate_target + 1, candidate_target * 2), MAX_PLAN_ATTEMPTS
    )

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

    # Contrast candidates interpret the P3 brief differently, rather than
    # silently moving the experiment's declared BPM or imposing a fixed lofi
    # texture regardless of the supplied genre and instrument palette.
    if candidate_target > 1:
        v2_id = variant_id(pid, "v2")
        variants.append(
            CreativeVariant(
                variant_id=v2_id,
                generation_plan_id=pid,
                variant_code="v2",
                parent_variant_id=v1_id,
                changed_dimensions={"arrangement_emphasis": "rhythmic_space"},
                change_reason="rhythmic_phrase_contrast",
                prompt_delta={
                    "extra_instruction": _variant_emphasis(
                        track_spec, dimension="rhythmic_space",
                    ),
                },
                created_at=now,
            )
        )

    if candidate_target > 2:
        v3_id = variant_id(pid, "v3")
        variants.append(
            CreativeVariant(
                variant_id=v3_id,
                generation_plan_id=pid,
                variant_code="v3",
                parent_variant_id=v1_id,
                changed_dimensions={"arrangement_emphasis": "dynamic_arc"},
                change_reason="dynamic_arrangement_contrast",
                prompt_delta={
                    "extra_instruction": _variant_emphasis(
                        track_spec, dimension="dynamic_arc",
                    ),
                },
                created_at=now,
            )
        )

    # The first three treatments retain the existing p4-v2 identity and
    # prompts. Additional independent interpretations vary phrasing, not P3's
    # controlled BPM, key, instrumentation or experimental arm.
    for index in range(4, candidate_target + 1):
        code = f"v{index}"
        emphasis = "rhythmic_space" if index % 2 == 0 else "dynamic_arc"
        variants.append(
            CreativeVariant(
                variant_id=variant_id(pid, code),
                generation_plan_id=pid,
                variant_code=code,
                parent_variant_id=v1_id,
                changed_dimensions={
                    "arrangement_emphasis": emphasis,
                    "interpretation_index": index,
                },
                change_reason="independent_phrase_interpretation",
                prompt_delta={
                    "extra_instruction": (
                        _variant_emphasis(track_spec, dimension=emphasis)
                        + f" For independent interpretation {index}, use a"
                        " distinct phrase and rest placement within these"
                        " constraints, without changing the supplied structure."
                    ),
                },
                created_at=now,
            )
        )

    return plan, variants
