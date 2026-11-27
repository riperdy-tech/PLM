"""``plm youtube reconcile`` asks the configured YouTube client (review 2026-11-26, P2 P7).

It always asked the mock, which never knows a real video, so a real deployment's
reconcile reported an empty remote status."""
from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest
from plm_contracts import ProviderMode
from plm_platform.execution import ExecutionSettings
from youtube_publishing import cli


class _Real:
    def get_video(self, video_id: str) -> dict[str, Any]:
        return {"id": video_id, "status": {"privacyStatus": "public"}}


def test_reconcile_uses_the_real_client_when_configured(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    attempt = SimpleNamespace(attempt_id="att_1", youtube_video_id="vid_1",
                              state=SimpleNamespace(value="verified"))
    monkeypatch.setattr(cli, "build_engine", lambda: None)
    monkeypatch.setattr(cli, "session_factory", lambda engine: lambda: nullcontext(None))
    monkeypatch.setattr(cli, "get_publish_attempt_by_id", lambda session, attempt_id: attempt)
    monkeypatch.setattr(cli, "load_execution_settings", lambda: ExecutionSettings(
        provider_modes={"youtube": ProviderMode.REAL}))
    monkeypatch.setattr(cli, "RealYouTubeClient", _Real)
    cli.cli_reconcile("att_1")
    assert json.loads(capsys.readouterr().out)["remote_status"] == {"privacyStatus": "public"}
