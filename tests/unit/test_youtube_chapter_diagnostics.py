"""Regression checks for read-only post-publication chapter diagnostics."""
from __future__ import annotations

import json
from datetime import UTC, datetime

from youtube_publishing.diagnostics import inspect_uploaded_video

NOW = datetime(2026, 10, 23, tzinfo=UTC)
TRACKS = [
    {"title": "Opening", "start_seconds": 0.0},
    {"title": "Middle", "start_seconds": 600.0},
    {"title": "Ending", "start_seconds": 1200.0},
]


def _resource(description: str | None = None) -> dict:
    return {
        "id": "live_123",
        "snippet": {"description": description if description is not None else (
            "Album mix\n\n00:00 - Opening\n10:00 - Middle\n20:00 - Ending\n"
        )},
        "status": {"privacyStatus": "public"},
        "processingDetails": {"processingStatus": "succeeded"},
        "contentDetails": {"duration": "PT30M"},
    }


def test_published_metadata_and_album_timeline_match_but_player_is_unverified() -> None:
    report = inspect_uploaded_video(_resource(), now=NOW, expected_tracks=TRACKS)
    assert report["description_chapters_valid"] is True, report["findings"]
    assert report["matches_source_timeline"] is True
    assert report["chapter_count"] == 3
    assert report["duration_seconds"] == 1800.0
    assert report["processing_status"] == "succeeded"
    assert report["player_chapters_visibility"] == "UNVERIFIED"


def test_detects_missing_chapters_even_after_successful_processing() -> None:
    report = inspect_uploaded_video(_resource("Album mix"), now=NOW, expected_tracks=TRACKS)
    assert not report["description_chapters_valid"]
    assert report["matches_source_timeline"] is False
    assert report["chapter_count"] == 0


def test_detects_different_titles_on_uploaded_description() -> None:
    resource = _resource()
    resource["snippet"]["description"] = resource["snippet"]["description"].replace(
        "Middle", "Different track"
    )
    report = inspect_uploaded_video(resource, now=NOW, expected_tracks=TRACKS)
    assert not report["description_chapters_valid"]
    assert report["matches_source_timeline"] is False


def test_detects_invalid_chapter_spacing_without_original_manifest() -> None:
    desc = "00:00 Opening\n00:05 Middle\n20:00 Ending"
    report = inspect_uploaded_video(_resource(desc), now=NOW)
    assert not report["description_chapters_valid"]
    assert report["matches_source_timeline"] is None
    assert any("10 seconds" in finding for finding in report["findings"])


def test_no_duration_never_passes_without_evidence() -> None:
    resource = _resource()
    resource["contentDetails"] = {}
    report = inspect_uploaded_video(resource, now=NOW)
    assert not report["description_chapters_valid"]
    assert "YouTube contentDetails.duration missing or invalid" in report["findings"]


def test_hour_timestamp_parsing_matches_original_seconds() -> None:
    resource = _resource("0:00 Start\n30:00 Middle\n1:00:00 End")
    resource["contentDetails"] = {"duration": "PT1H5M"}
    tracks = [
        {"title": "Start", "start_seconds": 0},
        {"title": "Middle", "start_seconds": 1800},
        {"title": "End", "start_seconds": 3600},
    ]
    result = inspect_uploaded_video(resource, now=NOW, expected_tracks=tracks)
    assert result["description_chapters_valid"], json.dumps(result)


def test_processing_pending_never_claims_player_verified() -> None:
    resource = _resource()
    resource["processingDetails"] = {"processingStatus": "processing"}
    result = inspect_uploaded_video(resource, now=NOW, expected_tracks=TRACKS)
    assert result["description_chapters_valid"]
    assert result["player_chapters_visibility"] == "UNVERIFIED"
    assert result["processing_status"] == "processing"
