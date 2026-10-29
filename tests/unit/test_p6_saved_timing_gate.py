"""Legacy P6 QA evidence cannot be laundered by a READY C6 status at P7."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest
from plm_platform.media_timing import classify_saved_media_timing
from youtube_publishing.client.mock import MockYouTubeClient

from scripts.audit_p6_ready_timing import audit_rows
from tests.support.seams import media_package
from tests.unit.test_youtube_api_r5 import HEADERS, _app


def _ready() -> dict[str, Any]:
    return media_package().to_wire()


def test_clean_complete_report_remains_publishable() -> None:
    pkg = _ready()
    assert classify_saved_media_timing(
        pkg["status"], pkg["video"]["duration_seconds"], pkg["render_report"],
    ) == "saved_stream_evidence_consistent"
    assert _app().post(
        "/youtube/publish", headers=HEADERS,
        json={"channel_id": "UC_r5", "operating_mode": "dry_run", "media_package": pkg},
    ).status_code == 200


@pytest.mark.parametrize(("edit", "reason"), [
    ("missing_all", "not_qa_passed"),
    ("missing_streams", "missing_stream_evidence"),
    ("missing_audio_duration", "missing_stream_evidence"),
    ("null_video_duration", "missing_stream_evidence"),
    ("audio_shorter", "saved_timing_mismatch"),
    ("container_disagrees", "saved_timing_mismatch"),
    ("qa_failed", "not_qa_passed"),
    ("qa_reason_codes", "not_qa_passed"),
    ("missing_timeline", "missing_timing_reference"),
    ("wide_tolerance", "missing_timing_reference"),
])
def test_p7_refuses_ready_package_with_unverifiable_saved_timing(
    edit: str, reason: str,
) -> None:
    pkg = deepcopy(_ready())
    report = pkg["render_report"]
    if edit == "missing_all":
        pkg["render_report"] = {}
    elif edit == "missing_streams":
        report["qa"]["metrics"]["probe"].pop("streams")
    elif edit == "missing_audio_duration":
        report["qa"]["metrics"]["probe"]["streams"][1].pop("duration_seconds")
    elif edit == "null_video_duration":
        report["qa"]["metrics"]["probe"]["streams"][0]["duration_seconds"] = None
    elif edit == "audio_shorter":
        report["qa"]["metrics"]["probe"]["streams"][1]["duration_seconds"] -= 20.0
    elif edit == "container_disagrees":
        pkg["video"]["duration_seconds"] += 20.0
    elif edit == "qa_failed":
        report["qa"]["passed"] = False
    elif edit == "qa_reason_codes":
        report["qa"]["reason_codes"] = ["stream_duration_mismatch"]
    elif edit == "missing_timeline":
        report.pop("timeline_seconds")
    elif edit == "wide_tolerance":
        report["render_profile"]["duration_tolerance_seconds"] = 30.0

    result = classify_saved_media_timing(
        pkg["status"], pkg["video"]["duration_seconds"], pkg["render_report"],
    )
    assert result == reason
    youtube = MockYouTubeClient()
    response = _app(client=youtube).post(
        "/youtube/publish", headers=HEADERS,
        json={"channel_id": "UC_r5", "operating_mode": "dry_run", "media_package": pkg},
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "package_not_ready"
    assert reason in response.json()["detail"]["message"]
    assert youtube.recorded_calls == []


def test_readonly_audit_reports_only_category_counts() -> None:
    report = _ready()["render_report"]
    rows = [
        ("ready_for_publish", 150.0, json.dumps(report)),
        ("ready_for_publish", 150.0, "{}"),
        ("ready_for_publish", 150.0, "{"),
        ("failed", 150.0, "{}"),
    ]
    assert audit_rows(rows) == {
        "not_qa_passed": 1,
        "not_ready": 1,
        "saved_stream_evidence_consistent": 1,
        "unreadable_report": 1,
    }
