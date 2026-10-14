# tests/contract/test_c6_media_to_publishing.py
"""Seam C6 — PHASE-P6 → PHASE-P7 → PHASE-P8. FROZEN by R9, ADR-0025, ADR-0029.

Hop 1: P6's real ``assemble_media_package`` → ``MediaPackage.to_c6_payload()`` → P7's real
publish-manifest builder and publishing engine (mock YouTube client).
Hop 2: P7's real ``youtube_video_published`` event → P8's real release submission.
All three services read ``plm_contracts.seams.c6``.

What this proves: the lineage P6 received from P5 reaches P7's event and P8's stored
release unchanged; P7 takes title, assets, checksums and the disclosure flag from the
package rather than defaults; P8 refuses a publication event that belongs to another
package; every consumer refuses incomplete, placeholder or future payloads.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c6 import MediaPackagePayload, YouTubePublicationPayload
from plm_platform.clock import FixedClock

from services.distribution.client.mock import MockDistributorClient
from services.distribution.engine import DistributionEngine
from services.distribution.storage.repositories import get_release_identity
from services.distribution.submission import (
    ReleaseMetadata,
    SubmissionRefusedError,
    submit_release_from_seams,
)
from services.media_factory.rendering.assembler import assemble_media_package
from services.media_factory.visuals.planner import BrandProfile
from services.youtube_publishing.client.mock import MockYouTubeClient
from services.youtube_publishing.domain.enums import OperatingMode
from services.youtube_publishing.domain.models import ChannelProfile
from services.youtube_publishing.engine import YouTubePublishingEngine
from services.youtube_publishing.manifests import PackageNotReadyError, build_publish_manifest
from services.youtube_publishing.storage.repositories import insert_publish_manifest
from tests.support.clearance import p5_holding
from tests.support.contract import SeamRegistry, assert_refused, with_value, without
from tests.support.distribution_db import distribution_db_session
from tests.support.seams import lineage_fields, release_manifest
from tests.support.youtube_db import youtube_db_session

REGISTRY_ENTRY = "C6 · PHASE-P6 → PHASE-P7 → PHASE-P8"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
SONGS = ("song_01", "song_02")
MANIFESTS = [release_manifest(candidate_id=s, measured_duration_seconds=180.0) for s in SONGS]
CHANNEL = ChannelProfile(
    id="prof_UC_contract", channel_id="UC_contract", channel_name="Contract Channel",
    brand_id="brand_neon", oauth_credential_ref="sec_oauth_contract",
    created_at=NOW, updated_at=NOW,
)
METADATA = ReleaseMetadata(
    artist_name="Contract Artist", release_date="2026-10-01", label_name="Neon Records",
    copyright_line="2026 Neon Records", publisher_line="2026 Neon Music Publishing",
    territories="WORLDWIDE",
)


@pytest.fixture(scope="module")
def hop1() -> dict[str, Any]:
    result = assemble_media_package(
        track_manifests=MANIFESTS,
        brand_profile=BrandProfile(brand_id="brand_neon", brand_name="Neon Dreams"),
        required_duration_seconds=360.0,
        visual_profile="brand_neon_rain_v1",
        playlist_concept="Late Night Synthwave",
        clock=FixedClock(NOW),
    )
    return json.loads(json.dumps(result.media_package.to_c6_payload()))


@pytest.fixture(scope="module")
def hop2(hop1: dict[str, Any]) -> dict[str, Any]:
    pkg = parse_seam(MediaPackagePayload, hop1)
    manifest = build_publish_manifest(pkg, CHANNEL, publish_at=None, now=NOW)
    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        session.commit()
        result = YouTubePublishingEngine(MockYouTubeClient(), chunk_size=512, clock=FixedClock(NOW), clearance=p5_holding(*MANIFESTS)).execute_publish(
            session, manifest, video_data=b"0" * 1024, thumbnail_data=b"IMG",
            operating_mode=OperatingMode.DRY_RUN,
        )
    assert result.event is not None, result.error
    return json.loads(json.dumps(result.event.to_wire()))


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)


def test_hop1_producer_emits_exactly_the_shared_wire(hop1: dict[str, Any]) -> None:
    assert tuple(hop1) == tuple(MediaPackagePayload.model_fields)
    assert hop1["lineage"] == lineage_fields()
    assert [t["song_id"] for t in hop1["tracklist"]] == list(SONGS)


def test_hop1_p7_takes_every_fact_from_the_package(hop1: dict[str, Any]) -> None:
    pkg = parse_seam(MediaPackagePayload, hop1)
    manifest = build_publish_manifest(pkg, CHANNEL, publish_at=None, now=NOW)
    assert manifest.lineage.model_dump() == hop1["lineage"]
    assert manifest.title == hop1["metadata_candidates"][0]["title"]
    assert manifest.video_checksum == hop1["video"]["checksum"]
    assert manifest.thumbnail_checksum == hop1["thumbnails"][0]["checksum"]
    assert manifest.contains_synthetic_media is hop1["ai_disclosure_required"]
    assert manifest.playlist_id is None  # no invented playlist


def test_hop2_event_carries_the_lineage(hop1: dict[str, Any], hop2: dict[str, Any]) -> None:
    assert tuple(hop2) == tuple(YouTubePublicationPayload.model_fields)
    assert hop2["lineage"] == hop1["lineage"]
    assert hop2["media_package_id"] == hop1["media_package_id"]
    assert hop2["contains_synthetic_media"] is hop1["ai_disclosure_required"]


def test_hop2_p8_release_stores_the_lineage(hop1: dict[str, Any], hop2: dict[str, Any]) -> None:
    with distribution_db_session() as session:
        result = submit_release_from_seams(
            session, DistributionEngine(MockDistributorClient(clock=FixedClock(NOW))),
            track_manifests=[m.to_wire() for m in MANIFESTS], media_package=hop1,
            clearance=p5_holding(*MANIFESTS),
            publication=hop2, metadata=METADATA, now=NOW,
        )
        lineage, package_id = get_release_identity(session, result.release.release_id)
    assert lineage.model_dump() == hop1["lineage"]
    assert package_id == hop1["media_package_id"]
    assert result.payload is not None
    assert result.payload.lineage.content_arm_id == hop1["lineage"]["content_arm_id"]


def test_p8_refuses_a_publication_from_another_package(
    hop1: dict[str, Any], hop2: dict[str, Any]
) -> None:
    with distribution_db_session() as session, pytest.raises(SubmissionRefusedError) as exc:
        submit_release_from_seams(
            session, DistributionEngine(MockDistributorClient(clock=FixedClock(NOW))),
            track_manifests=[m.to_wire() for m in MANIFESTS], media_package=hop1,
            clearance=p5_holding(*MANIFESTS),
            publication=with_value(hop2, "media_package_id", "mpkg_other"),
            metadata=METADATA, now=NOW,
        )
    assert exc.value.error.code == "publication_mismatch"


def test_p7_refuses_a_package_that_is_not_ready(hop1: dict[str, Any]) -> None:
    pkg = parse_seam(MediaPackagePayload, with_value(hop1, "status", "failed"))
    with pytest.raises(PackageNotReadyError):
        build_publish_manifest(pkg, CHANNEL, publish_at=None, now=NOW)


def _parse_hop1(payload: dict[str, Any]) -> object:
    return parse_seam(MediaPackagePayload, payload)


def _parse_hop2(payload: dict[str, Any]) -> object:
    return parse_seam(YouTubePublicationPayload, payload)


@pytest.mark.parametrize(
    "field",
    [*MediaPackagePayload.model_fields, "lineage.experiment_family_id", "video.checksum"],
)
def test_hop1_consumers_refuse_a_missing_fact(hop1: dict[str, Any], field: str) -> None:
    exc = assert_refused(_parse_hop1, without(hop1, field), SeamRejection, because=field)
    assert isinstance(exc, SeamRejection)
    assert exc.error.code == "missing_required_field"


@pytest.mark.parametrize(
    "field",
    [*YouTubePublicationPayload.model_fields, "lineage.content_genome_id"],
)
def test_hop2_consumers_refuse_a_missing_fact(hop2: dict[str, Any], field: str) -> None:
    exc = assert_refused(_parse_hop2, without(hop2, field), SeamRejection, because=field)
    assert isinstance(exc, SeamRejection)
    assert exc.error.code == "missing_required_field"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lineage.content_arm_id", "arm_unknown"),
        ("video.checksum", "chk_video_default"),
        ("thumbnails", []),
        ("metadata_candidates", []),
        ("schema_version", "2.0"),
        ("surprise", 1),
    ],
)
def test_hop1_consumers_refuse_invalid_values(
    hop1: dict[str, Any], field: str, value: Any
) -> None:
    assert_refused(_parse_hop1, with_value(hop1, field, value), SeamRejection, because=field)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lineage.experiment_id", "exp_unknown"),
        ("published_at", "2026-10-01T12:00:00"),  # naive time
        ("event", "video_uploaded"),
        ("schema_version", "2.0"),
    ],
)
def test_hop2_consumers_refuse_invalid_values(
    hop2: dict[str, Any], field: str, value: Any
) -> None:
    assert_refused(_parse_hop2, with_value(hop2, field, value), SeamRejection, because=field)
