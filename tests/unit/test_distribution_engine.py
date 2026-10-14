"""Unit tests for DistributionEngine state machine and idempotency (P8 §6, §11, §19, Ruling R9, R10)."""

from __future__ import annotations

from datetime import UTC, datetime

from distribution.artists.manager import get_or_create_artist
from distribution.client.mock import MockDistributorClient
from distribution.compiler.release_compiler import compile_canonical_release_package
from distribution.domain.enums import ReleaseStatus
from distribution.engine import DistributionEngine
from distribution.identifiers.allocator import allocate_isrc

from tests.support.distribution_db import distribution_db_session
from tests.support.seams import media_package, release_manifest, sample_lineage

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _create_sample_package(session, title: str = "Summer Rain", candidate: str = "cand_rain"):
    artist = get_or_create_artist(session, "Neon Dreamer", "brand_neon")
    pkg = compile_canonical_release_package(
        track_manifests=[release_manifest(candidate_id=candidate)],
        media_package=media_package(candidate),
        artist_id=artist.id,
        artist_name=artist.canonical_name,
        release_title=title,
        release_date="2026-10-01",
        label_name="Neon Records",
        copyright_line="2026 Neon Records",
        publisher_line="2026 Neon Music Publishing",
        territories="WORLDWIDE",
        allocate_isrc=lambda key: allocate_isrc(session, key, now=NOW),
        now=NOW,
    )
    return pkg, artist


IDENTITY = {"lineage": sample_lineage(), "media_package_id": "mpkg_test_01"}


def test_submit_release_success_and_c7_payload() -> None:
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)
        client = MockDistributorClient()
        engine = DistributionEngine(client)

        result = engine.submit_release(
            session,
            pkg,
            artist=artist,
            **IDENTITY,
        )

        assert result.is_success
        assert not result.is_duplicate
        assert result.release.status == ReleaseStatus.SUBMITTED
        assert result.release.provider_release_id is not None
        assert len(result.release.deliveries) == 6

        # Check Seam C7 payload with R9 fields
        assert result.payload is not None
        assert result.payload.experiment_id == IDENTITY["lineage"].experiment_id
        assert result.payload.content_arm_id == IDENTITY["lineage"].content_arm_id
        assert result.payload.media_package_id == "mpkg_test_01"


def test_submit_release_idempotency_guard() -> None:
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session, title="Idempotent Track")
        client = MockDistributorClient()
        engine = DistributionEngine(client)

        # First submission
        res1 = engine.submit_release(session, pkg, artist=artist, **IDENTITY)
        assert not res1.is_duplicate

        # Second submission with exact same fingerprint
        res2 = engine.submit_release(session, pkg, artist=artist, **IDENTITY)
        assert res2.is_duplicate
        assert res2.release.release_id == res1.release.release_id
        assert res2.release.submission_fingerprint == res1.release.submission_fingerprint


def test_reconcile_status_delivered_and_partial() -> None:
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)

        # 1. Full delivery
        client = MockDistributorClient(simulate_partial_delivery=False)
        engine = DistributionEngine(client)
        sub_res = engine.submit_release(session, pkg, artist=artist, **IDENTITY)

        rec_res = engine.reconcile_status(session, sub_res.release.release_id)
        assert rec_res.release.status == ReleaseStatus.DELIVERED
        assert all(d.status.value == "delivered" for d in rec_res.release.deliveries)

    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)

        # 2. Partial delivery
        client_partial = MockDistributorClient(simulate_partial_delivery=True)
        engine_partial = DistributionEngine(client_partial)
        sub_res = engine_partial.submit_release(session, pkg, artist=artist, **IDENTITY)

        rec_partial = engine_partial.reconcile_status(session, sub_res.release.release_id)
        assert rec_partial.release.status == ReleaseStatus.PARTIALLY_DELIVERED


def test_takedown_flow() -> None:
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)
        client = MockDistributorClient()
        engine = DistributionEngine(client)

        sub_res = engine.submit_release(session, pkg, artist=artist, **IDENTITY)
        takedown_res = engine.request_takedown(session, sub_res.release.release_id)
        assert takedown_res.release.status == ReleaseStatus.TAKEN_DOWN
