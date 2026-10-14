"""Structured canonical prompt builder and provenance hashing (PHASE-P4 §6)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..ingestion.p3_adapter import AdaptedTrackSpec

__all__ = ["CompiledPrompt", "build_generation_prompt", "compute_prompt_hash"]


@dataclass(frozen=True, slots=True)
class CompiledPrompt:
    """Complete assembled prompt package ready for an AI music provider."""

    prompt: str
    style_tags: tuple[str, ...]
    negative_prompt: str
    prompt_hash: str


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
) -> CompiledPrompt:
    """Build a deterministic generation prompt from an adapted C3 track spec."""
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
    prompt_hash = compute_prompt_hash(full_prompt, style_tuple, negative_prompt)

    return CompiledPrompt(
        prompt=full_prompt,
        style_tags=style_tuple,
        negative_prompt=negative_prompt,
        prompt_hash=prompt_hash,
    )
