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
    assert compiled.effective_bpm == 90


def test_structured_vocal_and_lyric_details_are_forwarded_without_invention() -> None:
    spec = TrackSpecPayload.build(
        **track_spec_fields(
            vocal_spec={"vocal_mode": "vocal", "vocal_gender": "female"},
            lyric_spec={"lyric_density": "sparse", "song_structure": "verse_bridge_outro"},
        )
    )
    compiled = build_generation_prompt(adapt_track_spec(spec.to_wire()))
    assert "vocal character: female" in compiled.prompt
    assert "lyric density: sparse" in compiled.prompt
    assert "section structure: verse_bridge_outro" in compiled.prompt


def test_instrumental_prompt_does_not_instruct_vocal_lyrics() -> None:
    spec = TrackSpecPayload.build(
        **track_spec_fields(
            hard_constraints={"genre": "ambient", "language": "english", "vocal_mode": "instrumental"},
            vocal_spec={"vocal_mode": "instrumental", "vocal_gender": "none"},
            lyric_spec={"lyric_density": "moderate", "song_structure": "intro_outro"},
        )
    )
    compiled = build_generation_prompt(adapt_track_spec(spec.to_wire()))
    assert "instrumental, no vocals" in compiled.prompt
    assert "section structure: intro_outro" in compiled.prompt
    assert "vocal character" not in compiled.prompt
    assert "lyric density" not in compiled.prompt


def test_variant_overridden_genre_is_retained_as_request_metadata() -> None:
    adapted = adapt_track_spec(SAMPLE_C3_SPEC)
    compiled = build_generation_prompt(adapted, variant_delta={"override_genre": "jazz"})
    assert compiled.effective_genre == "jazz"
    assert compiled.prompt.startswith("JAZZ track")
    assert "jazz" in compiled.style_tags
