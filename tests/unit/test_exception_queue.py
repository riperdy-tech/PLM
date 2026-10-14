"""The R3 exception queue with hold classes (PART XVIII, ADR-0033).

What these tests prove, and what they do not:

* Queue tests run the real ``plm_platform.exceptions.ExceptionQueue`` against the real
  table definitions on in-memory SQLite (``ops`` attached). They prove the hold-class
  rules, the state machine, idempotency and the decision record. They do not prove
  Postgres behaviour (the append-only trigger, concurrent raises); the integration tier
  covers the migration on Postgres.
* Policy tests load the JSON shipped in ``plm_platform/policy`` through
  ``importlib.resources`` and prove that policy data cannot relax a mandatory hard stop.
* P5 tests drive the real ``/qa`` FastAPI app in process (TestClient, SQLite). They prove
  the override route goes through ``authorize_resume`` and returns the refusal reason
  code. They are not evidence of a deployed service or a Temporal resume signal (R3
  workflow work).
"""

from __future__ import annotations

import importlib.util
import json
import logging
import math
import struct
import wave
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.exceptions import (
    EXCEPTIONS_METADATA,
    MANDATORY_HARD_STOPS,
    POLICY_FILE,
    POLICY_PACKAGE,
    CaseResolution,
    CaseStatus,
    DecisionCode,
    EventType,
    ExceptionQueue,
    ExternalOutcome,
    ExternalResolutionRefusedError,
    HoldClass,
    HoldClassPolicyError,
    ResumeRefusedError,
    ReviewDecision,
    StaticReviewerDirectory,
    load_hold_class_policy,
    parse_hold_class_policy,
)
from quality_gate.api.app import create_app
from quality_gate.config import load_quality_gate_settings
from quality_gate.domain.enums import QuarantineStatus
from quality_gate.domain.models import QuarantineCase
from quality_gate.storage.repositories import insert_quarantine_case
from sqlalchemy.orm import Session, sessionmaker

from tests.support.qa_db import create_qa_engine, qa_session
from tests.support.seams import candidate_event, lineage_fields, rights_fields

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "migrations" / "versions" / "plm" / "plm_0028_ops_exception_queue.py"

ALL_ROLES = [
    "admin",
    "rights_counsel",
    "rights_reviewer",
    "quality_reviewer",
    "platform_liaison",
    "account_owner",
    "distribution_liaison",
    "policy_owner",
]
REVIEWERS = StaticReviewerDirectory(
    {
        "root": ALL_ROLES,  # holds every role, including admin
        "rita": ["rights_reviewer"],
        "quinn": ["quality_reviewer"],
        "counsel": ["rights_counsel"],
    }
)
UNKNOWN_RIGHTS_EVIDENCE = {
    "provider_license_document_ref": "doc://provider/license/42",
    "license_terms_version": "2026-09",
    "commercial_use_confirmation_ref": "ticket://provider/7781",
}


@pytest.fixture
def session() -> Iterator[Session]:
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=sa.pool.StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ops")
    EXCEPTIONS_METADATA.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture
def queue() -> ExceptionQueue:
    return ExceptionQueue(reviewers=REVIEWERS)


def _raise(session: Session, queue: ExceptionQueue, reason_code: str, subject: str = "s1") -> str:
    return queue.raise_exception(
        session,
        origin_phase="P7",
        subject_ref=f"youtube.upload/{subject}",
        reason_code=reason_code,
        raised_by="youtube_publishing",
        now=NOW,
    ).case.id


def _full_evidence(queue: ExceptionQueue, reason_code: str) -> dict[str, str]:
    rule = queue.classify(reason_code).rule
    return {item: f"ref://{item}/1" for item in rule.required_evidence}


# ------------------------------------------------------------------------- hard stops


@pytest.mark.parametrize("reason_code", sorted(MANDATORY_HARD_STOPS))
def test_a_hard_stop_refuses_a_generic_approve_even_from_an_admin(
    session: Session, queue: ExceptionQueue, reason_code: str
) -> None:
    """Proves: no role (admin, or the case's own reviewer role) and no amount of evidence
    resumes a hard stop through the approve path, for every P0/P29 hard-stop category."""
    case_id = _raise(session, queue, reason_code)
    case = queue.require(session, case_id)
    assert case.hold_class is HoldClass.HARD_STOP and not case.override_permitted

    evidence = _full_evidence(queue, reason_code)
    for role in ("admin", case.reviewer_role):
        decision = queue.authorize_resume(
            session, case_id, actor="root", role=role, evidence=evidence, now=NOW
        )
        assert not decision.allowed
        assert decision.code is DecisionCode.HARD_STOP_NOT_OVERRIDABLE
    with pytest.raises(ResumeRefusedError) as refused:
        queue.resolve(
            session, case_id, decision=ReviewDecision.RESUME, actor="root", role="admin",
            evidence=evidence, notes="approve", now=NOW,
        )
    assert refused.value.decision.code is DecisionCode.HARD_STOP_NOT_OVERRIDABLE
    assert queue.require(session, case_id).status is CaseStatus.OPEN
    history = queue.history(session, case_id)
    assert [e.event_type for e in history] == [
        EventType.RAISED, EventType.RESUME_REFUSED, EventType.RESUME_REFUSED,
        EventType.RESUME_REFUSED,
    ]
    assert all(e.actor and e.occurred_at == NOW for e in history)


def test_a_hard_stop_resolves_only_through_external_resolution_evidence(
    session: Session, queue: ExceptionQueue
) -> None:
    """Proves: a hard stop closes only via record_external_resolution with every evidence
    item, recorded by the role the policy names; a cleared outcome is then resumable."""
    case_id = _raise(session, queue, "COPYRIGHT_DISPUTE")
    evidence = _full_evidence(queue, "COPYRIGHT_DISPUTE")

    with pytest.raises(ExternalResolutionRefusedError) as missing:
        queue.record_external_resolution(
            session, case_id, outcome=ExternalOutcome.CLEARED,
            evidence={"dispute_id": "YT-123"}, actor="counsel", role="rights_counsel",
            notes="", now=NOW,
        )
    assert missing.value.decision.code is DecisionCode.EVIDENCE_MISSING
    assert missing.value.decision.missing_evidence == ("dispute_outcome_ref",)

    with pytest.raises(ExternalResolutionRefusedError) as wrong_role:
        queue.record_external_resolution(
            session, case_id, outcome=ExternalOutcome.CLEARED, evidence=evidence,
            actor="root", role="admin", notes="", now=NOW,
        )
    assert wrong_role.value.decision.code is DecisionCode.REVIEWER_ROLE_MISMATCH
    assert queue.require(session, case_id).status is CaseStatus.OPEN

    later = NOW + timedelta(days=3)
    resolved = queue.record_external_resolution(
        session, case_id, outcome=ExternalOutcome.CLEARED, evidence=evidence,
        actor="counsel", role="rights_counsel", notes="dispute released", now=later,
    )
    assert resolved.status is CaseStatus.RESOLVED
    assert resolved.resolution is CaseResolution.CLEARED_BY_EXTERNAL_PROCESS
    assert (resolved.closed_by, resolved.closed_at) == ("counsel", later)

    decision = queue.authorize_resume(
        session, case_id, actor="youtube_worker", role=None, evidence={}, now=later
    )
    assert decision.allowed and decision.code is DecisionCode.EXTERNAL_RESOLUTION_RECORDED
    recorded = [
        e for e in queue.history(session, case_id)
        if e.event_type is EventType.EXTERNAL_RESOLUTION_RECORDED
    ]
    assert len(recorded) == 1 and recorded[0].evidence == evidence


def test_an_adverse_external_outcome_rejects_the_hard_stop_for_good(
    session: Session, queue: ExceptionQueue
) -> None:
    """Proves: an adverse external decision closes the case as REJECTED and no later
    approve or resume reopens it."""
    case_id = _raise(session, queue, "PLATFORM_CLAIM")
    queue.record_external_resolution(
        session, case_id, outcome=ExternalOutcome.ADVERSE,
        evidence=_full_evidence(queue, "PLATFORM_CLAIM"), actor="root",
        role="platform_liaison", notes="claim upheld", now=NOW,
    )
    case = queue.require(session, case_id)
    assert case.status is CaseStatus.REJECTED
    assert case.resolution is CaseResolution.REJECTED_BY_EXTERNAL_PROCESS
    decision = queue.authorize_resume(
        session, case_id, actor="root", role="admin", evidence={}, now=NOW
    )
    assert not decision.allowed and decision.code is DecisionCode.CASE_CLOSED


def test_external_resolution_does_not_apply_to_a_reviewable_quarantine(
    session: Session, queue: ExceptionQueue
) -> None:
    """Proves: the external path cannot be used to skip the reviewer rules."""
    case_id = _raise(session, queue, "UNKNOWN_COMMERCIAL_LICENSE")
    with pytest.raises(ExternalResolutionRefusedError) as refused:
        queue.record_external_resolution(
            session, case_id, outcome=ExternalOutcome.CLEARED,
            evidence=UNKNOWN_RIGHTS_EVIDENCE, actor="rita", role="rights_reviewer",
            notes="", now=NOW,
        )
    assert refused.value.decision.code is DecisionCode.EXTERNAL_RESOLUTION_NOT_APPLICABLE


# ---------------------------------------------------------------- reviewable quarantine


def test_a_reviewable_quarantine_refuses_a_non_reviewer(
    session: Session, queue: ExceptionQueue
) -> None:
    """Proves: the role must be the case's reviewer role (admin is not a wildcard) and the
    injected directory must grant it; a self-asserted role is not enough."""
    case_id = _raise(session, queue, "UNKNOWN_COMMERCIAL_LICENSE")

    def attempt(actor: str, role: str | None) -> DecisionCode:
        decision = queue.authorize_resume(
            session, case_id, actor=actor, role=role, evidence=UNKNOWN_RIGHTS_EVIDENCE, now=NOW
        )
        assert not decision.allowed
        return decision.code

    assert attempt("root", "admin") is DecisionCode.REVIEWER_ROLE_MISMATCH
    assert attempt("quinn", "quality_reviewer") is DecisionCode.REVIEWER_ROLE_MISMATCH
    assert attempt("mallory", "rights_reviewer") is DecisionCode.REVIEWER_NOT_AUTHORIZED
    assert attempt("rita", None) is DecisionCode.REVIEWER_ROLE_MISMATCH
    assert attempt("", "rights_reviewer") is DecisionCode.ACTOR_MISSING
    assert queue.require(session, case_id).status is CaseStatus.OPEN


def test_a_reviewable_quarantine_refuses_missing_evidence(
    session: Session, queue: ExceptionQueue
) -> None:
    """Proves: unknown rights never exit on a bare approve; every required item must be
    present and non-blank, and the refusal names what is missing."""
    case_id = _raise(session, queue, "UNKNOWN_COMMERCIAL_LICENSE")
    partial = {
        "provider_license_document_ref": "doc://provider/license/42",
        "license_terms_version": "   ",
    }
    for evidence in ({}, partial):
        with pytest.raises(ResumeRefusedError) as refused:
            queue.resolve(
                session, case_id, decision=ReviewDecision.RESUME, actor="rita",
                role="rights_reviewer", evidence=evidence, notes="looks fine", now=NOW,
            )
        assert refused.value.decision.code is DecisionCode.EVIDENCE_MISSING
    assert refused.value.decision.missing_evidence == (
        "license_terms_version", "commercial_use_confirmation_ref",
    )
    assert queue.require(session, case_id).status is CaseStatus.OPEN


def test_a_reviewable_quarantine_resumes_for_an_authorized_reviewer_with_evidence(
    session: Session, queue: ExceptionQueue
) -> None:
    """Proves: the one successful path for a reviewable hold, and that it is recorded with
    actor, role, evidence and the injected instant."""
    case_id = _raise(session, queue, "UNKNOWN_COMMERCIAL_LICENSE")
    decision = queue.authorize_resume(
        session, case_id, actor="rita", role="rights_reviewer",
        evidence=UNKNOWN_RIGHTS_EVIDENCE, now=NOW,
    )
    assert decision.allowed and decision.code is DecisionCode.REVIEWER_AUTHORIZED

    case = queue.resolve(
        session, case_id, decision=ReviewDecision.RESUME, actor="rita",
        role="rights_reviewer", evidence=UNKNOWN_RIGHTS_EVIDENCE, notes="license found",
        now=NOW,
    )
    assert case.status is CaseStatus.RESOLVED
    assert case.resolution is CaseResolution.RESUMED_BY_REVIEWER
    assert (case.closed_by, case.closed_at) == ("rita", NOW)
    events = queue.history(session, case_id)
    assert [e.event_type for e in events] == [
        EventType.RAISED, EventType.RESUME_AUTHORIZED, EventType.RESOLVED,
    ]
    assert (events[-1].actor, events[-1].role, events[-1].evidence) == (
        "rita", "rights_reviewer", UNKNOWN_RIGHTS_EVIDENCE,
    )
    again = queue.authorize_resume(
        session, case_id, actor="worker", role=None, evidence={}, now=NOW
    )
    assert again.allowed and again.code is DecisionCode.ALREADY_RESOLVED


def test_reject_and_withdraw_close_a_case_without_resuming_it(
    session: Session, queue: ExceptionQueue
) -> None:
    """Proves: the fail-safe exits need only an identified actor, are terminal, and leave
    the workflow held."""
    rejected = _raise(session, queue, "UNKNOWN_COMMERCIAL_LICENSE", subject="a")
    withdrawn = _raise(session, queue, "COPYRIGHT_DISPUTE", subject="b")
    queue.resolve(session, rejected, decision=ReviewDecision.REJECT, actor="quinn",
                  role=None, evidence=None, notes="discard", now=NOW)
    queue.resolve(session, withdrawn, decision=ReviewDecision.WITHDRAW, actor="quinn",
                  role=None, evidence=None, notes="re-evaluating", now=NOW)
    assert queue.require(session, rejected).status is CaseStatus.REJECTED
    assert queue.require(session, withdrawn).status is CaseStatus.WITHDRAWN
    for case_id in (rejected, withdrawn):
        decision = queue.authorize_resume(
            session, case_id, actor="rita", role="rights_reviewer",
            evidence=UNKNOWN_RIGHTS_EVIDENCE, now=NOW,
        )
        assert decision.code is DecisionCode.CASE_CLOSED
        with pytest.raises(ResumeRefusedError):
            queue.resolve(session, case_id, decision=ReviewDecision.REJECT, actor="quinn",
                          role=None, evidence=None, notes="", now=NOW)


# ------------------------------------------------------------- unmapped and idempotent


def test_an_unmapped_reason_code_is_treated_as_a_hard_stop(
    session: Session, queue: ExceptionQueue, caplog: pytest.LogCaptureFixture
) -> None:
    """Proves: a code the policy does not classify fails closed (HARD_STOP), is flagged on
    the case and in the log, and is not resumable by any role."""
    with caplog.at_level(logging.WARNING, logger="plm_platform.exceptions"):
        case_id = _raise(session, queue, "BRAND_NEW_SIGNAL")
    case = queue.require(session, case_id)
    assert case.hold_class is HoldClass.HARD_STOP
    assert case.policy_mapped is False and not case.override_permitted
    assert case.details["policy_unmapped_reason_code"] is True
    assert "BRAND_NEW_SIGNAL" in caplog.text
    decision = queue.authorize_resume(
        session, case_id, actor="root", role="admin",
        evidence=_full_evidence(queue, "BRAND_NEW_SIGNAL"), now=NOW,
    )
    assert decision.code is DecisionCode.HARD_STOP_NOT_OVERRIDABLE


def test_raising_the_same_case_twice_is_idempotent(
    session: Session, queue: ExceptionQueue
) -> None:
    """Proves: one case per (origin, subject, reason code); a repeat raise returns it and
    writes nothing, while a new subject or reason opens a new case."""
    first = queue.raise_exception(
        session, origin_phase="P5", subject_ref="qa.quarantine_case/q1",
        reason_code="UNKNOWN_COMMERCIAL_LICENSE", raised_by="quality_gate", now=NOW,
    )
    second = queue.raise_exception(
        session, origin_phase="P5", subject_ref="qa.quarantine_case/q1",
        reason_code="UNKNOWN_COMMERCIAL_LICENSE", raised_by="quality_gate",
        now=NOW + timedelta(hours=1),
    )
    assert first.created and not second.created
    assert second.case == first.case
    assert len(queue.history(session, first.case.id)) == 1
    other = queue.raise_exception(
        session, origin_phase="P5", subject_ref="qa.quarantine_case/q2",
        reason_code="UNKNOWN_COMMERCIAL_LICENSE", raised_by="quality_gate", now=NOW,
    )
    assert other.created and other.case.id != first.case.id
    count = session.execute(sa.text("SELECT count(*) FROM ops.exception_cases")).scalar()
    assert count == 2


def test_raise_refuses_an_unknown_origin(session: Session, queue: ExceptionQueue) -> None:
    with pytest.raises(ValueError, match="origin"):
        queue.raise_exception(
            session, origin_phase="P99", subject_ref="x", reason_code="KYC_REQUIRED",
            raised_by="me", now=NOW,
        )


# ----------------------------------------------------------------------------- policy


def _policy_json() -> dict[str, Any]:
    from importlib import resources

    text = resources.files(POLICY_PACKAGE).joinpath(POLICY_FILE).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    return data


def test_the_shipped_policy_classifies_every_p29_and_human_hard_stop() -> None:
    """Proves: the packaged data loads, every mandatory hard stop is HARD_STOP, unknown
    rights are reviewable with evidence, and no rule allows a bare approve."""
    policy = load_hold_class_policy()
    for code in MANDATORY_HARD_STOPS:
        assert policy.classify(code).rule.hold_class is HoldClass.HARD_STOP, code
    for code in ("UNKNOWN_COMMERCIAL_LICENSE", "INCOMPLETE_R6_PROVENANCE"):
        rule = policy.classify(code).rule
        assert rule.hold_class is HoldClass.REVIEWABLE_QUARANTINE
        assert rule.reviewer_role == "rights_reviewer" and rule.required_evidence
    assert all(rule.required_evidence for rule in policy.reason_codes.values())
    assert all(rule.source for rule in policy.reason_codes.values())


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d["reason_codes"]["COPYRIGHT_DISPUTE"].update(
            hold_class="REVIEWABLE_QUARANTINE", override_permitted=True), "reviewable"),
        (lambda d: d["reason_codes"].pop("PLATFORM_CLAIM"), "missing"),
        (lambda d: d["reason_codes"]["KYC_REQUIRED"].update(override_permitted=True),
         "cannot permit an override"),
        (lambda d: d["reason_codes"]["UNKNOWN_COMMERCIAL_LICENSE"].update(
            required_evidence=[]), "required_evidence"),
        (lambda d: d["unmapped"].update(hold_class="REVIEWABLE_QUARANTINE"), "unmapped"),
    ],
)
def test_policy_data_cannot_relax_a_hard_stop_or_allow_a_bare_approve(
    mutate: Any, message: str
) -> None:
    """Proves: a data edit that downgrades a human hard stop, permits overriding one, drops
    the evidence requirement or makes unmapped codes reviewable is refused at load."""
    data = _policy_json()
    mutate(data)
    with pytest.raises(HoldClassPolicyError, match=message):
        parse_hold_class_policy(data)


def test_the_policy_json_ships_as_package_data() -> None:
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '[tool.setuptools.package-data]' in pyproject
    assert 'plm_platform = ["policy/*.json"]' in pyproject


# -------------------------------------------------------------------------- migration


def test_migration_creates_exactly_the_queue_tables_and_downgrade_removes_them() -> None:
    """Proves: migration 0028 and the runtime table definitions describe the same tables
    and columns (drift guard, SQLite). Postgres-only DDL (the trigger) is not run here."""
    spec = importlib.util.spec_from_file_location("plm_0028_ops_exception_queue", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0028_ops_exception_queue"
    assert module.down_revision == "0027_ops_cost_ledger"

    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS ops")
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        inspector = sa.inspect(connection)
        created = set(inspector.get_table_names(schema="ops"))
        assert created == {t.name for t in EXCEPTIONS_METADATA.tables.values()}
        for table in EXCEPTIONS_METADATA.tables.values():
            columns = {c["name"] for c in inspector.get_columns(table.name, schema="ops")}
            assert columns == {c.name for c in table.columns}, table.name
        module.downgrade()
        assert set(sa.inspect(connection).get_table_names(schema="ops")) == set()


# ------------------------------------------------------------------------ P5 override

_TOKEN = "test-qa-secret-token"
_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": _TOKEN,
}
_HEADERS = {SERVICE_TOKEN_HEADER: _TOKEN}


@pytest.fixture
def qa_engine() -> sa.Engine:
    return create_qa_engine(static_pool=True)


@pytest.fixture
def qa_client(qa_engine: sa.Engine) -> TestClient:
    settings = load_quality_gate_settings(
        platform=load_settings(env=_ENV), reviewers=REVIEWERS
    )
    return TestClient(create_app(session_factory=lambda: qa_session(qa_engine), settings=settings))


def _wav(tmp_path: Path) -> Path:
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        frames = bytearray()
        for i in range(44100):
            v = int(14000.0 * math.sin(2.0 * math.pi * 440.0 * i / 44100))
            frames.extend(struct.pack("<hh", v, v))
        wf.writeframes(frames)
    return path


def _quarantined(qa_client: TestClient, tmp_path: Path) -> dict[str, Any]:
    event = candidate_event(
        candidate_id="cand_hold_01",
        lineage=lineage_fields(experiment_id="exp_hold", content_arm_id="arm_hold"),
        rights=rights_fields(commercial_use_status="unknown"),
    ).to_wire()
    res = qa_client.post(
        "/qa/evaluations",
        json={"candidate_event": event, "audio_path": str(_wav(tmp_path))},
        headers=_HEADERS,
    )
    assert res.status_code == 201
    body: dict[str, Any] = res.json()
    assert body["disposition"] == "QUARANTINED"
    return body


def test_p5_unknown_rights_cannot_be_overridden_without_evidence(
    qa_client: TestClient, qa_engine: sa.Engine, tmp_path: Path
) -> None:
    """Proves: P5's override route asks the queue; a bare approve and an approve without
    evidence are refused with 409 and the refusal reason code, nothing in qa changes, and
    the refused attempts are recorded in ops."""
    case_id = _quarantined(qa_client, tmp_path)["quarantine_case_id"]
    url = f"/qa/quarantine/{case_id}/resolve"

    bare = qa_client.post(
        url, json={"resolution": "CLEARED_BY_OVERRIDE", "auditor_id": "rita"}, headers=_HEADERS
    )
    assert bare.status_code == 409
    assert bare.json()["detail"]["code"] == "reviewer_role_mismatch"
    assert "UNKNOWN_COMMERCIAL_LICENSE" in bare.json()["detail"]["message"]

    no_evidence = qa_client.post(
        url,
        json={"resolution": "CLEARED_BY_OVERRIDE", "auditor_id": "rita",
              "reviewer_role": "rights_reviewer"},
        headers=_HEADERS,
    )
    assert no_evidence.status_code == 409
    detail = no_evidence.json()["detail"]
    assert detail["code"] == "evidence_missing"
    assert "UNKNOWN_COMMERCIAL_LICENSE" in detail["message"]
    assert set(detail["field_paths"]) == {f"evidence.{k}" for k in UNKNOWN_RIGHTS_EVIDENCE}

    assert qa_client.get("/qa/release-manifests/cand_hold_01", headers=_HEADERS).status_code == 404
    listed = qa_client.get("/qa/quarantine", headers=_HEADERS).json()
    (case,) = [c for c in listed if c["case_id"] == case_id]
    assert case["status"] == "OPEN"
    assert [(h["reason_code"], h["status"]) for h in case["holds"]] == [
        ("UNKNOWN_COMMERCIAL_LICENSE", "OPEN")
    ]
    with qa_session(qa_engine) as db:
        events = ExceptionQueue().history(db, case["holds"][0]["hold_id"])
    assert [e.event_type for e in events] == [
        EventType.RAISED, EventType.RESUME_REFUSED, EventType.RESUME_REFUSED,
    ]

    granted = qa_client.post(
        url,
        json={"resolution": "CLEARED_BY_OVERRIDE", "auditor_id": "rita",
              "reviewer_role": "rights_reviewer", "evidence": UNKNOWN_RIGHTS_EVIDENCE},
        headers=_HEADERS,
    )
    assert granted.status_code == 200
    assert granted.json()["holds"][0]["status"] == "RESOLVED"


def test_p5_override_of_a_hard_stop_is_refused_for_an_admin_with_evidence(
    qa_client: TestClient, qa_engine: sa.Engine, tmp_path: Path
) -> None:
    """Proves: a P5 case whose reason is a hard stop (here a case stored without holds,
    as before R3) gains its holds on resolve and the override is refused 409
    hard_stop_not_overridable, whatever role and evidence the caller brings."""
    body = _quarantined(qa_client, tmp_path)
    with qa_session(qa_engine) as db:
        insert_quarantine_case(
            db,
            QuarantineCase(
                id="qc_legacy_hard_stop",
                candidate_id="cand_hold_01",
                evaluation_id=body["evaluation_id"],
                reason_codes=["COMMERCIAL_LICENSE_UNCERTAIN"],
                priority="HIGH",
                status=QuarantineStatus.OPEN,
                created_at=NOW,
            ),
        )
        db.commit()
    evidence = {
        "provider_license_determination_ref": "doc://1",
        "rights_counsel_opinion_ref": "doc://2",
    }
    res = qa_client.post(
        "/qa/quarantine/qc_legacy_hard_stop/resolve",
        json={"resolution": "CLEARED_BY_OVERRIDE", "auditor_id": "root",
              "reviewer_role": "rights_counsel", "evidence": evidence},
        headers=_HEADERS,
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "hard_stop_not_overridable"
    assert "COMMERCIAL_LICENSE_UNCERTAIN" in res.json()["detail"]["message"]
    listed = qa_client.get("/qa/quarantine", headers=_HEADERS).json()
    (case,) = [c for c in listed if c["case_id"] == "qc_legacy_hard_stop"]
    assert case["status"] == "OPEN"
    assert [(h["hold_class"], h["status"]) for h in case["holds"]] == [("HARD_STOP", "OPEN")]


def test_p5_confirmed_rejection_closes_the_holds(
    qa_client: TestClient, tmp_path: Path
) -> None:
    """Proves: rejecting a P5 case rejects its holds (a fail-safe exit, no reviewer role)."""
    case_id = _quarantined(qa_client, tmp_path)["quarantine_case_id"]
    res = qa_client.post(
        f"/qa/quarantine/{case_id}/resolve",
        json={"resolution": "CONFIRMED_REJECTED", "auditor_id": "quinn"},
        headers=_HEADERS,
    )
    assert res.status_code == 200
    assert [h["status"] for h in res.json()["holds"]] == ["REJECTED"]


def test_p5_a_resolved_case_cannot_be_resolved_again(
    qa_client: TestClient, tmp_path: Path
) -> None:
    """Proves: a second override of an already-resolved case is refused (409) instead of
    issuing a second release manifest."""
    case_id = _quarantined(qa_client, tmp_path)["quarantine_case_id"]
    url = f"/qa/quarantine/{case_id}/resolve"
    body = {"resolution": "CLEARED_BY_OVERRIDE", "auditor_id": "rita",
            "reviewer_role": "rights_reviewer", "evidence": UNKNOWN_RIGHTS_EVIDENCE}
    assert qa_client.post(url, json=body, headers=_HEADERS).status_code == 200
    again = qa_client.post(url, json=body, headers=_HEADERS)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "quarantine_already_resolved"


def test_a_missing_pipeline_capability_is_a_classified_hard_stop() -> None:
    """Proves: a step with no implementation yet (R4/R5) holds as a mapped hard stop. No
    approval can produce the missing work, so it closes only when the capability ships."""
    classification = load_hold_class_policy().classify("CAPABILITY_UNAVAILABLE")
    assert classification.mapped is True
    assert classification.rule.hold_class.value == "HARD_STOP"
    assert classification.rule.override_permitted is False
