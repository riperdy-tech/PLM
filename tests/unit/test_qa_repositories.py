"""Unit tests for Phase P5 repositories on SQLite harness (P5 §82-§92)."""

from __future__ import annotations

from datetime import UTC, datetime

from quality_gate.domain.enums import (
    CheckSeverity,
    CheckStatus,
    EvaluationStatus,
    GateCategory,
    GateDisposition,
    QuarantineStatus,
    ResolutionCode,
)
from quality_gate.domain.models import (
    CandidateEvaluation,
    EvaluationSnapshot,
    EvidenceItem,
    GateDecision,
    LyricCheck,
    MusicalCheck,
    PolicyCheck,
    ProvenanceCheck,
    QuarantineCase,
    ReleaseManifest,
    RightsCheck,
    RiskFinding,
    TechnicalCheck,
)
from quality_gate.storage import repositories

from tests.support.qa_db import create_qa_engine, qa_session
from tests.support.seams import sample_lineage

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def test_candidate_evaluation_and_snapshot_crud() -> None:
    engine = create_qa_engine()
    with qa_session(engine) as session:
        ev = CandidateEvaluation(
            id="eval_001",
            candidate_id="cand_001",
            artifact_id="art_001",
            evaluation_policy_version="p5-v1",
            trigger="candidate_ready",
            status=EvaluationStatus.PENDING,
            started_at=NOW,
            created_at=NOW,
        )
        repositories.insert_candidate_evaluation(session, ev)

        snap = EvaluationSnapshot.create(
            evaluation_id_str="eval_001",
            candidate_id="cand_001",
            artifact_id="art_001",
            candidate_snapshot={"genre": "ambient"},
            artifact_snapshot={"duration": 60.0},
            provider_snapshot={"provider": "mock"},
            license_snapshot={"tier": "pro"},
            now=NOW,
        )
        repositories.insert_evaluation_snapshot(session, snap)
        session.commit()

        # Query back
        fetched_ev = repositories.get_candidate_evaluation(session, "eval_001")
        assert fetched_ev is not None
        assert fetched_ev.candidate_id == "cand_001"
        assert fetched_ev.status == EvaluationStatus.PENDING

        fetched_snap = repositories.get_evaluation_snapshot(session, "eval_001")
        assert fetched_snap is not None
        assert fetched_snap.candidate_snapshot == {"genre": "ambient"}

        # Update status
        repositories.update_candidate_evaluation_status(
            session=session,
            evaluation_id="eval_001",
            status=EvaluationStatus.COMPLETED,
            disposition=GateDisposition.PASSED,
            technical_status="PASSED",
            finished_at=NOW,
        )
        session.commit()

        updated_ev = repositories.get_candidate_evaluation(session, "eval_001")
        assert updated_ev is not None
        assert updated_ev.status == EvaluationStatus.COMPLETED
        assert updated_ev.disposition == GateDisposition.PASSED


def test_checks_findings_and_evidence_crud() -> None:
    engine = create_qa_engine()
    with qa_session(engine) as session:
        tc = TechnicalCheck.create(
            evaluation_id_str="eval_002",
            check_type="duration",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            measured_value=120.0,
            expected_value=120.0,
            unit="seconds",
            now=NOW,
        )
        repositories.insert_technical_check(session, tc)

        mc = MusicalCheck.create(
            evaluation_id_str="eval_002",
            check_type="lufs",
            score=0.9,
            confidence=0.85,
            severity=CheckSeverity.INFO,
            now=NOW,
        )
        repositories.insert_musical_check(session, mc)

        lc = LyricCheck.create(
            evaluation_id_str="eval_002",
            check_type="profanity",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            now=NOW,
        )
        repositories.insert_lyric_check(session, lc)

        pc = ProvenanceCheck.create(
            evaluation_id_str="eval_002",
            check_type="r6",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            now=NOW,
        )
        repositories.insert_provenance_check(session, pc)

        rc = RightsCheck.create(
            evaluation_id_str="eval_002",
            check_type="license",
            status=CheckStatus.PASS,
            risk_level="LOW",
            confidence=0.9,
            source_type="provider",
            source_ref="mock",
            now=NOW,
        )
        repositories.insert_rights_check(session, rc)

        pol = PolicyCheck.create(
            evaluation_id_str="eval_002",
            policy_namespace="platform",
            rule_id="disclosure",
            rule_version="1.0",
            status=CheckStatus.PASS,
            severity=CheckSeverity.INFO,
            now=NOW,
        )
        repositories.insert_policy_check(session, pol)

        rf = RiskFinding.create(
            evaluation_id_str="eval_002",
            finding_code="TEST_FINDING",
            category=GateCategory.QUALITY_GATE,
            severity=CheckSeverity.LOW,
            status="WARN",
            confidence=0.8,
            message="Test message",
            now=NOW,
        )
        repositories.insert_risk_finding(session, rf)

        ev_item = EvidenceItem.create(
            evaluation_id_str="eval_002",
            evidence_type="hash",
            source="system",
            source_reference="ref",
            content_hash="hash123",
            summary="test summary",
            now=NOW,
        )
        repositories.insert_evidence_item(session, ev_item)
        session.commit()

        # Retrieve and verify
        tcs = repositories.list_technical_checks(session, "eval_002")
        assert len(tcs) == 1
        assert tcs[0].check_type == "duration"

        rfs = repositories.list_risk_findings(session, "eval_002")
        assert len(rfs) == 1
        assert rfs[0].finding_code == "TEST_FINDING"

        evs = repositories.list_evidence_items(session, "eval_002")
        assert len(evs) == 1
        assert evs[0].content_hash == "hash123"


def test_gate_decision_quarantine_and_manifest_crud() -> None:
    engine = create_qa_engine()
    with qa_session(engine) as session:
        dec = GateDecision.create(
            evaluation_id_str="eval_003",
            disposition=GateDisposition.PASSED,
            gate_results={"test": "pass"},
            blocking_finding_ids=[],
            warning_finding_ids=[],
            now=NOW,
        )
        repositories.insert_gate_decision(session, dec)

        qcase = QuarantineCase.create(
            candidate_id="cand_003",
            evaluation_id_str="eval_003",
            reason_codes=["REASON_1"],
            now=NOW,
        )
        repositories.insert_quarantine_case(session, qcase)

        manifest = ReleaseManifest.create(
            candidate_id="cand_003",
            canonical_artifact_id="art_003",
            artifact_uri="music/test/art_003.wav",
            artifact_sha256="0" * 64,
            measured_duration_seconds=180.0,
            track_spec_id="spec_003",
            lineage=sample_lineage(experiment_id="exp_003", content_arm_id="arm_003"),
            disposition=GateDisposition.PASSED,
            override=None,
            technical_status="PASSED",
            commercial_use_status="ELIGIBLE",
            provenance_status="COMPLETE",
            ai_disclosure_required=True,
            rights_risk="LOW",
            policy_status="COMPLIANT",
            evaluation_id_str="eval_003",
            evaluation_policy_version="p5-v1",
            warning_codes=[],
            evidence_bundle_id="evi_003",
            now=NOW,
        )
        repositories.insert_release_manifest(session, manifest)
        session.commit()

        # Gate decision retrieval
        fetched_dec = repositories.get_gate_decision(session, "eval_003")
        assert fetched_dec is not None
        assert fetched_dec.disposition == GateDisposition.PASSED

        # Quarantine retrieval & resolution
        open_cases = repositories.list_open_quarantine_cases(session)
        assert len(open_cases) == 1
        assert open_cases[0].id == qcase.id

        repositories.resolve_quarantine_case(
            session=session,
            case_id=qcase.id,
            resolution=ResolutionCode.CLEARED_BY_OVERRIDE,
            auditor_id="auditor_test",
            notes="override notes",
            resolved_at=NOW,
        )
        session.commit()

        resolved_case = repositories.get_quarantine_case(session, qcase.id)
        assert resolved_case is not None
        assert resolved_case.status == QuarantineStatus.RESOLVED
        assert resolved_case.resolution == ResolutionCode.CLEARED_BY_OVERRIDE

        # Release manifest retrieval
        by_cand = repositories.get_release_manifest_by_candidate(session, "cand_003")
        assert by_cand is not None
        assert by_cand.p5_manifest_id == manifest.p5_manifest_id

        by_id = repositories.get_release_manifest_by_id(session, manifest.p5_manifest_id)
        assert by_id is not None
        assert by_id.candidate_id == "cand_003"
