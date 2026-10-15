"""P9's R5 surface: release links over HTTP, measurement from stored facts, dispatch.

In-process TestClient over in-memory SQLite. Proves: ``POST /analytics/evaluation/measure``
takes no number from the caller; it attributes through stored identifiers (the
publication event's lineage, an ingested C7 leg-1 link per ISRC), reads the window's
snapshot, the ISRCs' statement lines and the unit's costs, converts through recorded
FX rates, derives maturity, and enqueues the reward; every missing fact is a named
refusal with nothing recorded. ``POST /analytics/rewards/dispatch`` runs one outbox pass
and reports each fact's status; mock-sourced snapshots are stored as ``synthetic``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from analytics.api.app import create_app
from analytics.domain.enums import MetricSource
from analytics.storage.repositories import get_video_metrics
from fastapi.testclient import TestClient
from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c6 import YouTubePublicationPayload
from plm_contracts.seams.c7 import DistributionRecordPayload
from plm_platform.clock import FixedClock
from plm_platform.config import load_settings
from sqlalchemy.orm import sessionmaker

from tests.support.analytics_db import create_analytics_engine
from tests.support.decisions import log_allocation_decision
from tests.support.seams import lineage_fields, publication_fields
from tests.unit.test_analytics_api import HEADERS, MINIMAL_ENV

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
PUBLISHED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
OBSERVED = PUBLISHED + timedelta(days=28)
ISRCS = ["USPLM2600001", "USPLM2600002"]
VIDEO = "yt_test_01"


class _P2:
    def __init__(self) -> None:
        self.posted: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ar/results"
        self.posted.append(json.loads(request.content))
        return httpx.Response(200, json={"observation_id": "obs-1",
                                         "experiment_result_id": "res-1", "stored": True})


def _client(p2: _P2 | None = None) -> tuple[TestClient, Any]:
    engine = create_analytics_engine(static_pool=True)
    log_allocation_decision(engine, directive_id=lineage_fields()["production_directive_id"],
                            arm_id="arm_test_01", decided_at=PUBLISHED - timedelta(days=1))
    factory = sessionmaker(bind=engine)
    reward_client = (None if p2 is None else httpx.Client(
        base_url="http://p2.test", transport=httpx.MockTransport(p2.handler)))
    app = create_app(session_factory=lambda: factory(), settings=load_settings(env=MINIMAL_ENV),
                     clock=FixedClock(NOW), reward_client=reward_client)
    return TestClient(app), factory


def _record(lineage: dict[str, str] | None = None) -> dict[str, Any]:
    return DistributionRecordPayload.build(
        release_id="rel-1", artist_id="artist-1", release_version=1, isrcs=tuple(ISRCS),
        provider="mock_distributor", deliveries=(), source_package_hash="f" * 64,
        status="submitted", lineage=ExperimentLineage(**(lineage or lineage_fields())),
        media_package_id="mpkg_test_01").to_wire()


def _seed(client: TestClient, *, link: bool = True, snapshot: bool = True,
          statement: bool = True, cost: bool = True) -> None:
    def post(path: str, body: dict[str, Any]) -> None:
        res = client.post(path, json=body, headers=HEADERS)
        assert res.status_code == 201, res.text

    post("/analytics/fx-rates", {"rates": [{"base": "USD", "quote": "KRW", "rate": "1380",
                                            "source": "dev", "as_of": "2026-01-01T00:00:00Z"}]})
    if link:
        post("/analytics/ingest/releases", {"record": _record()})
    if snapshot:
        post("/analytics/ingest/youtube", {
            "video_id": VIDEO, "channel_id": "UC_test", "date_str": OBSERVED.date().isoformat(),
            "window": "28d", "observed_at": OBSERVED.isoformat(), "views": 3360,
            "watch_time_minutes": 21840.0, "average_percentage_viewed": 18.0,
            "subscribers_gained": 13, "estimated_revenue": "4.032", "revenue_currency": "USD",
            "source": "synthetic"})
    if statement:
        post("/analytics/ingest/distributor", {
            "provider": "mock_distributor", "period": f"{PUBLISHED.isoformat()}/"
                                                      f"{OBSERVED.isoformat()}",
            "currency": "USD", "raw_content": "isrc,streams,revenue\n...",
            "source_reference": "mock", "received_at": OBSERVED.isoformat(),
            "rows": [{"isrc": ISRCS[0], "streams": 1120, "revenue": "3.92"},
                     {"isrc": ISRCS[1], "streams": 1000, "revenue": "3.50"}]})
    if cost:
        post("/analytics/ingest/cost", {
            "entity_id": VIDEO, "entity_type": "video", "category": "third_party_api",
            "amount": "0", "currency": "USD", "period": "pipeline-run:wf:USD",
            "description": "simulated writes", "incurred_at": OBSERVED.isoformat()})


def _event(**overrides: Any) -> dict[str, Any]:
    return YouTubePublicationPayload.build(**publication_fields(
        published_at=PUBLISHED.isoformat(), **overrides)).to_wire()


def _measure(client: TestClient, **overrides: Any) -> httpx.Response:
    body = {"publication": _event(),
            "isrcs": ISRCS, "window": "28d", "observed_at": OBSERVED.isoformat(),
            "quality_status": None, **overrides}
    return client.post("/analytics/evaluation/measure", json=body, headers=HEADERS)


def test_measure_computes_the_outcome_from_stored_facts_and_enqueues_it() -> None:
    client, factory = _client()
    _seed(client)
    res = _measure(client)
    assert res.status_code == 200, res.text
    reward = res.json()
    assert (reward["content_id"], reward["arm_id"]) == (VIDEO, "arm_test_01")
    assert (reward["observation_window"], reward["status"]) == ("28d", "mature")
    assert reward["attribution_confidence"] == 1.0
    assert reward["reward_version"] == "engagement-contribution-proxy-v1"
    assert reward["raw_metrics"]["views"] == 3360
    assert 0.5 < reward["normalized_reward"] < 1.0  # positive net contribution
    with factory() as session:
        (snapshot,) = get_video_metrics(session, VIDEO)
    assert snapshot.source is MetricSource.SYNTHETIC

    # Measuring the same facts again replays the identical reward.
    assert _measure(client).json() == reward


@pytest.mark.parametrize(("missing", "status", "code"), [
    ("link", 409, "attribution_unlinked"),
    ("snapshot", 409, "measurement_missing"),
    ("statement", 409, "realized_revenue_not_reported"),
    ("cost", 409, "cost_not_recorded"),
])
def test_a_missing_fact_is_refused_by_name(missing: str, status: int, code: str) -> None:
    client, _ = _client()
    _seed(client, **{missing: False})
    res = _measure(client)
    assert res.status_code == status, res.text
    assert res.json()["detail"]["code"] == code


def test_attribution_and_timing_are_checked() -> None:
    client, _ = _client()
    _seed(client)
    other = _measure(client, publication=_event(
        lineage={**lineage_fields(), "content_arm_id": "arm_other"}))
    assert other.json()["detail"]["code"] == "attribution_mismatch"
    early = _measure(client, observed_at=(PUBLISHED - timedelta(hours=1)).isoformat())
    assert early.status_code == 422
    assert early.json()["detail"]["code"] == "observation_precedes_publication"
    broken = _measure(client, publication={"event": "youtube_video_published"})
    assert broken.status_code == 422
    assert _measure(client, isrcs=[]).status_code == 422
    conflicting = client.post("/analytics/ingest/releases", headers=HEADERS, json={
        "record": _record({**lineage_fields(), "content_arm_id": "arm_other"})})
    assert conflicting.status_code == 409


def test_dispatch_runs_the_outbox_and_reports_each_fact() -> None:
    p2 = _P2()
    client, _ = _client(p2)
    _seed(client)
    reward = _measure(client).json()
    fact = {k: reward[k] for k in ("experiment_id", "content_id", "observation_window",
                                   "status")}
    res = client.post("/analytics/rewards/dispatch", json={"facts": [fact]}, headers=HEADERS)
    assert res.status_code == 200, res.text
    (reported,) = res.json()["facts"]
    assert reported["delivery_status"] == "delivered"
    assert res.json()["report"]["delivered"] == 1
    assert [p["content_id"] for p in p2.posted] == [VIDEO]
    unknown = client.post("/analytics/rewards/dispatch", headers=HEADERS, json={
        "facts": [{**fact, "content_id": "never"}]}).json()
    assert unknown["facts"][0]["delivery_status"] is None
