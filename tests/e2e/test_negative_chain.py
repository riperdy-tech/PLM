# tests/e2e/test_negative_chain.py
"""Wave 6 Negative Chain and Hard-Stop Scenarios (W6.2).

Verifies Review Focus 3 and 4 of docs/P0_master_integration_implementation_plan.md §3/§6:
1. Gate hard-stop: A REJECTED candidate in P5 cannot reach P6 assembly or P8 distribution.
2. Publishing preflight hard-stop: Preflight validation failures block YouTube upload.
3. Distribution idempotency: Re-submitting the same release is strictly idempotent.
4. Royalty statement deduplication: Duplicate statements do not double-count revenue in P9.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from plm_contracts import GateDisposition
from plm_contracts.money import Money
from plm_contracts.seams import parse_seam
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_platform.clock import FixedClock
from sqlalchemy.orm import Session

# P9
from services.analytics.ingestion.distributor import ingest_distributor_statement
from services.analytics.storage.repositories import get_track_metrics

# P8
from services.distribution.artists.manager import get_or_create_artist
from services.distribution.client.mock import MockDistributorClient
from services.distribution.compiler.release_compiler import compile_canonical_release_package
from services.distribution.engine import DistributionEngine
from services.distribution.identifiers.allocator import allocate_isrc

# P5 & P6
from services.media_factory.playlist.assembler import PlaylistAssemblyError, assemble_playlist
from services.media_factory.rendering.assembler import assemble_media_package
from services.media_factory.visuals.planner import BrandProfile
from services.quality_gate.gate.manifest import build_release_manifest

# P7
from services.youtube_publishing.domain.enums import OperatingMode, PrivacyStatus
from services.youtube_publishing.domain.models import ChannelProfile
from services.youtube_publishing.preflight.gates import (
    evaluate_gate_a_channel,
    evaluate_gate_b_artifact,
    evaluate_gate_c_metadata,
)
from tests.support.media_backend import InMemoryArtifactStore, StandInToolchain
from tests.support.seams import media_package, release_manifest, sample_lineage

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def test_p5_rejected_candidate_cannot_be_assembled_into_media_package() -> None:
    """Review Focus 4: A REJECTED candidate must not reach publication or packaging."""
    # 1. P5 rejects candidate due to copyright / audio flaw
    rejected_manifest = build_release_manifest(
        candidate_id="cand_rejected_001",
        canonical_artifact_id="art_defect_001",
        artifact_uri="music/test/art_defect_001.wav",
        artifact_sha256="0" * 64,
        measured_duration_seconds=120.0,
        track_spec_id="trk_spec_001",
        lineage=sample_lineage(experiment_id="exp_fail_001", content_arm_id="arm_fail_001"),
        disposition=GateDisposition.REJECTED,
        override=None,
        technical_status="FAILED",
        commercial_use_status="ineligible",
        provenance_status="COMPLETE",
        ai_disclosure_required=True,
        rights_risk="HIGH",
        policy_status="VIOLATION",
        evaluation_id_str="eval_fail_001",
        evaluation_policy_version="p5-v1",
        warning_codes=["COPYRIGHT_SIMILARITY_EXCEEDED"],
        evidence_bundle_id="evi_fail_001",
        now=NOW,
    )
    assert rejected_manifest.disposition == GateDisposition.REJECTED
    track = parse_seam(ReleaseManifestPayload, rejected_manifest.to_wire())

    # 2. P6 playlist assembly hard-blocks the unapproved candidate (Rulings R7, R8)
    with pytest.raises(PlaylistAssemblyError, match="not passed by P5"):
        assemble_playlist(manifests=[track], features={}, brand_id="brand_neon",
                          required_duration_seconds=120.0, now=NOW)

    # 3. P6 top-level media package assembler also refuses it
    store, toolchain = InMemoryArtifactStore(), StandInToolchain()
    with pytest.raises(PlaylistAssemblyError, match="not passed by P5"):
        assemble_media_package(
            track_manifests=[track],
            brand_profile=BrandProfile(brand_id="brand_neon", brand_name="Neon Dreams"),
            required_duration_seconds=120.0,
            visual_profile="brand_neon_rain_v1",
            playlist_concept="Never Published",
            store=store,
            toolchain=toolchain,
            clock=FixedClock(NOW),
        )
    # Refused before its audio was even fetched, let alone rendered.
    assert toolchain.calls == []


def test_p7_preflight_failure_blocks_youtube_publishing() -> None:
    """Review Focus 4: Corrupted, missing disclosure, or inactive channel blocks publishing."""
    # Gate A fails if channel profile is inactive
    now = NOW
    inactive_channel = ChannelProfile(
        id="prof_inactive",
        channel_id="UC_inactive",
        channel_name="Inactive Channel",
        brand_id="brand_neon",
        oauth_credential_ref="sec_oauth",
        default_privacy_mode=PrivacyStatus.PRIVATE,
        active=False,
        operating_mode=OperatingMode.DRY_RUN,
        created_at=now,
        updated_at=now,
    )
    gate_a_result = evaluate_gate_a_channel(inactive_channel, now=NOW)
    assert not gate_a_result.passed
    assert "channel profile is inactive" in gate_a_result.diagnostics

    # Gate B fails if video asset checksum or uri is missing
    bad_artifact_payload = {
        "video": {
            "master_uri": "",
            "checksum": "",
            "duration_seconds": 0.0,
        },
        "thumbnails": [],
    }
    gate_b_result = evaluate_gate_b_artifact(bad_artifact_payload, now=NOW)
    assert not gate_b_result.passed

    # Gate C fails if metadata title is empty or synthetic media disclosure is invalid
    bad_metadata = {
        "title": "",
        "description": "Missing title test",
        "contains_synthetic_media": None,
    }
    gate_c_result = evaluate_gate_c_metadata(bad_metadata, now=NOW)
    assert not gate_c_result.passed
    assert any("title" in d for d in gate_c_result.diagnostics)


def test_p8_duplicate_submission_is_strictly_idempotent(distribution_db: Session) -> None:
    """Review Focus 2: Double submission to distributor is strictly idempotent."""
    artist = get_or_create_artist(distribution_db, "Neon Resident", "brand_neon", now=NOW)
    now = NOW
    pkg = compile_canonical_release_package(
        track_manifests=[release_manifest(candidate_id="cand_idem")],
        media_package=media_package("cand_idem"),
        artist_id=artist.id,
        artist_name=artist.canonical_name,
        release_title="Idempotent Beat",
        release_date="2026-10-01",
        label_name="Neon Records",
        copyright_line="2026 Neon Records",
        publisher_line="2026 Neon Music Publishing",
        territories="WORLDWIDE",
        allocate_isrc=lambda key: allocate_isrc(distribution_db, key, now=now),
        now=now,
    )
    identity = {"lineage": sample_lineage(), "media_package_id": "mpkg_test_01"}

    client = MockDistributorClient(clock=FixedClock(NOW))
    engine = DistributionEngine(client)

    # First submission succeeds
    sub1 = engine.submit_release(distribution_db, pkg, artist=artist, now=NOW, **identity)
    assert sub1.is_success
    assert not sub1.is_duplicate
    first_release_id = sub1.release.release_id
    assert len(client.submitted_packages) == 1

    # Second submission with exact same package / fingerprint
    sub2 = engine.submit_release(distribution_db, pkg, artist=artist, now=NOW, **identity)
    assert sub2.is_success
    assert sub2.is_duplicate
    assert sub2.release.release_id == first_release_id
    # Critical: No additional provider submission calls were made!
    assert len(client.submitted_packages) == 1


def test_p9_duplicate_statement_ingestion_prevents_double_counting(analytics_db: Session) -> None:
    """Review Focus 2 & Review Focus 5: Duplicate statements deduplicate without double-counting revenue."""
    raw_statement_csv = (
        "isrc,track_id,streams,revenue_usd\n"
        "USPLM2600001,trk_lofi_01,20000,80.00\n"
        "USPLM2600002,trk_lofi_02,10000,40.00\n"
    )

    # First statement ingestion
    stmt1, tracks1 = ingest_distributor_statement(
        analytics_db,
        provider="spotify",
        period="2026-10",
        currency="USD",
        raw_content=raw_statement_csv,
        source_reference="spotify_royalty_2026_10.csv",
        now=NOW,
    )
    assert stmt1.total_revenue == Money.of("120.00", "USD")
    assert stmt1.total_streams == 30000
    assert len(tracks1) == 2

    # Second ingestion of the exact same raw statement
    stmt2, tracks2 = ingest_distributor_statement(
        analytics_db,
        provider="spotify",
        period="2026-10",
        currency="USD",
        raw_content=raw_statement_csv,
        source_reference="spotify_royalty_2026_10.csv",
        now=NOW,
    )
    # Deduplicated at statement level
    assert stmt2.id == stmt1.id
    # Zero new duplicate track lines inserted
    assert len(tracks2) == 0

    # Query track ledger: only 1 entry exists for trk_lofi_01, preserving exact $80.00 revenue
    metrics_trk1 = get_track_metrics(analytics_db, "trk_lofi_01")
    assert len(metrics_trk1) == 1
    assert metrics_trk1[0].streams == 20000
    assert metrics_trk1[0].reported_revenue == Money.of("80.00", "USD")
