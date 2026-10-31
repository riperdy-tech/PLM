"""Versioned, deterministic album-level contrast directions for P3 track briefs.

Contrast changes *arrangement*, never the treatment's genre, BPM, vocal mode or duration.
These directions make the plan audibly more varied only if the provider follows them;
listening/embedding QA remains necessary.

* **Lead.** The foreground lead is always a melodic instrument. Textures and drums
  (vinyl crackle, a kick) stay in the palette but are never named as the lead.
* **Genre palette (v4, owner 2026-10-31).** The 2026-10-23 content audit traced the
  sameness of the first albums to one sonic universe on every track (warm tape texture,
  Rhodes, rain, a riff over a chord loop) and asked for an album-level style budget and
  time-varying energy. A genre listed in :data:`_GENRE_PALETTES` therefore varies, per
  track and in rotation, the key, lead, bass, drums, colour instrument, production,
  hook, structure and energy. Only the album texture (the genome's non-melodic,
  non-drum instruments, e.g. vinyl crackle) stays on every track. A dimension the
  experiment is testing (``held``) is never varied, so a treatment stays clean.
  A genre without a palette keeps its genome's values.
"""
from __future__ import annotations

import hashlib
from collections.abc import Collection, Sequence
from typing import Any, Final

ARRANGEMENT_VERSION: Final[str] = "p3-album-contrast-v4"
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
    "crackle", "hiss", "noise", "rain", "ambience", "field_recording", "room_tone",
    "texture", "kick", "snare", "hat", "drum", "perc", "808", "clap", "rimshot",
)
"""Name fragments of instruments that cannot carry a melody (never the lead)."""

_DRUMS: Final[tuple[str, ...]] = (
    "kick", "snare", "hat", "drum", "perc", "808", "clap", "rimshot")

_GENRE_PALETTES: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    # No rain and no Rhodes-on-every-track: those were the first albums' shared sound.
    "lofi": {
        "lead": ("dusty_rhodes", "felt_piano", "nylon_guitar", "clean_jazz_guitar",
                 "vibraphone", "muted_trumpet"),
        "bass": ("upright_bass", "warm_sub_bass", "muted_electric_bass"),
        # "" is a track without drums (an interlude)
        "drums": ("sidechain_kick", "dusty_boom_bap_drums", "brushed_drums",
                  "soft_rimshot_kit", ""),
        "color": ("soft_analog_pad", "warm_string_pad", "tape_hiss", "glockenspiel",
                  "cafe_room_tone", "music_box"),
        "key_profile": ("F_major", "D_minor", "Bb_major", "G_minor", "Eb_major", "C_minor",
                        "A_minor", "C_major"),
        "production_style": ("tape_saturated", "lofi_textured", "analog_warm"),
        "hook_profile": ("instrumental_riff", "atmospheric", "slow_burn"),
        "song_structure": ("loop_progression", "ambient_evolving", "loop_progression"),
        "energy": ("low", "medium", "low"),
    },
}
"""Per-genre values each track rotates through (v4)."""

# (step, lag) per dimension: a different stride and a per-cycle shift each, so no two
# dimensions move in lockstep and a lead never always meets the same bass or key.
_ROTATION: Final[dict[str, tuple[int, int]]] = {
    "lead": (1, 0), "bass": (2, 6), "drums": (3, 6), "color": (1, 3),
    "key_profile": (5, 0), "production_style": (1, 4), "hook_profile": (2, 5),
    "song_structure": (2, 7), "energy": (1, 7),
}

STYLE_DIMENSIONS: Final[tuple[str, ...]] = (
    "key_profile", "production_style", "hook_profile", "song_structure", "energy")
"""Genome dimensions a palette varies; the builder applies them to the track."""


_BUSY_GROOVES: Final[frozenset[str]] = frozenset({
    "syncopated offbeat accents", "percussive interlocking rhythm",
    "broken rhythm with unexpected rests", "gentle swung subdivisions"})
_CALM_GROOVES: Final[tuple[str, ...]] = (
    "steady spacious groove", "half-time with silent gaps", "straight laid-back pulse")
_SOFT_DRUMS: Final[dict[str, str]] = {
    "sidechain_kick": "brushed_drums", "dusty_boom_bap_drums": "soft_rimshot_kit"}


def is_melodic(instrument: str) -> bool:
    return not any(fragment in instrument.lower() for fragment in _NON_MELODIC)


def _is_drum(instrument: str) -> bool:
    return any(fragment in instrument.lower() for fragment in _DRUMS)


def _pick(values: tuple[str, ...], dimension: str, turn: int) -> str:
    step, lag = _ROTATION[dimension]
    return values[(step * turn + (turn // lag if lag else 0)) % len(values)]


def arrangement_direction(
    package_id: str, position: int, instrumentation: Sequence[str],
    *, genre: str | None = None, held: Collection[str] = (),
) -> dict[str, Any]:
    """Return reproducible, index-distinct track instructions from declared instruments.

    With a ``genre`` palette the result also carries the track's ``instrumentation``,
    its ``album_instruments`` (to rebuild extensions from) and every
    :data:`STYLE_DIMENSIONS` value not in ``held``, for the builder to apply.
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
    lead = _pick(palette["lead"], "lead", turn)
    drums = _pick(palette["drums"], "drums", turn)
    ambient = ("song_structure" not in held
               and _pick(palette["song_structure"], "song_structure", turn) == "ambient_evolving")
    if ambient:  # a drift gets a soft kit or none, and a calm groove, never a busy one
        drums = _SOFT_DRUMS.get(drums, drums)
        if direction["rhythmic_feel"] in _BUSY_GROOVES:
            direction["rhythmic_feel"] = _CALM_GROOVES[turn % len(_CALM_GROOVES)]
    texture = [item for item in instruments if not is_melodic(item) and not _is_drum(item)]
    chosen = [lead, _pick(palette["bass"], "bass", turn), drums,
              _pick(palette["color"], "color", turn), *texture]
    direction["lead_instrument"] = lead
    direction["instrumentation"] = list(dict.fromkeys(item for item in chosen if item))
    direction["album_instruments"] = instruments
    for dimension in STYLE_DIMENSIONS:
        if dimension not in held:
            direction[dimension] = _pick(palette[dimension], dimension, turn)
    if not drums:
        direction["rhythmic_feel"] = "no drums, free-time interlude"
    # A drift or an interlude is calm: lifts in energy go to looped tracks with drums.
    calm = not drums or direction.get("song_structure") == "ambient_evolving"
    if calm and direction.get("energy") not in (None, "low") and "energy" not in held:
        direction["energy"] = "low"
    return direction
