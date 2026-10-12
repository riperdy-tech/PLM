"""Unit tests for Phase P6 controlled dry-run smoke test (P6 §45, Ruling R9)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from media_factory.acceptance.smoke import _TOKEN_REASON, run_smoke_test
from plm_platform.cli import BLOCKED_EXIT_CODE, app
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from tests.support.media_db import create_media_engine

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
}


def test_media_smoke_test_blocks_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PLM_SERVICE_TOKEN", raising=False)
    monkeypatch.delenv("SERVICE_TOKEN", raising=False)

    reason = run_smoke_test()
    assert reason == _TOKEN_REASON


def test_cli_media_smoke_exits_code_three_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PLM_SERVICE_TOKEN", raising=False)

    result = CliRunner().invoke(app, ["media", "smoke"])
    assert result.exit_code == BLOCKED_EXIT_CODE
    assert "blocked: pending media factory service token" in result.output


def test_media_smoke_test_blocks_when_database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PLM_SERVICE_TOKEN", "mock-token-for-smoke")

    mock_engine = MagicMock()
    mock_engine.connect.side_effect = RuntimeError("connection refused")

    with patch("media_factory.acceptance.smoke.build_engine", return_value=mock_engine):
        reason = run_smoke_test()
        assert reason is not None
        assert "blocked: database unreachable" in reason


def test_media_smoke_successful_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PLM_SERVICE_TOKEN", "valid-media-token")

    media_engine = create_media_engine(static_pool=True)

    with (
        patch("media_factory.acceptance.smoke.build_engine", return_value=media_engine),
        patch(
            "media_factory.acceptance.smoke.session_factory",
            return_value=sessionmaker(bind=media_engine),
        ),
    ):
        reason = run_smoke_test()
        assert reason is None
