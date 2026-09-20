# tests/unit/market/test_cross_platform.py
from datetime import UTC, datetime

from services.market_intelligence.connectors.base import ConnectorError
from services.market_intelligence.connectors.browser import BrowserConnector
from services.market_intelligence.connectors.trends import PublicTrendsConnector, parse_trend_points
from services.market_intelligence.domain.enums import ConnectorErrorClass
from services.market_intelligence.features.cross_platform import cross_platform_signal

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def test_no_secondary_source_means_unknown_not_zero() -> None:
    signal = cross_platform_signal([])
    assert signal.is_unknown  # §17.3: reduce confidence, never assign zero demand


def test_trend_points_parse_from_the_configured_feed(mock_http) -> None:
    import json
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "fixtures" / "market" / "trends_feed.json")
        .read_text(encoding="utf-8")
    )
    mock_http.enqueue_json(fixture)
    connector = PublicTrendsConnector(
        mock_http.client(base_url="https://trends.example"),
        feed_path="/public/trends.json", observed_at=lambda: FIXED_NOW,
    )
    fetch = connector.fetch_points("lofi")
    points = parse_trend_points(fetch)
    assert points[0].term == "lofi" and points[0].interest == 78
    assert fetch.quota_units == 0  # public feeds do not consume provider quota


def test_browser_automation_is_unsupported_until_explicitly_enabled() -> None:
    import pytest

    with pytest.raises(ConnectorError) as excinfo:
        BrowserConnector().fetch_public_page("https://example.com")
    assert excinfo.value.error_class is ConnectorErrorClass.UNSUPPORTED_OPERATION
