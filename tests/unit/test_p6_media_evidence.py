"""Evidence snapshots are separate from any P6/P7 authorization or media write."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.p6_media_evidence import build_evidence, verify_evidence, write_local_evidence

_CAPTURE = "2026-10-25T12:34:56+00:00"
_SOURCE_SHA = "a" * 64
_SCRIPT_SHA = "b" * 64


def _row(status: str = "measured_timing_consistent_only") -> dict[str, object]:
    checked = status == "measured_timing_consistent_only"
    return {
        "row": 1, "declared_video_sha256": _SOURCE_SHA,
        "render_profile": "preview_360p_v1",
        "status": status, "sha256_content_verified": checked,
        "recorded_seconds": 10.0, "timeline_seconds": 10.0,
        "measured": {
            "audio_seconds": 10.0, "video_seconds": 10.0,
            "container_seconds": 10.0, "timeline_seconds": 10.0,
            "tolerance_seconds": 0.5,
        } if checked else None,
    }


def _document(*rows: dict[str, object]) -> dict[str, object]:
    return build_evidence(
        entries=list(rows), ffmpeg_version="ffmpeg version 7.0",
        audit_code_sha256=_SCRIPT_SHA, captured_at_utc=_CAPTURE,
    )


def test_evidence_is_deterministic_canonical_and_has_explicit_nonapproval() -> None:
    a = _document(_row())
    b = _document(_row())
    assert a == b and verify_evidence(a)
    assert a["publish_authorized"] is False
    assert a["counts"] == {"measured_timing_consistent_only": 1}


def test_mismatched_and_unknown_evidence_cannot_silently_pass() -> None:
    document = _document(_row("unrecognized_render_profile"))
    assert document["counts"] == {"unrecognized_render_profile": 1}
    assert verify_evidence(document)
    for change in ("time", "verified", "hash", "scope", "approval", "counter"):
        tampered = deepcopy(document)
        if change == "time":
            tampered["rows"][0]["timeline_seconds"] = 12.0
        elif change == "verified":
            tampered["rows"][0]["sha256_content_verified"] = True
        elif change == "hash":
            tampered["rows"][0]["declared_video_sha256"] = "e" * 64
        elif change == "scope":
            tampered["scope"] = "published"
        elif change == "approval":
            tampered["publish_authorized"] = True
        else:
            tampered["counts"] = {}
        assert not verify_evidence(tampered)


@pytest.mark.parametrize("name", [
    "../escape.json", "not-json.txt", "/tmp/leak.json", r"..\\leak.json",
    "subfolder/name.json",
])
def test_evidence_cannot_escape_ignored_workspace(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="filename"):
        write_local_evidence(workspace=tmp_path, filename=name, document=_document(_row()))


def test_evidence_writer_is_exclusive_and_detects_on_disk_change(tmp_path: Path) -> None:
    document = _document(_row())
    dest = write_local_evidence(
        workspace=tmp_path, filename="probe-1.json", document=document,
    )
    assert dest.parent == tmp_path / "output_media" / "audits"
    assert verify_evidence(__import__("json").loads(dest.read_text(encoding="utf-8")))
    with pytest.raises(FileExistsError):
        write_local_evidence(workspace=tmp_path, filename="probe-1.json", document=document)
    dest.write_text(dest.read_text(encoding="utf-8").replace("10.0", "12.0", 1),
                    encoding="utf-8")
    assert not verify_evidence(__import__("json").loads(dest.read_text(encoding="utf-8")))
