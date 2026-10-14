# tests/contract/test_c7_distribution_to_analytics.py
"""Seam C7 — PHASE-P8 → PHASE-P9 → PHASE-P2 (the feedback loop). FROZEN by Rulings R10, R11.

Leg 1 Producer: P8's `DistributionRecordPayload` via `DistributionEngine.build_c7_payload()`.
Consumer: P9's expected distribution record schema (P9 §38/§39, Ruling R9, R10).
"""

from __future__ import annotations

from datetime import UTC, datetime

from distribution.domain.enums import DeliveryStatus, ReleaseStatus, ReleaseType, StorePlatform
from distribution.domain.models import (
    DeliveryTarget,
    DistributionRelease,
)
from distribution.engine import DistributionEngine
from pydantic import BaseModel, ConfigDict

from tests.support.contract import SeamRegistry, assert_seam_payload
from tests.support.seams import sample_lineage

REGISTRY_ENTRY = "C7 · PHASE-P8 → PHASE-P9 → PHASE-P2"

REGISTRY_FIELDS: tuple[str, ...] = (
    "release_id",
    "artist_id",
    "release_version",
    "isrcs",
    "provider",
    "deliveries",
    "source_package_hash",
    "status",
    "experiment_id",
    "experiment_family_id",
    "content_arm_id",
    "content_genome_id",
    "production_directive_id",
    "media_package_id",
)


class DeliveryItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    store: str
    external_release_id: str | None = None
    status: str
    first_delivered_at: str | None = None


class ConsumerDistributionRecordSchema(BaseModel):
    """P9's expected distribution record schema for Leg 1, fields copied from registry entry C7."""

    model_config = ConfigDict(extra="forbid")

    release_id: str
    artist_id: str
    release_version: int
    isrcs: list[str]
    provider: str
    deliveries: list[DeliveryItem]
    source_package_hash: str
    status: str
    # R9 identity fields (see C6):
    experiment_id: str
    experiment_family_id: str
    content_arm_id: str
    content_genome_id: str
    production_directive_id: str
    media_package_id: str


def test_declared_fields_match_registry() -> None:
    assert set(ConsumerDistributionRecordSchema.model_fields) == set(REGISTRY_FIELDS)


def test_producer_event_validates_against_consumer_schema(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)

    now = datetime.now(UTC)
    deliveries = [
        DeliveryTarget(
            store=StorePlatform.SPOTIFY,
            external_release_id="ext_spotify_123",
            status=DeliveryStatus.DELIVERED,
            first_delivered_at=now,
        ),
        DeliveryTarget(
            store=StorePlatform.APPLE_MUSIC,
            external_release_id="ext_apple_123",
            status=DeliveryStatus.DELIVERED,
            first_delivered_at=now,
        ),
    ]

    release = DistributionRelease(
        id="dist_rel_test_001",
        release_id="rel_neon_midnight",
        artist_id="art_neon_seoul",
        release_type=ReleaseType.SINGLE,
        release_title="Midnight Drive",
        release_version=1,
        status=ReleaseStatus.DELIVERED,
        provider="mock_distributor",
        provider_release_id="prov_test_001",
        submission_fingerprint="fingerprint_abc_123",
        deliveries=deliveries,
        created_at=now,
        updated_at=now,
    )

    payload = DistributionEngine.build_c7_payload(
        release=release,
        isrcs=["USPLM2600001"],
        source_package_hash="fingerprint_abc_123",
        lineage=sample_lineage(
            experiment_id="exp_synth_01", experiment_family_id="fam_synth_01",
            content_arm_id="arm_synth_01", content_genome_id="gen_synth_01",
            production_directive_id="dir_synth_01",
        ),
        media_package_id="pkg_synth_01",
    )

    # Convert to dict for seam assertion
    payload_dict = payload.model_dump()

    validated = assert_seam_payload(
        payload_dict,
        ConsumerDistributionRecordSchema,
        seam="C7 (Leg 1: P8 → P9)",
        producer="distribution.engine.DistributionEngine.build_c7_payload",
        consumer="ConsumerDistributionRecordSchema (P9 §38)",
    )

    assert isinstance(validated, ConsumerDistributionRecordSchema)
    assert validated.release_id == "rel_neon_midnight"
    assert validated.artist_id == "art_neon_seoul"
    assert validated.release_version == 1
    assert validated.isrcs == ["USPLM2600001"]
    assert validated.provider == "mock_distributor"
    assert len(validated.deliveries) == 2
    assert validated.deliveries[0].store == "spotify"
    assert validated.status == "delivered"
    # Verify all 6 R9 identity fields:
    assert validated.experiment_id == "exp_synth_01"
    assert validated.experiment_family_id == "fam_synth_01"
    assert validated.content_arm_id == "arm_synth_01"
    assert validated.content_genome_id == "gen_synth_01"
    assert validated.production_directive_id == "dir_synth_01"
    assert validated.media_package_id == "pkg_synth_01"
