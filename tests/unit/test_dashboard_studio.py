"""The dashboard Studio: the owner's songs, thumbnails and approval without folders.

Fakes stand in for the exception actions, P6 and the importer; the inbox files and the
grant file are real files in a temporary folder. Proves what the Studio writes and which
resume it asks for, not the services behind them.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from plm_platform.clock import FixedClock
from plm_platform.dashboard.actions import ActionRefused
from plm_platform.dashboard.app import _range
from plm_platform.dashboard.studio import Studio, StudioPaths
from starlette.requests import Request

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
PKG = "mpkg_5d780a5bb27358a0"
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeActions:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def resume(self, case_id: str, *, actor: str, role: str | None,
                     evidence: dict[str, str], notes: str) -> dict[str, Any]:
        self.calls.append(("resume", case_id, {"actor": actor, "role": role, **evidence}))
        return {"case": {"case_id": case_id}}

    async def reject(self, case_id: str, *, actor: str, role: str | None,
                     notes: str) -> dict[str, Any]:
        self.calls.append(("reject", case_id, {"actor": actor, "role": role}))
        return {"case": {"case_id": case_id}}


def _studio(tmp_path: Path, holds: list[dict[str, Any]],
            imported: list[str] | None = None) -> tuple[Studio, FakeActions]:
    music, art = tmp_path / "music", tmp_path / "art"
    music.mkdir()
    (art / PKG).mkdir(parents=True)
    (music / "trk_1.prompts.txt").write_text(
        "PLM songs to make in Google Flow Music for track trk_1\n\n"
        "--- Song 1 ---\nSave as: trk_1-aaaa1111.wav\nInstrumental: yes\nPrompt: calm\n\n"
        "--- Song 2 ---\nSave as: trk_1-bbbb2222.wav\nInstrumental: yes\nPrompt: warm\n",
        encoding="utf-8")
    (art / PKG / "job.json").write_text(json.dumps({
        "media_package_id": PKG, "title": "T", "guide_version": "g",
        "variants": [{"variant_key": f"art_{x}", "style_id": f"s{x}", "text": "FOCUS",
                      "prompt": f"prompt {x}"} for x in "abc"]}), encoding="utf-8")
    actions = FakeActions()
    grants = tmp_path / "write-authorizations.json"
    grants.write_text(json.dumps([
        {"capability": "music.import", "granted_by": "owner",
         "granted_at": "2026-10-31T00:00:00+00:00", "expires_at": "2026-11-30T00:00:00+00:00"},
        {"capability": "youtube.upload", "granted_by": "owner",
         "granted_at": "2026-11-04T00:00:00+00:00", "expires_at": "2026-11-05T00:00:00+00:00"},
    ]), encoding="utf-8")

    def importer(package_id: str, folder: Path) -> dict[str, Any]:
        if imported is not None:
            imported.append(package_id)
        return {"media_package_id": package_id, "status": "ready_for_publish"}

    studio = Studio(
        paths=StudioPaths(music_inbox=music, art_inbox=art, grants_file=grants),
        actions=actions,  # type: ignore[arg-type]
        open_holds=lambda: holds,
        package=lambda pid: {"status": "awaiting_thumbnail_art", "metadata_candidates": [
            {"title": "Working Late", "description": "D", "tags": ["focus music"]}],
            "thumbnails": [{"id": "art_a"}], "chapters": [{}, {}, {}],
            "video_duration_seconds": 1425.8},
        media=lambda uri, checksum: b"",
        import_art=importer, clock=FixedClock(NOW))
    return studio, actions


def test_songs_show_the_waiting_sheet_and_take_only_its_wavs(tmp_path: Path) -> None:
    studio, actions = _studio(tmp_path, [{"case_id": "c1", "reason_code": "MANUAL_AUDIO_AWAITED"}])
    (sheet,) = studio.songs()["sheets"]
    assert [s["stem"] for s in sheet["songs"]] == ["trk_1-aaaa1111", "trk_1-bbbb2222"]
    assert "Prompt: calm" in sheet["songs"][0]["text"]
    with pytest.raises(ActionRefused, match="song_not_awaited"):
        studio.save_song("../evil", WAV)
    with pytest.raises(ActionRefused, match="song_not_awaited"):
        studio.save_song("trk_9-cccc3333", WAV)
    with pytest.raises(ActionRefused, match="not_a_wav"):
        studio.save_song("trk_1-aaaa1111", b"ID3 mp3 bytes")
    assert studio.save_song("trk_1-aaaa1111", WAV)["saved"] == "trk_1-aaaa1111.wav"
    assert (tmp_path / "music" / "trk_1-aaaa1111.wav").read_bytes() == WAV
    assert studio.songs()["sheets"][0]["songs"][0]["saved"] is True
    studio.save_song("trk_1-bbbb2222", WAV)
    assert studio.songs()["sheets"] == []  # every song saved: the sheet is done

    (answer,) = asyncio.run(studio.songs_done())
    assert answer["case"]["case_id"] == "c1"
    (call,) = actions.calls
    assert call[2]["role"] == "production_owner" and "manual_audio_inbox_ref" in call[2]


def test_songs_done_refuses_when_no_run_waits(tmp_path: Path) -> None:
    studio, _ = _studio(tmp_path, [])
    with pytest.raises(ActionRefused, match="no_run_waiting_for_songs"):
        asyncio.run(studio.songs_done())


def test_art_upload_keeps_one_file_per_letter_and_import_resumes_the_run(
        tmp_path: Path) -> None:
    imported: list[str] = []
    studio, actions = _studio(tmp_path, [{"case_id": "c2", "reason_code": "THUMBNAIL_ART_AWAITED",
                                          "details": {}}], imported)
    (job,) = studio.art_jobs()
    assert [v["letter"] for v in job["variants"]] == ["A", "B", "C"]
    assert job["variants"][0]["prompt"] == "prompt a"
    folder = tmp_path / "art" / PKG
    (folder / "A.jpg").write_bytes(b"\xff\xd8\xff old")
    with pytest.raises(ActionRefused, match="not_an_image"):
        studio.save_art(PKG, "A", b"GIF89a")
    with pytest.raises(ActionRefused, match="art_letter_invalid"):
        studio.save_art(PKG, "D", PNG)
    with pytest.raises(ActionRefused, match="thumbnail_job_unknown"):
        studio.save_art("../etc", "A", PNG)
    assert studio.save_art(PKG, "a", PNG)["saved"] == "A.png"
    assert sorted(p.name for p in folder.iterdir()) == ["A.png", "job.json"]
    assert studio.art_file(PKG, "A") == (PNG, "image/png")

    result = asyncio.run(studio.import_thumbnails(PKG))
    assert imported == [PKG] and result["package"]["status"] == "ready_for_publish"
    (call,) = actions.calls
    assert call[1] == "c2" and "thumbnail_art_inbox_ref" in call[2]


def test_approval_grants_this_upload_for_two_hours_then_resumes(tmp_path: Path) -> None:
    hold = {"case_id": "c3", "reason_code": "PUBLISH_APPROVAL_AWAITED",
            "details": {"media_package_ids": [PKG]}, "pipeline": {"workflow_id": "pipeline.d"}}
    studio, actions = _studio(tmp_path, [hold])
    (review,) = studio.overview()["reviews"]
    assert review["packages"][0]["title"] == "Working Late"
    assert review["packages"][0]["chapters"] == 3

    with pytest.raises(ActionRefused, match="approval_not_awaited"):
        asyncio.run(studio.approve("c-other"))
    answer = asyncio.run(studio.approve("c3"))
    assert answer["grants_expire_at"] == "2026-11-05T14:00:00+00:00"
    grants = json.loads((tmp_path / "write-authorizations.json").read_text(encoding="utf-8"))
    by_cap = {g["capability"]: g for g in grants}
    assert set(by_cap) == {"music.import", "youtube.upload", "youtube.thumbnail",
                           "youtube.playlist"}
    assert by_cap["music.import"]["expires_at"] == "2026-11-30T00:00:00+00:00"  # untouched
    assert by_cap["youtube.upload"]["expires_at"] == "2026-11-05T14:00:00+00:00"
    assert PKG in by_cap["youtube.upload"]["reference"]
    (call,) = actions.calls
    assert call[:2] == ("resume", "c3") and call[2]["actor"] == "owner"
    assert PKG in call[2]["publish_approval_ref"]

    asyncio.run(studio.reject("c3"))
    assert actions.calls[-1][:2] == ("reject", "c3")


def test_an_approval_without_a_grant_file_is_refused_and_resumes_nothing(
        tmp_path: Path) -> None:
    hold = {"case_id": "c4", "reason_code": "PUBLISH_APPROVAL_AWAITED",
            "details": {"media_package_ids": [PKG]}}
    studio, actions = _studio(tmp_path, [hold])
    studio.paths = StudioPaths(music_inbox=None, art_inbox=None, grants_file=None)
    with pytest.raises(ActionRefused, match="grants_file_not_configured"):
        asyncio.run(studio.approve("c4"))
    assert actions.calls == []


def _request(range_header: str | None) -> Request:
    headers = [] if range_header is None else [(b"range", range_header.encode())]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


def test_video_is_served_whole_or_by_the_range_the_player_asks_for() -> None:
    data = bytes(range(100))
    whole = _range(_request(None), data, "video/mp4")
    assert whole.status_code == 200 and whole.body == data
    part = _range(_request("bytes=10-19"), data, "video/mp4")
    assert part.status_code == 206 and part.body == data[10:20]
    assert part.headers["content-range"] == "bytes 10-19/100"
    tail = _range(_request("bytes=90-"), data, "video/mp4")
    assert tail.body == data[90:]
    assert _range(_request("bytes=200-"), data, "video/mp4").status_code == 416
