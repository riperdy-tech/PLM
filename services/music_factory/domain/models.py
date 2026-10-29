"""Domain models for Phase P4: Music Generation Factory (§65, R6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from plm_contracts.seams import ExperimentLineage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    ArtifactType,
    AttemptStatus,
    AttemptType,
    CandidateStatus,
    CommercialUseStatus,
    CostType,
    PlanStatus,
)

__all__ = [
    "AudioArtifact",
    "CostRecord",
    "CreativeVariant",
    "GeneratedCandidate",
    "GenerationAttempt",
    "GenerationPlan",
    "GenerationRequest",
    "GenerationResponse",
    "MeasuredAudio",
    "ProviderCapabilities",
    "RightsSnapshot",
]


class MeasuredAudio(BaseModel):
    """Audio physical measurement properties (§68, Seam C4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    duration_seconds: float = Field(ge=0.0)
    sample_rate: int = Field(ge=8000)
    channels: int = Field(ge=1, le=8)
    codec: str = "pcm_s16le"
    byte_size: int = Field(default=0, ge=0)
    sha256: str = ""
    mime_type: str = "audio/wav"
    probe_version: str = "probe-v1"


class ProviderCapabilities(BaseModel):
    """Capabilities discovered for a music generation provider/model (§4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str
    model_id: str
    max_duration_seconds: int = 300
    supports_stems: bool = True
    supports_lyrics: bool = True
    supports_instrumental: bool = True
    supported_genres: tuple[str, ...] = ("rnb", "pop", "hiphop", "electronic", "lofi")
    supported_vocal_modes: tuple[str, ...] = ("vocal", "instrumental", "mixed")


class GenerationRequest(BaseModel):
    """Normalized internal generation request sent to an audio provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    track_spec_id: str
    experiment_id: str
    prompt: str
    negative_prompt: str = ""
    duration_seconds: int = 180
    bpm: int | None = None
    vocal_mode: str = "vocal"
    language: str = "english"
    genre: str = "rnb"
    style_tags: tuple[str, ...] = ()
    seed: int = 0
    provider_id: str = "mock"
    model_id: str = "mock-v1"


class GenerationResponse(BaseModel):
    """Provider-agnostic response returned after generation submission or execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_job_id: str
    status: AttemptStatus
    # Only for failures known to have been refused before submission; an
    # empty job ID or FAILED status alone never proves a no-charge outcome.
    confirmed_unsubmitted: bool = False
    audio_data: bytes | None = None
    mime_type: str = "audio/wav"
    actual_duration_seconds: float | None = None
    # Unknown provider usage is not zero. A real provider must provide
    # a billing-reference-bearing observation before credits are trusted.
    cost_credits: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    credit_receipt_ref: str | None = None
    error_message: str | None = None
    model_version: str | None = None
    """The model version the provider reports for this output (P28); ``None`` means it
    reported none, which keeps the candidate out of P5 clearance."""
    lyrics_text: str | None = None
    """The lyrics the provider returned; ``None`` means it returned none (an instrumental,
    or a vocal track whose lyrics P5 then cannot screen)."""

    @model_validator(mode="after")
    def validate_unsubmitted_refusal(self) -> GenerationResponse:
        if self.confirmed_unsubmitted and (
            self.status is not AttemptStatus.FAILED
            or bool(self.provider_job_id)
            or self.audio_data is not None
            or self.cost_credits is not None
            or self.credit_receipt_ref is not None
        ):
            raise ValueError(
                "confirmed_unsubmitted requires FAILED without a provider job, "
                "audio or charge evidence"
            )
        return self


class CreativeVariant(BaseModel):
    """A distinct creative iteration path within a generation plan (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    variant_id: str
    generation_plan_id: str
    variant_code: str
    parent_variant_id: str | None = None
    changed_dimensions: dict[str, Any] = Field(default_factory=dict)
    change_reason: str = "initial_seed"
    prompt_delta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class GenerationPlan(BaseModel):
    """Batch plan coordinating multiple attempts to produce target candidates (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    track_spec_id: str
    experiment_id: str
    lineage: ExperimentLineage | None
    """From the C3 spec. ``None`` only for plans stored before lineage existed (ADR-0026);
    such plans cannot export candidates to P5."""
    track_spec_wire: dict[str, Any] | None
    """The C3 spec the plan was made from (ADR-0027); ``None`` only for legacy plans."""
    candidate_target: int = Field(default=2, ge=1)
    max_attempts: int = Field(default=5, ge=1)
    budget_limit: float = Field(default=50.0, ge=0.0, allow_inf_nan=False)
    credit_limit: float = Field(default=100.0, ge=0.0, allow_inf_nan=False)
    policy_version: str = "p4-v1"
    status: PlanStatus = PlanStatus.PLANNED
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class GenerationAttempt(BaseModel):
    """One single generation call to a music provider (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    attempt_id: str
    generation_plan_id: str
    creative_variant_id: str
    provider_id: str
    model_id: str
    provider_job_id: str
    model_version: str | None = None
    lyrics_text: str | None = None
    attempt_type: AttemptType = AttemptType.INITIAL
    status: AttemptStatus = AttemptStatus.SUBMITTED
    request_hash: str
    request_payload_hash: str
    started_at: datetime
    submitted_at: datetime
    completed_at: datetime | None = None
    # A provider credit amount does not imply any priced currency. NULL is
    # unknown, rather than a fabricated USD amount or numerical zero.
    cost_estimate: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    actual_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    money_basis: Literal["unpriced", "provider_reported", "legacy_unverified"] = "unpriced"
    cost_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    cost_receipt_ref: str | None = None
    credit_estimate: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    actual_credits: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    credit_basis: Literal["unknown", "mock_simulated", "provider_reported", "legacy_unverified"] = "unknown"
    credit_receipt_ref: str | None = None
    transport_retry_of: str | None = None
    failure_code: str | None = None

    @model_validator(mode="after")
    def validate_money_provenance(self) -> GenerationAttempt:
        if self.money_basis == "unpriced" and (
            self.cost_estimate is not None or self.actual_cost is not None
            or self.cost_currency is not None or self.cost_receipt_ref is not None
        ):
            raise ValueError("unpriced attempt must not carry an assumed monetary amount")
        if self.money_basis == "provider_reported" and (
            self.actual_cost is None or not self.cost_currency
            or not self.cost_receipt_ref
        ):
            raise ValueError("provider-reported money requires an amount and currency")
        return self

    @model_validator(mode="after")
    def validate_credit_provenance(self) -> GenerationAttempt:
        if self.credit_basis == "unknown" and (
            self.credit_estimate is not None or self.actual_credits is not None
            or self.credit_receipt_ref is not None
        ):
            raise ValueError("unknown credit usage must not be recorded as zero or estimated")
        if self.credit_basis == "mock_simulated" and (
            self.actual_credits is None or self.credit_receipt_ref is not None
        ):
            raise ValueError("mock credits need a simulated value, not a provider receipt")
        if self.credit_basis == "provider_reported" and (
            self.actual_credits is None or not self.credit_receipt_ref
        ):
            raise ValueError("reported credits require usage and provider receipt")
        return self


class GeneratedCandidate(BaseModel):
    """A generated audio track candidate ready for evaluation or downstream QA (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str
    track_spec_id: str
    experiment_id: str
    generation_plan_id: str
    status: CandidateStatus = CandidateStatus.GENERATED
    provider_id: str
    model_id: str
    created_at: datetime
    updated_at: datetime
    parent_candidate_id: str | None = None
    source_attempt_id: str | None
    """The attempt that produced this candidate; ``None`` only for rows stored before
    ADR-0027. Its rights snapshot is the one exported to P5."""


class AudioArtifact(BaseModel):
    """Stored audio file artifact with media probes and checksum (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str
    candidate_id: str
    artifact_type: ArtifactType = ArtifactType.FULL_MIX
    object_uri: str
    sha256: str
    mime_type: str = "audio/wav"
    byte_size: int = Field(ge=0)
    duration_seconds: float = Field(ge=0.0)
    sample_rate: int = 44100
    channels: int = 2
    codec: str = "pcm_s16le"
    probe_version: str = "probe-v1"
    created_at: datetime


class RightsSnapshot(BaseModel):
    """The license terms a provider adapter declared for one attempt (§65, R6, ADR-0027).

    No field has a default: a snapshot records what the provider declared, never what
    P4 assumed. An attempt whose provider declares no terms gets no snapshot at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_id: str
    attempt_id: str
    provider_id: str
    commercial_use_status: CommercialUseStatus
    basis: str
    license_reference: str
    policy_version: str
    checked_at: datetime
    plan_or_tier: str
    account_identity_ref: str
    terms_version: str
    created_at: datetime
    downloaded_at: datetime


class CostRecord(BaseModel):
    """Cost bookkeeping record for generation attempts (§65)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cost_record_id: str
    attempt_id: str
    cost_type: CostType = CostType.API_CREDIT
    estimated_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    actual_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    money_basis: Literal["unpriced", "provider_reported", "legacy_unverified"] = "unpriced"
    cost_receipt_ref: str | None = None
    credits_estimated: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    credits_actual: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    credit_basis: Literal["unknown", "mock_simulated", "provider_reported", "legacy_unverified"] = "unknown"
    credit_receipt_ref: str | None = None
    pricing_version: str = "unpriced"
    created_at: datetime

    @model_validator(mode="after")
    def validate_money_provenance(self) -> CostRecord:
        if self.money_basis == "unpriced" and (
            self.estimated_amount is not None or self.actual_amount is not None
            or self.currency is not None or self.cost_receipt_ref is not None
        ):
            raise ValueError("unpriced cost record must not carry an assumed monetary amount")
        if self.money_basis == "provider_reported" and (
            self.actual_amount is None or not self.currency
            or not self.cost_receipt_ref
            or self.pricing_version == "unpriced"
        ):
            raise ValueError("provider-reported cost requires price evidence and currency")
        return self

    @model_validator(mode="after")
    def validate_credit_provenance(self) -> CostRecord:
        if self.credit_basis == "unknown" and (
            self.credits_estimated is not None or self.credits_actual is not None
            or self.credit_receipt_ref is not None
        ):
            raise ValueError("unknown credit usage must not be zero or estimated")
        if self.credit_basis == "mock_simulated" and (
            self.credits_actual is None or self.credit_receipt_ref is not None
        ):
            raise ValueError("mock credits need a simulated value only")
        if self.credit_basis == "provider_reported" and (
            self.credits_actual is None or not self.credit_receipt_ref
        ):
            raise ValueError("reported credits require usage and provider receipt")
        return self
