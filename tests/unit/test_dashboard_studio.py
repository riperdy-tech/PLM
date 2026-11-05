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
                     evidence: dict[str, str], notes: str, **signal: Any) -> dict[str, Any]:
        self.calls.append(("resume", case_id, {"actor": actor, "role": role, "notes": notes,
                                               **evidence, **signal}))
        return {"case": {"case_id": case_id}}

    async def reject(self, case_id: str, *, actor: str, role: str | None,
                     notes: str) -> dict[str, Any]:
        self.calls.append(("reject", case_id, {"actor": actor, "role": role}))
        return {"case": {"case_id": case_id}}


def _studio(tmp_path: Path, holds: list[dict[str, Any]],
            imported: list[str] | None = None,
            status: str = "awaiting_thumbnail_art") -> tuple[Studio, FakeActions]:
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

    package_states: dict[str, str] = {}

    def importer(package_id: str, folder: Path) -> dict[str, Any]:
        if imported is not None:
            imported.append(package_id)
        package_states[package_id] = "ready_for_publish"
        return {"media_package_id": package_id, "status": "ready_for_publish"}

    studio = Studio(
        paths=StudioPaths(music_inbox=music, art_inbox=art, grants_file=grants,
                          notes_file=tmp_path / "studio-notes.json"),
        actions=actions,  # type: ignore[arg-type]
        open_holds=lambda: [{"pipeline": {"run_state": "HELD"}, **h} for h in holds],
        package=lambda pid: {"status": package_states.get(pid, status), "metadata_candidates": [
            {"title": "Working Late", "description": "D", "tags": ["focus music"]}],
            "thumbnails": [{"id": "art_a"}],
            "chapters": [{"song_id": f"cand_{n}", "title": f"T{n}",
                          "timestamp_str": f"0{n}:00"} for n in range(3)],
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
    studio, _ = _studio(tmp_path, [{"case_id": "old", "reason_code": "MANUAL_AUDIO_AWAITED",
                                    "pipeline": {"run_state": "CANCELLED"}}])
    assert studio.overview()["waiting"]["MANUAL_AUDIO_AWAITED"] == []
    with pytest.raises(ActionRefused, match="no_run_waiting_for_songs"):
        asyncio.run(studio.songs_done())


def test_art_upload_keeps_one_file_per_letter_and_import_resumes_the_run(
        tmp_path: Path) -> None:
    imported: list[str] = []
    studio, actions = _studio(tmp_path, [{"case_id": "c2", "reason_code": "THUMBNAIL_ART_AWAITED",
                                          "details": {"media_package_ids": [PKG]}}], imported)
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


def test_thumbnail_import_never_resumes_an_unrelated_or_unbound_hold(
        tmp_path: Path) -> None:
    other = "mpkg_1111222233334444"
    imported: list[str] = []
    studio, actions = _studio(tmp_path, [
        {"case_id": "unrelated", "reason_code": "THUMBNAIL_ART_AWAITED",
         "details": {"media_package_ids": [other]}},
        {"case_id": "legacy", "reason_code": "THUMBNAIL_ART_AWAITED",
         "details": {"error": f"unstructured note mentioning {PKG}"}},
    ], imported)
    with pytest.raises(ActionRefused, match="thumbnail_hold_not_found"):
        asyncio.run(studio.import_thumbnails(PKG))
    assert imported == [] and actions.calls == []


def test_thumbnail_import_waits_for_all_packages_in_the_same_hold(
        tmp_path: Path) -> None:
    other = "mpkg_1111222233334444"
    studio, actions = _studio(tmp_path, [
        {"case_id": "joint", "reason_code": "THUMBNAIL_ART_AWAITED",
         "details": {"media_package_ids": [PKG, other]}},
        {"case_id": "unrelated", "reason_code": "THUMBNAIL_ART_AWAITED",
         "details": {"media_package_ids": ["mpkg_ffffffffffffffff"]}},
    ])
    other_folder = tmp_path / "art" / other
    other_folder.mkdir()
    (other_folder / "job.json").write_text(
        (tmp_path / "art" / PKG / "job.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    first = asyncio.run(studio.import_thumbnails(PKG))
    assert first["resumed"] == [] and first["waiting_for"] == [other]
    assert actions.calls == []
    second = asyncio.run(studio.import_thumbnails(other))
    assert second["waiting_for"] == []
    assert [call[1] for call in actions.calls] == ["joint"]


def test_approval_grants_this_upload_for_two_hours_then_resumes(tmp_path: Path) -> None:
    hold = {"case_id": "c3", "reason_code": "PUBLISH_APPROVAL_AWAITED",
            "details": {"media_package_ids": [PKG]},
            "pipeline": {"workflow_id": "pipeline.d", "run_state": "HELD"}}
    studio, actions = _studio(tmp_path, [hold], status="ready_for_publish")
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
    studio, actions = _studio(tmp_path, [hold], status="ready_for_publish")
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


APPROVAL = {"case_id": "c5", "reason_code": "PUBLISH_APPROVAL_AWAITED",
            "details": {"media_package_ids": [PKG],
                        "packages": [{"media_package_id": PKG, "p3_package_id": "p3",
                                      "candidate_ids": ["cand_0", "cand_1", "cand_2"]}]},
            "pipeline": {"workflow_id": "pipeline.d", "run_state": "HELD"}}


def test_an_approval_waits_until_every_package_is_ready(tmp_path: Path) -> None:
    studio, actions = _studio(tmp_path, [APPROVAL])  # the package waits for new art
    with pytest.raises(ActionRefused, match="package_not_ready"):
        asyncio.run(studio.approve("c5"))
    assert actions.calls == []


def test_text_and_thumbnail_changes_apply_now_and_keep_the_review(tmp_path: Path) -> None:
    studio, actions = _studio(tmp_path, [APPROVAL], status="ready_for_publish")
    edits: list[tuple[str, dict[str, Any]]] = []
    studio.edit = lambda pid, changes: edits.append((pid, changes)) or {"media_package_id": pid}
    studio.edit_text("c5", PKG, title="New title", description=None, tags=["a"],
                     note="shorter title")
    studio.choose_thumbnail("c5", PKG, "art_b")
    assert edits == [(PKG, {"title": "New title", "description": None, "tags": ["a"]}),
                     (PKG, {"thumbnail_id": "art_b"})]
    assert actions.calls == []  # still waiting for approval
    with pytest.raises(ActionRefused, match="package_not_in_review"):
        studio.edit_text("c5", "mpkg_00000000", title="x", description=None, tags=None)
    (review,) = studio.overview()["reviews"]
    assert [n["kind"] for n in review["notes"]] == ["edit_text", "choose_thumbnail"]
    assert review["packages"][0]["tracks"][1] == {"song_id": "cand_1", "title": "T1",
                                                  "start": "01:00"}


def test_new_art_sends_the_package_back_to_the_thumbnail_step_with_the_note(
        tmp_path: Path) -> None:
    studio, actions = _studio(tmp_path, [APPROVAL], status="ready_for_publish")
    reopened: list[str] = []
    studio.reopen = lambda pid: reopened.append(pid) or {"status": "awaiting_thumbnail_art"}
    studio.request_new_art("c5", PKG, "warmer light, bigger text")
    assert reopened == [PKG] and actions.calls == []
    assert studio.request_new_art("c5", PKG, "  ")["note"]["note"] == "new art (no note)"
    studio.package = lambda pid: {"status": "awaiting_thumbnail_art"}
    (job,) = studio.art_jobs()
    assert [n["note"] for n in job["notes"]] == ["warmer light, bigger text", "new art (no note)"]


def test_a_song_swap_rewinds_the_run_without_its_songs_and_offers_the_old_art(
        tmp_path: Path) -> None:
    studio, actions = _studio(tmp_path, [APPROVAL], status="ready_for_publish")
    with pytest.raises(ActionRefused, match="song_not_in_package"):
        asyncio.run(studio.replace_songs("c5", ["cand_9"], "too loud"))
    asyncio.run(studio.replace_songs("c5", ["cand_1", "cand_1"], "too loud, make it calmer"))
    (call,) = actions.calls
    assert call[:2] == ("resume", "c5")
    assert call[2]["rewind_to"] == "assemble"
    assert call[2]["exclude_candidates"] == ("cand_1",)
    assert "publish_approval_ref" in call[2] and "too loud" in call[2]["notes"]

    # The rewind renders a new package, which asks for art again: the old art is offered.
    new = "mpkg_1111222233334444"
    (tmp_path / "art" / new).mkdir()
    (tmp_path / "art" / new / "job.json").write_text(
        (tmp_path / "art" / PKG / "job.json").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "art" / PKG / "A.png").write_bytes(PNG)
    studio.package = lambda pid: {"status": "awaiting_thumbnail_art"}
    jobs = {j["media_package_id"]: j for j in studio.art_jobs()}
    assert jobs[new]["reuse_from"] == [PKG]
    assert studio.reuse_art(new, PKG)["copied"] == ["A.png"]
    assert (tmp_path / "art" / new / "A.png").read_bytes() == PNG


def test_start_asks_for_suno_or_flow_music_and_shows_both_inboxes(tmp_path: Path) -> None:
    """The Start choice: Suno or Flow Music becomes the run's P4 provider; a tool whose
    inbox is not configured is refused before any run starts; sheets from both inboxes
    are listed with their tool, and a WAV goes to the inbox of its sheet."""
    started: list[str] = []

    async def start(provider: str) -> dict[str, Any]:
        started.append(provider)
        return {"workflow_id": "pipeline.d", "music_provider": provider}

    studio, _ = _studio(tmp_path, [])
    studio.start = start
    with pytest.raises(ActionRefused, match="music_inbox_not_configured"):
        asyncio.run(studio.start_run("suno"))  # no Suno inbox yet
    with pytest.raises(ActionRefused, match="music_tool_unknown"):
        asyncio.run(studio.start_run("udio"))
    assert started == []

    suno = tmp_path / "suno"
    suno.mkdir()
    (suno / "trk_2.prompts.txt").write_text(
        "PLM songs to make on suno.com for track trk_2\n\n"
        "--- Song 1 ---\nSave as: trk_2-cccc3333.wav\nInstrumental: yes\n"
        "Styles: lofi\n",
        encoding="utf-8")
    studio.paths = StudioPaths(music_inbox=tmp_path / "music", art_inbox=tmp_path / "art",
                               grants_file=None, suno_inbox=suno)
    asyncio.run(studio.start_run("suno"))
    asyncio.run(studio.start_run("flow_music"))
    assert started == ["suno_manual", "flow_music_manual"]

    sheets = studio.songs()["sheets"]
    assert [(s["source"], s["sheet"]) for s in sheets] == [
        ("Flow Music", "trk_1.prompts.txt"), ("Suno", "trk_2.prompts.txt")]
    studio.save_song("trk_2-cccc3333", WAV)
    assert (suno / "trk_2-cccc3333.wav").read_bytes() == WAV
    assert not (tmp_path / "music" / "trk_2-cccc3333.wav").exists()


def test_videos_now_shows_work_in_progress_and_whose_turn_it_is(tmp_path: Path) -> None:
    """A run that is rendering shows as working; a held run names the owner's step; a
    published or learning run is not listed."""
    studio, _ = _studio(tmp_path, [{"case_id": "c9", "reason_code": "THUMBNAIL_ART_AWAITED",
                                    "pipeline": {"workflow_id": "pipeline.b",
                                                 "run_state": "HELD"}}])
    studio.runs = lambda: [
        {"workflow_id": "pipeline.a", "state": "ASSEMBLING", "updated_at": "t1",
         "created_at": "t0"},
        {"workflow_id": "pipeline.b", "state": "HELD", "updated_at": "t2", "created_at": "t0"},
        {"workflow_id": "pipeline.c", "state": "LEARNING", "updated_at": "t3",
         "created_at": "t0"},
    ]
    videos = studio.overview()["videos"]
    assert [(v["workflow_id"], v["your_turn"]) for v in videos] == [
        ("pipeline.a", False), ("pipeline.b", True)]
    assert videos[0]["label"].startswith("Building the playlist and rendering")
    assert videos[1]["label"] == "Waiting for you: thumbnails (step 2)"
