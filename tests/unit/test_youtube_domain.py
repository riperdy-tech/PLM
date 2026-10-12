"""Unit tests for P7 domain models, enums, and ID generation."""

from __future__ import annotations

from datetime import UTC, datetime

from youtube_publishing.domain.enums import (
    PublishState,
)
from youtube_publishing.domain.ids import (
    build_attempt_id,
    build_idempotency_key,
    build_manifest_id,
    build_publication_record_id,
)
from youtube_publishing.domain.models import (
    YouTubeVideoPublishedEvent,
)


def test_publish_state_includes_processing_timeout_mandated_by_r9() -> None:
    assert PublishState.PROCESSING_TIMEOUT == "processing_timeout"
    assert PublishState.PROCESSING_TIMEOUT.value in [s.value for s in PublishState]


def test_publish_state_terminal_predicates() -> None:
    assert PublishState.VERIFIED.is_terminal()
    assert PublishState.FAILED_PERMANENT.is_terminal()
    assert PublishState.CANCELLED.is_terminal()
    assert not PublishState.UPLOADING.is_terminal()
    assert not PublishState.PROCESSING.is_terminal()
    assert PublishState.READY.is_active()


def test_idempotency_key_is_deterministic_and_unique() -> None:
    key1 = build_idempotency_key("UC_01", "pkg_01", "chk_video_a", "v1")
    key2 = build_idempotency_key("UC_01", "pkg_01", "chk_video_a", "v1")
    key3 = build_idempotency_key("UC_01", "pkg_01", "chk_video_b", "v1")

    assert key1 == key2
    assert key1 != key3
    assert key1.startswith("idem_")


def test_manifest_and_attempt_ids() -> None:
    man_id = build_manifest_id("UC_neon", "pkg_synth", "chk_123")
    assert man_id.startswith("pub_man_")

    att_id_1 = build_attempt_id(man_id, 1)
    att_id_2 = build_attempt_id(man_id, 2)
    assert att_id_1.endswith("_01")
    assert att_id_2.endswith("_02")

    rec_id = build_publication_record_id("dQw4w9WgXcQ")
    assert rec_id == "pub_rec_dQw4w9WgXcQ"


def test_hop2_event_contains_all_r9_identity_fields() -> None:
    now = datetime.now(UTC)
    event = YouTubeVideoPublishedEvent(
        youtube_video_id="dQw4w9WgXcQ",
        publish_attempt_id="att_001_01",
        experiment_id="exp_01",
        content_arm_id="arm_01",
        published_at=now,
        metadata_version="v1",
        experiment_family_id="fam_01",
        content_genome_id="gen_01",
        production_directive_id="dir_01",
        media_package_id="pkg_01",
    )
    dumped = event.model_dump()
    assert dumped["event"] == "youtube_video_published"
    assert dumped["experiment_family_id"] == "fam_01"
    assert dumped["content_genome_id"] == "gen_01"
    assert dumped["production_directive_id"] == "dir_01"
    assert dumped["media_package_id"] == "pkg_01"
