"""Genome Engine: normalization, template inheritance, and creative latitude bounds (PHASE-P3 §6–§9, §25, §67–§69)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import EnergyLevel, VocalGender, VocalMode
from ..domain.genome import BpmRange, ContentGenome, DurationRange

if TYPE_CHECKING:
    from ..ingestion.p2_adapter import AdaptedDirective

ALIAS_MAP: Mapping[str, str] = {
    "r&b": "rnb",
    "r_and_b": "rnb",
    "hip-hop": "hip_hop",
    "hip hop": "hip_hop",
    "lo-fi": "lofi",
    "lo fi": "lofi",
}


def normalize_dimension_value(dimension: str, value: str) -> str:
    """Normalize dimension string values to canonical lowercase snake_case tokens."""
    cleaned = value.strip().lower()
    cleaned = ALIAS_MAP.get(cleaned, cleaned)
    cleaned = re.sub(r"[\s\-]+", "_", cleaned)
    cleaned = re.sub(r"[^\w_]", "", cleaned)
    return cleaned.strip("_")


class TemplateNotFoundError(Exception):
    """Raised when a referenced genome template does not exist."""


DEFAULT_TEMPLATES: dict[str, dict[str, Any]] = {
    "rnb_default": {
        "genre": "rnb",
        "subgenre": "contemporary_rnb",
        "mood": ("melancholic", "intimate"),
        "use_case": ("chill", "late_night_drive"),
        "language": "english",
        "vocal_mode": VocalMode.VOCAL,
        "vocal_gender": VocalGender.FEMALE,
        "energy": EnergyLevel.MEDIUM,
        "bpm": {"min_bpm": 70, "max_bpm": 90, "target_bpm": 80},
        "key_profile": "C_minor",
        "lyric_density": "moderate",
        "song_structure": "verse_chorus_bridge",
        "hook_profile": "vocal_refrain",
        "instrumentation_profile": ("rhodes", "808_bass", "reverb_guitar"),
        "production_style": "warm_analog",
        "track_duration": {"min_seconds": 160, "max_seconds": 220, "target_seconds": 190},
        "playlist_duration": {"min_seconds": 2700, "max_seconds": 3600, "target_seconds": 3000},
        "visual_world": "rainy_asian_city",
        "visual_motion_profile": "cinematic_drift",
        "thumbnail_family": "illustrated_lofi",
        "title_archetype": "evocative_phrase",
    },
    "rnb_late_night": {
        "parent": "rnb_default",
        "mood": ("melancholic", "dreamy"),
        "use_case": ("late_night_drive",),
        "visual_world": "rainy_asian_city",
    },
    "lofi_chill": {
        "genre": "lofi",
        "subgenre": "lofi_hiphop",
        "mood": ("calm", "nostalgic", "reflective"),
        "use_case": ("study", "relax", "sleep"),
        "language": "english",
        "vocal_mode": VocalMode.INSTRUMENTAL,
        "vocal_gender": VocalGender.NONE,
        "energy": EnergyLevel.LOW,
        "bpm": {"min_bpm": 65, "max_bpm": 85, "target_bpm": 75},
        "key_profile": "F_major",
        "lyric_density": "sparse",
        "song_structure": "loop_progression",
        "hook_profile": "instrumental_riff",
        "instrumentation_profile": ("dusty_rhodes", "vinyl_crackle", "sidechain_kick"),
        "production_style": "tape_saturated",
        "track_duration": {"min_seconds": 120, "max_seconds": 180, "target_seconds": 150},
        "playlist_duration": {"min_seconds": 3600, "max_seconds": 7200, "target_seconds": 5400},
        "visual_world": "lofi_study_bedroom",
        "visual_motion_profile": "subtle_loop",
        "thumbnail_family": "anime_aesthetic",
        "title_archetype": "ambient_mood_time",
    },
}


class GenomeEngine:
    """Manages genome templates, inheritance, and normalization (§67–§69)."""

    def __init__(self, templates: dict[str, dict[str, Any]] | None = None) -> None:
        self._templates = dict(templates or DEFAULT_TEMPLATES)

    def get_template(self, name: str) -> dict[str, Any]:
        """Resolve a template, merging parent overrides if present."""
        if name not in self._templates:
            raise TemplateNotFoundError(f"Genome template '{name}' not found")

        template = dict(self._templates[name])
        parent_name = template.pop("parent", None)
        if parent_name:
            parent = self.get_template(parent_name)
            merged = dict(parent)
            merged.update(template)
            return merged
        return template


class CreativeLatitudeEnvelope(BaseModel):
    """Bounds on permissible creative variation downstream (§25)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    bpm_variance_pct: float = Field(ge=0.0, le=0.5)
    allowed_visual_worlds: tuple[str, ...]
    allowed_vocal_modes: tuple[VocalMode, ...] = (VocalMode.VOCAL, VocalMode.INSTRUMENTAL)

    @classmethod
    def default_for_exploration(cls, level: str) -> CreativeLatitudeEnvelope:
        if level == "radical_explore":
            return cls(
                bpm_variance_pct=0.15,
                allowed_visual_worlds=(
                    "rainy_asian_city",
                    "tokyo_cyberpunk",
                    "misty_highways",
                    "midnight_diner",
                ),
            )
        if level == "adjacent_explore":
            return cls(
                bpm_variance_pct=0.10,
                allowed_visual_worlds=("rainy_asian_city", "misty_highways"),
            )
        # exploit default
        return cls(
            bpm_variance_pct=0.05,
            allowed_visual_worlds=("rainy_asian_city",),
        )


def build_genome_from_directive(directive: AdaptedDirective) -> ContentGenome:
    """Synthesize a canonical ContentGenome from an ingested P2 directive."""
    brief = directive.brief

    raw_genre = str(brief.get("genre", "rnb"))
    genre = normalize_dimension_value("genre", raw_genre)

    # Pick base template
    engine = GenomeEngine()
    template_name = "rnb_late_night" if genre == "rnb" else "lofi_chill"
    base = engine.get_template(template_name)

    raw_moods = brief.get("mood", base["mood"])
    if isinstance(raw_moods, (list, tuple)):
        moods = tuple(normalize_dimension_value("mood", str(m)) for m in raw_moods)
    else:
        moods = (normalize_dimension_value("mood", str(raw_moods)),)

    raw_use_cases = brief.get("use_case", base["use_case"])
    if isinstance(raw_use_cases, (list, tuple)):
        use_cases = tuple(normalize_dimension_value("use_case", str(u)) for u in raw_use_cases)
    else:
        use_cases = (normalize_dimension_value("use_case", str(raw_use_cases)),)

    language = normalize_dimension_value("language", str(brief.get("language", base["language"])))
    raw_vocal = str(brief.get("vocal_mode", base["vocal_mode"])).lower()
    vocal_mode = VocalMode.INSTRUMENTAL if "instrumental" in raw_vocal else VocalMode.VOCAL

    visual_world = normalize_dimension_value(
        "visual_world", str(brief.get("visual_world", base["visual_world"]))
    )

    bpm_cfg = base["bpm"]
    bpm = BpmRange(
        min_bpm=bpm_cfg["min_bpm"],
        max_bpm=bpm_cfg["max_bpm"],
        target_bpm=bpm_cfg["target_bpm"],
    )

    trk_dur = DurationRange(
        min_seconds=base["track_duration"]["min_seconds"],
        max_seconds=base["track_duration"]["max_seconds"],
        target_seconds=base["track_duration"]["target_seconds"],
    )
    ply_dur = DurationRange(
        min_seconds=base["playlist_duration"]["min_seconds"],
        max_seconds=base["playlist_duration"]["max_seconds"],
        target_seconds=base["playlist_duration"]["target_seconds"],
    )

    return ContentGenome(
        genre=genre,
        subgenre=base.get("subgenre"),
        mood=moods,
        use_case=use_cases,
        language=language,
        vocal_mode=vocal_mode,
        vocal_gender=base["vocal_gender"] if vocal_mode == VocalMode.VOCAL else VocalGender.NONE,
        energy=base["energy"],
        bpm=bpm,
        key_profile=base.get("key_profile"),
        lyric_density=base["lyric_density"],
        song_structure=base["song_structure"],
        hook_profile=base["hook_profile"],
        instrumentation_profile=base["instrumentation_profile"],
        production_style=base["production_style"],
        track_duration=trk_dur,
        playlist_duration=ply_dur,
        visual_world=visual_world,
        visual_motion_profile=base["visual_motion_profile"],
        thumbnail_family=base["thumbnail_family"],
        title_archetype=base["title_archetype"],
    )
