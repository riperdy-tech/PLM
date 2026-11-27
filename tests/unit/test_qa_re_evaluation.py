"""A rejection says why; an unmeasured file and a RE_EVALUATE case are evaluated again;
quarantine cases whose holds were closed elsewhere are swept (review 2026-11-26 #47, #48).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from plm_platform.config import SERVICE_TOKEN_HEADER
from plm_platform.exceptions import ExceptionQueue, ReviewDecision
from quality_gate.audio.decode import DECODE_FAILED, DecodeFailure
from quality_gate.domain.enums import ResolutionCode
from quality_gate.gate.quarantine import quarantine_holds, sweep_orphan_quarantine_cases
from quality_gate.storage.repositories import get_quarantine_case

from tests.support.qa_db import qa_session
from tests.support.seams import lineage_fields, rights_fields
from tests.unit.test_qa_api import (  # noqa: F401 - pytest fixtures
    _TEST_TOKEN,
    _make_temp_wav,
    _stored_event,
    qa_client,
    qa_engine,
    store,
)

HEADERS = {SERVICE_TOKEN_HEADER: _TEST_TOKEN}
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


def _body(store, wav: Path, name: str, **fields: object) -> dict:  # type: ignore[no-untyped-def]  # noqa: F811
    return {"candidate_event": _stored_event(
        store, wav, candidate_id=f"cand_{name}", artifact_id=f"art_{name}",
        track_spec_id=f"spec_{name}",
        lineage=lineage_fields(experiment_id=f"exp_{name}", content_arm_id=f"arm_{name}"),
        prompt_text="relaxing lo-fi chill beat", **fields)}


@pytest.fixture
def wav():  # type: ignore[no-untyped-def]
    path = _make_temp_wav(duration=1.5)
    yield path
    if path.is_file():
        path.unlink()


def test_an_unmeasured_file_is_rejected_with_its_code_then_evaluated_again(
    qa_client, store, wav, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from quality_gate.evaluators import musical

    body = _body(store, wav, "flaky")
    monkeypatch.setattr(musical, "decode_audio", lambda *a, **k: DecodeFailure(
        DECODE_FAILED, "disk hiccup", "wav", "test"))
    first = qa_client.post("/qa/evaluations", json=body, headers=HEADERS).json()
    assert first["disposition"] == "REJECTED"
    assert first["blocking_finding_codes"] == ["MUSICAL_ANALYSIS_UNAVAILABLE"]
    monkeypatch.undo()
    second = qa_client.post("/qa/evaluations", json=body, headers=HEADERS)
    assert second.status_code == 201
    assert second.json()["evaluation_id"] != first["evaluation_id"]
    assert second.json()["disposition"] == "PASSED"
    assert second.json()["blocking_finding_codes"] == []
    third = qa_client.post("/qa/evaluations", json=body, headers=HEADERS).json()
    assert third["evaluation_id"] == second.json()["evaluation_id"]  # replayed
    stored = qa_client.get(f"/qa/evaluations/{first['evaluation_id']}", headers=HEADERS)
    assert stored.json()["blocking_finding_codes"] == ["MUSICAL_ANALYSIS_UNAVAILABLE"]


def test_a_measured_rejection_is_replayed_not_evaluated_again(
    qa_client, store, wav,  # noqa: F811
) -> None:
    body = _body(store, wav, "free_tier", rights=rights_fields(plan_or_tier="free"))
    first = qa_client.post("/qa/evaluations", json=body, headers=HEADERS).json()
    assert first["disposition"] == "REJECTED"
    assert "COMMERCIAL_USE_INELIGIBLE_TIER" in first["blocking_finding_codes"]
    again = qa_client.post("/qa/evaluations", json=body, headers=HEADERS).json()
    assert again["evaluation_id"] == first["evaluation_id"]


def test_re_evaluate_runs_a_new_evaluation(qa_client, store, wav) -> None:  # noqa: F811
    body = _body(store, wav, "re_eval", rights=rights_fields(commercial_use_status="unknown"))
    first = qa_client.post("/qa/evaluations", json=body, headers=HEADERS).json()
    assert first["disposition"] == "QUARANTINED"
    resolved = qa_client.post(f"/qa/quarantine/{first['quarantine_case_id']}/resolve", json={
        "resolution": "RE_EVALUATE", "auditor_id": "auditor_bob", "notes": "terms attached",
        "reviewer_role": "rights_reviewer", "evidence": {}}, headers=HEADERS)
    assert resolved.status_code == 200, resolved.text
    second = qa_client.post("/qa/evaluations", json=body, headers=HEADERS)
    assert second.status_code == 201
    assert second.json()["evaluation_id"] != first["evaluation_id"]
    assert second.json()["quarantine_case_id"] != first["quarantine_case_id"]


def test_cases_whose_holds_were_closed_elsewhere_are_swept(
    qa_client, qa_engine, store, wav,  # noqa: F811
) -> None:
    rejected = qa_client.post("/qa/evaluations", headers=HEADERS, json=_body(
        store, wav, "orphan_a", rights=rights_fields(commercial_use_status="unknown"))).json()
    withdrawn = qa_client.post("/qa/evaluations", headers=HEADERS, json=_body(
        store, wav, "orphan_b", rights=rights_fields(commercial_use_status="unknown"))).json()
    still_open = qa_client.post("/qa/evaluations", headers=HEADERS, json=_body(
        store, wav, "orphan_c", rights=rights_fields(commercial_use_status="unknown"))).json()
    queue = ExceptionQueue()
    with qa_session(qa_engine) as session:
        for result, decision in ((rejected, ReviewDecision.REJECT),
                                 (withdrawn, ReviewDecision.WITHDRAW)):
            for hold in quarantine_holds(session, queue, result["quarantine_case_id"]):
                queue.resolve(session, hold.id, decision=decision, actor="pipeline",
                              role=None, evidence=None, notes="run cancelled", now=NOW)
        session.commit()
    swept = qa_client.post("/qa/quarantine/sweep", headers=HEADERS,
                           json={"auditor_id": "quality_gate:sweep"})
    assert swept.status_code == 200, swept.text
    assert sorted(swept.json()["closed"]) == sorted(
        [rejected["quarantine_case_id"], withdrawn["quarantine_case_id"]])
    with qa_session(qa_engine) as session:
        assert get_quarantine_case(session, rejected["quarantine_case_id"]).resolution is \
            ResolutionCode.CONFIRMED_REJECTED
        assert get_quarantine_case(session, withdrawn["quarantine_case_id"]).resolution is \
            ResolutionCode.RE_EVALUATE
        assert get_quarantine_case(session, still_open["quarantine_case_id"]).resolution is None
        assert sweep_orphan_quarantine_cases(session, queue, auditor_id="x", now=NOW) == []
