"""Unit tests for P7 preflight validation gates A, B, and C."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from youtube_publishing.domain.enums import OperatingMode, PrivacyStatus
from youtube_publishing.domain.models import ChannelProfile
from youtube_publishing.preflight.gates import (
    evaluate_gate_a_channel,
    evaluate_gate_b_artifact,
    evaluate_gate_c_metadata,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _sample_channel(active: bool = True) -> ChannelProfile:
    now = NOW
    return ChannelProfile(
        id="prof_01",
        channel_id="UC_sample",
        channel_name="Sample Music Channel",
        brand_id="brand_sample",
        oauth_credential_ref="sec_oauth_sample",
        default_privacy_mode=PrivacyStatus.PRIVATE,
        active=active,
        operating_mode=OperatingMode.DRY_RUN,
        created_at=now,
        updated_at=now,
    )


def test_gate_a_channel_passes_for_valid_profile() -> None:
    res = evaluate_gate_a_channel(_sample_channel(active=True), now=NOW)
    assert res.passed
    assert len(res.diagnostics) == 0


def test_gate_a_channel_fails_when_profile_missing_or_inactive() -> None:
    res_none = evaluate_gate_a_channel(None, now=NOW)
    assert not res_none.passed
    assert "channel profile not found" in res_none.diagnostics

    res_inactive = evaluate_gate_a_channel(_sample_channel(active=False), now=NOW)
    assert not res_inactive.passed
    assert "channel profile is inactive" in res_inactive.diagnostics


def test_gate_b_artifact_validates_video_and_thumbnails() -> None:
    valid_payload = {
        "video": {
            "master_uri": "media/video/track.mp4",
            "checksum": "chk_video_valid",
            "duration_seconds": 120.0,
        },
        "thumbnails": [
            {"id": "thumb_01", "uri": "media/thumb/01.jpg", "checksum": "a" * 64},
        ],
    }
    assert evaluate_gate_b_artifact(valid_payload, now=NOW).passed

    # Missing video
    assert not evaluate_gate_b_artifact({}, now=NOW).passed

    # Zero duration
    bad_duration = {
        "video": {"master_uri": "media/video/track.mp4", "checksum": "chk_video_valid", "duration_seconds": 0.0},
        "thumbnails": [{"id": "thumb_01", "uri": "media/thumb/01.jpg", "checksum": "a" * 64}],
    }
    assert not evaluate_gate_b_artifact(bad_duration, now=NOW).passed

    # Missing thumbnails
    no_thumbs = {
        "video": {"master_uri": "media/video/track.mp4", "checksum": "chk_video_valid", "duration_seconds": 120.0},
        "thumbnails": [],
    }
    assert not evaluate_gate_b_artifact(no_thumbs, now=NOW).passed


def test_gate_b_checks_selected_thumbnail_not_just_first() -> None:
    package = {
        "video": {"master_uri": "media/test.mp4", "checksum": "a" * 64,
                  "duration_seconds": 120.0},
        "thumbnails": [
            {"id": "broken", "uri": "", "checksum": ""},
            {"id": "valid", "uri": "media/valid.png", "checksum": "b" * 64},
        ],
    }
    assert evaluate_gate_b_artifact(
        package, now=NOW, selected_thumbnail_id="valid",
    ).passed
    assert not evaluate_gate_b_artifact(
        package, now=NOW, selected_thumbnail_id="missing",
    ).passed


def test_gate_c_metadata_limits_and_category() -> None:
    valid_meta = {
        "title": "2AM Chill Beats | Lofi Study Music",
        "description": "Relaxing playlist for deep focus.",
        "tags": ["lofi", "beats", "study"],
        "category_id": "10",
    }
    assert evaluate_gate_c_metadata(valid_meta, now=NOW).passed

    # Title exceeds 100 chars
    long_title = dict(valid_meta, title="A" * 101)
    res_title = evaluate_gate_c_metadata(long_title, now=NOW)
    assert not res_title.passed
    assert any("exceeds YouTube 100-character limit" in d for d in res_title.diagnostics)

    # Description exceeds 5000 chars
    long_desc = dict(valid_meta, description="B" * 5001)
    res_desc = evaluate_gate_c_metadata(long_desc, now=NOW)
    assert not res_desc.passed
    assert any("exceeds YouTube 5000-character limit" in d for d in res_desc.diagnostics)

    # Unsupported category
    bad_cat = dict(valid_meta, category_id="22")
    assert not evaluate_gate_c_metadata(bad_cat, now=NOW).passed

    # Past scheduled date
    past_date = NOW - timedelta(hours=1)
    assert not evaluate_gate_c_metadata(valid_meta, publish_at=past_date, now=NOW).passed


def test_gate_c_schedule_window_is_judged_against_the_injected_now() -> None:
    meta = {"title": "Lofi", "description": "", "tags": [], "category_id": "10"}
    publish_at = NOW + timedelta(hours=1)

    future = evaluate_gate_c_metadata(meta, publish_at=publish_at, now=NOW)
    assert future.passed
    assert future.checked_at == NOW

    later = NOW + timedelta(hours=2)
    stale = evaluate_gate_c_metadata(meta, publish_at=publish_at, now=later)
    assert not stale.passed
    assert any("must be in the future" in d for d in stale.diagnostics)
    assert stale.checked_at == later


def test_gate_c_refuses_a_naive_schedule() -> None:
    meta = {"title": "Lofi", "description": "", "tags": [], "category_id": "10"}
    naive = datetime(2030, 1, 1, 12, 0)
    res = evaluate_gate_c_metadata(meta, publish_at=naive, now=NOW)
    assert not res.passed
    assert any("timezone-aware" in d for d in res.diagnostics)


def test_gates_stamp_checked_at_with_the_injected_now() -> None:
    assert evaluate_gate_a_channel(None, now=NOW).checked_at == NOW
    assert evaluate_gate_b_artifact({}, now=NOW).checked_at == NOW
