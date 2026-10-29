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
            ):
                value = direction.get(key)
                if isinstance(value, str) and value.strip():
                    descriptors.append(f"{label}: {value}")

    if "extra_instruction" in delta:
        descriptors.append(delta["extra_instruction"])

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
