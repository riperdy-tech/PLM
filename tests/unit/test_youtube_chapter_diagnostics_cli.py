"""Offline CLI smoke check: video diagnostic must work without OAuth or external writes."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.diagnose_youtube_chapters import main


def test_offline_video_diagnostics(tmp_path: Path, capsys) -> None:
    resource = {
        "items": [{
            "id": "album_test",
            "snippet": {"description": "0:00 Intro\n5:00 Interlude\n10:00 Outro"},
            "contentDetails": {"duration": "PT15M"},
            "processingDetails": {"processingStatus": "succeeded"},
            "status": {"privacyStatus": "public"},
        }]
    }
    summary = {"video_id": "album_test", "tracks": [
        {"title": "Intro", "start_seconds": 0},
        {"title": "Interlude", "start_seconds": 300},
        {"title": "Outro", "start_seconds": 600},
    ]}
    api_json, source_json = tmp_path / "api.json", tmp_path / "album.json"
    api_json.write_text(json.dumps(resource), encoding="utf-8")
    source_json.write_text(json.dumps(summary), encoding="utf-8")

    result = main(["album_test", "--api-json", str(api_json), "--summary", str(source_json)])
    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["description_chapters_valid"]
    assert output["matches_source_timeline"]
    assert output["player_chapters_visibility"] == "UNVERIFIED"


def test_cli_refuses_summary_from_unrelated_video(tmp_path: Path, capsys) -> None:
    source_json = tmp_path / "other.json"
    source_json.write_text(json.dumps({"video_id": "different", "tracks": []}), encoding="utf-8")
    result = main(["album_test", "--summary", str(source_json)])
    assert result == 2
    assert "does not match" in capsys.readouterr().err
