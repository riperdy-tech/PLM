"""Unit tests for P7 smoke test execution."""

from __future__ import annotations

from unittest.mock import patch

from youtube_publishing.acceptance.smoke import run_smoke_test


def test_smoke_test_returns_blocked_when_token_is_missing() -> None:
    with patch("youtube_publishing.acceptance.smoke.load_settings") as mock_load:
        mock_load.return_value.service_token = None
        reason = run_smoke_test()
        assert reason is not None
        assert "blocked: pending youtube publisher service token" in reason
