"""Read-only, bounded FFprobe audit of stored P6 video masters on local MinIO.

This is a diagnostic only: never writes to PostgreSQL, MinIO or media packages;
never treats measurements as rights, provenance or publish authorization.
Only explicit --local-dev permits connections, and it requires local endpoints.
Prints anonymized counts and sequence numbers, never URIs or stored content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import sqlalchemy as sa
from plm_platform.config import Env, Settings, load_settings
from plm_platform.db import build_engine
from plm_platform.storage import (
    MEDIA_PREFIX,
    build_client,
    digest_from_key,
    from_uri,
    split_endpoint,
)

from scripts.p6_media_evidence import build_evidence, write_local_evidence
from services.media_factory.rendering.ffmpeg import FFmpegToolchain
from services.media_factory.rendering.profiles import render_profile
from services.media_factory.rendering.toolchain import ProbeReport

_CHUNK_BYTES = 1024 * 1024
_DEFAULT_MAX_BYTES = 128 * 1024 * 1024
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})
_QUERY = sa.text(
    "SELECT mp.video_master_uri, mp.video_checksum, "
    "mp.video_duration_seconds, mp.render_profile, ps.actual_duration_seconds "
    "FROM media.media_packages mp "
    "LEFT JOIN media.playlist_sessions ps ON ps.id = mp.playlist_session_id "
    "WHERE mp.status = 'ready_for_publish' ORDER BY mp.id LIMIT :limit"
)


@dataclass(frozen=True)
class MediaTimingObservation:
    status: str
    audio_seconds: float | None = None
    video_seconds: float | None = None
    container_seconds: float | None = None
    timeline_seconds: float | None = None


def _positive(number: object) -> float | None:
    if isinstance(number, bool) or not isinstance(number, int | float):
        return None
    value = float(number)
    return value if math.isfinite(value) and value > 0 else None


def classify_probe(probe: ProbeReport, *, timeline: float | None,
                   recorded_duration: float, tolerance: float = 0.5) -> MediaTimingObservation:
    """Use actual FFprobe stream values, not the legacy saved QA report."""
    streams = list(probe.streams)
    audios = [s for s in streams if s.codec_type == "audio"]
    videos = [s for s in streams if s.codec_type == "video"]
    a = _positive(audios[0].duration_seconds) if len(audios) == 1 else None
    v = _positive(videos[0].duration_seconds) if len(videos) == 1 else None
    c = _positive(probe.duration_seconds)
    t = _positive(timeline)
    reference = _positive(recorded_duration)
    if len(streams) != 2 or len(audios) != 1 or len(videos) != 1:
        status = "unexpected_stream_count"
    elif a is None or v is None or c is None:
        status = "unmeasured_stream_or_container"
    elif t is None or reference is None:
        status = "missing_timeline_reference"
    elif (abs(c - t) > tolerance
          or abs(c - reference) > tolerance
          or abs(a - t) > tolerance
          or abs(v - t) > tolerance
          or abs(a - v) > tolerance):
        status = "measured_timing_mismatch"
    else:
        status = "measured_timing_consistent_only"
    return MediaTimingObservation(status, a, v, c, t)


def _require_local_dev(settings: Settings, db_host: str) -> None:
    if settings.env is not Env.LOCAL or db_host not in _LOCAL_HOSTS:
        raise RuntimeError("refusing non-local P6 database")
    endpoint, _ = split_endpoint(settings.minio_endpoint)
    hostname = urlsplit("http://" + endpoint).hostname
    if hostname not in _LOCAL_HOSTS:
        raise RuntimeError("refusing non-local MinIO endpoint")


def _download_verified(*, client: object, settings: Settings, uri: str,
                       checksum: str, target: Path, max_bytes: int) -> str:
    """Single GET, strict size budget, no overwrites or mutable object operations."""
    if not isinstance(checksum, str) or len(checksum) != 64:
        return "invalid_checksum"
    try:
        key = from_uri(uri, settings=settings)
        if not key.startswith(MEDIA_PREFIX) or digest_from_key(key) != checksum:
            return "invalid_artifact_reference"
    except ValueError:
        return "invalid_artifact_reference"

    # HEAD before GET: never download an oversized object or rely on an
    # optional Transfer-Encoding/Content-Length response header.
    minio = client
    metadata = minio.stat_object(settings.minio_bucket, key)  # type: ignore[attr-defined]
    length = int(metadata.size)
    if length <= 0:
        return "size_unknown"
    if length > max_bytes:
        return "artifact_exceeds_download_budget"
    obj = minio.get_object(settings.minio_bucket, key)  # type: ignore[attr-defined]
    hasher = hashlib.sha256()
    total = 0
    try:
        with target.open("xb") as out:
            for chunk in obj.stream(amt=_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    return "download_size_exceeded_or_unknown"
                hasher.update(chunk)
                out.write(chunk)
        if total != length or hasher.hexdigest() != checksum:
            return "download_digest_mismatch"
    finally:
        obj.close()
        obj.release_conn()
    return "verified_download"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-dev", action="store_true",
                        help="must explicitly request local read-only inspection")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--max-mib", type=int, default=128)
    parser.add_argument("--head-only", action="store_true",
                        help="read object sizes only; never download media")
    parser.add_argument("--evidence-out", metavar="BASENAME.json",
                        help="exclusively create a local ignored evidence snapshot")
    args = parser.parse_args()
    if not args.local_dev:
        parser.error("explicit --local-dev required")
    if not 1 <= args.limit <= 25 or not 1 <= args.max_mib <= 512:
        parser.error("limit or max-mib outside bounded allowed range")
    if args.evidence_out and args.head_only:
        parser.error("--evidence-out requires full content verification, not --head-only")
    if not args.head_only and shutil.which("ffprobe") is None:
        print(json.dumps({"error": "ffprobe_unavailable"}))
        return 2

    settings = load_settings(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
    engine = build_engine(settings)
    try:
        _require_local_dev(settings, str(engine.url.host))
        with engine.connect() as conn:
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            rows = conn.execute(_QUERY, {"limit": args.limit}).all()
        client = build_client(settings)
        probe_tool = FFmpegToolchain(timeout_seconds=75)
        counts: Counter[str] = Counter()
        anonymized: list[dict[str, object]] = []
        evidence_rows: list[dict[str, object]] = []
        for n, (uri, checksum, recorded, profile_name, timeline) in enumerate(rows, 1):
            if args.head_only:
                try:
                    key = from_uri(uri, settings=settings)
                    if not key.startswith(MEDIA_PREFIX) or digest_from_key(key) != checksum:
                        raise ValueError("invalid content-addressed key")
                    size = int(client.stat_object(settings.minio_bucket, key).size)
                    result = "metadata_only"
                except Exception:  # noqa: BLE001 — avoid exposing paths/auth details
                    size = 0
                    result = "metadata_unavailable"
                counts[result] += 1
                anonymized.append({
                    "row": n, "status": result,
                    "size_mib": round(size / (1024 * 1024), 2) if size > 0 else None,
                })
                continue
            observation = MediaTimingObservation("unmeasured")
            # Reject unknown profiles instead of assuming a timing tolerance.
            try:
                tolerance = render_profile(profile_name).duration_tolerance_seconds
            except ValueError:
                counts["unrecognized_render_profile"] += 1
                anonymized.append({"row": n, "status": "unrecognized_render_profile"})
                evidence_rows.append({
                    "row": n, "declared_video_sha256": checksum,
                    "render_profile": profile_name, "status": "unrecognized_render_profile",
                    "sha256_content_verified": False, "measured": None,
                    "recorded_seconds": recorded, "timeline_seconds": timeline,
                })
                continue
            probed = False
            with tempfile.TemporaryDirectory(prefix="p6_readonly_probe_") as dirname:
                target = Path(dirname) / "video.mp4"
                try:
                    download = _download_verified(
                        client=client, settings=settings, uri=uri, checksum=checksum,
                        target=target, max_bytes=args.max_mib * 1024 * 1024,
                    )
                    if download == "verified_download":
                        observation = classify_probe(
                            probe_tool.probe(target), timeline=timeline,
                            recorded_duration=recorded, tolerance=tolerance,
                        )
                        probed = True
                    else:
                        observation = MediaTimingObservation(download)
                except Exception:  # noqa: BLE001 — sanitize storage/probe errors
                    observation = MediaTimingObservation("read_or_probe_error")
            counts[observation.status] += 1
            anonymized.append({
                "row": n, "status": observation.status,
                "audio_seconds": observation.audio_seconds,
                "video_seconds": observation.video_seconds,
                "container_seconds": observation.container_seconds,
                "timeline_seconds": observation.timeline_seconds,
            })
            evidence_rows.append({
                "row": n, "declared_video_sha256": checksum,
                "render_profile": profile_name, "status": observation.status,
                "sha256_content_verified": probed,
                "recorded_seconds": recorded, "timeline_seconds": timeline,
                "measured": {
                    "audio_seconds": observation.audio_seconds,
                    "video_seconds": observation.video_seconds,
                    "container_seconds": observation.container_seconds,
                    "timeline_seconds": observation.timeline_seconds,
                    "tolerance_seconds": tolerance,
                } if probed else None,
            })
        summary: dict[str, object] = {
            "total": len(rows), "counts": dict(sorted(counts.items())),
            "observations": anonymized,
        }
        if args.evidence_out:
            document = build_evidence(
                entries=evidence_rows, ffmpeg_version=probe_tool.version,
                audit_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                captured_at_utc=datetime.now(UTC).isoformat(),
            )
            write_local_evidence(
                workspace=Path(__file__).resolve().parents[1],
                filename=args.evidence_out, document=document,
            )
            summary["evidence_filename"] = args.evidence_out
            summary["evidence_sha256"] = document["evidence_sha256"]
        print(json.dumps(summary, sort_keys=True))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
