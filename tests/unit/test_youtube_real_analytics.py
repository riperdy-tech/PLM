"""P7's real YouTube Analytics source and the analytics route over it (review 2026-11-26).

#35: the real source wraps P9's ``RealYouTubeAnalyticsClient``; windows are whole Pacific
days and are not final until three days after their last day (409 ``analytics_not_final``,
so the pipeline waits). #33: the window starts at YouTube's own publish time, read again
before measuring, and a video that is not public is not measured. Provider responses are
the docs-derived fixtures; nothing calls Google.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from analytics.ingestion.youtube_client import RealYouTubeAnalyticsClient, YouTubeAnalyticsError
from fastapi.testclient import TestClient
from plm_contracts import ObservationWindow
from plm_platform.clock import FixedClock
from plm_platform.config import load_settings
from plm_platform.testing.http import MockHTTP
from sqlalchemy.orm import sessionmaker
from youtube_publishing.analytics import (
    AnalyticsNotFinal,
    RealYouTubeAnalytics,
    pacific_report_days,
)
from youtube_publishing.api.app import create_app
from youtube_publishing.client.mock import MockYouTubeClient
from youtube_publishing.domain.enums import PrivacyStatus
from youtube_publishing.domain.models import YouTubePublicationRecord
from youtube_publishing.storage.repositories import insert_publication_record

from tests.support.clearance import p5_holding
from tests.support.provider_fixtures import enqueue
from tests.support.seams import release_manifest
from tests.support.youtube_db import create_youtube_engine
from tests.unit.test_youtube_api import MINIMAL_ENV
from tests.unit.test_youtube_api_r5 import HEADERS

P7_DONE = datetime(2026, 11, 2, 18, 0, tzinfo=UTC)  # 10:00 PST: P7 finished (private)
MADE_PUBLIC = datetime(2026, 11, 4, 20, 0, tzinfo=UTC)  # the owner made it public later
VIDEO = "lPYmaPHPQr4"


def test_a_window_is_read_over_pacific_days_and_final_three_days_later() -> None:
    start, end = pacific_report_days(P7_DONE, ObservationWindow.D7,
                                     datetime(2026, 11, 13, 8, 0, tzinfo=UTC))
    assert (start.isoformat(), end.isoformat()) == ("2026-11-02", "2026-11-09")
    with pytest.raises(AnalyticsNotFinal) as early:
        pacific_report_days(P7_DONE, ObservationWindow.D7,
                            datetime(2026, 11, 13, 7, 59, tzinfo=UTC))
    assert early.value.ready_at == datetime(2026, 11, 13, 8, 0, tzinfo=UTC)
    _, lifetime_end = pacific_report_days(P7_DONE, ObservationWindow.LIFETIME,
                                          datetime(2026, 11, 20, 12, 0, tzinfo=UTC))
    assert lifetime_end.isoformat() == "2026-11-16"


def _record() -> YouTubePublicationRecord:
    return YouTubePublicationRecord(
        publication_record_id=f"pub_{VIDEO}", youtube_video_id=VIDEO, channel_id="UC_r5",
        publish_attempt_id="att_1", media_package_id="pkg_r5", release_manifest_id="rm_1",
        experiment_id="exp_1", experiment_family_id="fam_1", content_arm_id="arm_1",
        content_genome_id="gen_1", production_directive_id="dir_1", title="t",
        privacy_status=PrivacyStatus.PRIVATE, contains_synthetic_media=True,
        published_at=P7_DONE, video_url="u", thumbnail_url="t", created_at=P7_DONE,
        updated_at=P7_DONE)


def test_the_real_source_reads_the_window_from_youtube_analytics(mock_http: MockHTTP) -> None:
    enqueue(mock_http, "youtube_analytics", "oauth_token", "metrics_video")
    client = RealYouTubeAnalyticsClient(client_id="c", client_secret="s", refresh_token="r",
                                        http_client=mock_http.client())
    source = RealYouTubeAnalytics(client, unavailable=YouTubeAnalyticsError)
    snapshot = source.snapshot(_record(), window=ObservationWindow.D7,
                               observed_at=datetime(2026, 11, 20, tzinfo=UTC),
                               published_at=MADE_PUBLIC)
    mock_http.assert_query_params(1, startDate="2026-11-04", endDate="2026-11-11",
                                  filters=f"video=={VIDEO}")
    assert (snapshot.views, snapshot.subscribers_gained) == (1520, 12)
    assert snapshot.estimated_revenue is None  # not read: unknown, never zero


class _YouTube(MockYouTubeClient):
    def __init__(self, resource: dict[str, Any] | None) -> None:
        super().__init__()
        self.resource = resource

    def get_video(self, video_id: str) -> dict[str, Any] | None:
        return self.resource


class _Analytics:
    def __init__(self) -> None:
        self.windows: list[tuple[str, str]] = []

    def fetch_video_metrics(self, *, video_id: str, start_date: str, end_date: str) -> Any:
        self.windows.append((start_date, end_date))
        return SimpleNamespace(views=10, watch_time_minutes=30.0,
                               average_view_duration_seconds=180.0,
                               average_percentage_viewed=25.0, subscribers_gained=1,
                               subscribers_lost=0)


def _app(resource: dict[str, Any] | None, analytics: _Analytics) -> TestClient:
    engine = create_youtube_engine(static_pool=True)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        insert_publication_record(session, _record())
        session.commit()
    app = create_app(
        session_factory=lambda: factory(), settings=load_settings(env=MINIMAL_ENV),
        client=_YouTube(resource), clock=FixedClock(P7_DONE),
        clearance=p5_holding(release_manifest(candidate_id="cand_r5")),
        analytics=RealYouTubeAnalytics(analytics, unavailable=YouTubeAnalyticsError),
        analytics_from_env=False)
    return TestClient(app)


def _get(client: TestClient, as_of: datetime) -> Any:
    return client.get(f"/youtube/videos/{VIDEO}/analytics", headers=HEADERS,
                      params={"window": "7d", "as_of": as_of.isoformat()})


def _public(published: datetime, privacy: str = "public") -> dict[str, Any]:
    return {"id": VIDEO, "status": {"privacyStatus": privacy},
            "snippet": {"publishedAt": published.isoformat().replace("+00:00", "Z")}}


def test_the_window_starts_when_youtube_made_the_video_public() -> None:
    analytics = _Analytics()
    client = _app(_public(MADE_PUBLIC), analytics)
    res = _get(client, datetime(2026, 11, 15, 8, 0, tzinfo=UTC))
    assert res.status_code == 200, res.text
    body = res.json()
    assert datetime.fromisoformat(body["published_at"]) == MADE_PUBLIC
    assert (body["provider_mode"], body["source"]) == ("real", "youtube_analytics")
    assert body["metrics"]["views"] == 10 and body["metrics"]["estimated_revenue"] is None
    assert analytics.windows == [("2026-11-04", "2026-11-11")]


def test_analytics_are_not_final_until_three_days_after_the_window() -> None:
    client = _app(_public(MADE_PUBLIC), _Analytics())
    # Final by P7's completion time, but not by YouTube's own publish time.
    res = _get(client, datetime(2026, 11, 13, 8, 0, tzinfo=UTC))
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "analytics_not_final"
    before = _get(client, MADE_PUBLIC - timedelta(hours=1))
    assert before.json()["detail"]["code"] == "analytics_not_final"


@pytest.mark.parametrize(("resource", "code"), [
    (_public(MADE_PUBLIC, privacy="private"), "video_not_public"),
    (_public(MADE_PUBLIC, privacy="unlisted"), "video_not_public"),
    (None, "video_not_found_on_youtube"),
    ({"id": VIDEO, "status": {"privacyStatus": "public"}, "snippet": {}},
     "publish_time_unknown"),
])
def test_a_video_that_is_not_public_is_not_measured(resource: dict[str, Any] | None,
                                                    code: str) -> None:
    res = _get(_app(resource, _Analytics()), datetime(2026, 11, 20, tzinfo=UTC))
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == code


def _real_reader(mock_http: MockHTTP, reply: str) -> RealYouTubeAnalyticsClient:
    enqueue(mock_http, "youtube_analytics", "oauth_token", reply)
    return RealYouTubeAnalyticsClient(client_id="c", client_secret="s", refresh_token="r",
                                      http_client=mock_http.client())


def test_a_final_window_with_no_rows_is_a_measured_zero(mock_http: MockHTTP) -> None:
    """Once final, a well-formed report (column headers) without rows is zero activity:
    counts are 0, ratios over zero views are undefined (null), and estimated revenue
    stays unknown (null), never 0."""
    client = _app(_public(MADE_PUBLIC), _real_reader(mock_http, "metrics_no_rows"))
    res = _get(client, datetime(2026, 11, 20, tzinfo=UTC))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "youtube_analytics"
    assert body["metrics"] == {
        "views": 0, "watch_time_minutes": 0.0, "average_view_duration_seconds": None,
        "average_percentage_viewed": None, "subscribers_gained": 0, "subscribers_lost": 0,
        "estimated_revenue": None}


def test_a_report_without_column_headers_is_refused(mock_http: MockHTTP) -> None:
    client = _app(_public(MADE_PUBLIC), _real_reader(mock_http, "metrics_no_columns"))
    res = _get(client, datetime(2026, 11, 20, tzinfo=UTC))
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "analytics_report_malformed"
