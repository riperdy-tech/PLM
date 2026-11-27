"""A YouTube-only video reaches a reward, end to end in process (review 2026-11-26 #35).

The pipeline's ``evaluate_outcome`` step with distribution disabled, against the real P7
and P9 applications (TestClients over in-memory SQLite): P7's real Analytics source (a
fake Analytics reader and a fake YouTube that shows the video public), P9 configured
with the channel as YouTube-only and the operator's dated non-monetized attestation.
Nothing calls Google. Without the attestation the measurement is refused by name.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from analytics.api.app import create_app as create_p9
from analytics.config import AnalyticsSettings, NonMonetizedAttestation
from analytics.ingestion.youtube_client import YouTubeAnalyticsError
from fastapi.testclient import TestClient
from plm_contracts.seams.c6 import YouTubePublicationPayload
from plm_platform.clock import FixedClock
from plm_platform.config import load_settings
from plm_platform.pipeline.config import parse_pipeline_config
from plm_platform.pipeline.http import HttpPhaseSteps, PhaseClient, PhaseRequestRefused
from plm_platform.pipeline.states import Step
from plm_platform.pipeline.types import StepRequest
from plm_platform.temporal import Phase
from sqlalchemy.orm import sessionmaker
from youtube_publishing.analytics import RealYouTubeAnalytics
from youtube_publishing.api.app import create_app as create_p7
from youtube_publishing.domain.enums import PrivacyStatus
from youtube_publishing.domain.models import YouTubePublicationRecord
from youtube_publishing.storage.repositories import insert_publication_record

from tests.support.analytics_db import create_analytics_engine
from tests.support.clearance import p5_holding
from tests.support.decisions import log_allocation_decision
from tests.support.seams import lineage_fields, publication_fields, release_manifest
from tests.support.youtube_db import create_youtube_engine
from tests.unit.test_youtube_api import MINIMAL_ENV
from tests.unit.test_youtube_real_analytics import _Analytics, _public, _YouTube

TOKEN = MINIMAL_ENV["PLM_SERVICE_TOKEN"]
CHANNEL = "UC_test"
VIDEO = "yt_test_01"
PUBLISHED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
NOW = PUBLISHED + timedelta(days=32)  # 28d window + Pacific day rounding + 3 days


def _p7() -> TestClient:
    factory = sessionmaker(bind=create_youtube_engine(static_pool=True))
    lineage = lineage_fields()
    with factory() as session:
        insert_publication_record(session, YouTubePublicationRecord(
            publication_record_id=f"pub_{VIDEO}", youtube_video_id=VIDEO, channel_id=CHANNEL,
            publish_attempt_id="att_test_01", media_package_id="mpkg_test_01",
            release_manifest_id="rm_1", title="t", privacy_status=PrivacyStatus.PUBLIC,
            contains_synthetic_media=True, published_at=PUBLISHED, video_url="u",
            thumbnail_url="t", created_at=PUBLISHED, updated_at=PUBLISHED, **lineage))
        session.commit()
    youtube = _YouTube(_public(PUBLISHED))
    youtube.resource["id"] = VIDEO  # type: ignore[index]
    return TestClient(create_p7(
        session_factory=lambda: factory(), settings=load_settings(env=MINIMAL_ENV),
        client=youtube, clock=FixedClock(NOW),
        clearance=p5_holding(release_manifest(candidate_id="cand_r5")),
        analytics=RealYouTubeAnalytics(_Analytics(), unavailable=YouTubeAnalyticsError),
        analytics_from_env=False))


def _p9(attested: bool) -> TestClient:
    engine = create_analytics_engine(static_pool=True)
    log_allocation_decision(engine, directive_id=lineage_fields()["production_directive_id"],
                            arm_id="arm_test_01", decided_at=PUBLISHED - timedelta(days=1))
    factory = sessionmaker(bind=engine)
    attestation = NonMonetizedAttestation(
        channel_id=CHANNEL, attested_by="owner", attested_at=PUBLISHED - timedelta(days=1),
        valid_until=NOW + timedelta(days=30), reference="not in the YouTube Partner Program")
    settings = AnalyticsSettings(youtube_only_channels=frozenset({CHANNEL}),
                                 non_monetized_attestations=(attestation,) if attested else ())
    return TestClient(create_p9(session_factory=lambda: factory(),
                                settings=load_settings(env=MINIMAL_ENV), clock=FixedClock(NOW),
                                analytics_settings=settings))


def _steps(p7: TestClient, p9: TestClient) -> HttpPhaseSteps:
    def unused(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected call {request.method} {request.url}")

    clients: dict[Phase, Any] = {phase: httpx.Client(
        base_url="http://unused.test", transport=httpx.MockTransport(unused)) for phase in Phase}
    clients[Phase.YOUTUBE], clients[Phase.ANALYTICS] = p7, p9
    config = parse_pipeline_config({
        "distribution": {"enabled": False},
        "fx_rates": [{"base": "USD", "quote": "KRW", "rate": "1380.00", "source": "test",
                      "as_of": "2026-01-01T00:00:00+00:00"}]})
    return HttpPhaseSteps({phase: PhaseClient(phase, client, token=TOKEN)
                           for phase, client in clients.items()}, config=config)


def _request() -> StepRequest:
    publication = {"media_package_id": "mpkg_test_01", "attempt_id": "att_test_01",
                   "youtube_video_id": VIDEO, "channel_id": CHANNEL,
                   "published_at": PUBLISHED.isoformat(),
                   "publication_event": YouTubePublicationPayload.build(**publication_fields(
                       published_at=PUBLISHED.isoformat())).to_wire()}
    cost = {"capability": "youtube.upload", "idempotency_key": "k:youtube.upload",
            "decision": "execute", "status": "committed",
            "actual": {"amount": "0", "currency": "USD"}, "basis": "rate_card",
            "settled_at": PUBLISHED.isoformat(), "context": {"media_package_id": "mpkg_test_01"}}
    return StepRequest(
        workflow_id="pipeline.dir-1", directive_id="dir_test_01", brand_id="brand",
        step=Step.EVALUATE_OUTCOME.value, idempotency_key="pipeline.dir-1:evaluate_outcome",
        requested_at=NOW, inputs={
            "outcome_horizon": "28d", "upload": {"publications": [publication]},
            "submit_distribution": {"releases": [], "external_writes": [],
                                    "skipped": "distribution_disabled"},
            "costs": [cost]})


def test_a_youtube_only_video_with_distribution_disabled_produces_a_reward() -> None:
    result = asyncio.run(_steps(_p7(), _p9(attested=True)).evaluate_outcome(_request()))
    (reward,) = result.outputs["rewards"]
    assert (reward["content_id"], reward["arm_id"]) == (VIDEO, "arm_test_01")
    assert reward["status"] == "mature" and reward["normalized_reward"] is not None
    assert reward["measurement_source"] == "youtube_analytics"


def test_without_the_attestation_the_measurement_is_refused_by_name() -> None:
    with pytest.raises(PhaseRequestRefused) as refused:
        asyncio.run(_steps(_p7(), _p9(attested=False)).evaluate_outcome(_request()))
    assert refused.value.code == "non_monetized_attestation_missing"
