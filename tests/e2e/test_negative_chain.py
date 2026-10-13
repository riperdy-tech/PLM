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
from sqlalchemy.orm import Session

# P9
from services.analytics.ingestion.distributor import ingest_distributor_statement
from services.analytics.storage.repositories import get_track_metrics

# P8
from services.distribution.artists.manager import get_or_create_artist
from services.distribution.client.mock import MockDistributorClient
from services.distribution.compiler.release_compiler import compile_canonical_release_package
from services.distribution.engine import DistributionEngine

# P5 & P6
from services.media_factory.playlist.assembler import assemble_playlist
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


def test_p5_rejected_candidate_cannot_be_assembled_into_media_package() -> None:
    """Review Focus 4: A REJECTED candidate must not reach publication or packaging."""
    # 1. P5 rejects candidate due to copyright / audio flaw
    rejected_manifest = build_release_manifest(
        candidate_id="cand_rejected_001",
        canonical_artifact_id="art_defect_001",
        experiment_id="exp_fail_001",
        track_spec_id="trk_spec_001",
        content_arm_id="arm_fail_001",
        disposition=GateDisposition.REJECTED,
        technical_status="FAIL",
        commercial_use_status="INELIGIBLE",
        provenance_status="FAILED",
        ai_disclosure_required=True,
        rights_risk="HIGH",
        policy_status="VIOLATION",
        evaluation_id_str="eval_fail_001",
        evaluation_policy_version="p5-v1",
        warning_codes=["COPYRIGHT_SIMILARITY_EXCEEDED"],
        evidence_bundle_id="evi_fail_001",
    )
    assert rejected_manifest.disposition == GateDisposition.REJECTED

    # 2. P6 playlist assembly hard-blocks the unapproved candidate (Rulings R7, R8)
    with pytest.raises(ValueError, match="not cleared"):
        assemble_playlist(
            release_manifest_id=rejected_manifest.p5_manifest_id,
            experiment_id=rejected_manifest.experiment_id,
            content_arm_id=rejected_manifest.content_arm_id,
            brand_id="brand_neon",
            candidate_manifests=[
                {
                    "candidate_id": rejected_manifest.candidate_id,
                    "disposition": rejected_manifest.disposition.value,
                    "canonical_artifact_id": rejected_manifest.canonical_artifact_id,
                }
            ],
            track_details=[
                {
                    "candidate_id": rejected_manifest.candidate_id,
                    "title": "Rejected Track",
                    "duration_seconds": 120.0,
                }
            ],
            required_duration_seconds=120.0,
        )

    # 3. P6 top-level media package assembler also refuses to assemble unapproved candidate
    with pytest.raises(ValueError, match="not cleared"):
        assemble_media_package(
            release_manifest={
                "release_manifest_id": rejected_manifest.p5_manifest_id,
                "experiment_id": rejected_manifest.experiment_id,
                "content_arm_id": rejected_manifest.content_arm_id,
                "brand_id": "brand_neon",
                "tracks": [
                    {
                        "song_id": rejected_manifest.candidate_id,
                        "title": "Rejected Track",
                        "duration_seconds": 120.0,
                        "provenance_status": "REJECTED",
                    }
                ],
            },
            brand_profile=BrandProfile(brand_id="brand_neon", brand_name="Neon Dreams"),
            render_profile="yt_longform_1080p_v1",
            required_duration_seconds=120.0,
        )


def test_p7_preflight_failure_blocks_youtube_publishing() -> None:
    """Review Focus 4: Corrupted, missing disclosure, or inactive channel blocks publishing."""
    # Gate A fails if channel profile is inactive
    now = datetime.now(UTC)
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
    gate_a_result = evaluate_gate_a_channel(inactive_channel)
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
    gate_b_result = evaluate_gate_b_artifact(bad_artifact_payload)
    assert not gate_b_result.passed

    # Gate C fails if metadata title is empty or synthetic media disclosure is invalid
    bad_metadata = {
        "title": "",
        "description": "Missing title test",
        "contains_synthetic_media": None,
    }
    gate_c_result = evaluate_gate_c_metadata(bad_metadata)
    assert not gate_c_result.passed
    assert any("title" in d for d in gate_c_result.diagnostics)


def test_p8_duplicate_submission_is_strictly_idempotent(distribution_db: Session) -> None:
    """Review Focus 2: Double submission to distributor is strictly idempotent."""
    artist = get_or_create_artist(distribution_db, "Neon Resident", "brand_neon")
    p5_manifest = {"p5_manifest_id": "p5_idem_001", "disposition": "PASSED"}
    p6_media = {
        "media_package_id": "p6_idem_001",
        "brand_id": "brand_neon",
        "audio_master_uri": "media/audio/idem.wav",
        "audio_checksum": "chk_audio_idem",
        "thumbnails": [{"uri": "media/artwork/cover.jpg", "checksum": "chk_art_idem"}],
        "tracklist": [
            {
                "title": "Idempotent Beat",
                "duration_seconds": 180.0,
                "audio_uri": "media/audio/idem.wav",
                "audio_checksum": "chk_audio_idem",
            }
        ],
    }
    pkg = compile_canonical_release_package(
        p5_manifest=p5_manifest,
        media_package=p6_media,
        artist_id=artist.id,
        artist_name=artist.canonical_name,
        brand_id="brand_neon",
        release_title="Idempotent Beat",
    )

    client = MockDistributorClient()
    engine = DistributionEngine(client)

    # First submission succeeds
    sub1 = engine.submit_release(distribution_db, pkg, artist=artist)
    assert sub1.is_success
    assert not sub1.is_duplicate
    first_release_id = sub1.release.release_id
    assert len(client.submitted_packages) == 1

    # Second submission with exact same package / fingerprint
    sub2 = engine.submit_release(distribution_db, pkg, artist=artist)
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
        raw_content=raw_statement_csv,
        source_reference="spotify_royalty_2026_10.csv",
    )
    assert stmt1.total_revenue_usd == 120.0
    assert stmt1.total_streams == 30000
    assert len(tracks1) == 2

    # Second ingestion of the exact same raw statement
    stmt2, tracks2 = ingest_distributor_statement(
        analytics_db,
        provider="spotify",
        period="2026-10",
        raw_content=raw_statement_csv,
        source_reference="spotify_royalty_2026_10.csv",
    )
    # Deduplicated at statement level
    assert stmt2.id == stmt1.id
    # Zero new duplicate track lines inserted
    assert len(tracks2) == 0

    # Query track ledger: only 1 entry exists for trk_lofi_01, preserving exact $80.00 revenue
    metrics_trk1 = get_track_metrics(analytics_db, "trk_lofi_01")
    assert len(metrics_trk1) == 1
    assert metrics_trk1[0].streams == 20000
    assert metrics_trk1[0].reported_revenue_usd == 80.00
