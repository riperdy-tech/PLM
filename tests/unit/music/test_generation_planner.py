"""Tests for generation planner and batch scheduling (Task P4.7)."""

from __future__ import annotations

from plm_contracts.seams.c3 import TrackSpecPayload

from services.music_factory.domain.enums import PlanStatus
from services.music_factory.generation.planner import plan_generation
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


def test_generation_planning_two_candidates() -> None:
    adapted = adapt_track_spec(SAMPLE_SPEC)
    plan, variants = plan_generation(adapted)

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
    plan, variants = plan_generation(adapted)

    assert plan.candidate_target == 3
    assert plan.max_attempts == 6
    assert len(variants) == 3
    assert variants[2].variant_code == "v3"
