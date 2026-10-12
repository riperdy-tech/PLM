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


def _sample_channel(active: bool = True) -> ChannelProfile:
    now = datetime.now(UTC)
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
    res = evaluate_gate_a_channel(_sample_channel(active=True))
    assert res.passed
    assert len(res.diagnostics) == 0


def test_gate_a_channel_fails_when_profile_missing_or_inactive() -> None:
    res_none = evaluate_gate_a_channel(None)
    assert not res_none.passed
    assert "channel profile not found" in res_none.diagnostics

    res_inactive = evaluate_gate_a_channel(_sample_channel(active=False))
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
            {"id": "thumb_01", "uri": "media/thumb/01.jpg"},
        ],
    }
    assert evaluate_gate_b_artifact(valid_payload).passed

    # Missing video
    assert not evaluate_gate_b_artifact({}).passed

    # Zero duration
    bad_duration = {
        "video": {"master_uri": "media/video/track.mp4", "checksum": "chk_video_valid", "duration_seconds": 0.0},
        "thumbnails": [{"id": "thumb_01", "uri": "media/thumb/01.jpg"}],
    }
    assert not evaluate_gate_b_artifact(bad_duration).passed

    # Missing thumbnails
    no_thumbs = {
        "video": {"master_uri": "media/video/track.mp4", "checksum": "chk_video_valid", "duration_seconds": 120.0},
        "thumbnails": [],
    }
    assert not evaluate_gate_b_artifact(no_thumbs).passed


def test_gate_c_metadata_limits_and_category() -> None:
    valid_meta = {
        "title": "2AM Chill Beats | Lofi Study Music",
        "description": "Relaxing playlist for deep focus.",
        "tags": ["lofi", "beats", "study"],
        "category_id": "10",
    }
    assert evaluate_gate_c_metadata(valid_meta).passed

    # Title exceeds 100 chars
    long_title = dict(valid_meta, title="A" * 101)
    res_title = evaluate_gate_c_metadata(long_title)
    assert not res_title.passed
    assert any("exceeds YouTube 100-character limit" in d for d in res_title.diagnostics)

    # Description exceeds 5000 chars
    long_desc = dict(valid_meta, description="B" * 5001)
    res_desc = evaluate_gate_c_metadata(long_desc)
    assert not res_desc.passed
    assert any("exceeds YouTube 5000-character limit" in d for d in res_desc.diagnostics)

    # Unsupported category
    bad_cat = dict(valid_meta, category_id="22")
    assert not evaluate_gate_c_metadata(bad_cat).passed

    # Past scheduled date
    past_date = datetime.now(UTC) - timedelta(hours=1)
    assert not evaluate_gate_c_metadata(valid_meta, publish_at=past_date).passed
