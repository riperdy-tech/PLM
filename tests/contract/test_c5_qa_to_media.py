# tests/contract/test_c5_qa_to_media.py
"""Seam C5 — PHASE-P5 → PHASE-P6 (release manifest). FROZEN by Ruling R7, R8.

Producer: P5's `ReleaseManifest` and `ReleaseManifestPayload`.
Consumer schema: P6's expected per-track release manifest schema.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from quality_gate.api.schemas import ReleaseManifestPayload
from quality_gate.domain.enums import GateDisposition
from quality_gate.gate.manifest import build_release_manifest

from tests.support.contract import SeamRegistry, assert_seam_payload

REGISTRY_ENTRY = "C5 · PHASE-P5 → PHASE-P6"

REGISTRY_FIELDS: tuple[str, ...] = (
    "p5_manifest_id",
    "release_manifest_version",
    "candidate_id",
    "canonical_artifact_id",
    "experiment_id",
    "track_spec_id",
    "disposition",
    "technical_status",
    "commercial_use_status",
    "provenance_status",
    "ai_disclosure_required",
    "rights_risk",
    "policy_status",
    "evaluation_id",
    "evaluation_policy_version",
    "warning_codes",
    "evidence_bundle_id",
)


class ConsumerReleaseManifestSchema(BaseModel):
    """P6's expected per-track release manifest schema, fields copied from registry entry C5."""

    model_config = ConfigDict(extra="forbid")

    p5_manifest_id: str
    release_manifest_version: str
    candidate_id: str
    canonical_artifact_id: str
    experiment_id: str
    track_spec_id: str
    disposition: str
    technical_status: str
    commercial_use_status: str
    provenance_status: str
    ai_disclosure_required: bool
    rights_risk: str
    policy_status: str
    evaluation_id: str
    evaluation_policy_version: str
    warning_codes: list[str]
    evidence_bundle_id: str


def test_declared_fields_match_registry() -> None:
    assert set(ConsumerReleaseManifestSchema.model_fields) == set(REGISTRY_FIELDS)


def test_producer_event_validates_against_consumer_schema() -> None:
    manifest = build_release_manifest(
        candidate_id="cand_test_01",
        canonical_artifact_id="art_test_01",
        experiment_id="exp_01",
        track_spec_id="spec_01",
        content_arm_id="arm_01",
        disposition=GateDisposition.PASSED,
        technical_status="PASSED",
        commercial_use_status="ELIGIBLE",
        provenance_status="COMPLETE",
        ai_disclosure_required=True,
        rights_risk="LOW",
        policy_status="COMPLIANT",
        evaluation_id_str="eval_01",
        evaluation_policy_version="p5-v1",
        warning_codes=[],
        evidence_bundle_id="evi_01",
    )
    payload_obj = ReleaseManifestPayload(
        p5_manifest_id=manifest.p5_manifest_id,
        release_manifest_version=manifest.release_manifest_version,
        candidate_id=manifest.candidate_id,
        canonical_artifact_id=manifest.canonical_artifact_id,
        experiment_id=manifest.experiment_id,
        track_spec_id=manifest.track_spec_id,
        disposition=manifest.disposition.value,
        technical_status=manifest.technical_status,
        commercial_use_status=manifest.commercial_use_status,
        provenance_status=manifest.provenance_status,
        ai_disclosure_required=manifest.ai_disclosure_required,
        rights_risk=manifest.rights_risk,
        policy_status=manifest.policy_status,
        evaluation_id=manifest.evaluation_id,
        evaluation_policy_version=manifest.evaluation_policy_version,
        warning_codes=manifest.warning_codes,
        evidence_bundle_id=manifest.evidence_bundle_id,
    )
    payload = payload_obj.model_dump(mode="json")
    assert_seam_payload(
        payload,
        ConsumerReleaseManifestSchema,
        seam=REGISTRY_ENTRY,
        producer="P5 ReleaseManifest -> ReleaseManifestPayload",
        consumer="P6 Media Factory release manifest ingestion",
    )


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    assert contract_registry.exists
    contract_registry.require_entry(REGISTRY_ENTRY)
