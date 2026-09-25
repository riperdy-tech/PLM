"""Event schemas emitted by music factory for downstream consumption (Seam C4, Ruling R6)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["CandidateReadyForP5Event", "build_candidate_ready_event"]


class MeasuredAudioPayload(BaseModel):
    """Audio physical measurements embedded in event (§68, Seam C4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    duration_seconds: float = Field(ge=0.0)
    sample_rate: int = Field(ge=8000)
    channels: int = Field(ge=1, le=8)


class CandidateReadyForP5Event(BaseModel):
    """Frozen Seam C4 event emitted when a candidate is ready for QA release gating (§68)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["music_candidate_ready_for_p5"] = "music_candidate_ready_for_p5"
    event_version: Literal["1"] = "1"
    candidate_id: str
    artifact_id: str
    track_spec_id: str
    experiment_id: str
    content_arm_id: str
    provider_id: str
    model_id: str
    measured_audio: MeasuredAudioPayload
    rights_status: str
    provenance_complete: bool


def build_candidate_ready_event(
    *,
    candidate_id: str,
    artifact_id: str,
    track_spec_id: str,
    experiment_id: str,
    content_arm_id: str,
    provider_id: str,
    model_id: str,
    duration_seconds: float,
    sample_rate: int,
    channels: int,
    rights_status: str,
    provenance_complete: bool = True,
) -> CandidateReadyForP5Event:
    """Factory creating a strictly validated Seam C4 event."""
    return CandidateReadyForP5Event(
        candidate_id=candidate_id,
        artifact_id=artifact_id,
        track_spec_id=track_spec_id,
        experiment_id=experiment_id,
        content_arm_id=content_arm_id,
        provider_id=provider_id,
        model_id=model_id,
        measured_audio=MeasuredAudioPayload(
            duration_seconds=duration_seconds,
            sample_rate=sample_rate,
            channels=channels,
        ),
        rights_status=rights_status,
        provenance_complete=provenance_complete,
    )
