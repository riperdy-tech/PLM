"""P7's R5 surface: guard receipts, the publication on the attempt, the analytics source.

In-process TestClient over in-memory SQLite. Proves: a publish reports one receipt per
write (the guard's decision, or for a replay what must have been decided); a guard
``deny`` is a 409 in the shape the pipeline maps to a hold, with nothing sent; the
attempt status carries the publication event and ``published_at``; the mock analytics
source answers deterministically per arm and labelled ``mock``, refuses an unknown video,
an observation before publication, and a deployment with no source.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from plm_contracts import ExecutionPolicy, ProviderMode
from plm_contracts.seams import parse_seam
from plm_contracts.seams.c6 import YouTubePublicationPayload
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.execution import ExecutionSettings
from plm_platform.testing.mock_outcomes import load_mock_outcome_model
from youtube_publishing.analytics import MockYouTubeAnalytics
from youtube_publishing.api.app import create_app
from youtube_publishing.client.mock import MockYouTubeClient
from youtube_publishing.domain.models import ChannelProfile
from youtube_publishing.engine import YouTubePublishingEngine
from youtube_publishing.storage.repositories import insert_channel_profile

from tests.support.clearance import p5_holding
from tests.support.seams import media_package, release_manifest
from tests.support.youtube_db import create_youtube_engine
from tests.unit.test_youtube_api import MINIMAL_ENV

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
HEADERS = {SERVICE_TOKEN_HEADER: "test-secret-token"}
MODEL = load_mock_outcome_model(Path(__file__).resolve().parents[2] / "config" / "dev"
                                / "mock_outcomes.json")


class RealModeClient(MockYouTubeClient):
    """Stands in for a real client: the guard must decide (no SIMULATE)."""

    provider_mode = ProviderMode.REAL


def _app(*, client: Any = None, execution: ExecutionSettings | None = None,
         analytics: bool = True) -> TestClient:
    engine = create_youtube_engine(static_pool=True)

    def session_factory():
        from sqlalchemy.orm import sessionmaker

        return sessionmaker(bind=engine)()

    with session_factory() as session:
        insert_channel_profile(session, ChannelProfile(
            id="prof_UC_r5", channel_id="UC_r5", channel_name="R5", brand_id="brand_neon",
            oauth_credential_ref="sec_oauth_test", created_at=T0, updated_at=T0))
        session.commit()
    app = create_app(
        session_factory=session_factory, settings=load_settings(env=MINIMAL_ENV),
        client=client or MockYouTubeClient(), clock=FixedClock(T0), execution=execution,
        clearance=p5_holding(release_manifest(candidate_id="cand_r5")),
        analytics=MockYouTubeAnalytics(MODEL) if analytics else None,
        analytics_from_env=False,
    )
    return TestClient(app)


def _publish(client: TestClient) -> Any:
    return client.post("/youtube/publish", headers=HEADERS, json={
        "channel_id": "UC_r5", "operating_mode": "private_only",
        "media_package": media_package("cand_r5", media_package_id="pkg_r5").to_wire()})


def test_publish_reports_a_receipt_per_write_and_a_replay_reports_them_again() -> None:
    client = _app()
    first = _publish(client)
    assert first.status_code == 200, first.text
    body = first.json()
    assert datetime.fromisoformat(body["published_at"]) == T0
    receipts = {r["capability"]: r for r in body["external_writes"]}
    assert set(receipts) == {"youtube.upload", "youtube.thumbnail"}
    assert {r["decision"] for r in receipts.values()} == {"simulate"}
    assert all(r["idempotency_key"].endswith(f":{c}") for c, r in receipts.items())

    replay = _publish(client).json()
    assert replay["youtube_video_id"] == body["youtube_video_id"]
    assert {(r["capability"], r["idempotency_key"], r["decision"], r["reason"])
            for r in replay["external_writes"]} == {
        (c, r["idempotency_key"], "simulate", "performed_earlier") for c, r in receipts.items()}

    attempt = client.get(f"/youtube/attempts/{body['attempt_id']}", headers=HEADERS).json()
    assert datetime.fromisoformat(attempt["published_at"]) == T0
    event = parse_seam(YouTubePublicationPayload, attempt["publication_event"])
    assert event.youtube_video_id == body["youtube_video_id"]


def test_a_guard_denial_is_a_409_with_the_capability_and_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real client under a live policy with no operator authorization: the guard
    denies, nothing is sent, and the answer names the capability and reason."""
    monkeypatch.setattr(YouTubePublishingEngine, "_media_bytes",
                        lambda self, supplied, attr: b"video-bytes")
    real = RealModeClient()
    execution = ExecutionSettings(provider_modes={"youtube": ProviderMode.REAL},
                                  policies={"youtube.upload": ExecutionPolicy.LIVE})
    client = _app(client=real, execution=execution)
    res = _publish(client)
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "external_write_deny"
    assert res.json()["detail"]["message"] == "youtube.upload not performed: no_authorization"
    assert real.recorded_calls == []  # nothing reached the provider


def test_the_mock_analytics_source_is_deterministic_per_arm_and_labelled() -> None:
    client = _app()
    video = _publish(client).json()["youtube_video_id"]
    as_of = (T0 + timedelta(days=28)).isoformat()
    first = client.get(f"/youtube/videos/{video}/analytics", headers=HEADERS,
                       params={"window": "28d", "as_of": as_of})
    assert first.status_code == 200, first.text
    again = client.get(f"/youtube/videos/{video}/analytics", headers=HEADERS,
                       params={"window": "28d", "as_of": as_of}).json()
    data = first.json()
    assert data == again
    assert (data["provider_mode"], data["source"]) == ("mock", "mock_youtube_analytics")
    assert data["model_version"] == "mock-outcomes-v1"
    expected = MODEL.video_snapshot(video_id=video, arm_id=data["content_arm_id"],
                                    published_at=T0, window=_window("28d"),
                                    observed_at=T0 + timedelta(days=28))
    assert data["metrics"]["views"] == expected.views > 0
    assert data["metrics"]["estimated_revenue"]["currency"] == "USD"

    before = client.get(f"/youtube/videos/{video}/analytics", headers=HEADERS,
                        params={"window": "28d", "as_of": (T0 - timedelta(hours=1)).isoformat()})
    assert before.status_code == 422
    unknown = client.get("/youtube/videos/nope/analytics", headers=HEADERS,
                         params={"window": "28d", "as_of": as_of})
    assert unknown.status_code == 404
    naive = client.get(f"/youtube/videos/{video}/analytics", headers=HEADERS,
                       params={"window": "28d", "as_of": "2026-10-29T12:00:00"})
    assert naive.status_code == 422


def test_without_a_source_the_analytics_route_refuses() -> None:
    client = _app(analytics=False)
    res = client.get("/youtube/videos/v/analytics", headers=HEADERS,
                     params={"window": "28d", "as_of": T0.isoformat()})
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "analytics_source_not_configured"


def _window(value: str) -> Any:
    from plm_contracts import ObservationWindow

    return ObservationWindow(value)
