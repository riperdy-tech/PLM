"""Canonical Content Genome representation and constraint definitions (PHASE-P3 §6–§9)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import ConstraintType, EnergyLevel, VocalGender, VocalMode

CANONICAL_DIMENSIONS: Final[tuple[str, ...]] = (
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

DEFAULT_CONSTRAINT_HIERARCHY: Final[Mapping[str, ConstraintType]] = {
    # Hard business / channel constraints
    "genre": ConstraintType.HARD,
    "language": ConstraintType.HARD,
    "vocal_mode": ConstraintType.HARD,
    "track_duration": ConstraintType.HARD,
    "playlist_duration": ConstraintType.HARD,
    # Soft musical / stylistic targets
    "subgenre": ConstraintType.SOFT,
    "mood": ConstraintType.SOFT,
    "use_case": ConstraintType.SOFT,
    "vocal_gender": ConstraintType.SOFT,
    "energy": ConstraintType.SOFT,
    "bpm": ConstraintType.SOFT,
    "key_profile": ConstraintType.SOFT,
    "lyric_density": ConstraintType.SOFT,
    "song_structure": ConstraintType.SOFT,
    "hook_profile": ConstraintType.SOFT,
    "instrumentation_profile": ConstraintType.SOFT,
    "production_style": ConstraintType.SOFT,
    "visual_world": ConstraintType.SOFT,
    # Creative freedom
    "visual_motion_profile": ConstraintType.FREE,
    "thumbnail_family": ConstraintType.FREE,
    "title_archetype": ConstraintType.FREE,
}


class BpmRange(BaseModel):
    """BPM target and acceptable tolerance range (§7.9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_bpm: int = Field(ge=20, le=300)
    max_bpm: int = Field(ge=20, le=300)
    target_bpm: int | None = Field(default=None, ge=20, le=300)

    @model_validator(mode="after")
    def validate_range(self) -> BpmRange:
        if self.min_bpm > self.max_bpm:
            raise ValueError(f"min_bpm ({self.min_bpm}) cannot exceed max_bpm ({self.max_bpm})")
        if self.target_bpm is not None and not (self.min_bpm <= self.target_bpm <= self.max_bpm):
            raise ValueError(
                f"target_bpm ({self.target_bpm}) must be within [{self.min_bpm}, {self.max_bpm}]"
            )
        return self


class DurationRange(BaseModel):
    """Duration bounds and target (§7.16, §7.17)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_seconds: int = Field(ge=1)
    max_seconds: int = Field(ge=1)
    target_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_range(self) -> DurationRange:
        if self.min_seconds > self.max_seconds:
            raise ValueError(
                f"min_seconds ({self.min_seconds}) cannot exceed max_seconds ({self.max_seconds})"
            )
        if self.target_seconds is not None and not (
            self.min_seconds <= self.target_seconds <= self.max_seconds
        ):
            raise ValueError(
                f"target_seconds ({self.target_seconds}) must be within "
                f"[{self.min_seconds}, {self.max_seconds}]"
            )
        return self


class GenomeDimensionValue(BaseModel):
    """Generic dimension descriptor with assigned constraint level."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: str
    value: Any
    constraint: ConstraintType = ConstraintType.SOFT


class ConstraintEnvelope(BaseModel):
    """Defines the constraint level for each dimension in a genome (§9, §25)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    overrides: dict[str, ConstraintType] = Field(default_factory=dict)

    def get_constraint(self, dimension: str) -> ConstraintType:
        if dimension in self.overrides:
            return self.overrides[dimension]
        return DEFAULT_CONSTRAINT_HIERARCHY.get(dimension, ConstraintType.SOFT)


class ContentGenome(BaseModel):
    """The canonical 21-dimension Content Genome (§6, §8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 1. Core musical taxonomy
    genre: str
    subgenre: str | None = None
    mood: tuple[str, ...] = ()
    use_case: tuple[str, ...] = ()
    language: str = "english"

    # 2. Vocal specifications
    vocal_mode: VocalMode = VocalMode.VOCAL
    vocal_gender: VocalGender = VocalGender.NONE

    # 3. Dynamic and tempo specifications
    energy: EnergyLevel = EnergyLevel.MEDIUM
    bpm: BpmRange
    key_profile: str | None = None

    # 4. Compositional structure
    lyric_density: str = "moderate"
    song_structure: str = "verse_chorus_bridge"
    hook_profile: str = "vocal_refrain"
    instrumentation_profile: tuple[str, ...] = ()
    production_style: str = "clean_modern"

    # 5. Durations
    track_duration: DurationRange
    playlist_duration: DurationRange

    # 6. Packaging & visual presentation
    visual_world: str
    visual_motion_profile: str = "slow_pan"
    thumbnail_family: str = "illustrated_lofi"
    title_archetype: str = "evocative_phrase"

    @model_validator(mode="after")
    def validate_vocal_consistency(self) -> ContentGenome:
        if self.vocal_mode == VocalMode.INSTRUMENTAL and self.vocal_gender != VocalGender.NONE:
            raise ValueError("Instrumental track cannot specify vocal_gender other than NONE")
        return self
