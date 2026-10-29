"""Fail-closed legacy P6 audit-snapshot to current-DB reconciliation tests."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from scripts.p6_media_evidence import build_evidence
from scripts.reconcile_p6_media_evidence import reconcile_evidence

_SHA = "a" * 64
_CAPTURE = "2026-10-25T12:34:56+00:00"


def _row(**changes: Any) -> dict[str, Any]:
    row = {
        "row": 1, "declared_video_sha256": _SHA,
        "render_profile": "preview_360p_v1", "status": "measured_timing_consistent_only",
        "sha256_content_verified": True, "recorded_seconds": 10.0,
        "timeline_seconds": 10.0,
        "measured": {
            "audio_seconds": 10.0, "video_seconds": 10.0,
            "container_seconds": 10.0, "timeline_seconds": 10.0,
            "tolerance_seconds": 0.5,
        },
    }
    row.update(changes)
    return row


def _report(*rows: dict[str, Any]) -> dict[str, Any]:
    return build_evidence(
        entries=list(rows), ffmpeg_version="ffmpeg version 7.0",
        audit_code_sha256="b" * 64, captured_at_utc=_CAPTURE,
    )


def _db(**changes: Any) -> dict[str, Any]:
    record = {
        "video_checksum": _SHA, "status": "ready_for_publish",
        "render_profile": "preview_360p_v1",
        "video_duration_seconds": 10.0, "actual_duration_seconds": 10.0,
        "render_report_json": "{}",
    }
    record.update(changes)
    return record


def _reason(report: dict[str, Any], db: list[dict[str, Any]]) -> str:
    result = reconcile_evidence(report, db)
    assert result["publish_authorized"] is False
    assert result["db_or_media_modified"] is False
    assert result["snapshot_self_checksum_valid"] is True
    assert result["total"] == 1
    return result["outcomes"][0]["status"]


def test_matching_snapshot_still_does_not_remove_p7_legacy_hold() -> None:
    result = reconcile_evidence(_report(_row()), [_db()])
    assert result["counts"] == {"metadata_matched_legacy_p7_hold": 1}
    assert result["publish_authorized"] is False
    assert result["db_or_media_modified"] is False


@pytest.mark.parametrize(("row_changes", "db_changes", "reason"), [
    ({}, {"video_checksum": "c" * 64}, "checksum_absent_from_current_db"),
    ({}, {"status": "failed"}, "database_package_not_ready"),
    ({}, {"render_profile": "yt_longform_1080p_v1"}, "database_render_profile_changed"),
    ({}, {"video_duration_seconds": 10.5}, "database_container_record_changed"),
    ({}, {"actual_duration_seconds": 10.5}, "database_timeline_changed"),
    ({"recorded_seconds": 10.5}, {}, "database_container_record_changed"),
    ({"timeline_seconds": 10.5}, {}, "database_timeline_changed"),
    ({"status": "read_or_probe_error", "sha256_content_verified": False, "measured": None},
     {}, "snapshot_does_not_show_consistent_verified_media"),
    ({"measured": {"audio_seconds": 10.0}}, {}, "snapshot_measurements_incomplete"),
    ({"measured": {"audio_seconds": 10.0, "video_seconds": 10.0,
                    "container_seconds": 10.0, "timeline_seconds": 12.0}},
     {}, "snapshot_timeline_inconsistent"),
    ({"measured": {"audio_seconds": 10.0, "video_seconds": 10.0,
                    "container_seconds": 11.0, "timeline_seconds": 10.0}},
     {}, "snapshot_container_inconsistent"),
    ({"measured": {"audio_seconds": 8.0, "video_seconds": 10.0,
                    "container_seconds": 10.0, "timeline_seconds": 10.0,
                    "tolerance_seconds": 0.5}},
     {}, "snapshot_stream_timing_mismatch"),
    ({"measured": {"audio_seconds": 10.0, "video_seconds": 10.0,
                    "container_seconds": 10.0, "timeline_seconds": 10.0,
                    "tolerance_seconds": 20.0}},
     {}, "snapshot_stream_timing_mismatch"),
])
def test_every_mismatch_remains_nonapproving(
    row_changes: dict[str, Any], db_changes: dict[str, Any], reason: str,
) -> None:
    assert _reason(_report(_row(**row_changes)), [_db(**db_changes)]) == reason


def test_duplicate_current_db_digest_is_not_assumed_unique() -> None:
    assert _reason(_report(_row()), [_db(), _db()]) == "ambiguous_current_db_checksum"


def test_duplicate_snapshot_digest_is_refused() -> None:
    second = _row(row=2)
    result = reconcile_evidence(_report(_row(), second), [_db()])
    assert result["counts"] == {"duplicate_snapshot_checksum": 2}
    assert result["publish_authorized"] is False


def test_corrupted_or_repackaged_snapshot_is_rejected_before_database_matching() -> None:
    original = _report(_row())
    damaged = deepcopy(original)
    damaged["rows"][0]["declared_video_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="integrity"):
        reconcile_evidence(damaged, [_db()])
    assert original["rows"][0]["declared_video_sha256"] == _SHA
