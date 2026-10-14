"""Ingestion and adaptation of P3 track specs (Seam C3, Ruling R5, ADR-0025).

The wire is parsed with the shared :class:`~plm_contracts.seams.c3.TrackSpecPayload`.
Every value P4 uses comes from it; nothing is defaulted on this side. The spec's
:class:`~plm_contracts.seams.ExperimentLineage` is kept whole so the plan, and every
candidate it produces, stays attributable to its experiment and arm.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from plm_contracts import ErrorShape
from plm_contracts.seams import ExperimentLineage, SeamRejection, parse_seam
from plm_contracts.seams.c3 import TrackSpecPayload
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["AdaptedTrackSpec", "TrackSpecValidationError", "adapt_track_spec"]


class TrackSpecValidationError(ValueError):
    """An incoming P3 track spec was refused; ``error`` carries the reason code and fields."""

    def __init__(self, error: ErrorShape) -> None:
        self.error = error
        super().__init__(f"{error.code}: {error.message}")


class AdaptedTrackSpec(BaseModel):
    """P4's view of one P3 track spec. Every field comes from the wire."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    track_spec_id: str
    playlist_spec_id: str
    lineage: ExperimentLineage
    brand_profile_id: str
    genre: str
    vocal_mode: str
    language: str
    bpm: int
    key_profile: str | None
    moods: tuple[str, ...]
    use_cases: tuple[str, ...]
    requested_duration: int = Field(ge=1)
    candidate_target: int = Field(ge=1)
    priority: int = Field(ge=1)
    budget_limit: float = Field(ge=0.0)
    allowed_providers: tuple[str, ...] = Field(min_length=1)
    raw_spec: dict[str, Any]

    @property
    def experiment_id(self) -> str:
        return self.lineage.experiment_id

    @property
    def content_arm_id(self) -> str:
        return self.lineage.content_arm_id


def adapt_track_spec(wire_payload: Mapping[str, Any]) -> AdaptedTrackSpec:
    """Parse a C3 track spec and project the fields P4 generates from; refuse gaps."""
    try:
        spec = parse_seam(TrackSpecPayload, wire_payload)
    except SeamRejection as rejection:
        raise TrackSpecValidationError(rejection.error) from rejection

    return AdaptedTrackSpec(
        track_spec_id=spec.track_spec_id,
        playlist_spec_id=spec.playlist_spec_id,
        lineage=spec.lineage,
        brand_profile_id=spec.brand_profile_id,
        genre=spec.hard_constraints.genre,
        vocal_mode=spec.hard_constraints.vocal_mode,
        language=spec.hard_constraints.language,
        bpm=spec.bounded_preferences.bpm,
        key_profile=spec.bounded_preferences.key_profile,
        moods=spec.bounded_preferences.mood,
        use_cases=spec.bounded_preferences.use_case,
        requested_duration=spec.requested_duration,
        candidate_target=spec.candidate_target,
        priority=spec.priority,
        budget_limit=spec.budget_limit,
        allowed_providers=spec.provider_policy.allowed_providers,
        raw_spec=spec.to_wire(),
    )
