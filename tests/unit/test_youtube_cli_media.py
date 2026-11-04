"""``plm youtube publish`` sends a real client the package's own verified bytes.

Before this, the CLI called ``execute_publish`` without ``video_data``, so a real upload
always stopped at ``PREFLIGHT_ARTIFACT_MISSING``.
"""

from __future__ import annotations

import pytest
from youtube_publishing import cli

DIGEST = "a" * 64
URI = f"plm://plm-media/media/renders/b/m/video_master.mp4/{DIGEST}"


def test_artifact_bytes_fetches_the_key_the_uri_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []
    monkeypatch.setattr(cli, "from_uri", lambda uri: uri.removeprefix("plm://plm-media/"))
    monkeypatch.setattr(cli, "get_verified", lambda key: fetched.append(key) or b"video")
    assert cli._artifact_bytes(URI, DIGEST) == b"video"
    assert fetched == [f"media/renders/b/m/video_master.mp4/{DIGEST}"]


def test_artifact_bytes_refuses_a_uri_that_pins_another_checksum(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "from_uri", lambda uri: uri.removeprefix("plm://plm-media/"))
    monkeypatch.setattr(cli, "get_verified", lambda key: pytest.fail("fetched"))
    with pytest.raises(ValueError, match="does not pin"):
        cli._artifact_bytes(URI, "b" * 64)
