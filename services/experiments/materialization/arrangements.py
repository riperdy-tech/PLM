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

ARRANGEMENT_VERSION: Final[str] = "p3-album-contrast-v5"
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
    # Values for production, hook and structure come from each genre's template
    # alternatives; instruments and keys are the genre's common vocabulary.
    "ambient": {
        "lead": ("felt_piano", "bowed_cello", "ambient_electric_guitar_swells", "soft_flute",
                 "celesta", "glass_harmonica"),
        "bass": ("sub_drone", "bowed_double_bass", "low_analog_drone"),
        "drums": ("", "", "distant_frame_drum", ""),
        "color": ("shimmer_pad", "choir_pad", "wind_chimes", "tape_loop", "string_drone",
                  "water_ambience"),
        "key_profile": ("D_major", "A_minor", "F_major", "E_minor", "C_major", "G_major",
                        "B_minor", "Eb_major"),
        "production_style": ("cinematic", "minimalist"),
        "hook_profile": ("atmospheric", "slow_burn", "atmospheric"),
        "song_structure": ("ambient_evolving",),
        "energy": ("very_low", "low", "very_low"),
    },
    "synthwave": {
        "lead": ("analog_poly_synth", "saw_lead_synth", "fm_electric_piano", "synth_brass",
                 "chorused_electric_guitar", "arpeggiated_pluck_synth"),
        "bass": ("arpeggiated_synth_bass", "octave_synth_bass", "fretless_electric_bass"),
        "drums": ("gated_reverb_drums", "linn_drum_machine", "electronic_tom_kit", "",
                  "808_kit"),
        "color": ("lush_string_pad", "choir_pad", "tape_hiss", "neon_city_ambience",
                  "bell_synth", "noise_sweep"),
        "key_profile": ("A_minor", "E_minor", "D_minor", "F#_minor", "C_minor", "G_minor",
                        "Bb_major", "F_major"),
        "production_style": ("analog_warm", "clean_modern", "tape_saturated"),
        "hook_profile": ("instrumental_riff", "slow_burn", "atmospheric"),
        "song_structure": ("loop_progression", "intro_verse_chorus", "ambient_evolving"),
        "energy": ("medium", "low", "high"),
    },
    "jazz": {
        "lead": ("jazz_piano", "tenor_saxophone", "muted_trumpet", "vibraphone",
                 "hollow_body_guitar", "flugelhorn"),
        "bass": ("upright_bass", "walking_upright_bass", "fretless_electric_bass"),
        "drums": ("brushed_drums", "ride_cymbal_kit", "soft_rimshot_kit", ""),
        "color": ("hammond_organ", "string_section", "rhodes_comping", "flute_doubling",
                  "cafe_room_tone", "vinyl_crackle"),
        "key_profile": ("Bb_major", "F_major", "Eb_major", "C_minor", "D_minor", "G_minor",
                        "Ab_major", "C_major"),
        "production_style": ("clean_modern", "analog_warm"),
        "hook_profile": ("instrumental_hook", "slow_burn", "atmospheric"),
        "song_structure": ("head_solos_head", "ballad_aaba", "head_solos_head"),
        "energy": ("low", "medium", "low"),
    },
    "city_pop": {
        "lead": ("electric_piano", "synth_brass", "clean_funk_guitar", "alto_saxophone",
                 "fm_electric_piano", "glossy_poly_synth"),
        "bass": ("slap_bass", "fingerstyle_electric_bass", "synth_bass"),
        "drums": ("tight_funk_kit", "linn_drum_machine", "disco_kit", "live_pop_kit"),
        "color": ("string_section", "horn_section", "glossy_pad", "congas", "chorus_guitar",
                  "bell_synth"),
        "key_profile": ("Eb_major", "F_major", "Ab_major", "Bb_major", "D_major", "E_major",
                        "C_major", "G_major"),
        "production_style": ("analog_warm", "clean_modern"),
        "hook_profile": ("immediate", "first_chorus", "slow_burn"),
        "song_structure": ("verse_chorus_bridge", "intro_verse_chorus", "hook_forward"),
        "energy": ("medium", "high", "medium", "low"),
    },
    "rnb": {
        "lead": ("rhodes", "reverb_guitar", "wurlitzer", "nylon_guitar", "felt_piano",
                 "synth_pluck"),
        "bass": ("808_bass", "warm_sub_bass", "fingerstyle_electric_bass"),
        "drums": ("trap_hat_808_kit", "laid_back_live_kit", "snap_and_rim_kit",
                  "boom_bap_drums"),
        "color": ("string_pad", "vocal_chop_texture", "airy_synth_pad", "finger_snaps",
                  "vinyl_crackle", "choir_pad"),
        "key_profile": ("C_minor", "Eb_major", "F_minor", "Bb_minor", "Ab_major", "G_minor",
                        "Db_major", "D_minor"),
        "production_style": ("warm_analog", "clean_modern", "cinematic"),
        "hook_profile": ("vocal_refrain", "slow_burn", "first_chorus", "immediate"),
        "song_structure": ("verse_chorus_bridge", "intro_verse_chorus", "hook_forward"),
        "energy": ("medium", "low", "medium"),
    },
    "house": {
        "lead": ("warm_synth_stabs", "piano_chords", "rhodes", "plucked_synth",
                 "vocal_chop_lead", "organ_stabs"),
        "bass": ("deep_bass", "rolling_sub_bass", "acid_bassline"),
        # house keeps its four-on-the-floor kick on every track
        "drums": ("four_on_floor_kick", "four_on_floor_kick_with_shuffled_hats",
                  "four_on_floor_kick_with_congas", "minimal_four_on_floor_kick"),
        "color": ("lush_pad", "string_stabs", "filtered_noise_riser", "shaker_percussion",
                  "dub_chords", "choir_pad"),
        "key_profile": ("A_minor", "F_minor", "D_minor", "G_minor", "C_minor", "E_minor",
                        "Bb_minor", "F#_minor"),
        "production_style": ("clean_modern", "analog_warm"),
        "hook_profile": ("immediate", "slow_burn", "first_chorus"),
        "song_structure": ("build_drop_breakdown", "extended_groove", "build_drop_breakdown"),
        "energy": ("high", "medium", "high"),
        "groove": ("steady four-on-the-floor pulse", "shuffled offbeat hats",
                   "rolling percussive groove", "stripped-back kick and clap",
                   "syncopated bass-led groove"),
    },
}
"""Per-genre values each track rotates through (v4: lofi; v5: all seven template genres)."""

# (step, lag) per dimension: a different stride and a per-cycle shift each, so no two
# dimensions move in lockstep and a lead never always meets the same bass or key.
_ROTATION: Final[dict[str, tuple[int, int]]] = {
    "lead": (1, 0), "bass": (2, 6), "drums": (3, 6), "color": (1, 3),
    "key_profile": (5, 0), "production_style": (1, 5), "hook_profile": (2, 5),
    "song_structure": (2, 7), "energy": (1, 7), "groove": (2, 3),
}

STYLE_DIMENSIONS: Final[tuple[str, ...]] = (
    "key_profile", "production_style", "hook_profile", "song_structure", "energy")
"""Genome dimensions a palette varies; the builder applies them to the track."""


_BUSY_GROOVES: Final[frozenset[str]] = frozenset({
    "syncopated offbeat accents", "percussive interlocking rhythm",
    "broken rhythm with unexpected rests", "gentle swung subdivisions"})
_CALM_GROOVES: Final[tuple[str, ...]] = (
    "steady spacious groove", "half-time with silent gaps", "straight laid-back pulse")
_LIFTED: Final[frozenset[str]] = frozenset({"medium", "high", "very_high"})
"""Energies a calm track is brought down from; very_low stays very_low."""

_SOFT_DRUMS: Final[dict[str, str]] = {
    "sidechain_kick": "brushed_drums", "dusty_boom_bap_drums": "soft_rimshot_kit",
    "gated_reverb_drums": "soft_electronic_drums", "linn_drum_machine": "soft_electronic_drums",
    "electronic_tom_kit": "soft_electronic_drums", "808_kit": "soft_electronic_drums"}


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
    if "groove" in palette:  # a genre whose feel the generic grooves would contradict
        direction["rhythmic_feel"] = _pick(palette["groove"], "groove", turn)
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
    if calm and direction.get("energy") in _LIFTED and "energy" not in held:
        direction["energy"] = "low"
    return direction
