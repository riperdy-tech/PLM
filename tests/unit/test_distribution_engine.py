"""Unit tests for DistributionEngine state machine and idempotency (P8 §6, §11, §19, Ruling R9, R10)."""

from __future__ import annotations

from distribution.artists.manager import get_or_create_artist
from distribution.client.mock import MockDistributorClient
from distribution.compiler.release_compiler import compile_canonical_release_package
from distribution.domain.enums import ReleaseStatus
from distribution.engine import DistributionEngine

from tests.support.distribution_db import distribution_db_session


def _create_sample_package(session, title: str = "Summer Rain"):
    artist = get_or_create_artist(session, "Neon Dreamer", "brand_neon")
    p5 = {"p5_manifest_id": "p5_eng_001", "disposition": "PASSED"}
    p6 = {
        "media_package_id": "p6_eng_001",
        "brand_id": "brand_neon",
        "audio_master_uri": "media/audio/rain.wav",
        "audio_checksum": "chk_audio_rain",
        "thumbnails": [{"uri": "media/artwork/rain.jpg", "checksum": "chk_art_rain"}],
        "tracklist": [
            {
                "title": title,
                "duration_seconds": 185.0,
                "audio_uri": "media/audio/rain.wav",
                "audio_checksum": "chk_audio_rain",
            }
        ],
    }
    pkg = compile_canonical_release_package(
        p5_manifest=p5,
        media_package=p6,
        artist_id=artist.id,
        artist_name=artist.canonical_name,
        brand_id="brand_neon",
        release_title=title,
    )
    return pkg, artist


def test_submit_release_success_and_c7_payload() -> None:
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)
        client = MockDistributorClient()
        engine = DistributionEngine(client)

        result = engine.submit_release(
            session,
            pkg,
            artist=artist,
            experiment_id="exp_001",
            experiment_family_id="fam_001",
            content_arm_id="arm_001",
            content_genome_id="gen_001",
            production_directive_id="dir_001",
            media_package_id="pkg_001",
        )

        assert result.is_success
        assert not result.is_duplicate
        assert result.release.status == ReleaseStatus.SUBMITTED
        assert result.release.provider_release_id is not None
        assert len(result.release.deliveries) == 6

        # Check Seam C7 payload with R9 fields
        assert result.payload is not None
        assert result.payload.experiment_id == "exp_001"
        assert result.payload.content_arm_id == "arm_001"
        assert result.payload.media_package_id == "pkg_001"


def test_submit_release_idempotency_guard() -> None:
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session, title="Idempotent Track")
        client = MockDistributorClient()
        engine = DistributionEngine(client)

        # First submission
        res1 = engine.submit_release(session, pkg, artist=artist)
        assert not res1.is_duplicate

        # Second submission with exact same fingerprint
        res2 = engine.submit_release(session, pkg, artist=artist)
        assert res2.is_duplicate
        assert res2.release.release_id == res1.release.release_id
        assert res2.release.submission_fingerprint == res1.release.submission_fingerprint


def test_reconcile_status_delivered_and_partial() -> None:
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)

        # 1. Full delivery
        client = MockDistributorClient(simulate_partial_delivery=False)
        engine = DistributionEngine(client)
        sub_res = engine.submit_release(session, pkg, artist=artist)

        rec_res = engine.reconcile_status(session, sub_res.release.release_id)
        assert rec_res.release.status == ReleaseStatus.DELIVERED
        assert all(d.status.value == "delivered" for d in rec_res.release.deliveries)

    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)

        # 2. Partial delivery
        client_partial = MockDistributorClient(simulate_partial_delivery=True)
        engine_partial = DistributionEngine(client_partial)
        sub_res = engine_partial.submit_release(session, pkg, artist=artist)

        rec_partial = engine_partial.reconcile_status(session, sub_res.release.release_id)
        assert rec_partial.release.status == ReleaseStatus.PARTIALLY_DELIVERED


def test_takedown_flow() -> None:
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)
        client = MockDistributorClient()
        engine = DistributionEngine(client)

        sub_res = engine.submit_release(session, pkg, artist=artist)
        takedown_res = engine.request_takedown(session, sub_res.release.release_id)
        assert takedown_res.release.status == ReleaseStatus.TAKEN_DOWN
