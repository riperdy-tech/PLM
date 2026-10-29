"""Read-only reconciliation of local album WAVs and publication summaries.

Local inventory and exact byte reuse only; not provenance, listening, or rights
verification. Never contacts YouTube, databases, providers, or object stores.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

import soundfile as sf

CLIP_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
MAX_ALBUMS = 8
MAX_WAVS = 200
MAX_WAV_BYTES = 300_000_000


def _finite_positive(value: object, *, allow_zero: bool = False) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (float, int))
        and math.isfinite(value)
        and (value >= 0 if allow_zero else value > 0)
    )


def audit_local_albums(folders: Sequence[Path]) -> dict[str, object]:
    """Inspect supplied local directories; never publish or modify their contents."""
    if not 1 <= len(folders) <= MAX_ALBUMS:
        raise ValueError("provide_1_to_8_local_albums")
    if len({folder.resolve() for folder in folders}) != len(folders):
        raise ValueError("duplicate_album_directory")
    results: list[dict[str, object]] = []
    digests: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for slot, folder in enumerate(folders, start=1):
        if not folder.is_dir() or folder.is_symlink():
            raise ValueError(f"album_directory_unavailable_at_slot_{slot}")
        track_dir = folder / "tracks"
        if track_dir.is_symlink():
            raise ValueError(f"wav_directory_unsafe_at_slot_{slot}")
        files = sorted(track_dir.glob("*.wav")) if track_dir.is_dir() else []
        if len(files) > MAX_WAVS:
            raise ValueError(f"wav_file_limit_exceeded_at_slot_{slot}")
        wav_durations: dict[str, float | None] = {}
        for file in files:
            if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_WAV_BYTES:
                raise ValueError(f"wav_file_unsafe_at_slot_{slot}")
            with file.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            digests[digest].append((slot, file.name))
            try:
                info = sf.info(str(file))
                wav_durations[file.name] = float(info.duration) if info.frames > 0 else None
            except (OSError, RuntimeError, ValueError):
                wav_durations[file.name] = None

        # Index only filename hints, never infer complete UUID/source provenance.
        wav_by_clip_prefix: dict[str, list[tuple[int, str]]] = defaultdict(list)
        for name in wav_durations:
            match = re.fullmatch(r"track_([0-9]{2})_([0-9a-f]{8})\.wav", name)
            if match:
                wav_by_clip_prefix[match.group(2)].append((int(match.group(1)), name))

        result: dict[str, object] = {
            "album_slot": slot,
            "manifest_status": "missing",
            "wav_file_count": len(files),
            "manifest_track_count": None,
            "declared_track_count": None,
            "matched_wav_count": 0,
            "missing_source_track_indices": [],
            "clip_prefix_absent_track_indices": [],
            "ambiguous_clip_prefix_track_indices": [],
            "displaced_clip_prefix_matches": [],
            "displaced_clip_prefix_duration_mismatch_indices": [],
            "duration_mismatch_track_indices": [],
            "invalid_track_row_indices": [],
            "unreadable_wav_count": sum(v is None for v in wav_durations.values()),
            "unlisted_wav_count": len(files),
            "reused_title_group_count": 0,
            "track_rows_with_reused_title_count": 0,
            "nonmonotonic_start_count": 0,
            "final_duration_mismatch": None,
        }
        summary = folder / "publication_summary.json"
        if summary.is_symlink():
            result["manifest_status"] = "invalid"
        elif summary.is_file():
            try:
                document = json.loads(summary.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError):
                document = None
            if isinstance(document, dict) and isinstance(document.get("tracks"), list):
                tracks = document["tracks"]
                result["manifest_status"] = "present"
                result["manifest_track_count"] = len(tracks)
                count = document.get("track_count")
                result["declared_track_count"] = (
                    count if isinstance(count, int) and not isinstance(count, bool)
                    and 0 <= count <= MAX_WAVS else None
                )
                expected_files: set[str] = set()
                manifest_prefix_counts = Counter(
                    row["clip_id"][:8] for row in tracks
                    if isinstance(row, dict)
                    and isinstance(row.get("clip_id"), str)
                    and CLIP_ID.fullmatch(row["clip_id"])
                )
                titles: Counter[str] = Counter()
                previous_start: float | None = None
                final_end: float | None = None
                for idx, row in enumerate(tracks, start=1):
                    if not isinstance(row, dict):
                        result["invalid_track_row_indices"].append(idx)
                        continue
                    clip = row.get("clip_id")
                    start = row.get("start_seconds")
                    duration = row.get("duration_seconds")
                    title = row.get("title")
                    if (
                        row.get("index") != idx or isinstance(row.get("index"), bool)
                        or not isinstance(clip, str) or not CLIP_ID.fullmatch(clip)
                        or not _finite_positive(duration)
                        or not _finite_positive(start, allow_zero=True)
                    ):
                        result["invalid_track_row_indices"].append(idx)
                        continue
                    if isinstance(title, str) and title.strip():
                        titles[title.strip()] += 1
                    name = f"track_{idx:02d}_{clip[:8]}.wav"
                    expected_files.add(name)
                    if name not in wav_durations:
                        # The expected position-specific filename is absent.
                        # A unique matching 8-character prefix at another position
                        # is only a candidate, not a verified full clip UUID.
                        result["missing_source_track_indices"].append(idx)
                        candidates = wav_by_clip_prefix.get(clip[:8], [])
                        if len(candidates) == 1 and manifest_prefix_counts[clip[:8]] == 1:
                            found_index, found_name = candidates[0]
                            result["displaced_clip_prefix_matches"].append({
                                "manifest_index": idx,
                                "local_file_index": found_index,
                            })
                            measured = wav_durations[found_name]
                            if measured is not None and abs(measured - duration) > 0.5:
                                result["displaced_clip_prefix_duration_mismatch_indices"].append(idx)
                        elif candidates:
                            result["ambiguous_clip_prefix_track_indices"].append(idx)
                        else:
                            result["clip_prefix_absent_track_indices"].append(idx)
                    else:
                        result["matched_wav_count"] += 1
                        measured = wav_durations[name]
                        if measured is not None and abs(measured - duration) > 0.5:
                            result["duration_mismatch_track_indices"].append(idx)
                    if previous_start is not None and start < previous_start:
                        result["nonmonotonic_start_count"] += 1
                    previous_start = start
                    final_end = start + duration
                result["unlisted_wav_count"] = len(set(wav_durations) - expected_files)
                repeated = [v for v in titles.values() if v > 1]
                result["reused_title_group_count"] = len(repeated)
                result["track_rows_with_reused_title_count"] = sum(repeated)
                if final_end is not None and _finite_positive(document.get("duration_seconds")):
                    result["final_duration_mismatch"] = abs(
                        final_end - document["duration_seconds"]
                    ) > 1.0
                result["track_count_mismatch"] = (
                    document.get("track_count") != len(tracks)
                    or isinstance(document.get("track_count"), bool)
                )
            else:
                result["manifest_status"] = "invalid"
        results.append(result)

    exact_groups = [items for items in digests.values() if len(items) > 1]
    return {
        "evaluation": "local_inventory_only_no_provenance_or_rights_clearance",
        "albums": results,
        "exact_duplicate_wav_group_count": len(exact_groups),
        "cross_album_exact_duplicate_wav_group_count": sum(
            len({slot for slot, _ in items}) > 1 for items in exact_groups
        ),
        "cross_album_exact_duplicate_wav_file_count": sum(
            len(items) for items in exact_groups
            if len({slot for slot, _ in items}) > 1
        ),
        "rights_verified": False,
        "provenance_independently_verified": False,
        "human_listening_verified": False,
        "limitations": [
            "same SHA256 establishes only identical current file bytes",
            "different SHA256 or duplicated titles cannot prove unique recordings",
            "local publication summary is not a live YouTube verification",
            "no independent source hashes, commercial rights or human ratings were checked",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only local album source reconciliation")
    parser.add_argument("--album-dir", action="append", required=True)
    args = parser.parse_args(argv)
    try:
        report = audit_local_albums([Path(x) for x in args.album_dir])
    except ValueError as exc:
        parser.exit(2, f"Local album audit refused: {exc}\n")
    except OSError:
        parser.exit(2, "Local album audit refused: local_file_io_error\n")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
