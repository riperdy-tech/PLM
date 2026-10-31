"""Genome Engine: normalization, data-driven templates and creative latitude (P3 §6–§9, §25, §67–§69).

Templates are versioned configuration, not code (§67): they live in
``genome/templates/genome_templates.json``, keyed by genre, and are validated when loaded.
A genre without a template is refused with ``unknown_genre``; it never borrows another
genre's template. Each template also carries the genre's ontology for experimentable
categorical dimensions (``alternatives``), which the treatment generator draws from.

The same file declares ``playlist_duration_buckets``: the playlist-length range each P2
``duration_bucket`` stands for, and ``playlist_duration_policy``: the owner's playlist
length (decision 2026-10-15, ADR-0036: 45–75 minutes, the ``45_75m`` bucket; the target
is only the planning midpoint). The policy is the single source of a playlist range:

* a brief that names a bucket gets that bucket's range, which must lie inside the
  policy (``playlist_duration_outside_policy`` otherwise, never clamped);
* a brief that names none gets the policy range itself (P1's arms carry no duration
  bucket unless a title names one, so P2's directives often name none);
* a bucket the table does not declare is refused (``playlist_duration_bucket_unknown``),
  never mapped to a nearby one;
* every genre template's ``playlist_duration`` must lie inside the policy, or the
  library refuses to load (R5: lofi and jazz templates declared 60–120 min, ambient
  60–180, and an open brief inherited them).
"""

from __future__ import annotations

import hashlib
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
from ..domain.genome import CANONICAL_DIMENSIONS, ContentGenome, DurationRange

if TYPE_CHECKING:
    from ..ingestion.p2_adapter import AdaptedDirective

__all__ = [
    "ALTERNATIVE_DIMENSIONS",
    "PLAYLIST_DURATION_BUCKET_UNKNOWN",
    "PLAYLIST_DURATION_OUTSIDE_POLICY",
    "TEMPLATES_PATH",
    "CreativeLatitudeEnvelope",
    "GenomeEngine",
    "GenreTemplate",
    "TemplateLibrary",
    "TemplateLibraryError",
    "TemplateNotFoundError",
    "UnknownGenreError",
    "brief_genre",
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
    # P1 names a house subgenre; P3's house template is the deep-house one.
    "deep_house": "house",
    "deep house": "house",
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

PLAYLIST_DURATION_BUCKET_UNKNOWN: Final[str] = "playlist_duration_bucket_unknown"
PLAYLIST_DURATION_OUTSIDE_POLICY: Final[str] = "playlist_duration_outside_policy"


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
        self._inference = self._parse_inference(raw.get("brief_genre_inference"))
        self._buckets = self._parse_buckets(raw.get("playlist_duration_buckets", {}))
        self._policy = self._parse_policy(raw.get("playlist_duration_policy"))
        self._resolved: dict[str, GenreTemplate] = {}
        for name in sorted(self._templates):
            self._resolved[name] = self._build(name)
            declared = self._resolved[name].genome.playlist_duration
            if not self._inside_policy(declared):
                raise TemplateLibraryError(
                    f"{PLAYLIST_DURATION_OUTSIDE_POLICY}: template {name!r} declares a "
                    f"{declared.min_seconds}-{declared.max_seconds} s playlist, outside the "
                    f"policy range {self._policy.min_seconds}-{self._policy.max_seconds} s"
                )
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

        for genres in [*self._inference.values()]:
            unknown = [genre for genre in genres if genre not in self._genres]
            if unknown:
                raise TemplateLibraryError(f"brief_genre_inference names unknown genres {unknown}")

    @staticmethod
    def _parse_inference(raw: Any) -> dict[str, tuple[str, ...]]:
        """``{use_case: genres}`` plus ``""`` for an open brief; absent means no inference."""
        if raw is None:
            return {}
        try:
            table = {str(k): tuple(str(g) for g in v) for k, v in raw["by_use_case"].items()}
            table[""] = tuple(str(g) for g in raw["open_brief"])
        except (KeyError, AttributeError, TypeError) as exc:
            raise TemplateLibraryError(f"brief_genre_inference is malformed: {exc}") from exc
        if any(not genres for genres in table.values()):
            raise TemplateLibraryError("brief_genre_inference lists an empty genre set")
        return table

    def infer_genre(self, use_cases: tuple[str, ...], directive_id: str) -> tuple[str, str]:
        """A genre for a brief that names none, and the note that records why.

        The first use case with an entry decides the candidates (none: the open list); a
        stable hash of the directive id picks one, so repeated arms spread across genres.
        """
        use_case = next((u for u in use_cases if u in self._inference), "")
        candidates = self._inference.get(use_case)
        if candidates is None:
            raise GenomeBuildError(
                "genre_unspecified",
                "The directive brief names no genre and the templates declare no inference",
                field_paths=["brief.genre"],
            )
        digest = hashlib.sha256(directive_id.encode("utf-8")).digest()
        genre = candidates[int.from_bytes(digest[:4], "big") % len(candidates)]
        return genre, f"genre_inferred:{genre}:use_case={use_case or 'none'}"

    @classmethod
    def from_file(cls, path: Path) -> TemplateLibrary:
        with path.open(encoding="utf-8") as handle:
            return cls(json.load(handle))

    @property
    def genres(self) -> tuple[str, ...]:
        return tuple(sorted(self._genres))

    @property
    def playlist_duration_buckets(self) -> tuple[str, ...]:
        return tuple(sorted(self._buckets))

    @property
    def playlist_duration_policy(self) -> DurationRange:
        """The owner's playlist range (ADR-0036): what an open brief gets."""
        return self._policy

    def playlist_duration_for_bucket(self, bucket: str) -> DurationRange:
        """The declared playlist range of a P2 ``duration_bucket``; an unknown bucket or
        one outside the policy range is refused, never mapped or clamped."""
        found = self._buckets.get(bucket)
        if found is None:
            raise GenomeBuildError(
                PLAYLIST_DURATION_BUCKET_UNKNOWN,
                f"duration_bucket {bucket!r} declares no playlist range; declared buckets: "
                f"{', '.join(sorted(self._buckets)) or 'none'}",
                field_paths=["brief.duration_bucket"],
            )
        if not self._inside_policy(found):
            raise GenomeBuildError(
                PLAYLIST_DURATION_OUTSIDE_POLICY,
                f"duration_bucket {bucket!r} ({found.min_seconds}-{found.max_seconds} s) lies "
                f"outside the playlist policy {self._policy.min_seconds}-"
                f"{self._policy.max_seconds} s (ADR-0036)",
                field_paths=["brief.duration_bucket"],
            )
        return found

    def _inside_policy(self, declared: DurationRange) -> bool:
        return (self._policy.min_seconds <= declared.min_seconds
                and declared.max_seconds <= self._policy.max_seconds)

    def _parse_policy(self, raw: Any) -> DurationRange:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("default_bucket"), str):
            raise TemplateLibraryError(
                "playlist_duration_policy with a default_bucket is required (ADR-0036); "
                "nothing defaults a playlist length")
        bucket = str(raw["default_bucket"])
        if bucket not in self._buckets:
            raise TemplateLibraryError(
                f"playlist_duration_policy names undeclared bucket {bucket!r}")
        return self._buckets[bucket]

    @staticmethod
    def _parse_buckets(raw: Any) -> dict[str, DurationRange]:
        if not isinstance(raw, Mapping):
            raise TemplateLibraryError("playlist_duration_buckets must be an object")
        buckets: dict[str, DurationRange] = {}
        for name, value in sorted(raw.items()):
            try:
                declared = DurationRange.model_validate(value)
            except ValidationError as exc:
                raise TemplateLibraryError(
                    f"playlist duration bucket {name!r} is not a valid range: {exc}"
                ) from exc
            midpoint = round((declared.min_seconds + declared.max_seconds) / 2)
            if declared.target_seconds != midpoint:
                raise TemplateLibraryError(
                    f"playlist duration bucket {name!r} must declare its midpoint "
                    f"{midpoint} as target_seconds (the target is only a planning midpoint)"
                )
            buckets[str(name)] = declared
        return buckets

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


def brief_genre(
    directive: AdaptedDirective, *, library: TemplateLibrary | None = None
) -> tuple[str, str | None]:
    """The brief's genre, or one inferred from its use case (with the note recording it)."""
    lib = library or default_template_library()
    raw_genre = directive.brief.get("genre")
    if _present(raw_genre):
        return normalize_dimension_value("genre", str(raw_genre)), None
    use_cases = (_token_tuple("use_case", directive.brief["use_case"])
                 if _present(directive.brief.get("use_case")) else ())
    return lib.infer_genre(use_cases, directive.directive_id)


def build_genome_from_directive(
    directive: AdaptedDirective, *, library: TemplateLibrary | None = None
) -> ContentGenome:
    """Build the base genome: the genre's template, overridden where the brief is explicit.

    A brief that names no genre gets one from its use case (:func:`brief_genre`, owner
    decision 2026-10-31); a named genre without a template is refused (``unknown_genre``). Brief dimensions that are unset
    are open, so the template's default applies, except the playlist range: a named
    ``duration_bucket`` sets it to the bucket's declared range (inside the policy), and
    an open one to the policy range (ADR-0036).
    """
    lib = library or default_template_library()
    brief = directive.brief
    genre, _ = brief_genre(directive, library=lib)
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
    if _present(brief.get("duration_bucket")):
        bucket = normalize_dimension_value("duration_bucket", str(brief["duration_bucket"]))
        updates["playlist_duration"] = lib.playlist_duration_for_bucket(bucket)
    else:
        updates["playlist_duration"] = lib.playlist_duration_policy

    dumped = base.model_dump()
    dumped.update(updates)
    return ContentGenome.model_validate(dumped)
