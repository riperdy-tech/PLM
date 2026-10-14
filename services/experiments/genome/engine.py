"""Genome Engine: normalization, data-driven templates and creative latitude (P3 §6–§9, §25, §67–§69).

Templates are versioned configuration, not code (§67): they live in
``genome/templates/genome_templates.json``, keyed by genre, and are validated when loaded.
A genre without a template is refused with ``unknown_genre``; it never borrows another
genre's template. Each template also carries the genre's ontology for experimentable
categorical dimensions (``alternatives``), which the treatment generator draws from.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from plm_contracts import AllocationType
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..domain.enums import VocalGender, VocalMode
from ..domain.errors import GenomeBuildError
from ..domain.genome import CANONICAL_DIMENSIONS, ContentGenome

if TYPE_CHECKING:
    from ..ingestion.p2_adapter import AdaptedDirective

__all__ = [
    "ALTERNATIVE_DIMENSIONS",
    "TEMPLATES_PATH",
    "CreativeLatitudeEnvelope",
    "GenomeEngine",
    "GenreTemplate",
    "TemplateLibrary",
    "TemplateLibraryError",
    "TemplateNotFoundError",
    "UnknownGenreError",
    "build_genome_from_directive",
    "default_template_library",
    "normalize_dimension_value",
]

TEMPLATES_PATH: Final[Path] = Path(__file__).resolve().parent / "templates" / "genome_templates.json"

ALIAS_MAP: Mapping[str, str] = {
    "r&b": "rnb",
    "r_and_b": "rnb",
    "hip-hop": "hip_hop",
    "hip hop": "hip_hop",
    "lo-fi": "lofi",
    "lo fi": "lofi",
}

ALTERNATIVE_DIMENSIONS: Final[frozenset[str]] = frozenset(
    {
        "visual_world",
        "visual_motion_profile",
        "thumbnail_family",
        "title_archetype",
        "hook_profile",
        "song_structure",
        "production_style",
        "lyric_density",
        "key_profile",
    }
)
"""Categorical dimensions a template may give an ontology of alternatives for."""

_TEMPLATE_META_KEYS: Final[frozenset[str]] = frozenset({"parent", "alternatives"})


def normalize_dimension_value(dimension: str, value: str) -> str:
    """Normalize dimension string values to canonical lowercase snake_case tokens."""
    cleaned = value.strip().lower()
    cleaned = ALIAS_MAP.get(cleaned, cleaned)
    cleaned = re.sub(r"[\s\-]+", "_", cleaned)
    cleaned = re.sub(r"[^\w_]", "", cleaned)
    return cleaned.strip("_")


class TemplateLibraryError(ValueError):
    """The template data file is malformed. Raised at load time, never at use time."""


class TemplateNotFoundError(GenomeBuildError):
    """A referenced genome template does not exist."""

    def __init__(self, name: str) -> None:
        super().__init__(
            "template_not_found", f"Genome template '{name}' not found", field_paths=[name]
        )


class UnknownGenreError(GenomeBuildError):
    """No template is declared for the genre; another genre's template is never used."""

    def __init__(self, genre: str, known: tuple[str, ...]) -> None:
        super().__init__(
            "unknown_genre",
            f"No genome template for genre {genre!r}; known genres: {', '.join(known)}",
            field_paths=["brief.genre"],
        )


class GenreTemplate(BaseModel):
    """A resolved, validated template: the default genome and the genre's ontology."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_name: str
    genome: ContentGenome
    alternatives: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class TemplateLibrary:
    """Validated genome templates keyed by name, and the genre → template mapping."""

    def __init__(self, raw: Mapping[str, Any]) -> None:
        try:
            self.version = str(raw["templates_version"])
            self._templates: dict[str, dict[str, Any]] = {
                str(k): dict(v) for k, v in raw["templates"].items()
            }
            self._genres: dict[str, str] = {
                str(k): str(v) for k, v in raw["genre_templates"].items()
            }
        except (KeyError, AttributeError, TypeError) as exc:
            raise TemplateLibraryError(f"template library is missing a section: {exc}") from exc
        if not self.version:
            raise TemplateLibraryError("templates_version must be non-empty")
        self._resolved: dict[str, GenreTemplate] = {}
        for name in sorted(self._templates):
            self._resolved[name] = self._build(name)
        for genre, name in sorted(self._genres.items()):
            if normalize_dimension_value("genre", genre) != genre:
                raise TemplateLibraryError(f"genre key {genre!r} is not a normalized token")
            if name not in self._resolved:
                raise TemplateLibraryError(f"genre {genre!r} maps to missing template {name!r}")
            if self._resolved[name].genome.genre != genre:
                raise TemplateLibraryError(
                    f"genre {genre!r} maps to template {name!r} of genre "
                    f"{self._resolved[name].genome.genre!r}"
                )

    @classmethod
    def from_file(cls, path: Path) -> TemplateLibrary:
        with path.open(encoding="utf-8") as handle:
            return cls(json.load(handle))

    @property
    def genres(self) -> tuple[str, ...]:
        return tuple(sorted(self._genres))

    def resolve(self, name: str, _chain: tuple[str, ...] = ()) -> dict[str, Any]:
        """Merge a template over its parents; a child wins only where it sets a value (§68)."""
        if name not in self._templates:
            raise TemplateNotFoundError(name)
        if name in _chain:
            raise TemplateLibraryError(f"template inheritance cycle: {' -> '.join((*_chain, name))}")
        template = dict(self._templates[name])
        parent_name = template.pop("parent", None)
        if parent_name is None:
            return template
        merged = self.resolve(str(parent_name), (*_chain, name))
        alternatives = dict(merged.get("alternatives", {}))
        alternatives.update(template.pop("alternatives", {}))
        merged.update(template)
        if alternatives:
            merged["alternatives"] = alternatives
        return merged

    def template(self, name: str) -> GenreTemplate:
        if name not in self._resolved:
            raise TemplateNotFoundError(name)
        return self._resolved[name]

    def for_genre(self, genre: str) -> GenreTemplate:
        name = self._genres.get(genre)
        if name is None:
            raise UnknownGenreError(genre, self.genres)
        return self._resolved[name]

    def _build(self, name: str) -> GenreTemplate:
        unknown = set(self._templates[name]) - set(CANONICAL_DIMENSIONS) - _TEMPLATE_META_KEYS
        if unknown:
            raise TemplateLibraryError(f"template {name!r} has unknown keys {sorted(unknown)}")
        resolved = self.resolve(name)
        missing = [d for d in CANONICAL_DIMENSIONS if d not in resolved]
        if missing:
            raise TemplateLibraryError(f"template {name!r} does not declare {missing}")
        try:
            genome = ContentGenome.model_validate({d: resolved[d] for d in CANONICAL_DIMENSIONS})
        except ValidationError as exc:
            raise TemplateLibraryError(f"template {name!r} is not a valid genome: {exc}") from exc
        alternatives: dict[str, tuple[str, ...]] = {}
        for dim, values in dict(resolved.get("alternatives", {})).items():
            if dim not in ALTERNATIVE_DIMENSIONS:
                raise TemplateLibraryError(
                    f"template {name!r} declares alternatives for {dim!r}, which is not an "
                    "alternative-bearing dimension"
                )
            tokens = tuple(str(v) for v in values)
            if not tokens or any(normalize_dimension_value(dim, t) != t for t in tokens):
                raise TemplateLibraryError(
                    f"template {name!r} alternatives for {dim!r} must be non-empty normalized tokens"
                )
            if len(set(tokens)) != len(tokens):
                raise TemplateLibraryError(f"template {name!r} repeats an alternative for {dim!r}")
            alternatives[dim] = tokens
        return GenreTemplate(template_name=name, genome=genome, alternatives=alternatives)


@lru_cache(maxsize=1)
def default_template_library() -> TemplateLibrary:
    """The shipped template library, loaded and validated once per process."""
    return TemplateLibrary.from_file(TEMPLATES_PATH)


class GenomeEngine:
    """Manages genome templates, inheritance, and normalization (§67–§69)."""

    def __init__(self, library: TemplateLibrary | None = None) -> None:
        self.library = library or default_template_library()

    def get_template(self, name: str) -> dict[str, Any]:
        """Resolve a template, merging parent overrides if present."""
        return self.library.resolve(name)

    def template_for_genre(self, genre: str) -> GenreTemplate:
        return self.library.for_genre(genre)


PACKAGING_VARIABLES: Final[tuple[str, ...]] = (
    "title_archetype",
    "thumbnail_family",
    "visual_motion_profile",
)


class CreativeLatitudeEnvelope(BaseModel):
    """Bounds on permissible creative variation for one exploration level (§25).

    ``experimentable_variables`` is the set of genome dimensions an experiment at this
    level may vary; values for categorical ones come from the genre's ontology.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    exploration_level: AllocationType
    bpm_variance_pct: float = Field(ge=0.0, le=0.5)
    energy_steps: int = Field(ge=0, le=4)
    experimentable_variables: tuple[str, ...]
    allowed_vocal_modes: tuple[VocalMode, ...] = (VocalMode.VOCAL, VocalMode.INSTRUMENTAL)

    def permits(self, variable: str) -> bool:
        return variable in self.experimentable_variables

    @classmethod
    def default_for_exploration(cls, level: str) -> CreativeLatitudeEnvelope:
        allocation = AllocationType(level)
        if allocation is AllocationType.RADICAL_EXPLORE:
            return cls(
                exploration_level=allocation,
                bpm_variance_pct=0.15,
                energy_steps=2,
                experimentable_variables=(
                    "bpm",
                    "energy",
                    *PACKAGING_VARIABLES,
                    "visual_world",
                    "hook_profile",
                    "production_style",
                    "song_structure",
                    "lyric_density",
                ),
            )
        if allocation is AllocationType.ADJACENT_EXPLORE:
            return cls(
                exploration_level=allocation,
                bpm_variance_pct=0.10,
                energy_steps=1,
                experimentable_variables=(
                    "bpm",
                    "energy",
                    *PACKAGING_VARIABLES,
                    "visual_world",
                    "hook_profile",
                    "production_style",
                ),
            )
        return cls(
            exploration_level=allocation,
            bpm_variance_pct=0.05,
            energy_steps=1,
            experimentable_variables=("bpm", "energy", *PACKAGING_VARIABLES),
        )


def _present(value: Any) -> bool:
    return value not in (None, "", [], ())


def _token_tuple(dimension: str, value: Any) -> tuple[str, ...]:
    items = value if isinstance(value, (list, tuple)) else (value,)
    return tuple(normalize_dimension_value(dimension, str(item)) for item in items)


def build_genome_from_directive(
    directive: AdaptedDirective, *, library: TemplateLibrary | None = None
) -> ContentGenome:
    """Build the base genome: the genre's template, overridden where the brief is explicit.

    The genre must be named in the brief and have a template; otherwise the directive is
    refused (``genre_unspecified`` / ``unknown_genre``). Brief dimensions that are unset
    are open, so the template's default applies.
    """
    lib = library or default_template_library()
    brief = directive.brief

    raw_genre = brief.get("genre")
    if not _present(raw_genre):
        raise GenomeBuildError(
            "genre_unspecified",
            "The directive brief names no genre; P3 cannot choose a genome template",
            field_paths=["brief.genre"],
        )
    genre = normalize_dimension_value("genre", str(raw_genre))
    base = lib.for_genre(genre).genome

    updates: dict[str, Any] = {}
    if _present(brief.get("mood")):
        updates["mood"] = _token_tuple("mood", brief["mood"])
    if _present(brief.get("use_case")):
        updates["use_case"] = _token_tuple("use_case", brief["use_case"])
    if _present(brief.get("language")):
        updates["language"] = normalize_dimension_value("language", str(brief["language"]))
    if _present(brief.get("visual_world")):
        updates["visual_world"] = normalize_dimension_value(
            "visual_world", str(brief["visual_world"])
        )
    if _present(brief.get("vocal_mode")):
        token = normalize_dimension_value("vocal_mode", str(brief["vocal_mode"]))
        try:
            vocal_mode = VocalMode(token)
        except ValueError as exc:
            raise GenomeBuildError(
                "unknown_vocal_mode",
                f"Brief vocal_mode {token!r} is not one of {[m.value for m in VocalMode]}",
                field_paths=["brief.vocal_mode"],
            ) from exc
        updates["vocal_mode"] = vocal_mode
        if vocal_mode is VocalMode.INSTRUMENTAL:
            updates["vocal_gender"] = VocalGender.NONE

    dumped = base.model_dump()
    dumped.update(updates)
    return ContentGenome.model_validate(dumped)
