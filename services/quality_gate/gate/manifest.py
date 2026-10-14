"""Release manifest generator for Phase P5 (P5 §77, §92, Seam C5, Rulings R7, R8).

Builds the per-candidate manifest P6 assembles from (ADR-0028). Every fact comes from the
candidate's C4 event and its evaluation; nothing is defaulted.
"""

from __future__ import annotations

from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c5 import GateOverrideRecord

from ..domain.enums import GateDisposition
from ..domain.models import ReleaseManifest

__all__ = ["build_release_manifest"]


def build_release_manifest(
    *,
    candidate_id: str,
    canonical_artifact_id: str,
    artifact_uri: str,
    artifact_sha256: str,
    measured_duration_seconds: float,
    track_spec_id: str,
    lineage: ExperimentLineage,
    disposition: GateDisposition,
    override: GateOverrideRecord | None,
    technical_status: str,
    commercial_use_status: str,
    provenance_status: str,
    ai_disclosure_required: bool,
    rights_risk: str,
    policy_status: str,
    evaluation_id_str: str,
    evaluation_policy_version: str,
    warning_codes: list[str],
    evidence_bundle_id: str,
    manifest_version: str = "1.0",
) -> ReleaseManifest:
    """Build an immutable, hashed release manifest for downstream P6."""
    return ReleaseManifest.create(
        candidate_id=candidate_id,
        canonical_artifact_id=canonical_artifact_id,
        artifact_uri=artifact_uri,
        artifact_sha256=artifact_sha256,
        measured_duration_seconds=measured_duration_seconds,
        track_spec_id=track_spec_id,
        lineage=lineage,
        disposition=disposition,
        override=override,
        technical_status=technical_status,
        commercial_use_status=commercial_use_status,
        provenance_status=provenance_status,
        ai_disclosure_required=ai_disclosure_required,
        rights_risk=rights_risk,
        policy_status=policy_status,
        evaluation_id_str=evaluation_id_str,
        evaluation_policy_version=evaluation_policy_version,
        warning_codes=warning_codes,
        evidence_bundle_id=evidence_bundle_id,
        version=manifest_version,
    )
