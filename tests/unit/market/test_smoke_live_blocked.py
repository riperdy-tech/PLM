# tests/unit/market/test_smoke_live_blocked.py
"""§40's acceptance is `blocked: pending credentials` — never a silent skip, never a pass.

The blocked path is asserted to *not construct an HTTP client at all*: a smoke test
that quietly falls back to mocks, or that reports success without credentials, would
be worse than no smoke test.

`load_settings(env=...)` still layers the repo's `.env` underneath, so the test names
the variables it needs missing explicitly; the repo `.env` carries no YouTube values.
"""

from __future__ import annotations

import httpx
from plm_platform.cli import BLOCKED_EXIT_CODE, app
from plm_platform.config import load_settings
from typer.testing import CliRunner

runner = CliRunner()
REQUIRED_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
}
ALL_YOUTUBE_VARS = ("YOUTUBE_API_KEY", "YOUTUBE_OAUTH_CLIENT_ID", "YOUTUBE_OAUTH_CLIENT_SECRET")


def test_the_live_smoke_is_blocked_with_a_reason_not_skipped(monkeypatch) -> None:
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)
    for name in ALL_YOUTUBE_VARS:
        monkeypatch.delenv(name, raising=False)
    calls: list[str] = []
    monkeypatch.setattr(httpx, "Client", lambda **_: calls.append("client") or None)
    result = runner.invoke(app, ["market", "smoke-live"])
    output = str(result.output)
    assert result.exit_code == BLOCKED_EXIT_CODE
    assert result.exit_code != 0, "blocked is not a passing state"
    assert "blocked: pending credentials" in output
    for name in ALL_YOUTUBE_VARS:
        assert name in output, f"the blocked reason must name {name}"
    assert "passed" not in output.lower() and "ok" not in output.lower()
    assert calls == [], "the blocked path must not construct an HTTP client"


def test_the_blocked_reason_names_every_missing_variable() -> None:
    from services.market_intelligence.acceptance.youtube_smoke import missing_credentials

    platform = load_settings(env=REQUIRED_ENV)
    assert missing_credentials(platform) == ALL_YOUTUBE_VARS

    with_key_only = {**REQUIRED_ENV, "YOUTUBE_API_KEY": "key",
                     "YOUTUBE_OAUTH_CLIENT_ID": "id", "YOUTUBE_OAUTH_CLIENT_SECRET": "secret"}
    assert missing_credentials(load_settings(env=with_key_only)) == ()
    without_key = {**with_key_only}
    del without_key["YOUTUBE_API_KEY"]
    assert missing_credentials(load_settings(env=without_key)) == ("YOUTUBE_API_KEY",)
