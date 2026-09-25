"""Unit tests for Phase P5 domain models, enums, IDs and invariants (P5 §6, §82-§92, R7, R8)."""

from __future__ import annotations

from quality_gate.domain.enums import (
    GateDisposition,
    QuarantineStatus,
    ResolutionCode,
)
from quality_gate.domain.ids import (
    evaluation_id,
    p5_manifest_id,
    snapshot_id,
)
from quality_gate.domain.models import (
    EvaluationSnapshot,
    GateDecision,
    QuarantineCase,
    ReleaseManifest,
)


def test_id_determinism_and_collision_resistance() -> None:
    id1 = evaluation_id("cand_100", "p5-v1", "candidate_ready")
    id2 = evaluation_id("cand_100", "p5-v1", "candidate_ready")
    id3 = evaluation_id("cand_100", "p5-v2", "candidate_ready")
    assert id1 == id2
    assert id1 != id3
    assert id1.startswith("eval_")

    sid = snapshot_id(id1)
    assert sid.startswith("snap_")

    mid1 = p5_manifest_id("cand_100", id1, "1.0")
    mid2 = p5_manifest_id("cand_100", id1, "1.0")
    assert mid1 == mid2
    assert mid1.startswith("rel_man_")


def test_evaluation_snapshot_hash_reproducibility() -> None:
    snap1 = EvaluationSnapshot.create(
        evaluation_id_str="eval_01",
        candidate_id="cand_01",
        artifact_id="art_01",
        candidate_snapshot={"key": "val1"},
        artifact_snapshot={"duration": 120.0},
        provider_snapshot={"provider": "mock"},
        license_snapshot={"plan": "pro"},
    )
    snap2 = EvaluationSnapshot.create(
        evaluation_id_str="eval_01",
        candidate_id="cand_01",
        artifact_id="art_01",
        candidate_snapshot={"key": "val1"},
        artifact_snapshot={"duration": 120.0},
        provider_snapshot={"provider": "mock"},
        license_snapshot={"plan": "pro"},
    )
    assert snap1.snapshot_hash == snap2.snapshot_hash
    assert len(snap1.snapshot_hash) == 64


def test_gate_decision_and_manifest_hashes() -> None:
    dec = GateDecision.create(
        evaluation_id_str="eval_01",
        disposition=GateDisposition.PASSED,
        gate_results={"technical": "PASSED"},
        blocking_finding_ids=[],
        warning_finding_ids=["warn_01"],
        decision_version="v1.0",
    )
    assert dec.decision_hash is not None
    assert len(dec.decision_hash) == 64

    man = ReleaseManifest.create(
        candidate_id="cand_01",
        canonical_artifact_id="art_01",
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
    assert man.p5_manifest_id.startswith("rel_man_")
    assert len(man.manifest_hash) == 64


def test_quarantine_case_resolution() -> None:
    case = QuarantineCase.create(
        candidate_id="cand_01",
        evaluation_id_str="eval_01",
        reason_codes=["UNKNOWN_COMMERCIAL_LICENSE"],
        priority="HIGH",
    )
    assert case.status == QuarantineStatus.OPEN
    assert case.resolution is None

    resolved = case.resolve(
        resolution=ResolutionCode.CLEARED_BY_OVERRIDE,
        auditor_id="auditor_jane",
        notes="Verified enterprise master license.",
    )
    assert resolved.status == QuarantineStatus.RESOLVED
    assert resolved.resolution == ResolutionCode.CLEARED_BY_OVERRIDE
    assert resolved.assigned_to == "auditor_jane"
    assert resolved.resolution_notes == "Verified enterprise master license."
    assert resolved.resolved_at is not None
