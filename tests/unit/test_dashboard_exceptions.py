"""The Exceptions view's actions drive the R3 exception queue (R5, ADR-0033, PART XVIII).

What these prove: every decision goes through :class:`ExceptionQueue` with the reviewer
directory the dashboard is configured with; a hard stop cannot be approved from the
dashboard; a reviewable hold refuses a wrong role, an unauthorized actor and missing
evidence (each refusal returned with its code and recorded as a case event); the
authorized reviewer with every evidence item resolves it; a hold raised by a pipeline run
is then signalled to that run (here a recording fake, not Temporal); an undeliverable
signal leaves the decision recorded and the signal pending; mutating routes need the
service token; no reviewer directory means no action.

What they do not prove: delivery to a real Temporal workflow. That is
``tests/integration/pipeline/test_dashboard_live.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.dashboard.actions import (
    ExceptionActions,
    ReviewerConfig,
    SignalRequest,
    pipeline_link,
)
from plm_platform.dashboard.app import create_live_app
from plm_platform.dashboard.live import LiveDashboard
from plm_platform.dashboard.sources import PhaseApis, ReadOnlyDatabase
from plm_platform.exceptions import ExceptionCase, ExceptionQueue
from plm_platform.pipeline.types import ExternalResolutionSignal, ResumeSignal
from sqlalchemy.orm import Session, sessionmaker

from tests.support.c1_api import PLATFORM_ENV, SEAM_TOKEN
from tests.support.dashboard_db import create_dashboard_engine, raise_hold, record_run

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
AUTH = {SERVICE_TOKEN_HEADER: SEAM_TOKEN}
REVIEWERS = ReviewerConfig(
    grants={"rita": frozenset({"rights_reviewer"}), "counsel": frozenset({"rights_counsel"}),
            "admin": frozenset({"admin", "budget_owner"})},
    source="test")
WORKFLOW_ID = "pipeline.dir_dash"
RUN_ID = "run-0001"


@dataclass
class FakeSignaller:
    """Records every signal; ``fail`` makes delivery raise (Temporal unreachable)."""

    sent: list[SignalRequest] = field(default_factory=list)
    fail: Exception | None = None

    async def signal(self, request: SignalRequest) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append(request)


@dataclass
class Harness:
    factory: sessionmaker[Session]
    client: TestClient
    signaller: FakeSignaller

    def history(self, case_id: str) -> list[tuple[str, str | None]]:
        with self.factory() as session:
            return [(e.event_type.value, e.decision_code)
                    for e in ExceptionQueue().history(session, case_id)]

    def case(self, case_id: str) -> ExceptionCase:
        with self.factory() as session:
            return ExceptionQueue().require(session, case_id)


def _harness(*, reviewers: ReviewerConfig | None = REVIEWERS, platform: bool = True,
             signaller: FakeSignaller | None = None) -> Harness:
    engine = create_dashboard_engine(schemas=("ops",))
    factory = sessionmaker(bind=engine)
    fake = signaller if signaller is not None else FakeSignaller()
    live = LiveDashboard(ReadOnlyDatabase(factory), PhaseApis({}, token=None),
                         reviewers=reviewers, clock=FixedClock(NOW))
    actions = ExceptionActions(factory, reviewers=reviewers, signaller=fake,
                               clock=FixedClock(NOW))
    settings = load_settings(env=PLATFORM_ENV) if platform else None
    return Harness(factory, TestClient(create_live_app(live, actions, platform=settings)), fake)


@pytest.fixture
def harness() -> Iterator[Harness]:
    yield _harness()


def _pipeline_hold(h: Harness, reason: str, *, episode: int = 1, record: bool = True
                   ) -> ExceptionCase:
    case = raise_hold(h.factory, reason, NOW, subject_ref=f"{WORKFLOW_ID}:{RUN_ID}:qa:{episode}",
                      raised_by=f"pipeline:{WORKFLOW_ID}")
    if record:
        record_run(h.factory, WORKFLOW_ID, NOW, hold_case_ids=[case.id])
    return case


def _evidence(case: ExceptionCase) -> dict[str, str]:
    return {name: f"ref-{name}" for name in case.required_evidence}


def _post(h: Harness, case_id: str, action: str, body: dict[str, Any]) -> Any:
    return h.client.post(f"/api/exceptions/{case_id}/{action}", json=body, headers=AUTH)


# ----------------------------------------------------------------------- hard stops


def test_a_hard_stop_refuses_approve_from_the_dashboard_for_every_role(harness: Harness) -> None:
    case = raise_hold(harness.factory, "COPYRIGHT_DISPUTE", NOW)
    for actor, role in (("counsel", "rights_counsel"), ("admin", "admin")):
        response = _post(harness, case.id, "resume",
                         {"actor": actor, "role": role, "evidence": _evidence(case)})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "hard_stop_not_overridable"
    assert harness.case(case.id).status.value == "OPEN"
    assert harness.history(case.id) == [
        ("RAISED", None), ("RESUME_REFUSED", "hard_stop_not_overridable"),
        ("RESUME_REFUSED", "hard_stop_not_overridable")]
    view = harness.client.get("/api/views/exceptions").json()
    (item,) = view["panels"]["open"]["data"]["items"]
    assert "resume" not in item["allowed_actions"] and item["approve_available"] is False


def test_a_hard_stop_resolves_only_through_external_evidence_and_signals_the_run(
        harness: Harness) -> None:
    case = _pipeline_hold(harness, "COPYRIGHT_DISPUTE")
    missing = _post(harness, case.id, "external-resolution",
                    {"actor": "counsel", "role": "rights_counsel",
                     "evidence": {"dispute_id": "D-1"}})
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "evidence_missing"
    assert missing.json()["detail"]["field_paths"] == ["evidence.dispute_outcome_ref"]
    assert harness.signaller.sent == []

    response = _post(harness, case.id, "external-resolution",
                     {"actor": "counsel", "role": "rights_counsel", "outcome": "CLEARED",
                      "evidence": _evidence(case), "notes": "dispute released"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_source"] == "live"
    assert body["case"]["status"] == "RESOLVED"
    assert body["case"]["resolution"] == "CLEARED_BY_EXTERNAL_PROCESS"
    assert body["signal"]["status"] == "sent"
    (sent,) = harness.signaller.sent
    assert (sent.workflow_id, sent.run_id, sent.signal) == (WORKFLOW_ID, RUN_ID,
                                                            "external_resolution")
    assert sent.payload == ExternalResolutionSignal(
        actor="counsel", role="rights_counsel", evidence=_evidence(case), outcome="CLEARED",
        notes="dispute released")


def test_external_resolution_does_not_apply_to_a_reviewable_hold(harness: Harness) -> None:
    case = raise_hold(harness.factory, "UNKNOWN_COMMERCIAL_LICENSE", NOW)
    response = _post(harness, case.id, "external-resolution",
                     {"actor": "rita", "role": "rights_reviewer", "evidence": _evidence(case)})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "external_resolution_not_applicable"


# ------------------------------------------------------------------- reviewable holds


def test_a_reviewable_hold_refuses_a_wrong_role_an_outsider_and_missing_evidence(
        harness: Harness) -> None:
    case = _pipeline_hold(harness, "UNKNOWN_COMMERCIAL_LICENSE")
    attempts = [
        ({"actor": "rita", "role": "budget_owner", "evidence": _evidence(case)},
         "reviewer_role_mismatch"),
        ({"actor": "admin", "role": "rights_reviewer", "evidence": _evidence(case)},
         "reviewer_not_authorized"),
        ({"actor": "rita", "role": "rights_reviewer",
          "evidence": {"provider_license_document_ref": "doc-1"}}, "evidence_missing"),
    ]
    for body, code in attempts:
        response = _post(harness, case.id, "resume", body)
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == code
    detail = _post(harness, case.id, "resume", attempts[2][0]).json()["detail"]
    assert detail["field_paths"] == ["evidence.license_terms_version",
                                     "evidence.commercial_use_confirmation_ref"]
    assert harness.case(case.id).status.value == "OPEN"
    assert harness.signaller.sent == []  # a refusal never signals the run
    assert [code for _, code in harness.history(case.id)[1:]] == [
        "reviewer_role_mismatch", "reviewer_not_authorized", "evidence_missing",
        "evidence_missing"]


def test_the_authorized_reviewer_with_evidence_resolves_and_the_run_is_signalled(
        harness: Harness) -> None:
    case = _pipeline_hold(harness, "UNKNOWN_COMMERCIAL_LICENSE")
    response = _post(harness, case.id, "resume",
                     {"actor": "rita", "role": "rights_reviewer", "evidence": _evidence(case),
                      "notes": "license verified"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["case"]["status"] == "RESOLVED"
    assert body["case"]["resolution"] == "RESUMED_BY_REVIEWER"
    assert body["signal"] == {"status": "sent", "signal": "resume", "workflow_id": WORKFLOW_ID,
                              "run_id": RUN_ID, "reason": None, "open_case_ids": []}
    (sent,) = harness.signaller.sent
    assert sent.payload == ResumeSignal(actor="rita", role="rights_reviewer",
                                        evidence=_evidence(case))
    assert harness.history(case.id)[-1] == ("RESOLVED", "reviewer_authorized")
    # The open-holds view no longer lists it; a second resume is refused as closed.
    view = harness.client.get("/api/views/exceptions").json()
    assert view["panels"]["open"]["data"]["items"] == []


def test_a_hold_not_raised_by_a_pipeline_run_needs_no_signal(harness: Harness) -> None:
    case = raise_hold(harness.factory, "SEVERE_CLIPPING_DETECTED", NOW)
    assert pipeline_link(case) is None
    response = _post(harness, case.id, "resume",
                     {"actor": "admin", "role": "quality_reviewer", "evidence": _evidence(case)})
    assert response.json()["detail"]["code"] == "reviewer_not_authorized"
    granted = _harness(reviewers=ReviewerConfig(
        grants={"qa": frozenset({"quality_reviewer"})}, source="test"))
    case = raise_hold(granted.factory, "SEVERE_CLIPPING_DETECTED", NOW)
    response = _post(granted, case.id, "resume",
                     {"actor": "qa", "role": "quality_reviewer", "evidence": _evidence(case)})
    assert response.status_code == 200
    assert response.json()["signal"]["status"] == "not_applicable"
    assert granted.signaller.sent == []


def test_a_cleared_hold_waits_for_the_other_holds_of_its_episode(harness: Harness) -> None:
    first = _pipeline_hold(harness, "UNKNOWN_COMMERCIAL_LICENSE", record=False)
    second = _pipeline_hold(harness, "INCOMPLETE_R6_PROVENANCE", record=False)
    record_run(harness.factory, WORKFLOW_ID, NOW, hold_case_ids=[first.id, second.id])
    body = _post(harness, first.id, "resume", {"actor": "rita", "role": "rights_reviewer",
                                               "evidence": _evidence(first)}).json()
    assert body["signal"]["status"] == "deferred"
    assert body["signal"]["open_case_ids"] == [second.id]
    assert harness.signaller.sent == []
    body = _post(harness, second.id, "resume", {"actor": "rita", "role": "rights_reviewer",
                                                "evidence": _evidence(second)}).json()
    assert body["signal"]["status"] == "sent"
    assert len(harness.signaller.sent) == 1


# --------------------------------------------------------------------------- reject


def test_reject_needs_the_case_role_and_signals_the_run_to_end(harness: Harness) -> None:
    case = _pipeline_hold(harness, "UNKNOWN_COMMERCIAL_LICENSE")
    outsider = _post(harness, case.id, "reject", {"actor": "admin", "role": "rights_reviewer"})
    assert outsider.status_code == 409
    assert outsider.json()["detail"]["code"] == "reviewer_not_authorized"
    response = _post(harness, case.id, "reject", {"actor": "rita", "role": "rights_reviewer",
                                                  "notes": "discard"})
    assert response.status_code == 200
    assert response.json()["case"]["status"] == "REJECTED"
    (sent,) = harness.signaller.sent
    assert sent.signal == "resume"  # the run re-checks its holds and ends on the rejection
    again = _post(harness, case.id, "reject", {"actor": "rita", "role": "rights_reviewer"})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "case_closed"


# ------------------------------------------------------------------- pending signals


def test_an_unreachable_temporal_leaves_the_decision_recorded_and_the_signal_pending() -> None:
    signaller = FakeSignaller(fail=TimeoutError("temporal at 127.0.0.1:7233 did not answer"))
    h = _harness(signaller=signaller)
    case = _pipeline_hold(h, "UNKNOWN_COMMERCIAL_LICENSE")
    response = _post(h, case.id, "resume", {"actor": "rita", "role": "rights_reviewer",
                                            "evidence": _evidence(case)})
    assert response.status_code == 200
    body = response.json()
    assert body["case"]["status"] == "RESOLVED"
    assert body["signal"]["status"] == "pending"
    assert "did not answer" in body["signal"]["reason"]
    run = h.client.get("/api/views/production-queue").json()["panels"]["runs"]["data"]
    assert run["active"][0]["awaiting_signal"] is True

    signaller.fail = None
    resent = h.client.post(f"/api/pipeline/runs/{WORKFLOW_ID}/signal", json={"actor": "rita"},
                           headers=AUTH)
    assert resent.status_code == 200, resent.text
    assert resent.json()["signal"]["status"] == "sent"
    (sent,) = signaller.sent
    assert (sent.workflow_id, sent.run_id, sent.signal) == (WORKFLOW_ID, RUN_ID, "resume")


def test_resend_is_refused_while_a_hold_is_open_or_for_an_unknown_actor(
        harness: Harness) -> None:
    _pipeline_hold(harness, "UNKNOWN_COMMERCIAL_LICENSE")
    url = f"/api/pipeline/runs/{WORKFLOW_ID}/signal"
    assert harness.client.post(url, json={"actor": "rita"}, headers=AUTH).json()[
        "detail"]["code"] == "holds_still_open"
    outsider = harness.client.post(url, json={"actor": "mallory"}, headers=AUTH)
    assert outsider.status_code == 403
    assert harness.signaller.sent == []


def test_no_temporal_configured_reports_the_signal_pending() -> None:
    engine = create_dashboard_engine(schemas=("ops",))
    factory = sessionmaker(bind=engine)
    actions = ExceptionActions(factory, reviewers=REVIEWERS, signaller=None,
                               clock=FixedClock(NOW))
    live = LiveDashboard(ReadOnlyDatabase(factory), PhaseApis({}, token=None),
                         reviewers=REVIEWERS)
    client = TestClient(create_live_app(live, actions, platform=load_settings(env=PLATFORM_ENV)))
    case = raise_hold(factory, "UNKNOWN_COMMERCIAL_LICENSE", NOW,
                      subject_ref=f"{WORKFLOW_ID}:{RUN_ID}:qa:1",
                      raised_by=f"pipeline:{WORKFLOW_ID}")
    body = client.post(f"/api/exceptions/{case.id}/resume", headers=AUTH, json={
        "actor": "rita", "role": "rights_reviewer", "evidence": _evidence(case)}).json()
    assert body["signal"]["status"] == "pending"
    assert body["signal"]["reason"] == "temporal not configured (TEMPORAL_HOST)"


# ------------------------------------------------------------------ auth and directory


@pytest.mark.parametrize("action", ["resume", "reject", "external-resolution"])
def test_mutating_routes_need_the_service_token(harness: Harness, action: str) -> None:
    case = raise_hold(harness.factory, "UNKNOWN_COMMERCIAL_LICENSE", NOW)
    url = f"/api/exceptions/{case.id}/{action}"
    body = {"actor": "rita", "role": "rights_reviewer", "evidence": _evidence(case)}
    assert harness.client.post(url, json=body).status_code == 401
    wrong = harness.client.post(url, json=body, headers={SERVICE_TOKEN_HEADER: "nope"})
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "invalid_service_token"
    closed = _harness(platform=False)
    case = raise_hold(closed.factory, "UNKNOWN_COMMERCIAL_LICENSE", NOW)
    response = closed.client.post(f"/api/exceptions/{case.id}/{action}", json=body, headers=AUTH)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "blocked_pending_credentials"
    assert closed.history(case.id) == [("RAISED", None)]
    resend = harness.client.post(f"/api/pipeline/runs/{WORKFLOW_ID}/signal",
                                 json={"actor": "rita"})
    assert resend.status_code == 401


def test_without_a_reviewer_directory_every_action_is_refused() -> None:
    h = _harness(reviewers=None)
    case = raise_hold(h.factory, "UNKNOWN_COMMERCIAL_LICENSE", NOW)
    for action, body in (
        ("resume", {"actor": "rita", "role": "rights_reviewer", "evidence": _evidence(case)}),
        ("reject", {"actor": "rita", "role": "rights_reviewer"}),
        ("external-resolution", {"actor": "rita", "role": "rights_reviewer"}),
    ):
        response = _post(h, case.id, action, body)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "reviewer_directory_not_configured"
    assert h.case(case.id).status.value == "OPEN"
    assert h.history(case.id) == [("RAISED", None)]
    reviewers = h.client.get("/api/reviewers").json()
    assert reviewers == {"data_source": "live", "configured": False, "reviewers": []}


def test_an_unknown_case_is_404(harness: Harness) -> None:
    response = _post(harness, "exc_missing", "resume", {"actor": "rita",
                                                        "role": "rights_reviewer"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_case"
    assert harness.client.get("/api/exceptions/exc_missing").status_code == 404


def test_case_detail_carries_the_decision_history(harness: Harness) -> None:
    case = raise_hold(harness.factory, "UNKNOWN_COMMERCIAL_LICENSE", NOW)
    _post(harness, case.id, "resume", {"actor": "rita", "role": "rights_reviewer"})
    detail = harness.client.get(f"/api/exceptions/{case.id}").json()
    assert detail["data_source"] == "live"
    assert [(e["event_type"], e["decision_code"]) for e in detail["history"]] == [
        ("RAISED", None), ("RESUME_REFUSED", "evidence_missing")]
    assert detail["history"][1]["actor"] == "rita"
