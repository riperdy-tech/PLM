"""Offline tests for recent-upload polling (no OAuth, external writes or scheduling)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from analytics.ingestion.youtube_client import (
    YouTubeAnalyticsError,
    YouTubeRetentionPoint,
    YouTubeRetentionReport,
    YouTubeTrafficSource,
    YouTubeTrafficSourcesReport,
    YouTubeVideoMetrics,
)
from analytics.ingestion.youtube_reach import parse_reach_csv

from scripts import poll_live_analytics
from scripts.poll_live_analytics import poll_recent_uploads

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def _clients() -> tuple[MagicMock, MagicMock]:
    data = MagicMock()
    data.list_recent_uploads.return_value = [
        {"video_id": "vid_recent", "title": "New Album", "published_at": "2026-10-20T12:00:00Z"},
        {"video_id": "vid_old", "title": "Older Album", "published_at": "2026-09-01T00:00:00Z"},
    ]
    data.get_video.side_effect = lambda video_id: {
        "id": video_id,
        "snippet": {"description": "0:00 Opening\n10:00 Middle\n20:00 Ending"},
        "contentDetails": {"duration": "PT30M"},
        "processingDetails": {"processingStatus": "succeeded"},
        "status": {"privacyStatus": "public"},
    }
    analytics = MagicMock()
    analytics.fetch_video_metrics.side_effect = lambda **params: YouTubeVideoMetrics(
        video_id=params["video_id"], start_date=params["start_date"],
        end_date=params["end_date"], views=7, watch_time_minutes=20.,
        average_view_duration_seconds=171., average_percentage_viewed=14.,
        subscribers_gained=None, subscribers_lost=None,
    )
    analytics.fetch_video_retention.side_effect = lambda **params: YouTubeRetentionReport(
        video_id=params["video_id"], start_date=params["start_date"],
        end_date=params["end_date"], points=(
            YouTubeRetentionPoint(1 / 60, .72, .6),
            YouTubeRetentionPoint(1 / 15, .55, .5),
            YouTubeRetentionPoint(.10, .62, .5),
            YouTubeRetentionPoint(1 / 6, .43, .4),
            YouTubeRetentionPoint(.50, .14, .4),
        ),
    )
    analytics.fetch_video_traffic_sources.side_effect = (
        lambda **params: YouTubeTrafficSourcesReport(
            video_id=params["video_id"], start_date=params["start_date"],
            end_date=params["end_date"],
            rows=(YouTubeTrafficSource("YT_SEARCH", 42, 104.5),),
        )
    )
    return data, analytics


def _fake_downloaded(rows):
    return SimpleNamespace(
        job_id="job_1", report_id="report_2", report_type="channel_reach_basic_a1",
        create_time=NOW, start_time=NOW, end_time=NOW,
        csv_sha256="c" * 64, rows=rows,
    )


def test_cli_authenticated_report_reaches_diagnostic_without_external_write(
    monkeypatch: pytest.MonkeyPatch, caplog,
) -> None:
    data, analytics = _clients()
    data.is_configured = True
    analytics.is_configured = True
    seen = []
    rows = parse_reach_csv(
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-10-21,UC_test,vid_recent,50,0.05\n",
        ctr_unit="ratio",
    )

    class FakeDownloader:
        def __init__(self, **kwargs):
            assert callable(kwargs["access_token"])

        def fetch(self, **kwargs):
            seen.append(kwargs)
            return _fake_downloaded(rows)

    monkeypatch.setattr(poll_live_analytics, "RealYouTubeClient", lambda: data)
    monkeypatch.setattr(poll_live_analytics, "RealYouTubeAnalyticsClient", lambda: analytics)
    monkeypatch.setattr(poll_live_analytics, "AuthenticatedReachDownloader", FakeDownloader)

    def fake_poll(*args, **kwargs):
        assert kwargs["channel_id"] == "UC_test"
        assert kwargs["reach_rows"] == rows
        return 1, 0

    monkeypatch.setattr(poll_live_analytics, "poll_recent_uploads", fake_poll)
    with caplog.at_level(logging.INFO):
        result = poll_live_analytics.main([
            "--reach-job-id", "job_1", "--reach-report-id", "report_2",
            "--reach-ctr-unit", "ratio", "--channel-id", "UC_test",
        ])
    assert result == 0
    assert seen == [{"job_id": "job_1", "report_id": "report_2", "ctr_unit": "ratio"}]
    assert "report=report_2" in caplog.text
    assert "bytes_sha256=" in caplog.text


def test_cli_authenticated_report_mismatched_channel_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, analytics = _clients()
    data.is_configured = True
    analytics.is_configured = True
    rows = parse_reach_csv(
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-10-21,UC_wrong,vid_recent,50,0.05\n",
        ctr_unit="ratio",
    )
    monkeypatch.setattr(poll_live_analytics, "RealYouTubeClient", lambda: data)
    monkeypatch.setattr(poll_live_analytics, "RealYouTubeAnalyticsClient", lambda: analytics)
    monkeypatch.setattr(
        poll_live_analytics, "AuthenticatedReachDownloader",
        lambda **kwargs: SimpleNamespace(fetch=lambda **kw: _fake_downloaded(rows)),
    )
    assert poll_live_analytics.main([
        "--reach-job-id", "job_1", "--reach-report-id", "report_2",
        "--reach-ctr-unit", "ratio", "--channel-id", "UC_test",
    ]) == 2
    data.list_recent_uploads.assert_not_called()


@pytest.mark.parametrize("arguments", [
    ["--reach-job-id", "job_1"],
    ["--reach-report-id", "report_2"],
    ["--reach-job-id", "job_1", "--reach-report-id", "report_2"],
    ["--reach-job-id", "job_1", "--reach-report-id", "report_2",
     "--reach-ctr-unit", "ratio", "--channel-id", "UC_test", "--reach-report", "x.csv"],
])
def test_cli_report_selection_arguments_fail_before_io(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        poll_live_analytics.main(arguments)
    assert exc.value.code == 2


def test_recent_uploads_have_publish_aware_windows_and_measured_retention(caplog) -> None:
    data, analytics = _clients()
    with caplog.at_level(logging.INFO):
        inspected, errors = poll_recent_uploads(
            data, analytics, now=NOW, limit=2, channel_id="UC_test",
        )
    assert (inspected, errors) == (2, 0)
    data.list_recent_uploads.assert_called_once_with(max_results=2, channel_id="UC_test")
    requests = [call.kwargs for call in analytics.fetch_video_metrics.call_args_list]
    assert requests == [
        {"video_id": "vid_recent", "start_date": "2026-10-20", "end_date": "2026-10-22"},
        {"video_id": "vid_old", "start_date": "2026-09-25", "end_date": "2026-10-22"},
    ]
    assert analytics.fetch_video_retention.call_count == 2
    assert analytics.fetch_video_traffic_sources.call_count == 2
    assert "Source YT_SEARCH: views=42, watch_minutes=104.50" in caplog.text
    assert "at 10%=62.0% observed" in caplog.text
    assert "at 50%=14.0% observed" in caplog.text
    assert "clickable chapters=UNVERIFIED" in caplog.text
    assert data.get_video.call_count == 2
    assert "0:30 / 2:00 / 5:00" in caplog.text
    assert "72.0% observed / 55.0% observed / 43.0% observed" in caplog.text


def test_missing_analytics_rows_are_explicitly_unknown(caplog) -> None:
    data, analytics = _clients()
    data.list_recent_uploads.return_value = data.list_recent_uploads.return_value[:1]
    analytics.fetch_video_metrics.return_value = SimpleNamespace(
        views=None, watch_time_minutes=None, average_view_duration_seconds=None,
        average_percentage_viewed=None,
    )
    analytics.fetch_video_metrics.side_effect = None
    analytics.fetch_video_retention.return_value = SimpleNamespace(points=())
    analytics.fetch_video_retention.side_effect = None
    with caplog.at_level(logging.WARNING):
        result = poll_recent_uploads(data, analytics, now=NOW)
    assert result == (1, 0)
    assert "not a measured zero" in caplog.text
    assert "Retention curve unavailable" in caplog.text


def test_one_video_error_does_not_hide_other_video_success(caplog) -> None:
    data, analytics = _clients()
    analytics.fetch_video_metrics.side_effect = [
        YouTubeAnalyticsError("permission denied"),
        SimpleNamespace(
            views=4, watch_time_minutes=20., average_view_duration_seconds=300.,
            average_percentage_viewed=8.3,
        ),
    ]
    with caplog.at_level(logging.ERROR):
        inspected, errors = poll_recent_uploads(
            data, analytics, now=NOW, with_retention=False,
        )
    assert (inspected, errors) == (1, 1)
    assert "permission denied" in caplog.text
    analytics.fetch_video_retention.assert_not_called()


def test_official_reach_report_preserves_partial_coverage_and_weighted_ctr(caplog) -> None:
    data, analytics = _clients()
    data.list_recent_uploads.return_value = data.list_recent_uploads.return_value[:1]
    rows = parse_reach_csv(
        "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
        "2026-10-20,UC_test,vid_recent,100,0.1\n"
        "2026-10-21,UC_test,vid_recent,900,0.02\n",
        ctr_unit="ratio",
    )
    with caplog.at_level(logging.INFO):
        assert poll_recent_uploads(
            data, analytics, now=NOW, channel_id="UC_test",
            reach_rows=rows, with_retention=False,
        ) == (1, 0)
    assert "Thumbnail impressions=1000; weighted CTR=2.80%" in caplog.text
    assert "observed_days=2/3" in caplog.text
    assert "only part of the requested analysis window" in caplog.text


def test_reach_requires_authenticated_channel_match_before_network() -> None:
    data, analytics = _clients()
    with pytest.raises(ValueError, match="channel_id"):
        poll_recent_uploads(
            data, analytics, now=NOW, reach_rows=(),
        )
    data.list_recent_uploads.assert_not_called()


def test_source_unavailable_is_counted_independently_of_retention(caplog) -> None:
    data, analytics = _clients()
    data.list_recent_uploads.return_value = data.list_recent_uploads.return_value[:1]
    analytics.fetch_video_traffic_sources.side_effect = YouTubeAnalyticsError("traffic source unauthorized")
    with caplog.at_level(logging.ERROR):
        result = poll_recent_uploads(data, analytics, now=NOW)
    assert result == (1, 1)
    assert "traffic source unauthorized" in caplog.text
    analytics.fetch_video_retention.assert_called_once()


def test_source_query_can_be_disabled_for_operator_safety() -> None:
    data, analytics = _clients()
    data.list_recent_uploads.return_value = data.list_recent_uploads.return_value[:1]
    assert poll_recent_uploads(data, analytics, now=NOW, with_sources=False) == (1, 0)
    analytics.fetch_video_traffic_sources.assert_not_called()


def test_upload_too_new_is_not_measured_or_assumed_zero() -> None:
    data, analytics = _clients()
    data.list_recent_uploads.return_value = [
        {"video_id": "today", "title": "Today", "published_at": "2026-10-23T02:00:00Z"}
    ]
    assert poll_recent_uploads(data, analytics, now=NOW) == (0, 0)
    analytics.fetch_video_metrics.assert_not_called()


def test_missing_album_chapters_is_flagged_even_if_metrics_exist(caplog) -> None:
    data, analytics = _clients()
    data.list_recent_uploads.return_value = data.list_recent_uploads.return_value[:1]
    data.get_video.side_effect = lambda video_id: {
        "id": video_id,
        "snippet": {"description": "No timestamps"},
        "contentDetails": {"duration": "PT1H"},
        "processingDetails": {"processingStatus": "succeeded"},
        "status": {"privacyStatus": "public"},
    }
    with caplog.at_level(logging.ERROR):
        assert poll_recent_uploads(data, analytics, now=NOW, with_retention=False) == (1, 1)
    assert "Album chapters metadata invalid" in caplog.text
    assert analytics.fetch_video_metrics.call_count == 1


def test_missing_video_resource_is_an_error_before_analytics_call() -> None:
    data, analytics = _clients()
    data.list_recent_uploads.return_value = data.list_recent_uploads.return_value[:1]
    data.get_video.side_effect = None
    data.get_video.return_value = None
    assert poll_recent_uploads(data, analytics, now=NOW) == (0, 1)
    analytics.fetch_video_metrics.assert_not_called()


def test_invalid_published_date_is_failure_not_success() -> None:
    data, analytics = _clients()
    data.list_recent_uploads.return_value = [
        {"video_id": "bad", "title": "Bad", "published_at": "not-a-date"}
    ]
    assert poll_recent_uploads(data, analytics, now=NOW) == (0, 1)
    analytics.fetch_video_metrics.assert_not_called()


@pytest.mark.parametrize("limit,days", [(0, 28), (51, 28), (10, 0), (10, 366)])
def test_request_limit_validated_before_discovery(limit: int, days: int) -> None:
    data, analytics = _clients()
    with pytest.raises(ValueError):
        poll_recent_uploads(data, analytics, now=NOW, limit=limit, window_days=days)
    data.list_recent_uploads.assert_not_called()


def test_no_videos_in_authenticated_channel_is_not_old_hardcoded_fallback() -> None:
    data, analytics = _clients()
    data.list_recent_uploads.return_value = []
    assert poll_recent_uploads(data, analytics, now=NOW) == (0, 0)
    analytics.fetch_video_metrics.assert_not_called()
