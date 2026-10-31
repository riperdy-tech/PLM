"""Versioned, deterministic album-level contrast directions for P3 track briefs.

Contrast changes *arrangement*, never the treatment's genre, BPM, vocal mode or duration.
These directions make the plan audibly more varied only if the provider follows them;
listening/embedding QA remains necessary.

* **Lead.** The foreground lead is always a melodic instrument. Textures and drums
  (vinyl crackle, a kick) stay in the palette but are never named as the lead.
* **Genre palette (v3, owner 2026-10-31).** A genre listed in :data:`_GENRE_PALETTES`
  also varies each track's key and adds a lead, a bass and a colour instrument, while the album's
  own instruments stay on every track, so 23 tracks are not one key and three
  instruments. A genre without a palette keeps its genome's key and instruments.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any, Final

ARRANGEMENT_VERSION: Final[str] = "p3-album-contrast-v3"
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


_NON_MELODIC: Final[tuple[str, ...]] = (
    "crackle", "hiss", "noise", "rain", "ambience", "field_recording", "texture",
    "kick", "snare", "hat", "drum", "perc", "808", "clap",
)
"""Name fragments of instruments that cannot carry a melody (never the lead)."""

_GENRE_PALETTES: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "lofi": {
        "leads": ("dusty_rhodes", "felt_piano", "nylon_guitar", "clean_jazz_guitar",
                  "vibraphone", "muted_trumpet"),
        "bass": ("upright_bass", "warm_sub_bass", "muted_electric_bass"),
        "color": ("soft_analog_pad", "brushed_drums", "dusty_boom_bap_drums", "tape_hiss",
                  "rain_ambience", "warm_string_pad"),
        "keys": ("F_major", "D_minor", "Bb_major", "G_minor", "Eb_major", "C_minor",
                 "A_minor", "C_major"),
    },
}
"""Per-genre leads, one bass, one colour instrument and keys a track draws from (v3)."""


def is_melodic(instrument: str) -> bool:
    return not any(fragment in instrument.lower() for fragment in _NON_MELODIC)


def arrangement_direction(
    package_id: str, position: int, instrumentation: Sequence[str],
    *, genre: str | None = None,
) -> dict[str, Any]:
    """Return reproducible, index-distinct track instructions from declared instruments.

    With a ``genre`` palette the result also carries ``key_profile`` and
    ``added_instruments`` for the builder to apply to the track.
    """
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
    turn = position + offset
    instruments = [item.strip() for item in instrumentation if item.strip()]
    direction: dict[str, Any] = {
        "policy_version": ARRANGEMENT_VERSION,
        "rhythmic_feel": rhythm,
        "melodic_motion": melody,
        "harmonic_motion": harmony,
        "arrangement_arc": arc,
    }
    palette = _GENRE_PALETTES.get((genre or "").lower())
    if palette is None:
        melodic = [item for item in instruments if is_melodic(item)]
        direction["lead_instrument"] = melodic[turn % len(melodic)] if melodic else ""
        return direction
    # Independent strides, so lead, key and support do not move in lockstep.
    leads, basses, colors = palette["leads"], palette["bass"], palette["color"]
    lead = leads[turn % len(leads)]
    bass = basses[(2 * turn + turn // len(leads)) % len(basses)]
    color = colors[(turn + 2 * (turn // len(leads)) + 1) % len(colors)]
    added = [item for item in (lead, bass, color) if item not in instruments]
    direction["lead_instrument"] = lead
    direction["added_instruments"] = list(dict.fromkeys(added))
    keys = palette["keys"]
    direction["key_profile"] = keys[(5 * turn) % len(keys)]
    return direction
