"""Local-only P6 MP4 audit evidence; a digest is not a signature or approval.

The report binds exact *declared* video SHA256 values to measurements collected
after full-byte digest verification. It never upgrades P6/P7 records or signs
a release. All output is opt-in, exclusive-create, and restricted to an ignored
local workspace folder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

__all__ = ["build_evidence", "verify_evidence", "write_local_evidence"]

_VERSION = "p6_readonly_mp4_timing_evidence_v1"
_NAME = re.compile(r"\A[a-zA-Z0-9][a-zA-Z0-9_.-]{0,96}\.json\Z")
_SHA = re.compile(r"\A[0-9a-f]{64}\Z")
_SUCCESS = "measured_timing_consistent_only"
_CHECKED = frozenset({_SUCCESS, "measured_timing_mismatch", "missing_timeline_reference",
                      "unmeasured_stream_or_container", "unexpected_stream_count"})


def _canonical(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def build_evidence(
    *, entries: Sequence[dict[str, Any]], ffmpeg_version: str,
    audit_code_sha256: str, captured_at_utc: str,
) -> dict[str, Any]:
    """Create a deterministic, self-checkable manifest of local observations."""
    rows = list(entries)
    counts = dict(sorted(Counter(str(item["status"]) for item in rows).items()))
    content = {
        "schema": _VERSION,
        "scope": "local_minio_mp4_timing_only_not_publication_approval",
        "ffmpeg_version": ffmpeg_version,
        "audit_script_sha256": audit_code_sha256,
        "captured_at_utc": captured_at_utc,
        "total": len(rows),
        "counts": counts,
        "rows": rows,
        "publish_authorized": False,
    }
    result = dict(content)
    result["evidence_sha256"] = hashlib.sha256(_canonical(content)).hexdigest()
    if not verify_evidence(result):
        raise ValueError("incomplete or inconsistent audit evidence")
    return result


def verify_evidence(document: Any) -> bool:
    """Check internal consistency, self-digest and fail-closed evidence claims.

    This cannot independently authenticate the source MP4 or the local tool.
    """
    if not isinstance(document, dict) or document.get("schema") != _VERSION:
        return False
    content = dict(document)
    claimed = content.pop("evidence_sha256", None)
    if not isinstance(claimed, str) or not _SHA.fullmatch(claimed):
        return False
    try:
        if hashlib.sha256(_canonical(content)).hexdigest() != claimed:
            return False
    except (TypeError, ValueError):
        return False
    if content.get("publish_authorized") is not False:
        return False
    if content.get("scope") != "local_minio_mp4_timing_only_not_publication_approval":
        return False
    if not isinstance(content.get("ffmpeg_version"), str):
        return False
    captured = content.get("captured_at_utc")
    if not isinstance(captured, str) or not captured.endswith("+00:00"):
        return False
    try:
        datetime.fromisoformat(captured)
    except ValueError:
        return False
    if not isinstance(content.get("audit_script_sha256"), str) or not _SHA.fullmatch(
        content["audit_script_sha256"]
    ):
        return False
    rows = content.get("rows")
    if not isinstance(rows, list) or len(rows) != content.get("total"):
        return False
    if any(not isinstance(row, dict) for row in rows):
        return False
    if not all(
        isinstance(row.get("declared_video_sha256"), str)
        and _SHA.fullmatch(row["declared_video_sha256"])
        and isinstance(row.get("status"), str)
        and row.get("sha256_content_verified") is (row["status"] in _CHECKED)
        and (row.get("measured") is not None if row["status"] in _CHECKED
             else row.get("measured") is None)
        for row in rows
    ):
        return False
    counts = dict(sorted(Counter(row["status"] for row in rows).items()))
    return content.get("counts") == counts


def write_local_evidence(*, workspace: Path, filename: str,
                         document: Mapping[str, Any]) -> Path:
    """Explicit immutable local output; refuse paths/symlinks/overwrites."""
    if not _NAME.fullmatch(filename) or filename in (".", ".."):
        raise ValueError("evidence filename must be a simple .json basename")
    if not verify_evidence(dict(document)):
        raise ValueError("invalid audit evidence")
    output = workspace.resolve() / "output_media" / "audits"
    output.mkdir(parents=True, exist_ok=True)
    if output.resolve() != output:
        raise ValueError("evidence directory must not be a symlink")
    destination = output / filename
    # Exclusive creation means a replay cannot overwrite any earlier evidence.
    with destination.open("xb") as out:
        out.write(_canonical(document) + b"\n")
    return destination


def main() -> int:
    """Verify the local snapshot's format and self-checksum without network/DB."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", required=True, help="existing audit .json basename")
    args = parser.parse_args()
    if not _NAME.fullmatch(args.verify):
        parser.error("--verify requires a simple .json basename")
    root = Path(__file__).resolve().parents[1]
    folder = root / "output_media" / "audits"
    target = folder / args.verify
    if folder.resolve() != folder or target.is_symlink() or target.stat().st_size > 262144:
        raise ValueError("invalid evidence path or size")
    result = json.loads(target.read_text(encoding="utf-8"))
    valid = verify_evidence(result)
    print(json.dumps({
        "self_checksum_valid": valid,
        "scope": "self_consistency_only_no_mp4_reprobe_no_publication_approval",
        "total": result.get("total") if valid else None,
        "counts": result.get("counts") if valid else None,
    }, sort_keys=True))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
