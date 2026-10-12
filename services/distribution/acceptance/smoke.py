"""Controlled dry-run smoke test for Phase P8 DSP / Music Distribution Engine (P8 §26)."""

from __future__ import annotations

from datetime import UTC, datetime

from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from sqlalchemy import text

from ..artists.manager import get_or_create_artist
from ..client.mock import MockDistributorClient
from ..compiler.release_compiler import compile_canonical_release_package
from ..engine import DistributionEngine
from ..reports.ingest import ingest_provider_report

__all__ = ["run_smoke_test"]

_TOKEN_REASON = "blocked: pending distributor service token (PLM_SERVICE_TOKEN)"


def run_smoke_test() -> str | None:
    """Run the P8 dry-run smoke test. Returns None on success, or blocked reason string."""
    platform = load_settings()
    if platform.service_token is None:
        return _TOKEN_REASON

    # Test database connectivity
    engine = build_engine(platform)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return f"blocked: database unreachable ({exc})"

    now = datetime.now(UTC)
    ts = int(now.timestamp())

    try:
        factory = session_factory(engine)
        with factory() as session:
            # 1. Create or resolve artist
            artist = get_or_create_artist(
                session,
                name=f"Smoke Artist {ts}",
                brand_id="brand_neon",
            )

            # 2. Compile release package
            p5_manifest = {
                "p5_manifest_id": f"p5_smoke_{ts}",
                "disposition": "PASSED",
            }
            media_package = {
                "media_package_id": f"p6_smoke_{ts}",
                "brand_id": "brand_neon",
                "audio_master_uri": f"media/audio/smoke_{ts}.wav",
                "audio_checksum": f"chk_audio_{ts}",
                "thumbnails": [
                    {
                        "uri": f"media/artwork/smoke_{ts}.jpg",
                        "checksum": f"chk_art_{ts}",
                    }
                ],
                "tracklist": [
                    {
                        "title": f"Smoke Track {ts}",
                        "duration_seconds": 180.0,
                        "audio_uri": f"media/audio/smoke_{ts}.wav",
                        "audio_checksum": f"chk_audio_{ts}",
                    }
                ],
            }

            package = compile_canonical_release_package(
                p5_manifest=p5_manifest,
                media_package=media_package,
                artist_id=artist.id,
                artist_name=artist.canonical_name,
                brand_id="brand_neon",
                release_title=f"Smoke Release {ts}",
            )

            # 3. Submit release via DistributionEngine
            dist_client = MockDistributorClient()
            dist_engine = DistributionEngine(dist_client)

            result = dist_engine.submit_release(
                session,
                package,
                artist=artist,
                experiment_id=f"exp_smoke_{ts}",
                experiment_family_id=f"fam_smoke_{ts}",
                content_arm_id=f"arm_smoke_{ts}",
                content_genome_id=f"gen_smoke_{ts}",
                production_directive_id=f"dir_smoke_{ts}",
                media_package_id=f"p6_smoke_{ts}",
            )

            if not result.is_success:
                return f"Release submission failed: status={result.release.status}"

            # 4. Ingest raw provider report
            report = ingest_provider_report(
                session,
                provider="mock_distributor",
                provider_period=f"2026-{ts % 12 + 1:02d}",
                raw_content=f"smoke_statement_data_{ts}",
                source_reference=f"smoke_sftp_{ts}.csv",
            )

            if not report.report_id:
                return "Failed to ingest provider report"

            session.commit()
    except Exception as exc:  # noqa: BLE001
        return f"Distribution smoke execution failed: {exc}"

    return None
