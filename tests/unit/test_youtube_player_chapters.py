"""P7 upload must never promise player chapters that the description cannot produce."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from youtube_publishing.preflight.gates import evaluate_gate_d_chapters

NOW = datetime(2026, 10, 23, tzinfo=UTC)


def _package() -> dict:
    return {
        "video": {"duration_seconds": 1800},
        "tracklist": [{"song_id": str(n)} for n in range(3)],
        "chapters": [
            {"start_seconds": n * 600, "timestamp_str": f"{n * 10:02d}:00",
             "title": f"Track {n + 1}"}
            for n in range(3)
        ],
    }


def _metadata() -> dict:
    return {"description": (
        "Listening session\n\nTracklist:\n"
        "00:00 - Track 1\n10:00 - Track 2\n20:00 - Track 3\n"
    )}


def test_longform_chapters_match_the_description() -> None:
    result = evaluate_gate_d_chapters(_package(), _metadata(), now=NOW)
    assert result.passed, result.diagnostics
    assert result.checked_at == NOW


@pytest.mark.parametrize("problem", [
    "missing", "wrong_title", "wrong_start", "short_segment", "nonzero_first",
])
def test_longform_chapter_mismatches_block_publication(problem: str) -> None:
    package = _package()
    metadata = _metadata()
    if problem == "missing":
        package["chapters"] = []
    elif problem == "wrong_title":
        metadata["description"] = metadata["description"].replace("Track 2", "Wrong track")
    elif problem == "wrong_start":
        package["chapters"][1]["timestamp_str"] = "11:00"
    elif problem == "short_segment":
        package["chapters"][1]["start_seconds"] = 1195
        package["chapters"][1]["timestamp_str"] = "19:55"
        metadata["description"] = metadata["description"].replace("10:00", "19:55")
    else:
        package["chapters"][0]["start_seconds"] = 1
        package["chapters"][0]["timestamp_str"] = "00:01"
        metadata["description"] = metadata["description"].replace("00:00", "00:01")
    result = evaluate_gate_d_chapters(package, metadata, now=NOW)
    assert not result.passed
    assert result.diagnostics


def test_short_single_track_legacy_video_need_not_have_chapters() -> None:
    package = {"video": {"duration_seconds": 150}, "tracklist": [{"song_id": "1"}],
               "chapters": []}
    assert evaluate_gate_d_chapters(package, {"description": "One track"}, now=NOW).passed
