"""Offline read-only P6 album contrast proxy report (no P4/P5/P6 side effects).

Example:
  python scripts/probe_album_audio_contrast.py --track A=first.wav --track B=second.wav
Optionally verify the expected original bytes with --sha256 A=<64hex> per track.
No provider, database, MinIO, copyright or approval gate is consulted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from media_factory.audio.contrast_probe import measure_contrast, probe_album_features


def _assignments(values: list[str], *, option: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in values:
        name, sep, value = entry.partition("=")
        if not sep or not name.strip() or not value.strip() or name in result:
            raise ValueError(f"{option} needs unique nonempty NAME=VALUE arguments")
        result[name] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline diagnostic audio contrast proxies")
    parser.add_argument("--track", action="append", required=True, metavar="NAME=LOCAL_FILE")
    parser.add_argument("--sha256", action="append", default=[], metavar="NAME=HEX")
    args = parser.parse_args(argv)
    try:
        files = _assignments(args.track, option="--track")
        hashes = _assignments(args.sha256, option="--sha256")
        if len(files) < 2 or len(files) > 20 or set(hashes) - set(files):
            raise ValueError("provide 2..20 named tracks; checksum names must match")
        # Only one source's bytes are retained at once; reports keep compact
        # measured vectors, not a 20-track cache of decoded/provider recordings.
        measured = {}
        for name, value in files.items():
            path = Path(value)
            if not path.is_file() or path.stat().st_size > 300_000_000:
                raise ValueError(f"missing or oversized local audio for {name}")
            with path.open("rb") as source:
                payload = source.read(300_000_001)
            if len(payload) > 300_000_000:
                raise ValueError(f"oversized local audio for {name}")
            expected = hashes.get(name)
            if expected is not None:
                if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected.lower()):
                    raise ValueError(f"invalid SHA256 for {name}")
                if hashlib.sha256(payload).hexdigest() != expected.lower():
                    raise ValueError(f"checksum mismatch for {name}")
            measured[name] = measure_contrast(payload)
            del payload
        report = probe_album_features(measured)
        report["provided_checksum_verified"] = sorted(hashes)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Offline contrast probe refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
