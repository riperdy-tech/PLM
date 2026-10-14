"""Domain enums for Phase P4: Music Generation Factory (§65)."""

from __future__ import annotations

from enum import StrEnum

from plm_contracts import CommercialUseEligibility


class ProviderStatus(StrEnum):
    """Status of an AI music generation provider."""

    ACTIVE = "active"
    DEGRADED = "degraded"
    DISABLED = "disabled"


class ModelStatus(StrEnum):
    """Status of a specific provider generation model."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    EXPERIMENTAL = "experimental"


class PlanStatus(StrEnum):
    """Lifecycle status of a generation plan (§65)."""

    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptType(StrEnum):
    """Type of generation attempt (§65)."""

    INITIAL = "initial"
    RETRY = "retry"
    EXTENSION = "extension"
    INPAINT = "inpaint"


class AttemptStatus(StrEnum):
    """Status of a specific generation attempt (§65)."""

    SUBMITTED = "submitted"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CandidateStatus(StrEnum):
    """Status of a generated music candidate (§65)."""

    GENERATED = "generated"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPORTED_TO_P5 = "exported_to_p5"


class ArtifactType(StrEnum):
    """Type of audio artifact stored in object storage (§65)."""

    FULL_MIX = "full_mix"
    STEM_VOCAL = "stem_vocal"
    STEM_INSTRUMENTAL = "stem_instrumental"
    STEM_DRUMS = "stem_drums"
    STEM_BASS = "stem_bass"
    PREVIEW = "preview"


CommercialUseStatus = CommercialUseEligibility
"""Commercial-use eligibility of generated output; the shared P5 §34 vocabulary (ADR-0027).

P4 used its own permitted/restricted/prohibited tokens, which P5 never matched, so every
real candidate was classed unknown. Migration 0017 maps stored values."""


class CostType(StrEnum):
    """Type of cost incurred per generation attempt (§65)."""

    API_CREDIT = "api_credit"
    DOLLARS = "dollars"
    FIXED_SUBSCRIPTION = "fixed_subscription"
