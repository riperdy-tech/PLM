"""Tests for P3 domain contracts and the 21-dimension Content Genome."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.experiments.domain.enums import (
    ConstraintType,
    EnergyLevel,
    VocalGender,
    VocalMode,
)
from services.experiments.domain.genome import (
    CANONICAL_DIMENSIONS,
    BpmRange,
    ConstraintEnvelope,
    ContentGenome,
    DurationRange,
)


def test_21_canonical_dimensions_are_defined() -> None:
    expected = (
        "genre",
        "subgenre",
        "mood",
        "use_case",
        "language",
        "vocal_mode",
        "vocal_gender",
        "energy",
        "bpm",
        "key_profile",
        "lyric_density",
        "song_structure",
        "hook_profile",
        "instrumentation_profile",
        "production_style",
        "track_duration",
        "playlist_duration",
        "visual_world",
        "visual_motion_profile",
        "thumbnail_family",
        "title_archetype",
    )
    assert CANONICAL_DIMENSIONS == expected
    assert len(CANONICAL_DIMENSIONS) == 21


def test_genome_instantiation_and_immutability() -> None:
    genome = ContentGenome(
        genre="rnb",
        subgenre="contemporary_rnb",
        mood=("melancholic", "intimate"),
        use_case=("late_night_drive", "chill"),
        language="english",
        vocal_mode=VocalMode.VOCAL,
        vocal_gender=VocalGender.FEMALE,
        energy=EnergyLevel.MEDIUM,
        bpm=BpmRange(min_bpm=75, max_bpm=85, target_bpm=80),
        key_profile="C_minor",
        lyric_density="moderate",
        song_structure="verse_chorus_bridge",
        hook_profile="vocal_refrain",
        instrumentation_profile=("rhodes", "808_bass", "reverb_guitar"),
        production_style="warm_analog",
        track_duration=DurationRange(min_seconds=160, max_seconds=220, target_seconds=190),
        playlist_duration=DurationRange(min_seconds=2700, max_seconds=3600, target_seconds=3000),
        visual_world="rainy_asian_city",
        visual_motion_profile="cinematic_drift",
        thumbnail_family="illustrated_lofi",
        title_archetype="evocative_phrase",
    )

    assert genome.genre == "rnb"
    assert genome.bpm.target_bpm == 80
    assert genome.vocal_mode == VocalMode.VOCAL

    with pytest.raises((TypeError, ValidationError)):
        genome.genre = "jazz"  # type: ignore[misc]


def test_bpm_range_validation() -> None:
    with pytest.raises(ValidationError):
        BpmRange(min_bpm=120, max_bpm=80, target_bpm=100)

    with pytest.raises(ValidationError):
        BpmRange(min_bpm=80, max_bpm=100, target_bpm=110)


def test_duration_range_validation() -> None:
    with pytest.raises(ValidationError):
        DurationRange(min_seconds=200, max_seconds=100)


def test_constraint_envelope_defaults() -> None:
    envelope = ConstraintEnvelope()
    assert envelope.get_constraint("genre") == ConstraintType.HARD
    assert envelope.get_constraint("bpm") == ConstraintType.SOFT
    assert envelope.get_constraint("thumbnail_family") == ConstraintType.FREE

    custom = ConstraintEnvelope(
        overrides={"genre": ConstraintType.SOFT, "bpm": ConstraintType.HARD}
    )
    assert custom.get_constraint("genre") == ConstraintType.SOFT
    assert custom.get_constraint("bpm") == ConstraintType.HARD
    assert custom.get_constraint("mood") == ConstraintType.SOFT
