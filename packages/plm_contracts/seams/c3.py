"""Seam C3 · PHASE-P3 → PHASE-P4: the track spec (R5, ADR-0025, ADR-0026, ADR-0032).

One track spec asks P4 for candidates of one track. It carries the full
:class:`~plm_contracts.seams.lineage.ExperimentLineage` so every candidate P4 produces
can be attributed to its experiment, family, arm, genome and directive (R9).

Values P3 *derives* rather than reads from the genome follow documented rules (R5):
``requested_duration`` and ``bounded_preferences.bpm`` are the genome target, or the
rounded midpoint of the genome's range when no target is set. ``duration_range`` is the
genome's track-duration range itself (ADR-0032): it is the declared tolerance P5 judges
the measured audio against, so ``requested_duration`` must lie inside it. P4 never fills
a gap: every field below is required, and a spec with no permitted provider is refused.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .base import SeamModel
from .lineage import ExperimentLineage

__all__ = [
    "BoundedPreferences",
    "DurationRangeSeconds",
    "HardConstraints",
    "ProviderPolicy",
    "TrackRole",
    "TrackSpecPayload",
]

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class TrackRole(StrEnum):
    """Role of a track within a playlist or release package (P3 §20)."""

    FOCUS = "focus"
    ANCHOR = "anchor"
    SECONDARY = "secondary"
    BRIDGE = "bridge"
    AMBIENT = "ambient"
    INTERLUDE = "interlude"


class HardConstraints(BaseModel):
    """Constraints P4 must satisfy; none may be guessed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    genre: NonEmpty
    language: NonEmpty
    vocal_mode: NonEmpty


class BoundedPreferences(BaseModel):
    """Preferences P4 aims for inside the creative latitude."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bpm: int = Field(ge=20, le=300)
    key_profile: str | None
    """``None`` means the genome leaves key open, not that it is missing."""
    mood: tuple[str, ...]
    use_case: tuple[str, ...]


class DurationRangeSeconds(BaseModel):
    """The inclusive track-duration range a candidate must fall in (ADR-0032).

    Shared by C3 (``duration_range``) and C4 (``requested_duration_range``): the range is
    declared by the genome and travels with the candidate, so P5 never invents a
    tolerance of its own.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_seconds: int = Field(ge=1)
    max_seconds: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> DurationRangeSeconds:
        if self.min_seconds > self.max_seconds:
            raise ValueError(
                f"min_seconds ({self.min_seconds}) exceeds max_seconds ({self.max_seconds})"
            )
        return self

    def contains(self, seconds: float) -> bool:
        """Whether ``seconds`` lies inside the range, both bounds included."""
        return self.min_seconds <= seconds <= self.max_seconds


class ProviderPolicy(BaseModel):
    """Which providers P4 may use. An empty list is refused (P4 §7)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_providers: tuple[NonEmpty, ...] = Field(min_length=1)


class TrackSpecPayload(SeamModel):
    """The frozen C3 wire: one track request with its lineage."""

    SEAM: ClassVar[str] = "C3"
    SCHEMA_VERSION: ClassVar[str] = "2.0"
    """2.0 adds the required ``duration_range`` (ADR-0032)."""

    track_spec_id: NonEmpty
    playlist_spec_id: NonEmpty
    lineage: ExperimentLineage
    brand_profile_id: NonEmpty
    hard_constraints: HardConstraints
    bounded_preferences: BoundedPreferences
    creative_latitude: dict[str, Any]
    lyric_spec: dict[str, Any]
    vocal_spec: dict[str, Any]
    instrumentation_spec: dict[str, Any]
    production_style: NonEmpty
    track_role: TrackRole
    requested_duration: int = Field(ge=1)
    """Seconds: the point P4 asks the provider for. Lies inside ``duration_range``."""
    duration_range: DurationRangeSeconds
    """Seconds: the genome's allowed track duration, which P5 judges against."""
    candidate_target: int = Field(ge=1)
    priority: int = Field(ge=1)
    budget_limit: float = Field(ge=0.0)
    provider_policy: ProviderPolicy

    @model_validator(mode="after")
    def _requested_inside_range(self) -> TrackSpecPayload:
        if not self.duration_range.contains(self.requested_duration):
            raise ValueError(
                f"requested_duration {self.requested_duration} lies outside duration_range "
                f"[{self.duration_range.min_seconds}, {self.duration_range.max_seconds}]"
            )
        return self

    @property
    def experiment_id(self) -> str:
        return self.lineage.experiment_id

    @property
    def content_arm_id(self) -> str:
        return self.lineage.content_arm_id
