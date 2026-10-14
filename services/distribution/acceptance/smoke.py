"""Controlled dry-run smoke test for Phase P8 DSP / Music Distribution Engine (P8 §26)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from plm_contracts import CommercialUseEligibility, GateDisposition, ProvenanceStatus
from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_contracts.seams.c6 import MediaPackagePayload
from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from sqlalchemy import text

from ..client.mock import MockDistributorClient
from ..engine import DistributionEngine
from ..reports.ingest import ingest_provider_report
from ..submission import ReleaseMetadata, submit_release_from_seams

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
            # 1-3. Compile and submit from complete seam payloads
            lineage = ExperimentLineage(
                experiment_id=f"exp_smoke_{ts}",
                experiment_family_id=f"fam_smoke_{ts}",
                content_arm_id=f"arm_smoke_{ts}",
                content_genome_id=f"gen_smoke_{ts}",
                production_directive_id=f"dir_smoke_{ts}",
            )
            digest = hashlib.sha256(f"smoke_{ts}".encode()).hexdigest()
            candidate = f"cand_smoke_{ts}"
            manifest = ReleaseManifestPayload.build(
                p5_manifest_id=f"rel_man_smoke_{ts}",
                release_manifest_version="1.0",
                candidate_id=candidate,
                canonical_artifact_id=f"art_smoke_{ts}",
                artifact_uri=f"music/smoke/{candidate}.wav",
                artifact_sha256=digest,
                measured_duration_seconds=180.0,
                track_spec_id=f"trk_smoke_{ts}",
                lineage=lineage,
                disposition=GateDisposition.PASSED,
                override=None,
                technical_status="PASSED",
                commercial_use_status=CommercialUseEligibility.ELIGIBLE,
                provenance_status=ProvenanceStatus.COMPLETE,
                ai_disclosure_required=True,
                rights_risk="LOW",
                policy_status="COMPLIANT",
                evaluation_id=f"eval_smoke_{ts}",
                evaluation_policy_version="p5-v1",
                warning_codes=(),
                evidence_bundle_id=f"evi_smoke_{ts}",
            )
            package = MediaPackagePayload.build(
                media_package_id=f"mpkg_smoke_{ts}",
                release_manifest_id=f"plm_smoke_{ts}",
                lineage=lineage,
                brand_id="brand_neon",
                ai_disclosure_required=True,
                video={
                    "master_uri": f"media/smoke/{ts}.mp4",
                    "checksum": digest,
                    "duration_seconds": 180.0,
                    "render_profile": "yt_longform_1080p_v1",
                },
                audio={"master_uri": f"media/smoke/{ts}.wav", "checksum": digest},
                thumbnails=[
                    {
                        "id": "thumb_a",
                        "uri": f"media/smoke/{ts}.jpg",
                        "checksum": digest,
                        "width": 1280,
                        "height": 720,
                    }
                ],
                tracklist=[
                    {
                        "position": 1,
                        "song_id": candidate,
                        "title": f"Smoke Track {ts}",
                        "duration_seconds": 180.0,
                        "source_artifact_id": f"art_smoke_{ts}",
                    }
                ],
                chapters=[],
                metadata_candidates=[
                    {
                        "variant_id": "meta_a",
                        "title": f"Smoke Release {ts}",
                        "description": "Distribution smoke test.",
                        "tags": ["smoke"],
                        "category_id": "10",
                        "contains_synthetic_media": True,
                    }
                ],
                short_source_assets=[],
                render_report={},
                status="ready_for_publish",
            )
            result = submit_release_from_seams(
                session,
                DistributionEngine(MockDistributorClient()),
                track_manifests=[manifest.to_wire()],
                media_package=package.to_wire(),
                publication=None,
                metadata=ReleaseMetadata(
                    artist_name=f"Smoke Artist {ts}",
                    release_date=now.strftime("%Y-%m-%d"),
                    label_name="PLM Smoke Label",
                    copyright_line="2026 PLM Smoke Label",
                    publisher_line="2026 PLM Smoke Publishing",
                    territories="WORLDWIDE",
                ),
                now=now,
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
