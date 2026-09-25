"""Seam C4 consumer adapter (P5 §7, §83, R6, R8).

Consumes `CandidateReadyForP5Event` and creates initial evaluation records and immutable snapshots.
"""

from __future__ import annotations

from typing import Any

from music_factory.api.events import CandidateReadyForP5Event

from ..domain.enums import EvaluationStatus
from ..domain.ids import evaluation_id
from ..domain.models import CandidateEvaluation, EvaluationSnapshot

__all__ = ["consume_c4_event", "create_evaluation_from_c4"]


def consume_c4_event(raw_event: dict[str, Any] | CandidateReadyForP5Event) -> CandidateReadyForP5Event:
    """Validate and parse an incoming Seam C4 event from P4."""
    if isinstance(raw_event, CandidateReadyForP5Event):
        return raw_event
    return CandidateReadyForP5Event.model_validate(raw_event)


def create_evaluation_from_c4(
    event: CandidateReadyForP5Event,
    *,
    candidate_metadata: dict[str, Any],
    artifact_metadata: dict[str, Any],
    provider_metadata: dict[str, Any],
    license_metadata: dict[str, Any],
    evaluation_policy_version: str = "p5-v1",
    trigger: str = "candidate_ready",
) -> tuple[CandidateEvaluation, EvaluationSnapshot]:
    """Initialize an evaluation aggregate and snapshot from a validated C4 event and retrieved metadata."""
    eid = evaluation_id(event.candidate_id, evaluation_policy_version, trigger)

    snapshot = EvaluationSnapshot.create(
        evaluation_id_str=eid,
        candidate_id=event.candidate_id,
        artifact_id=event.artifact_id,
        candidate_snapshot={
            **candidate_metadata,
            "track_spec_id": event.track_spec_id,
            "experiment_id": event.experiment_id,
            "content_arm_id": event.content_arm_id,
        },
        artifact_snapshot={
            **artifact_metadata,
            "measured_audio": event.measured_audio.model_dump(),
        },
        provider_snapshot={
            **provider_metadata,
            "provider_id": event.provider_id,
            "model_id": event.model_id,
        },
        license_snapshot={
            **license_metadata,
            "rights_status": event.rights_status,
            "provenance_complete": event.provenance_complete,
        },
        p3_snapshot_ref=event.track_spec_id,
        p4_snapshot_ref=event.candidate_id,
        policy_snapshot_ref=evaluation_policy_version,
    )

    evaluation = CandidateEvaluation(
        id=eid,
        candidate_id=event.candidate_id,
        artifact_id=event.artifact_id,
        evaluation_policy_version=evaluation_policy_version,
        trigger=trigger,
        status=EvaluationStatus.PENDING,
        snapshot=snapshot,
    )

    return evaluation, snapshot
