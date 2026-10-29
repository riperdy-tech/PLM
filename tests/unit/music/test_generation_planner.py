"""Tests for generation planner and batch scheduling (Task P4.7)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from plm_contracts.seams.c3 import TrackSpecPayload

from services.music_factory.domain.enums import PlanStatus
from services.music_factory.generation.planner import (
    GenerationPlanCapacityError,
    plan_generation,
)
from services.music_factory.generation.prompts import build_generation_prompt
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from tests.support.seams import lineage_fields, track_spec_fields

SAMPLE_SPEC = TrackSpecPayload.build(
    **track_spec_fields(
        track_spec_id='trk_plan_01',
        playlist_spec_id='ply_01',
        brand_profile_id='neon_seoul',
        hard_constraints={'genre': 'rnb', 'language': 'english', 'vocal_mode': 'vocal'},
        creative_latitude={},
        lyric_spec={},
        vocal_spec={'vocal_mode': 'vocal'},
        instrumentation_spec={},
        production_style='lofi',
        requested_duration=180,
        candidate_target=2,
        priority=1,
        budget_limit=35.0,
        lineage=lineage_fields(experiment_id='exp_01', content_arm_id='arm_01'),
        bounded_preferences={'bpm': 80,
         'key_profile': None,
         'mood': ['melancholic'],
         'use_case': ['late_night_drive']},
        provider_policy={'allowed_providers': ['mock']},
    )
).to_wire()

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("target", [4, 7, 10])
def test_requested_four_to_ten_candidates_have_distinct_variants(target: int) -> None:
    """Every authorized target has enough independent treatments to be reachable."""
    spec = adapt_track_spec(dict(SAMPLE_SPEC, candidate_target=target))
    plan, variants = plan_generation(spec, created_at=FIXED_NOW)
    assert plan.candidate_target == target
    assert len(variants) == target
    assert [variant.variant_code for variant in variants] == [
        f"v{index}" for index in range(1, target + 1)
    ]
    assert len({variant.variant_id for variant in variants}) == target
    assert len({build_generation_prompt(spec, variant_delta=v.prompt_delta).prompt_hash
                for v in variants}) == target
    assert plan.max_attempts >= target
    assert plan.max_attempts <= 10
    assert (plan, variants) == plan_generation(spec, created_at=FIXED_NOW)


def test_candidate_target_above_hard_attempt_cap_is_refused() -> None:
    spec = adapt_track_spec(dict(SAMPLE_SPEC, candidate_target=11))
    with pytest.raises(GenerationPlanCapacityError, match="exceeds P4 maximum 10"):
        plan_generation(spec, created_at=FIXED_NOW)


def test_generation_planning_two_candidates() -> None:
    adapted = adapt_track_spec(SAMPLE_SPEC)
    plan, variants = plan_generation(adapted, created_at=FIXED_NOW)

    assert plan.plan_id.startswith("gen_plan_")
    assert plan.track_spec_id == "trk_plan_01"
    assert plan.candidate_target == 2
    assert plan.max_attempts == 4
    assert plan.budget_limit == 35.0
    assert plan.status == PlanStatus.PLANNED
    assert len(variants) == 2
    assert variants[0].variant_code == "v1"
    assert variants[1].variant_code == "v2"


def test_generation_planning_three_candidates() -> None:
    spec_3 = dict(SAMPLE_SPEC, candidate_target=3)
    adapted = adapt_track_spec(spec_3)
    plan, variants = plan_generation(adapted, created_at=FIXED_NOW)

    assert plan.candidate_target == 3
    assert plan.max_attempts == 6
    assert len(variants) == 3
    assert variants[2].variant_code == "v3"
    assert plan.policy_version == "p4-v2"


def test_variants_reuse_p3_arrangement_without_changing_experiment_factors() -> None:
    wire = dict(SAMPLE_SPEC)
    wire["candidate_target"] = 3
    wire["creative_latitude"] = {
        "track_arrangement": {
            "policy_version": "p3-album-contrast-v2",
            "rhythmic_feel": "syncopated offbeat accents",
            "melodic_motion": "short ascending question phrases",
            "harmonic_motion": "sparse sustained voicings",
            "arrangement_arc": "minimal opening, gradual layers",
            "lead_instrument": "electric piano",
        },
    }
    spec = adapt_track_spec(wire)
    plan, variants = plan_generation(spec, created_at=FIXED_NOW)
    replay, variants_again = plan_generation(spec, created_at=FIXED_NOW)
    old_plan, _ = plan_generation(spec, policy_version="p4-v1", created_at=FIXED_NOW)
    assert plan.plan_id != old_plan.plan_id
    assert (plan, variants) == (replay, variants_again)
    assert [v.changed_dimensions for v in variants] == [
        {},
        {"arrangement_emphasis": "rhythmic_space"},
        {"arrangement_emphasis": "dynamic_arc"},
    ]
    compiled = [
        build_generation_prompt(spec, variant_delta=v.prompt_delta) for v in variants
    ]
    assert len({prompt.prompt_hash for prompt in compiled}) == 3
    assert all(prompt.effective_bpm == 80 for prompt in compiled)
    assert all(prompt.effective_genre == "rnb" for prompt in compiled)
    assert "syncopated offbeat accents" in compiled[1].prompt
    assert "short ascending question phrases" in compiled[1].prompt
    assert "minimal opening, gradual layers" in compiled[2].prompt
    assert "electric piano" in compiled[2].prompt
    assert all(
        "smooth_chords" not in prompt.style_tags
        and "tape_warmth" not in prompt.style_tags
        for prompt in compiled
    )


def test_legacy_brief_without_arrangement_does_not_invent_instruments() -> None:
    wire = dict(SAMPLE_SPEC, candidate_target=3)
    spec = adapt_track_spec(wire)
    _, variants = plan_generation(spec, created_at=FIXED_NOW)
    prompt2 = build_generation_prompt(spec, variant_delta=variants[1].prompt_delta)
    prompt3 = build_generation_prompt(spec, variant_delta=variants[2].prompt_delta)
    assert "leave intentional space" in prompt2.prompt
    assert "within the existing instrument palette" in prompt3.prompt
    assert "Rhodes" not in prompt2.prompt + prompt3.prompt
    assert all("bpm_offset" not in v.prompt_delta for v in variants)
