"""The Studio runs P4's own song check on save and lists only running runs' sheets
(review 2026-11-26 #45, #52)."""

from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Any

import pytest
from plm_platform.dashboard.actions import ActionRefused

from tests.unit.test_dashboard_studio import _studio


def _wav(seconds: float) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(b"\x01\x00" * int(8000 * seconds))
    return buffer.getvalue()


def test_a_cut_short_or_too_long_song_is_refused_on_save(tmp_path: Path) -> None:
    studio, _ = _studio(tmp_path, [])
    sheet = tmp_path / "music" / "trk_1.prompts.txt"
    sheet.write_text(sheet.read_text(encoding="utf-8").replace(
        "Save as: trk_1-aaaa1111.wav\n",
        "Save as: trk_1-aaaa1111.wav\nAccepted length: 0:02 to 0:05\n"), encoding="utf-8")
    target = tmp_path / "music" / "trk_1-aaaa1111.wav"
    with pytest.raises(ActionRefused, match="cut short"):
        studio.save_song("trk_1-aaaa1111", _wav(3)[:-8000])
    assert not target.exists()
    with pytest.raises(ActionRefused, match="the accepted length is 0:02 to 0:05"):
        studio.save_song("trk_1-aaaa1111", _wav(6))
    assert not target.exists()
    assert studio.save_song("trk_1-aaaa1111", _wav(3))["saved"] == "trk_1-aaaa1111.wav"


class _Store:
    def __init__(self, runs: list[dict[str, Any]], tracks: dict[str, list[str]]) -> None:
        self._runs, self._tracks = runs, tracks

    def runs(self) -> list[dict[str, Any]]:
        return self._runs

    def track_ids(self, experiment_id: str) -> list[str]:
        return self._tracks.get(experiment_id, [])


def test_only_sheets_of_running_runs_are_listed(tmp_path: Path) -> None:
    studio, _ = _studio(tmp_path, [])
    (tmp_path / "music" / "trk_old.prompts.txt").write_text(
        "old\n--- Song 1 ---\nSave as: trk_old-v1.wav\nInstrumental: yes\n", encoding="utf-8")
    studio.store = _Store([{"state": "HELD", "experiment_id": "exp_new"},
                           {"state": "CANCELLED", "experiment_id": "exp_old"}],
                          {"exp_new": ["trk_1"], "exp_old": ["trk_old"]})
    assert [s["sheet"] for s in studio.songs()["sheets"]] == ["trk_1.prompts.txt"]
    with pytest.raises(ActionRefused, match="song_not_awaited"):
        studio.save_song("trk_old-v1", _wav(3))
    assert (tmp_path / "music" / "cancelled" / "trk_old" / "trk_old.prompts.txt").is_file()
    studio.store = None  # no project store: every sheet in the inbox with a song missing
    (tmp_path / "music" / "trk_two.prompts.txt").write_text(
        "--- Song 1 ---\nSave as: trk_two-v1.wav\nInstrumental: yes\n", encoding="utf-8")
    assert len(studio.songs()["sheets"]) == 2


def test_a_finished_runs_songs_move_and_are_still_shown_and_played(tmp_path: Path) -> None:
    studio, _ = _studio(tmp_path, [])
    music = tmp_path / "music"
    sheet = music / "trk_1.prompts.txt"
    sheet.write_text("Plan: gen_plan_1\n" + sheet.read_text(encoding="utf-8"), encoding="utf-8")
    (music / "trk_1-aaaa1111.wav").write_bytes(_wav(3))
    (music / "trk_old.prompts.txt").write_text(
        "--- Song 1 ---\nSave as: trk_old-v1.wav\nInstrumental: yes\n", encoding="utf-8")
    (music / "trk_old-v1.wav").write_bytes(_wav(2))
    studio.store = _Store([{"state": "CANCELLED", "experiment_id": "exp_1"},
                           {"state": "PUBLISHED", "experiment_id": "exp_old"}],
                          {"exp_1": ["trk_1"], "exp_old": ["trk_old"]})
    assert studio.songs()["sheets"] == []
    cancelled, imported = music / "cancelled" / "gen_plan_1", music / "imported" / "trk_old"
    assert sorted(p.name for p in cancelled.iterdir()) == [
        "trk_1-aaaa1111.wav", "trk_1.prompts.txt"]
    assert sorted(p.name for p in imported.iterdir()) == ["trk_old-v1.wav",
                                                          "trk_old.prompts.txt"]
    assert sorted(p.name for p in music.iterdir()) == ["cancelled", "imported"]
    assert studio.song_file("trk_1-aaaa1111") == cancelled / "trk_1-aaaa1111.wav"
    assert studio.song_file("trk_old-v1") == imported / "trk_old-v1.wav"
    (shown,) = studio._sheets("Flow Music", music, tracks={"trk_1"}, include_done=True)
    assert [(s["stem"], s["saved"]) for s in shown["songs"]] == [
        ("trk_1-aaaa1111", True), ("trk_1-bbbb2222", False)]
    studio.songs()  # nothing left to move: idempotent
    assert sorted(p.name for p in cancelled.iterdir()) == [
        "trk_1-aaaa1111.wav", "trk_1.prompts.txt"]
