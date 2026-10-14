"""Seam C5 · PHASE-P5 → PHASE-P6: the per-track release manifest (R7, R8, ADR-0028).

P5 issues one manifest per candidate that passed, or that a reviewer cleared by audited
override. P6 assembles the playlist-level package from several of them (R7).

The verdict is two fields, never one (R8, P5 §125): ``disposition`` is the automated
gate decision, one of P5's four values, and ``override`` records an audited human
override when there is one. ``CLEARED_BY_OVERRIDE`` is the override's status, not a
fifth disposition, so an override can never be mistaken for a pass, or a pass for an
override.

The manifest carries the track facts P6 needs (artifact URI and checksum, measured
duration) and the experiment lineage, so P6 never invents them.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ..enums import (
    CommercialUseEligibility,
    GateDisposition,
    GateOverrideStatus,
    ProvenanceStatus,
)
from .base import SeamModel
from .lineage import ExperimentLineage

__all__ = ["CLEARED_DISPOSITIONS", "GateOverrideRecord", "ReleaseManifestPayload"]

NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

CLEARED_DISPOSITIONS: frozenset[GateDisposition] = frozenset(
    {GateDisposition.PASSED, GateDisposition.PASSED_WITH_WARNINGS}
)
"""Automated dispositions a consumer may accept without an override (R8)."""


class GateOverrideRecord(BaseModel):
    """An audited human override of a quarantined or rejected candidate (P5 §79)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: GateOverrideStatus
    quarantine_case_id: NonEmpty
    auditor_id: NonEmpty
    resolved_at: AwareDatetime
    reason_codes: tuple[NonEmpty, ...]


class ReleaseManifestPayload(SeamModel):
    """One track's release clearance, with the facts P6 assembles from."""

    SEAM: ClassVar[str] = "C5"
    SCHEMA_VERSION: ClassVar[str] = "1.0"

    p5_manifest_id: NonEmpty
    release_manifest_version: NonEmpty
    candidate_id: NonEmpty
    canonical_artifact_id: NonEmpty
    artifact_uri: NonEmpty
    artifact_sha256: Sha256Hex
    measured_duration_seconds: float = Field(gt=0.0)
    track_spec_id: NonEmpty
    lineage: ExperimentLineage
    disposition: GateDisposition
    override: GateOverrideRecord | None
    """``None`` means no human override was applied."""
    technical_status: NonEmpty
    commercial_use_status: CommercialUseEligibility
    provenance_status: ProvenanceStatus
    ai_disclosure_required: bool
    rights_risk: NonEmpty
    policy_status: NonEmpty
    evaluation_id: NonEmpty
    evaluation_policy_version: NonEmpty
    warning_codes: tuple[NonEmpty, ...]
    evidence_bundle_id: NonEmpty

    @model_validator(mode="after")
    def _override_only_clears_a_blocked_candidate(self) -> Self:
        if self.override is not None and self.disposition in CLEARED_DISPOSITIONS:
            raise ValueError(
                "an override may only accompany a QUARANTINED or REJECTED disposition"
            )
        return self

    @property
    def cleared_for_release(self) -> bool:
        """R7/R8: passed (with or without warnings), or cleared by an audited override."""
        return self.disposition in CLEARED_DISPOSITIONS or self.override is not None
