"""Domain contracts, enums, and identifiers for the music generation factory."""

from __future__ import annotations

from .enums import (
    ArtifactType,
    AttemptStatus,
    AttemptType,
    CandidateStatus,
    CommercialUseStatus,
    CostType,
    ModelStatus,
    PlanStatus,
    ProviderStatus,
)
from .ids import (
    artifact_id,
    attempt_id,
    candidate_id,
    deterministic_id,
    plan_id,
    rights_snapshot_id,
    variant_id,
)
from .models import (
    AudioArtifact,
    CostRecord,
    CreativeVariant,
    GeneratedCandidate,
    GenerationAttempt,
    GenerationPlan,
    GenerationRequest,
    GenerationResponse,
    MeasuredAudio,
    ProviderCapabilities,
    RightsSnapshot,
)

__all__ = [
    "ArtifactType",
    "AttemptStatus",
    "AttemptType",
    "AudioArtifact",
    "CandidateStatus",
    "CommercialUseStatus",
    "CostRecord",
    "CostType",
    "CreativeVariant",
    "GeneratedCandidate",
    "GenerationAttempt",
    "GenerationPlan",
    "GenerationRequest",
    "GenerationResponse",
    "MeasuredAudio",
    "ModelStatus",
    "PlanStatus",
    "ProviderCapabilities",
    "ProviderStatus",
    "RightsSnapshot",
    "artifact_id",
    "attempt_id",
    "candidate_id",
    "deterministic_id",
    "plan_id",
    "rights_snapshot_id",
    "variant_id",
]
