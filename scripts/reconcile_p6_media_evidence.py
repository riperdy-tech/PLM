"""Read-only comparison of a local P6 MP4 evidence snapshot to *current* DB rows.

This never supplies or repairs post-render QA, changes READY status, calls P7,
or certifies a video's complete decode, rights or publication eligibility.
It explicitly refuses remote databases and accepts only an ignored local report.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from plm_platform.config import Env, load_settings
from plm_platform.db import build_engine
from plm_platform.media_timing import classify_saved_media_timing

from scripts.p6_media_evidence import verify_evidence

_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
_QUERY = sa.text(
    "SELECT mp.video_checksum, mp.status, mp.render_profile, "
    "mp.video_duration_seconds, ps.actual_duration_seconds, mp.render_report_json "
    "FROM media.media_packages mp "
    "LEFT JOIN media.playlist_sessions ps ON ps.id = mp.playlist_session_id"
)


def _positive(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    n = float(value)
    return n if math.isfinite(n) and n > 0 else None


def _same(a: Any, b: Any) -> bool:
    a_value = _positive(a)
    b_value = _positive(b)
    return a_value is not None and b_value is not None and abs(a_value - b_value) < 1e-6


def _streams_agree(measured: Mapping[str, Any]) -> bool:
    """Re-evaluate the snapshot's stream facts, never trust its status label."""
    reference = _positive(measured.get("timeline_seconds"))
    tolerance = measured.get("tolerance_seconds")
    if (reference is None or isinstance(tolerance, bool)
            or not isinstance(tolerance, int | float)):
        return False
    margin = float(tolerance)
    if not math.isfinite(margin) or margin < 0 or margin > 0.5:
        return False
    values = [
        _positive(measured.get("audio_seconds")),
        _positive(measured.get("video_seconds")),
        _positive(measured.get("container_seconds")),
    ]
    return (all(v is not None and abs(v - reference) <= margin for v in values)
            and values[0] is not None and values[1] is not None
            and abs(values[0] - values[1]) <= margin)


def reconcile_evidence(
    report: Mapping[str, Any], current_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate each reported hash against a unique, unchanged current package.

    Matching metadata is explicitly *not* publication certification.
    """
    if not verify_evidence(report):
        raise ValueError("snapshot failed integrity/structure checks")
    index: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in current_records:
        digest = record.get("video_checksum")
        if isinstance(digest, str):
            index[digest].append(record)
    rows = report["rows"]
    hashes = Counter(row["declared_video_sha256"] for row in rows)
    outcomes: list[dict[str, Any]] = []
    for row in rows:
        digest = row["declared_video_sha256"]
        matches = index[digest]
        measured = row.get("measured")
        if hashes[digest] != 1:
            reason = "duplicate_snapshot_checksum"
        elif len(matches) == 0:
            reason = "checksum_absent_from_current_db"
        elif len(matches) > 1:
            reason = "ambiguous_current_db_checksum"
        elif row["sha256_content_verified"] is not True or (
            row["status"] != "measured_timing_consistent_only"
        ):
            reason = "snapshot_does_not_show_consistent_verified_media"
        elif not isinstance(measured, dict) or not all(
            _positive(measured.get(key)) is not None
            for key in ("audio_seconds", "video_seconds", "container_seconds",
                        "timeline_seconds")
        ):
            reason = "snapshot_measurements_incomplete"
        else:
            db = matches[0]
            if db.get("status") != "ready_for_publish":
                reason = "database_package_not_ready"
            elif db.get("render_profile") != row.get("render_profile"):
                reason = "database_render_profile_changed"
            elif not _same(db.get("video_duration_seconds"), row.get("recorded_seconds")):
                reason = "database_container_record_changed"
            elif not _same(db.get("actual_duration_seconds"), row.get("timeline_seconds")):
                reason = "database_timeline_changed"
            elif not _same(measured["timeline_seconds"], db.get("actual_duration_seconds")):
                reason = "snapshot_timeline_inconsistent"
            elif not _same(measured["container_seconds"], db.get("video_duration_seconds")):
                reason = "snapshot_container_inconsistent"
            elif not _streams_agree(measured):
                reason = "snapshot_stream_timing_mismatch"
            else:
                # A provenance match must not lift the P7 legacy hold.
                saved_reason = classify_saved_media_timing(
                    db["status"], db["video_duration_seconds"], db["render_report_json"],
                )
                reason = (
                    "metadata_matched_legacy_p7_hold"
                    if saved_reason != "saved_stream_evidence_consistent"
                    else "metadata_matched_qa_present_no_approval"
                )
        outcomes.append({"row": row["row"], "status": reason})
    counts = dict(sorted(Counter(str(o["status"]) for o in outcomes).items()))
    return {
        "snapshot_self_checksum_valid": True,
        "snapshot_evidence_sha256": report["evidence_sha256"],
        "total": len(outcomes),
        "counts": counts,
        "outcomes": outcomes,
        "publish_authorized": False,
        "db_or_media_modified": False,
    }


def _load_snapshot(root: Path, name: str) -> dict[str, Any]:
    from scripts.p6_media_evidence import _NAME

    if not _NAME.fullmatch(name):
        raise ValueError("only simple .json basename permitted")
    folder = root.resolve() / "output_media" / "audits"
    path = folder / name
    if folder.resolve() != folder or path.is_symlink():
        raise ValueError("refusing symlinked audit snapshot")
    if not path.is_file() or path.stat().st_size > 262144:
        raise ValueError("snapshot missing or exceeds read budget")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not verify_evidence(report):
        raise ValueError("snapshot failed integrity check")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-dev", action="store_true")
    parser.add_argument("--evidence", required=True, help="existing local snapshot basename")
    args = parser.parse_args()
    if not args.local_dev:
        parser.error("explicit --local-dev required (read only)")
    report = _load_snapshot(_ROOT, args.evidence)
    settings = load_settings(dotenv_path=_ROOT / ".env")
    engine = build_engine(settings)
    try:
        if settings.env is not Env.LOCAL or engine.url.host not in _LOCAL_HOSTS:
            raise RuntimeError("refusing a non-local database")
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            current = [
                {
                    "video_checksum": checksum, "status": status,
                    "render_profile": profile, "video_duration_seconds": duration,
                    "actual_duration_seconds": timeline, "render_report_json": saved_report,
                }
                for checksum, status, profile, duration, timeline, saved_report
                in conn.execute(_QUERY).all()
            ]
        print(json.dumps(reconcile_evidence(report, current), sort_keys=True))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
