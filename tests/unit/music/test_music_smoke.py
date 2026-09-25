"""Tests for P4 controlled dry-run smoke test (§111, R6)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from plm_platform.cli import BLOCKED_EXIT_CODE, app
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from services.music_factory.acceptance.smoke import run_smoke_test
from tests.support.music_db import create_music_engine

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
}


def test_smoke_blocked_without_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PLM_SERVICE_TOKEN", raising=False)

    reason = run_smoke_test()
    assert reason == "blocked: pending music factory service token (PLM_SERVICE_TOKEN)"


def test_cli_smoke_exits_code_three_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PLM_SERVICE_TOKEN", raising=False)

    result = CliRunner().invoke(app, ["music", "smoke"])
    assert result.exit_code == BLOCKED_EXIT_CODE
    assert "blocked: pending music factory service token" in result.output


def test_smoke_blocked_when_database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PLM_SERVICE_TOKEN", "valid-token")

    mock_engine = MagicMock()
    mock_engine.connect.side_effect = RuntimeError("connection refused")

    with patch("services.music_factory.acceptance.smoke.build_engine", return_value=mock_engine):
        reason = run_smoke_test()
        assert reason is not None
        assert reason.startswith("blocked: database unreachable")


def test_smoke_successful_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PLM_SERVICE_TOKEN", "valid-token")

    engine = create_music_engine(static_pool=True)
    factory = sessionmaker(bind=engine)

    with (
        patch("services.music_factory.acceptance.smoke.build_engine", return_value=engine),
        patch("services.music_factory.acceptance.smoke.session_factory", return_value=factory),
    ):
        result = run_smoke_test()
        assert result is None
