"""P8's R5 surface: the submission's C7 record and guard receipt, and mock statements.

In-process TestClient over in-memory SQLite. Proves: a submission answers with its
ISRCs, the Seam C7 leg-1 record and the receipt of its write (a duplicate reports what
must have been decided); the mock statement source gives one line per ISRC for the
period, deterministic and driven by the release's stored arm, labelled ``mock``, and in
the shape P9's statement ingest takes; an unknown release, an inverted period and a
deployment with no source are refused.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from distribution.api.app import create_app
from distribution.client.mock import MockDistributorClient
from distribution.engine import DistributionEngine
from distribution.statements import MockDistributorStatements
from fastapi.testclient import TestClient
from plm_contracts.seams import parse_seam
from plm_contracts.seams.c7 import DistributionRecordPayload
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.testing.mock_outcomes import load_mock_outcome_model

from tests.support.clearance import p5_holding
from tests.support.distribution_db import create_distribution_engine
from tests.support.seams import media_package, release_manifest
from tests.unit.test_distribution_api import MINIMAL_ENV

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
HEADERS = {SERVICE_TOKEN_HEADER: "test-dist-token"}
MODEL = load_mock_outcome_model(Path(__file__).resolve().parents[2] / "config" / "dev"
                                / "mock_outcomes.json")


def _client(*, statements: bool = True) -> TestClient:
    engine = create_distribution_engine(static_pool=True)

    def session_factory():
        from sqlalchemy.orm import sessionmaker

        return sessionmaker(bind=engine)()

    clock = FixedClock(NOW)
    app = create_app(
        session_factory=session_factory, settings=load_settings(env=MINIMAL_ENV),
        engine=DistributionEngine(MockDistributorClient(clock=clock)), clock=clock,
        clearance=p5_holding(release_manifest(candidate_id="cand_a"),
                             release_manifest(candidate_id="cand_b")),
        statements=MockDistributorStatements(MODEL) if statements else None,
        statements_from_env=False,
    )
    return TestClient(app)


def _submit(client: TestClient) -> Any:
    return client.post("/distribution/releases", headers=HEADERS, json={
        "track_manifests": [release_manifest(candidate_id=c).to_wire()
                            for c in ("cand_a", "cand_b")],
        "media_package": media_package("cand_a", "cand_b").to_wire(),
        "publication": None, "artist_name": "A", "release_date": "2026-10-01",
        "label_name": "L", "copyright_line": "C", "publisher_line": "P",
        "territories": "WORLDWIDE"})


def test_a_submission_answers_with_its_isrcs_record_and_receipt() -> None:
    client = _client()
    res = _submit(client)
    assert res.status_code == 201, res.text
    body = res.json()
    record = parse_seam(DistributionRecordPayload, body["distribution_record"])
    assert list(record.isrcs) == body["isrcs"] and len(body["isrcs"]) == 2
    assert record.lineage.content_arm_id == "arm_test_01"
    (receipt,) = body["external_writes"]
    assert (receipt["capability"], receipt["decision"]) == ("distribution.submit", "simulate")
    assert receipt["idempotency_key"] == f"{body['submission_fingerprint']}:submit"

    again = _submit(client).json()
    assert again["release_id"] == body["release_id"] and again["isrcs"] == body["isrcs"]
    (replayed,) = again["external_writes"]
    assert (replayed["decision"], replayed["reason"]) == ("simulate", "performed_earlier")
    assert replayed["idempotency_key"] == receipt["idempotency_key"]


def test_the_mock_statements_are_monthly_deterministic_per_arm_and_ingestible() -> None:
    client = _client()
    release = _submit(client).json()
    as_of = NOW + timedelta(days=45)  # 2026-10-01 .. 2026-11-15
    path = f"/distribution/releases/{release['release_id']}/statements"
    first = client.get(path, headers=HEADERS, params={"as_of": as_of.isoformat()})
    assert first.status_code == 200, first.text
    data = first.json()
    assert data == client.get(path, headers=HEADERS, params={"as_of": as_of.isoformat()}).json()
    assert (data["provider"], data["provider_mode"]) == ("mock_distributor", "mock")
    assert datetime.fromisoformat(data["accrual_start"]) == NOW  # the submission
    assert [s["period"] for s in data["statements"]] == ["2026-10", "2026-11"]
    october, november = data["statements"]
    assert datetime.fromisoformat(october["period_end"]) == datetime(2026, 11, 1, tzinfo=UTC)
    assert datetime.fromisoformat(november["period_end"]) == as_of
    assert [row["isrc"] for row in october["rows"]] == release["isrcs"]
    expected = MODEL.statement_lines(isrcs=release["isrcs"], arm_id="arm_test_01",
                                     period_start=NOW,
                                     period_end=datetime(2026, 11, 1, tzinfo=UTC),
                                     period="2026-10")
    assert [Decimal(r["revenue"]) for r in october["rows"]] == [x.revenue.amount
                                                                for x in expected]
    assert october["raw_content"].splitlines()[0] == "isrc,release_id,streams,revenue"
    assert len(october["raw_content"].splitlines()) == 3


def test_statement_refusals() -> None:
    client = _client()
    release = _submit(client).json()
    path = f"/distribution/releases/{release['release_id']}/statements"
    early = client.get(path, headers=HEADERS,
                       params={"as_of": (NOW - timedelta(days=1)).isoformat()})
    assert early.status_code == 422
    unknown = client.get("/distribution/releases/nope/statements", headers=HEADERS,
                         params={"as_of": NOW.isoformat()})
    assert unknown.status_code == 404
    none = _client(statements=False).get(path, headers=HEADERS,
                                         params={"as_of": NOW.isoformat()})
    assert none.status_code == 409
    assert none.json()["detail"]["code"] == "statement_source_not_configured"
