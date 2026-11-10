"""Studio projects (owner 2026-11-09/10): numbered projects, every step kept, a plain log,
stops explained with what to do, songs replaced from the thumbnail step, cancel, and the
picture import running on its own with a time estimate.

Fakes stand in for the project store, the exception actions and P6; the inbox files are
real files in a temporary folder.
"""

from __future__ import annotations

import asyncio
import struct
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from plm_platform.dashboard.actions import ActionRefused
from plm_platform.dashboard.projects import explain_hold, number_runs, plain_log
from plm_platform.dashboard.studio import wav_seconds

from tests.unit.test_dashboard_studio import PKG, _studio

NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)


def _wav(seconds: float, rate: int = 48000) -> bytes:
    data = b"\x00" * int(seconds * rate * 4)
    fmt = struct.pack("<HHIIHH", 1, 2, rate, rate * 4, 4, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"LIST" + struct.pack("<I", 4) + b"INFO"
            + b"data" + struct.pack("<I", len(data)) + data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_wav_length_is_read_from_the_header_past_other_chunks(tmp_path: Path) -> None:
    path = tmp_path / "s.wav"
    path.write_bytes(_wav(2.5))
    assert wav_seconds(path) == 2.5
    path.write_bytes(b"not a wav")
    assert wav_seconds(path) is None


def test_projects_are_numbered_by_start_and_test_runs_are_not_projects() -> None:
    runs = [{"workflow_id": "pipeline.b", "created_at": "2026-11-09T10:00:00+00:00"},
            {"workflow_id": "pipeline.gate-x", "created_at": "2026-11-01T10:00:00+00:00"},
            {"workflow_id": "pipeline.a", "created_at": "2026-11-01T09:00:00+00:00"}]
    assert [(r["workflow_id"], r["number"]) for r in number_runs(runs)] == [
        ("pipeline.a", 1), ("pipeline.b", 2)]


def _t(seq: int, to: str, **kw: Any) -> dict[str, Any]:
    return {"sequence": seq, "occurred_at": f"2026-11-09T10:{seq:02d}:00+00:00",
            "to_state": to, "from_state": kw.pop("frm", None), **kw}


def test_the_log_tells_the_story_in_plain_words() -> None:
    log = plain_log([
        _t(1, "IDEA"),
        _t(2, "HELD", frm="GENERATING", step="generate", error_code="manual_audio_awaited"),
        _t(3, "GENERATING", frm="HELD", outputs_json='{"actor": "owner", '
           '"evidence_keys": ["manual_audio_inbox_ref"], "verdicts": []}'),
        _t(4, "GENERATED", frm="GENERATING", step="generate",
           outputs_json='{"candidate_ids": ["a", "b"]}'),
        _t(5, "PASSED", frm="QA", step="qa", outputs_json='{"evaluations": ['
           '{"disposition": "PASSED"}, {"disposition": "QUARANTINED"}]}'),
        _t(6, "HELD", frm="ASSEMBLING", step="assemble", error_code="STEP_PLATFORM_FAULT",
           error_detail="the visual world was used"),
    ])
    assert [(line["kind"], line["text"]) for line in log] == [
        ("step", "Project started"),
        ("wait", "Waiting for you: songs"),
        ("you", "You sent the songs"),
        ("done", "2 song(s) received"),
        ("done", "Songs checked: 1 of 2 passed"),
        ("problem", "Stopped: step platform fault"),
    ]
    assert log[-1]["detail"] == "the visual world was used"


def test_a_stop_says_what_happened_and_whether_the_owner_may_act() -> None:
    case = {"case_id": "c1", "reason_code": "BUDGET_RESERVATION_REFUSED",
            "reviewer_role": "budget_owner", "details": {"error": "upload replayed"}}
    told = explain_hold(case, owner_roles={"production_owner"})
    assert told["title"].startswith("Stopped:") and told["detail"] == "upload replayed"
    assert told["action"] == "retry" and told["owner_may_act"] is False
    assert "budget_owner" in told["blocked_because"]
    yours = explain_hold({"reason_code": "THUMBNAIL_ART_AWAITED",
                          "reviewer_role": "production_owner"},
                         owner_roles={"production_owner"})
    assert yours["your_turn"] is True and yours["action"] == "art"


class FakeStore:
    def __init__(self, held: bool = True) -> None:
        self.held = held

    def runs(self) -> list[dict[str, Any]]:
        return [{"workflow_id": "pipeline.old", "state": "CANCELLED",
                 "created_at": "2026-11-01T00:00:00+00:00",
                 "updated_at": "2026-11-01T00:00:00+00:00", "experiment_id": "exp_old"},
                {"workflow_id": "pipeline.now", "state": "HELD" if self.held else "ASSEMBLING",
                 "created_at": "2026-11-09T00:00:00+00:00",
                 "updated_at": "2026-11-09T01:00:00+00:00", "experiment_id": "exp_now",
                 "style": "minimal_pulse", "length": [5400, 7200], "title": "Night Shift",
                 "media_package_id": PKG, "youtube": None}]

    def transitions(self, workflow_id: str) -> list[dict[str, Any]]:
        return [_t(1, "IDEA")]

    def track_ids(self, experiment_id: str) -> list[str]:
        return ["trk_1"]

    def packages(self, experiment_id: str) -> list[dict[str, Any]]:
        return [{"media_package_id": PKG, "status": "awaiting_thumbnail_art"}]


ART_HOLD = {"case_id": "c-art", "reason_code": "THUMBNAIL_ART_AWAITED",
            "reviewer_role": "production_owner", "required_evidence": ["thumbnail_art_inbox_ref"],
            "details": {"media_package_ids": [PKG]},
            "pipeline": {"run_state": "HELD", "workflow_id": "pipeline.now"}}


def _project_studio(tmp_path: Path, holds: list[dict[str, Any]]) -> Any:
    studio, actions = _studio(tmp_path, holds)
    studio.open_holds = lambda: holds
    studio.store = FakeStore()
    return studio, actions


def test_a_project_page_keeps_every_step_and_its_finished_songs(tmp_path: Path) -> None:
    """Owner: prompts and songs must not disappear once done; every project has a number."""
    studio, _ = _project_studio(tmp_path, [ART_HOLD])
    (tmp_path / "music" / "trk_1-aaaa1111.wav").write_bytes(_wav(1.0))
    (tmp_path / "music" / "trk_1-bbbb2222.wav").write_bytes(_wav(2.0))
    listed = studio.projects()
    assert [p["number"] for p in listed] == [2, 1]  # newest first
    assert listed[0]["your_turn"] is True and listed[0]["style_label"] == "Minimal pulse"
    page = studio.project(2)
    (sheet,) = page["songs"]["sheets"]  # every song saved, still shown
    assert [s["seconds"] for s in sheet["songs"]] == [1.0, 2.0]
    assert page["art_waiting"] is True and page["can_replace_songs"] is True
    assert [v["letter"] for v in page["art"][0]["variants"]] == ["A", "B", "C"]
    assert page["hold"]["title"].startswith("Waiting for you")
    assert page["log"][0]["text"] == "Project started"
    with pytest.raises(ActionRefused, match="project_unknown"):
        studio.project(9)


def test_songs_are_replaced_from_the_thumbnail_step(tmp_path: Path) -> None:
    """Owner: at the image step, go back and change a song. The run rewinds to assembly
    without it (the workflow then asks for a new one)."""
    studio, actions = _project_studio(tmp_path, [ART_HOLD])
    with pytest.raises(ActionRefused, match="song_not_in_package"):
        asyncio.run(studio.replace_project_songs(2, ["cand_9"], ""))
    asyncio.run(studio.replace_project_songs(2, ["cand_1"], "too bright"))
    (call,) = actions.calls
    assert call[1] == "c-art" and call[2]["rewind_to"] == "assemble"
    assert call[2]["exclude_candidates"] == ("cand_1",)
    assert "thumbnail_art_inbox_ref" in call[2]
    assert studio.notes()[-1]["kind"] == "replace_song"


def test_a_stop_is_retried_only_by_a_role_the_owner_holds(tmp_path: Path) -> None:
    fault = {"case_id": "c-f", "reason_code": "STEP_PLATFORM_FAULT",
             "reviewer_role": "platform_owner", "required_evidence": ["platform_fix_ref"],
             "details": {"error": "P6 refused"},
             "pipeline": {"run_state": "HELD", "workflow_id": "pipeline.now"}}
    studio, actions = _project_studio(tmp_path, [fault])
    with pytest.raises(ActionRefused, match="owner_lacks_role"):
        asyncio.run(studio.retry_hold("c-f"))

    class Reviewers:
        def roles_of(self, actor: str) -> frozenset[str]:
            return frozenset({"platform_owner"})

    actions.reviewers = Reviewers()  # type: ignore[attr-defined]
    asyncio.run(studio.retry_hold("c-f"))
    (call,) = actions.calls
    assert call[2]["role"] == "platform_owner" and "P6 refused" in call[2]["platform_fix_ref"]
    with pytest.raises(ActionRefused, match="hold_not_open"):
        asyncio.run(studio.retry_hold("nope"))


def test_cancel_stops_an_unfinished_project_only(tmp_path: Path) -> None:
    studio, _ = _project_studio(tmp_path, [ART_HOLD])
    sent: list[tuple[str, str]] = []

    async def cancel(workflow_id: str, reason: str) -> None:
        sent.append((workflow_id, reason))

    studio.cancel = cancel
    asyncio.run(studio.cancel_project(2, "wrong style"))
    assert sent == [("pipeline.now", "wrong style")]
    with pytest.raises(ActionRefused, match="project_finished"):
        asyncio.run(studio.cancel_project(1))


def test_the_picture_import_runs_on_its_own_with_a_time_estimate(tmp_path: Path) -> None:
    """Owner: the import looked stuck. It runs in the background; the project shows it
    running with an estimate (a quarter of the video's length) and then its result."""
    studio, actions = _project_studio(tmp_path, [ART_HOLD])

    async def run() -> dict[str, Any]:
        started = await studio.import_thumbnails(PKG)
        assert studio.project(2)["working"]["kind"] == "import"
        await asyncio.gather(*studio._tasks)
        return started

    started = asyncio.run(run())
    assert started["state"] == "running"
    assert started["expected_seconds"] == round(1425.8 * 0.25) + 60
    assert studio._imports[PKG]["state"] == "done"
    assert [c[1] for c in actions.calls] == ["c-art"]


def test_start_takes_a_length_choice(tmp_path: Path) -> None:
    studio, _ = _studio(tmp_path, [])
    (tmp_path / "suno").mkdir()
    studio.paths = type(studio.paths)(music_inbox=studio.paths.music_inbox,
                                      art_inbox=studio.paths.art_inbox,
                                      grants_file=studio.paths.grants_file,
                                      suno_inbox=tmp_path / "suno")
    seen: list[str] = []

    async def start(provider: str, subgenre: str | None, avoid: tuple[str, ...],
                    length: str) -> dict[str, Any]:
        seen.append(length)
        return {}

    studio.start = start
    asyncio.run(studio.start_run("suno", None, "90_120m"))
    assert seen == ["90_120m"]
    with pytest.raises(ActionRefused, match="length_unknown"):
        asyncio.run(studio.start_run("suno", None, "5_10m"))
