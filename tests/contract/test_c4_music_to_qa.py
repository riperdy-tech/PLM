# tests/contract/test_c4_music_to_qa.py
"""Seam C4 — PHASE-P4 → PHASE-P5 (candidate ready). FROZEN by Ruling R6.

Producer: P4's `CandidateReadyForP5Event` and `build_candidate_ready_event`.
Consumer schema: P5's expected input event schema.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from services.music_factory.api.events import (
    build_candidate_ready_event,
)
from tests.support.contract import SeamRegistry, assert_seam_payload

REGISTRY_ENTRY = "C4 · PHASE-P4 → PHASE-P5"

REGISTRY_FIELDS: tuple[str, ...] = (
    "event_type",
    "event_version",
    "candidate_id",
    "artifact_id",
    "track_spec_id",
    "experiment_id",
    "content_arm_id",
    "provider_id",
    "model_id",
    "measured_audio",
    "rights_status",
    "provenance_complete",
)

REGISTRY_MEASURED_AUDIO_FIELDS: tuple[str, ...] = (
    "duration_seconds",
    "sample_rate",
    "channels",
)


class ConsumerMeasuredAudioSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_seconds: float = Field(ge=0.0)
    sample_rate: int = Field(ge=8000)
    channels: int = Field(ge=1, le=8)


class ConsumerCandidateReadySchema(BaseModel):
    """P5's expected candidate ready event schema, fields copied from registry entry C4."""

    model_config = ConfigDict(extra="forbid")

    event_type: str
    event_version: str
    candidate_id: str
    artifact_id: str
    track_spec_id: str
    experiment_id: str
    content_arm_id: str
    provider_id: str
    model_id: str
    measured_audio: ConsumerMeasuredAudioSchema
    rights_status: str
    provenance_complete: bool


def test_declared_fields_match_registry() -> None:
    assert set(ConsumerCandidateReadySchema.model_fields) == set(REGISTRY_FIELDS)
    assert set(ConsumerMeasuredAudioSchema.model_fields) == set(REGISTRY_MEASURED_AUDIO_FIELDS)


def test_producer_event_validates_against_consumer_schema() -> None:
    event = build_candidate_ready_event(
        candidate_id="cand_test_01",
        artifact_id="art_test_01",
        track_spec_id="trk_spec_01",
        experiment_id="exp_01",
        content_arm_id="arm_01",
        provider_id="mock",
        model_id="mock-v1",
        duration_seconds=120.0,
        sample_rate=44100,
        channels=2,
        rights_status="permitted",
        provenance_complete=True,
    )
    payload = event.model_dump(mode="json")
    assert_seam_payload(
        payload,
        ConsumerCandidateReadySchema,
        seam=REGISTRY_ENTRY,
        producer="P4 build_candidate_ready_event -> CandidateReadyForP5Event",
        consumer="P5 QA candidate gate",
    )


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    assert contract_registry.exists
    contract_registry.require_entry(REGISTRY_ENTRY)
