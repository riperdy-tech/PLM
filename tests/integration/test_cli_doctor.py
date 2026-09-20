"""W0.12 integration: ``plm migrate`` then ``plm doctor`` green on the stack.

This encodes the Wave 0 gate against the real compose services: migrate the
chain to head (idempotent), then require doctor to exit 0 with zero FAIL
rows. The file sorts first in ``tests/integration``, so the upgrade it leaves
behind is what the suites with migration assumptions (``test_migrations``'s
round trip starts and ends at head regardless) expect. Skips with an explicit
reason when any of postgres/minio/Temporal is unreachable.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
from plm_platform.cli import app
from plm_platform.config import Settings, load_settings
from plm_platform.db import database_url
from typer.testing import CliRunner

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE_TIMEOUT_SECONDS = 2.0
runner = CliRunner()


def _settings() -> Settings:
    return load_settings(dotenv_path=REPO_ROOT / ".env")


def _tcp_targets() -> list[tuple[str, int]]:
    settings = _settings()
    url = database_url(settings)
    minio_host, _, minio_port = settings.minio_endpoint.partition(":")
    temporal_host, _, temporal_port = settings.temporal_host.partition(":")
    return [
        (url.host or "localhost", url.port or 5432),
        (minio_host, int(minio_port or 9000)),
        (temporal_host, int(temporal_port or 7233)),
    ]


def _skip_reason() -> str | None:
    for host, port in _tcp_targets():
        try:
            socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SECONDS).close()
        except OSError as exc:
            return (
                f"stack not reachable at {host}:{port} - {type(exc).__name__}: {exc}; "
                "start it with: docker compose up -d --wait"
            )
    return None


@pytest.fixture(scope="module", autouse=True)
def _require_stack() -> None:
    reason = _skip_reason()
    if reason is not None:
        pytest.skip(reason)


def test_migrate_then_doctor_is_green() -> None:
    migrated = runner.invoke(app, ["migrate"])
    assert migrated.exit_code == 0, f"plm migrate failed:\n{migrated.output}"

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, f"plm doctor is not green:\n{result.output}"
    assert "0 FAIL" in result.output
    assert "task_queues" in result.output
