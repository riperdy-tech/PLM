"""Unit tests for Phase P5 controlled dry-run smoke test (P5 §94, R7, R8)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from plm_platform.cli import BLOCKED_EXIT_CODE, app
from quality_gate.acceptance.smoke import run_smoke_test
from sqlalchemy.orm import sessionmaker
from typer.testing import CliRunner

from tests.support.qa_db import create_qa_engine

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
}


def test_qa_smoke_blocked_without_service_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PLM_SERVICE_TOKEN", raising=False)

    reason = run_smoke_test()
    assert reason == "blocked: pending quality gate service token (PLM_SERVICE_TOKEN)"


def test_cli_qa_smoke_exits_code_three_when_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PLM_SERVICE_TOKEN", raising=False)

    result = CliRunner().invoke(app, ["qa", "smoke"])
    assert result.exit_code == BLOCKED_EXIT_CODE
    assert "blocked: pending quality gate service token" in result.output


def test_qa_smoke_blocked_when_database_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PLM_SERVICE_TOKEN", "valid-token")

    mock_engine = MagicMock()
    mock_engine.connect.side_effect = RuntimeError("connection refused")

    with patch("quality_gate.acceptance.smoke.build_engine", return_value=mock_engine):
        reason = run_smoke_test()
        assert reason is not None
        assert reason.startswith("blocked: database unreachable")


def test_qa_smoke_successful_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in MINIMAL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("PLM_SERVICE_TOKEN", "valid-token")

    qa_engine = create_qa_engine(static_pool=True)

    with (
        patch("quality_gate.acceptance.smoke.build_engine", return_value=qa_engine),
        patch("quality_gate.acceptance.smoke.session_factory", return_value=sessionmaker(bind=qa_engine)),
    ):
        reason = run_smoke_test()
        assert reason is None
