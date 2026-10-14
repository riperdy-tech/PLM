"""Tests for prompt builder and prompt provenance (Task P4.6)."""

from __future__ import annotations

from plm_contracts.seams.c3 import TrackSpecPayload

from services.music_factory.generation.prompts import (
    build_generation_prompt,
)
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from tests.support.seams import lineage_fields, track_spec_fields

SAMPLE_C3_SPEC = TrackSpecPayload.build(
    **track_spec_fields(
        track_spec_id='trk_prompt_01',
        playlist_spec_id='ply_01',
        brand_profile_id='neon_seoul',
        hard_constraints={'genre': 'rnb', 'language': 'english', 'vocal_mode': 'vocal'},
        creative_latitude={},
        lyric_spec={},
        vocal_spec={'vocal_mode': 'vocal'},
        instrumentation_spec={},
        production_style='lofi_contemporary',
        requested_duration=180,
        candidate_target=2,
        priority=1,
        budget_limit=50.0,
        lineage=lineage_fields(experiment_id='exp_01', content_arm_id='arm_01'),
        bounded_preferences={'bpm': 84,
         'key_profile': None,
         'mood': ['melancholic', 'intimate'],
         'use_case': ['late_night_drive']},
        provider_policy={'allowed_providers': ['mock']},
    )
).to_wire()


def test_basic_prompt_compilation() -> None:
    adapted = adapt_track_spec(SAMPLE_C3_SPEC)
    compiled = build_generation_prompt(adapted)

    assert "RNB track" in compiled.prompt
    assert "84 BPM" in compiled.prompt
    assert "english vocal" in compiled.prompt
    assert "rnb" in compiled.style_tags
    assert "84bpm" in compiled.style_tags
    assert len(compiled.prompt_hash) == 64


def test_prompt_hash_is_deterministic() -> None:
    adapted = adapt_track_spec(SAMPLE_C3_SPEC)
    c1 = build_generation_prompt(adapted)
    c2 = build_generation_prompt(adapted)
    assert c1.prompt_hash == c2.prompt_hash


def test_variant_delta_overrides() -> None:
    adapted = adapt_track_spec(SAMPLE_C3_SPEC)
    delta = {
        "bpm_offset": 6,
        "additional_moods": ["rainy_vibes"],
        "extra_instruction": "emphasize smooth Rhodes piano chords",
    }
    compiled = build_generation_prompt(adapted, variant_delta=delta)
    assert "90 BPM" in compiled.prompt
    assert "rainy_vibes" in compiled.prompt
    assert "Rhodes piano" in compiled.prompt
    assert "90bpm" in compiled.style_tags
