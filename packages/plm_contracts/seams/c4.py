"""Seam C4 · PHASE-P4 → PHASE-P5: a candidate is ready for the release gate (R6, ADR-0027,
ADR-0032).

The event carries everything P5 needs to judge the candidate without guessing:

* its lineage (ADR-0026), so the gate decision stays attributable;
* the measured audio, the duration the spec asked for, and the spec's declared duration
  range, which is what P5 judges the measured duration against (ADR-0032);
* how it was generated (vocal mode, genre tags, the prompt), because P5's lyric and
  policy checks depend on them, and a defaulted ``instrumental`` silently skipped them;
* the rights snapshot the provider adapter *declared* for the attempt that produced
  this candidate. ``rights`` is ``None`` when the provider declared no terms; P5 then
  treats commercial use as unknown and quarantines. Rights are never assumed.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from ..enums import CommercialUseEligibility
from .base import SeamModel
from .c3 import DurationRangeSeconds
from .lineage import ExperimentLineage

__all__ = [
    "CandidateReadyPayload",
    "MeasuredAudio",
    "ProviderLicenseTerms",
    "RightsSnapshotPayload",
]

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ProviderLicenseTerms(BaseModel):
    """What a provider adapter declares about the output it produces (R6 fields)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    commercial_use_status: CommercialUseEligibility
    basis: NonEmpty
    license_reference: NonEmpty
    plan_or_tier: NonEmpty
    account_identity_ref: NonEmpty
    terms_version: NonEmpty


class RightsSnapshotPayload(ProviderLicenseTerms):
    """The declared terms as recorded for one generation attempt."""

    attempt_id: NonEmpty
    provider_id: NonEmpty
    policy_version: NonEmpty
    checked_at: AwareDatetime
    downloaded_at: AwareDatetime


class MeasuredAudio(BaseModel):
    """Physical measurements of the delivered artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    duration_seconds: float = Field(ge=0.0)
    sample_rate: int = Field(ge=8000)
    channels: int = Field(ge=1, le=8)


class CandidateReadyPayload(SeamModel):
    """``music_candidate_ready_for_p5``: one candidate, everything P5 judges it on."""

    SEAM: ClassVar[str] = "C4"
    SCHEMA_VERSION: ClassVar[str] = "2.0"
    """2.0 adds the required ``requested_duration_range`` (ADR-0032)."""

    event_type: Literal["music_candidate_ready_for_p5"]
    candidate_id: NonEmpty
    artifact_id: NonEmpty
    artifact_uri: NonEmpty
    """Where the artifact is stored (C9); consumers read it by this URI and checksum."""
    artifact_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    track_spec_id: NonEmpty
    lineage: ExperimentLineage
    provider_id: NonEmpty
    model_id: NonEmpty
    measured_audio: MeasuredAudio
    requested_duration_seconds: int = Field(ge=1)
    """The point the C3 spec asked the provider for; informational, not the gate."""
    requested_duration_range: DurationRangeSeconds
    """The C3 spec's ``duration_range``: P5 passes a measured duration inside it only."""
    vocal_mode: NonEmpty
    genre_tags: tuple[NonEmpty, ...] = Field(min_length=1)
    prompt_text: NonEmpty
    lyrics_text: str | None
    """``None`` means the provider returned no lyrics, not that lyrics were checked."""
    rights: RightsSnapshotPayload | None
    """``None`` means the provider declared no terms: commercial use is unknown."""

    @model_validator(mode="after")
    def _requested_inside_range(self) -> CandidateReadyPayload:
        if not self.requested_duration_range.contains(self.requested_duration_seconds):
            rng = self.requested_duration_range
            raise ValueError(
                f"requested_duration_seconds {self.requested_duration_seconds} lies outside "
                f"requested_duration_range [{rng.min_seconds}, {rng.max_seconds}]"
            )
        return self
