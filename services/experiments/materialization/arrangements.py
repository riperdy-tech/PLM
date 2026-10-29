"""Versioned, deterministic album-level contrast directions for P3 track briefs.

Contrast changes *arrangement*, not the treatment's genre, BPM, vocal mode,
duration, or key constraints. These directions make the plan audibly more varied
only if the provider follows them; listening/embedding QA remains necessary.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Final

ARRANGEMENT_VERSION: Final[str] = "p3-album-contrast-v2"
_PATTERNS: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("syncopated offbeat accents", "short ascending question phrases", "sparse sustained voicings", "minimal opening, gradual layers"),
    ("straight laid-back pulse", "long descending legato lines", "moving inner voices", "melody-first opening, sparse ending"),
    ("half-time with silent gaps", "call-and-response fragments", "pedal harmony with subtle color changes", "delayed lead entry, late lift"),
    ("gentle swung subdivisions", "repeating three-note cell with variation", "alternating chord inversions", "full opening, reduced middle section"),
    ("broken rhythm with unexpected rests", "stepwise wandering motif", "slow harmonic rhythm", "textural introduction, focused finale"),
    ("steady spacious groove", "leaps separated by long pauses", "higher-register color voicings", "intimate opening, expanding second half"),
    ("percussive interlocking rhythm", "rhythmic motif more than melody", "lower-register countermotion", "instrumental break midway"),
    ("restrained pulse with brief fills", "answering melody in a contrasting register", "open intervals and delayed resolutions", "gradual thinning toward the ending"),
)


def arrangement_direction(
    package_id: str, position: int, instrumentation: Sequence[str]
) -> dict[str, str]:
    """Return reproducible, index-distinct track instructions from declared instruments."""
    if position < 0:
        raise ValueError("track position must be nonnegative")
    offset = int.from_bytes(hashlib.sha256(package_id.encode("utf-8")).digest()[:2], "big")
    # V1 repeated the entire four-part arrangement every eight tracks. Rotate
    # the independent axes across each eight-track cycle, so the rhythm/melody
    # pair stays unique for 64 successive positions without changing genre,
    # target BPM, duration, key or the experiment's treatment dimensions.
    size = len(_PATTERNS)
    cycle, slot = divmod(position + offset, size)
    rhythm = _PATTERNS[slot][0]
    melody = _PATTERNS[(slot + cycle) % size][1]
    harmony = _PATTERNS[(slot + 3 * cycle) % size][2]
    arc = _PATTERNS[(slot + 5 * cycle) % size][3]
    instruments = [item.strip() for item in instrumentation if item.strip()]
    lead = instruments[(position + offset) % len(instruments)] if instruments else ""
    return {
        "policy_version": ARRANGEMENT_VERSION,
        "rhythmic_feel": rhythm,
        "melodic_motion": melody,
        "harmonic_motion": harmony,
        "arrangement_arc": arc,
        "lead_instrument": lead,
    }
