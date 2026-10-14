"""Seam C3 · PHASE-P3 → PHASE-P4: the track spec (R5, ADR-0025, ADR-0026).

One track spec asks P4 for candidates of one track. It carries the full
:class:`~plm_contracts.seams.lineage.ExperimentLineage` so every candidate P4 produces
can be attributed to its experiment, family, arm, genome and directive (R9).

Values P3 *derives* rather than reads from the genome follow documented rules (R5):
``requested_duration`` and ``bounded_preferences.bpm`` are the genome target, or the
rounded midpoint of the genome's range when no target is set. P4 never fills a gap:
every field below is required, and a spec with no permitted provider is refused.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .base import SeamModel
from .lineage import ExperimentLineage

__all__ = [
    "BoundedPreferences",
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


class ProviderPolicy(BaseModel):
    """Which providers P4 may use. An empty list is refused (P4 §7)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_providers: tuple[NonEmpty, ...] = Field(min_length=1)


class TrackSpecPayload(SeamModel):
    """The frozen C3 wire: one track request with its lineage."""

    SEAM: ClassVar[str] = "C3"
    SCHEMA_VERSION: ClassVar[str] = "1.0"

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
    """Seconds."""
    candidate_target: int = Field(ge=1)
    priority: int = Field(ge=1)
    budget_limit: float = Field(ge=0.0)
    provider_policy: ProviderPolicy

    @property
    def experiment_id(self) -> str:
        return self.lineage.experiment_id

    @property
    def content_arm_id(self) -> str:
        return self.lineage.content_arm_id
