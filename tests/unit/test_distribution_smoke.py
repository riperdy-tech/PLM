"""Unit tests for Phase P8 distribution smoke test execution."""

from __future__ import annotations

from unittest.mock import patch

from distribution.acceptance.smoke import run_smoke_test
from plm_platform.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_smoke_test_returns_blocked_when_token_is_missing() -> None:
    with patch("distribution.acceptance.smoke.load_settings") as mock_load:
        mock_load.return_value.service_token = None
        reason = run_smoke_test()
        assert reason is not None
        assert "blocked: pending distributor service token" in reason


def test_cli_smoke_exits_code_3_when_uncredentialed(monkeypatch) -> None:
    with patch("distribution.acceptance.smoke.load_settings") as mock_load:
        mock_load.return_value.service_token = None
        res = runner.invoke(app, ["distribution", "smoke"])
        assert res.exit_code == 3
        assert "blocked: pending distributor service token" in res.output
