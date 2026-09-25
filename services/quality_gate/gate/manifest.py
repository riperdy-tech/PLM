"""Release manifest generator for Phase P5 (P5 §77, §92, Seam C5, Rulings R7, R8).

Constructs the immutable downstream release manifest consumed by P6.
"""

from __future__ import annotations

from ..domain.enums import GateDisposition
from ..domain.models import ReleaseManifest

__all__ = ["build_release_manifest"]


def build_release_manifest(
    *,
    candidate_id: str,
    canonical_artifact_id: str,
    experiment_id: str,
    track_spec_id: str,
    content_arm_id: str,
    disposition: GateDisposition,
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
    """Build an immutable, cryptographically hashed release manifest for downstream P6."""
    return ReleaseManifest.create(
        candidate_id=candidate_id,
        canonical_artifact_id=canonical_artifact_id,
        experiment_id=experiment_id,
        track_spec_id=track_spec_id,
        content_arm_id=content_arm_id,
        disposition=disposition,
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
