"""Structured canonical prompt builder and provenance hashing (PHASE-P4 §6).

Before a prompt exists it is screened against the protected-artist policy shared with
P5 (``plm_platform/policy/protected_artists.json``, R4). A prompt or style tag that names
a protected artist, or asks for a voice clone or impersonation, is refused with
:class:`ProtectedArtistPromptRefused` carrying the reason code: no prompt is returned, so
nothing can be sent to a provider.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from plm_platform.protected_artists import (
    ProtectedArtistPolicy,
    ScreeningHit,
    load_protected_artist_policy,
)

if TYPE_CHECKING:
    from ..ingestion.p3_adapter import AdaptedTrackSpec

__all__ = [
    "CompiledPrompt",
    "ProtectedArtistPromptRefused",
    "build_generation_prompt",
    "compute_prompt_hash",
]


class ProtectedArtistPromptRefused(ValueError):
    """The prompt names a protected artist or requests impersonation (P29, R4).

    ``reason_codes`` are the shared policy's codes (``PROTECTED_ARTIST_REFERENCE_DETECTED``
    and/or ``AI_POLICY_CONFLICT``); ``hits`` name what matched; ``policy_version`` is the
    policy that refused it.
    """

    def __init__(
        self, reason_codes: tuple[str, ...], hits: tuple[ScreeningHit, ...], policy_version: str
    ) -> None:
        terms = ", ".join(f"{h.reason_code}:{h.term_id}" for h in hits)
        super().__init__(f"prompt refused before generation ({terms}) [{policy_version}]")
        self.reason_codes = reason_codes
        self.hits = hits
        self.policy_version = policy_version


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    """Complete assembled prompt package ready for an AI music provider."""

    prompt: str
    style_tags: tuple[str, ...]
    negative_prompt: str
    prompt_hash: str
    effective_bpm: int
    effective_genre: str


def compute_prompt_hash(
    prompt: str,
    style_tags: tuple[str, ...],
    negative_prompt: str,
) -> str:
    """Calculate deterministic SHA256 hash representing exact prompt provenance."""
    canonical = {
        "prompt": prompt.strip(),
        "style_tags": sorted(style_tags),
        "negative_prompt": negative_prompt.strip(),
    }
    encoded = json.dumps(canonical, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_CHARACTER_ORDER: tuple[str, ...] = (
    "mood", "energy shape", "foreground lead", "texture", "scene", "instrument palette",
    "production approach", "groove", "melodic character", "harmonic motion",
    "arrangement progression", "hook character", "section structure", "harmonic key", "bpm",
    "energy", "genre", "for", "vocals", "vocal character", "mix balance",
)
"""The descriptor order of a track with its own character (P3 v9, owner 2026-11-27):
Suno weights the opening words most, so the song's mood, energy shape, lead and texture
come first and the genre, purpose and "no vocals" (the same on every song) later."""


def _open_with_character(
    descriptors: list[str], direction: dict[str, Any], genre: str
) -> list[str]:
    """``descriptors`` reordered by :data:`_CHARACTER_ORDER`, with the track's energy
    shape, texture and scene added, the lead named once (not again in the palette) and
    the genre as words with its sub-style. A descriptor not listed keeps its place at the
    end (a variant's instruction)."""
    lead = direction.get("lead_instrument")
    subgenre = direction.get("subgenre")
    style = genre.replace("_", " ").lower()
    if isinstance(subgenre, str) and subgenre:
        sub = subgenre.removesuffix("_focus").replace("_", " ")
        style = f"{sub} {style}"
    slots: dict[str, str] = {
        label: f"{label}: {direction[key]}"
        for label, key in (("energy shape", "energy_shape"), ("texture", "texture"),
                           ("scene", "scene"))
        if isinstance(direction.get(key), str) and direction[key].strip()
    }
    rest: list[str] = []
    for text in descriptors:
        label, sep, value = text.partition(": ")
        if text.endswith(" BPM"):
            slots["bpm"] = text
        elif text.endswith(" track") and " " not in text[:-6]:
            slots["genre"] = style
        elif text.startswith("for "):
            slots["for"] = text
        elif text == "instrumental, no vocals" or (not sep and text.endswith(" vocal")):
            slots["vocals"] = text
        elif sep and label == "instrument palette":
            others = [item for item in value.split(", ") if item != lead]
            if others:
                slots[label] = f"{label}: {', '.join(others)}"
        elif sep and label in _CHARACTER_ORDER:
            slots[label] = text
        else:
            rest.append(text)
    return [slots[label] for label in _CHARACTER_ORDER if label in slots] + rest


def build_generation_prompt(
    track_spec: AdaptedTrackSpec,
    *,
    variant_delta: dict[str, Any] | None = None,
    protected_artists: ProtectedArtistPolicy | None = None,
) -> CompiledPrompt:
    """Build a deterministic generation prompt from an adapted C3 track spec.

    Raises :class:`ProtectedArtistPromptRefused` when the prompt or a style tag matches
    the protected-artist policy (the shipped one unless ``protected_artists`` is given).
    """
    delta = variant_delta or {}

    # 1. Core attributes, all from the parsed C3 spec (no fallbacks).
    genre = track_spec.genre
    bpm = track_spec.bpm
    moods = list(track_spec.moods)
    use_cases = list(track_spec.use_cases)
    vocal_mode = track_spec.vocal_mode
    language = track_spec.language

    # 2. Apply variant overrides if present
    if "bpm_offset" in delta:
        bpm += delta["bpm_offset"]
    if "additional_moods" in delta:
        moods.extend(delta["additional_moods"])
    if "override_genre" in delta:
        genre = delta["override_genre"]

    # 3. Assemble descriptive prompt
    descriptors: list[str] = [f"{genre.upper()} track"]
    if moods:
        descriptors.append(f"mood: {', '.join(moods)}")
    if use_cases:
        descriptors.append(f"for {', '.join(use_cases)}")
    descriptors.append(f"{bpm} BPM")

    if vocal_mode == "instrumental":
        descriptors.append("instrumental, no vocals")
    else:
        descriptors.append(f"{language} {vocal_mode}")
        vocal_spec = track_spec.raw_spec.get("vocal_spec", {})
        if isinstance(vocal_spec, dict):
            gender = vocal_spec.get("vocal_gender")
            if isinstance(gender, str) and gender.strip() and gender != "none":
                descriptors.append(f"vocal character: {gender}")

    if track_spec.key_profile:
        descriptors.append(f"harmonic key: {track_spec.key_profile}")
    production_style = track_spec.raw_spec.get("production_style")
    if isinstance(production_style, str) and production_style.strip():
        descriptors.append(f"production approach: {production_style}")

    instrumentation = track_spec.raw_spec.get("instrumentation_spec", {})
    if isinstance(instrumentation, dict):
        instruments = instrumentation.get("instrumentation_profile", [])
        if isinstance(instruments, list) and instruments:
            descriptors.append("instrument palette: " + ", ".join(str(x) for x in instruments))
        hook = instrumentation.get("hook_profile")
        if isinstance(hook, str) and hook.strip():
            descriptors.append(f"hook character: {hook}")

    lyrics = track_spec.raw_spec.get("lyric_spec", {})
    if isinstance(lyrics, dict):
        structure = lyrics.get("song_structure")
        if isinstance(structure, str) and structure.strip():
            descriptors.append(f"section structure: {structure}")
        if vocal_mode != "instrumental":
            density = lyrics.get("lyric_density")
            if isinstance(density, str) and density.strip():
                descriptors.append(f"lyric density: {density}")

    avoid = None
    latitude = track_spec.raw_spec.get("creative_latitude", {})
    if isinstance(latitude, dict):
        energy = latitude.get("energy")
        if isinstance(energy, str) and energy.strip():
            descriptors.append(f"energy: {energy}")
        direction = latitude.get("track_arrangement", {})
        if isinstance(direction, dict):
            for key, label in (
                ("lead_instrument", "foreground lead"),
                ("rhythmic_feel", "groove"),
                ("melodic_motion", "melodic character"),
                ("harmonic_motion", "harmonic motion"),
                ("arrangement_arc", "arrangement progression"),
                ("mix_balance", "mix balance"),
            ):
                value = direction.get(key)
                if isinstance(value, str) and value.strip():
                    descriptors.append(f"{label}: {value}")
            avoid = direction.get("avoid")

    if "extra_instruction" in delta:
        descriptors.append(delta["extra_instruction"])
    if isinstance(latitude, dict) and isinstance(latitude.get("track_arrangement"), dict):
        direction = latitude["track_arrangement"]
        if isinstance(direction.get("energy_shape"), str):  # P3 v9: the song's character
            descriptors = _open_with_character(descriptors, direction, genre)

    full_prompt = ". ".join(descriptors) + "."

    # 4. Style tags
    style_tags_list = [genre.lower(), f"{bpm}bpm"]
    for m in moods:
        style_tags_list.append(m.lower().replace(" ", "_"))
    if vocal_mode == "instrumental":
        style_tags_list.append("instrumental")
    else:
        style_tags_list.append(language.lower())

    if "additional_tags" in delta:
        style_tags_list.extend(delta["additional_tags"])

    # 5. Negative prompt
    negative_prompt = (
        "low quality, distorted audio, noise, harsh frequencies, sudden cutoffs, out of tune"
    )
    # P3's arrangement may name more to avoid (focus music: anything that stands out).
    if isinstance(avoid, str) and avoid.strip():
        negative_prompt = f"{negative_prompt}, {avoid.strip()}"

    style_tuple = tuple(dict.fromkeys(style_tags_list))

    # 6. Refuse before anything can reach a provider (shared policy with P5).
    policy = protected_artists or load_protected_artist_policy()
    screening = policy.screen(full_prompt, *style_tuple)
    if screening.blocked:
        raise ProtectedArtistPromptRefused(
            screening.reason_codes, screening.hits, screening.policy_version
        )

    prompt_hash = compute_prompt_hash(full_prompt, style_tuple, negative_prompt)

    return CompiledPrompt(
        prompt=full_prompt,
        style_tags=style_tuple,
        negative_prompt=negative_prompt,
        prompt_hash=prompt_hash,
        effective_bpm=bpm,
        effective_genre=genre,
    )
