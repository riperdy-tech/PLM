"""scripts/authorize_youtube.py: the OAuth state check and the atomic .env write.

No network and no browser: the callback handler is driven directly.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from scripts import authorize_youtube as auth


def _callback(path: str) -> int:
    handler: Any = object.__new__(auth.OAuthCallbackHandler)
    handler.path = path
    handler.wfile = io.BytesIO()
    status: list[int] = []
    handler.send_response = status.append
    handler.send_header = lambda *args: None
    handler.end_headers = lambda: None
    handler.do_GET()
    return status[0]


@pytest.fixture(autouse=True)
def fresh_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth.OAuthCallbackHandler, "auth_code", None)
    monkeypatch.setattr(auth.OAuthCallbackHandler, "error", None)
    monkeypatch.setattr(auth.OAuthCallbackHandler, "expected_state", "expected-state")


@pytest.mark.parametrize("query", ["code=abc", "code=abc&state=other", "error=x&state=wrong"])
def test_a_callback_without_this_runs_state_is_refused(query: str) -> None:
    assert _callback(f"/?{query}") == 400
    assert auth.OAuthCallbackHandler.auth_code is None
    assert auth.OAuthCallbackHandler.error == "state_mismatch"


def test_the_matching_state_accepts_the_code() -> None:
    assert _callback("/?code=abc&state=expected-state") == 200
    assert auth.OAuthCallbackHandler.auth_code == "abc"


def test_env_write_replaces_only_the_refresh_token(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("A=1\nYOUTUBE_OAUTH_REFRESH_TOKEN=old\nB=2\n", encoding="utf-8")
    auth._write_env_atomically(env, auth._with_refresh_token(env.read_text("utf-8"), "new"))
    assert env.read_text(encoding="utf-8") == "A=1\nYOUTUBE_OAUTH_REFRESH_TOKEN=new\nB=2\n"
    assert [p.name for p in tmp_path.iterdir()] == [".env"], "no temp file left behind"
    assert auth._with_refresh_token("A=1\n", "t") == "A=1\nYOUTUBE_OAUTH_REFRESH_TOKEN=t\n"
