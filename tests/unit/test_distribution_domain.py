"""Unit tests for distribution domain models, enums and deterministic IDs (P8 §6, §7, §8)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from distribution.domain.enums import (
    ArtistType,
    DeliveryStatus,
    ISRCSource,
    ReleaseStatus,
    ReleaseType,
    StorePlatform,
)
from distribution.domain.ids import (
    build_release_id,
    build_submission_fingerprint,
    build_track_id,
    generate_deterministic_isrc,
)
from distribution.domain.models import (
    ArtistProfile,
    DistributionRecordPayload,
)
from pydantic import ValidationError

from tests.support.seams import lineage_fields


def test_enums_values() -> None:
    assert ReleaseType.SINGLE.value == "single"
    assert ReleaseType.EP.value == "ep"
    assert ReleaseType.ALBUM.value == "album"

    assert ReleaseStatus.DRAFT.is_active()
    assert not ReleaseStatus.DRAFT.is_terminal()
    assert ReleaseStatus.DELIVERED.is_terminal()
    assert ReleaseStatus.REJECTED.is_terminal()
    assert ReleaseStatus.TAKEN_DOWN.is_terminal()

    assert DeliveryStatus.DELIVERED.value == "delivered"
    assert StorePlatform.SPOTIFY.value == "spotify"
    assert StorePlatform.APPLE_MUSIC.value == "apple_music"
    assert ISRCSource.INTERNAL_ALLOCATION.value == "internal_allocation"
    assert ArtistType.VIRTUAL.value == "virtual"


def test_deterministic_ids_generation() -> None:
    isrc1 = generate_deterministic_isrc("US", "PLM", "26", 1)
    isrc2 = generate_deterministic_isrc("US", "PLM", "26", 2)
    assert isrc1 == "USPLM2600001"
    assert isrc2 == "USPLM2600002"
    assert len(isrc1) == 12

    rel_id1 = build_release_id("brand_neon", "midnight_drive")
    rel_id2 = build_release_id("brand_neon", "midnight_drive")
    assert rel_id1 == rel_id2
    assert rel_id1.startswith("rel_")

    trk_id = build_track_id(rel_id1, 1)
    assert trk_id.endswith("_01")

    fp1 = build_submission_fingerprint("art_1", "Title", ["USPLM2600001"], "chk_art", 1)
    fp2 = build_submission_fingerprint("art_1", "Title", ["USPLM2600001"], "chk_art", 1)
    fp3 = build_submission_fingerprint("art_1", "Title", ["USPLM2600001"], "chk_art", 2)
    assert fp1 == fp2
    assert fp1 != fp3


def test_models_immutability() -> None:
    now = datetime.now(UTC)
    artist = ArtistProfile(
        id="art_1",
        canonical_name="Neon Soul",
        normalized_name="neon soul",
        brand_id="brand_neon",
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(ValidationError):
        # Frozen models cannot be mutated
        artist.canonical_name = "New Name"  # type: ignore[misc]


def test_distribution_record_payload_strictness() -> None:
    # R9 identity fields must be present and extra fields forbidden
    valid_data = {
        "release_id": "rel_001",
        "artist_id": "art_001",
        "release_version": 1,
        "isrcs": ["USPLM2600001"],
        "provider": "mock",
        "deliveries": [],
        "source_package_hash": "hash_123",
        "status": "delivered",
        "lineage": lineage_fields(experiment_id="exp_1"),
        "media_package_id": "pkg_1",
    }
    payload = DistributionRecordPayload.build(**valid_data)
    assert payload.lineage.experiment_id == "exp_1"
    valid_data = payload.to_wire()

    # Missing R9 field raises ValidationError
    invalid_data = dict(valid_data)
    invalid_data["lineage"] = {k: v for k, v in valid_data["lineage"].items()
                               if k != "experiment_family_id"}
    with pytest.raises(ValidationError):
        DistributionRecordPayload.model_validate(invalid_data)

    # Extra field raises ValidationError due to extra="forbid"
    extra_data = dict(valid_data)
    extra_data["unknown_extra_field"] = "bad"
    with pytest.raises(ValidationError):
        DistributionRecordPayload.model_validate(extra_data)
