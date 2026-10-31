"""Human-readable track names for a playlist's chapters and tracklist (ADR-0044).

No title reaches P6 for a track: P3's track spec, P4's candidate and P5's manifest
(Seam C5) carry none. P6 names each track itself, deterministically from its track id:
``sha256(track id)`` picks a two-word name from a curated list that fits the
focus-music identity; a name already taken in the playlist moves to the next free one,
so no two tracks in one playlist share a name and the same playlist always gets the
same names.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Final

__all__ = ["track_names"]

_FIRST: Final[tuple[str, ...]] = (
    "Amber", "Graphite", "Quiet", "Steady", "Parallel", "Lucid", "Linear", "Silent",
    "Distant", "Late", "Clear", "Slow", "Deep", "Soft", "Cold", "Warm",
    "Hidden", "Open", "Long", "Low", "Still", "Northern", "Inner", "Half-Lit",
)
_SECOND: Final[tuple[str, ...]] = (
    "Circuit", "Draft", "Lattice", "Monolith", "Vector", "Pulse", "Grid", "Terminal",
    "Index", "Kernel", "Horizon", "Archive", "Signal", "Blueprint", "Atrium", "Compiler",
    "Workspace", "Frequency", "Meridian", "Protocol", "Threshold", "Orbit", "Relay", "Studio",
)
_NAMES: Final[tuple[str, ...]] = tuple(f"{a} {b}" for a in _FIRST for b in _SECOND)


def track_names(track_ids: Sequence[str]) -> list[str]:
    """One distinct name per track id, in order; the same ids always get the same names."""
    if len(track_ids) > len(_NAMES):
        raise ValueError(f"{len(track_ids)} tracks exceed the {len(_NAMES)} names available")
    taken: set[int] = set()
    names: list[str] = []
    for track_id in track_ids:
        index = int.from_bytes(hashlib.sha256(track_id.encode("utf-8")).digest()[:8], "big")
        index %= len(_NAMES)
        while index in taken:
            index = (index + 1) % len(_NAMES)
        taken.add(index)
        names.append(_NAMES[index])
    return names
