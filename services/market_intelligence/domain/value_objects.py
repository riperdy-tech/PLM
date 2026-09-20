# services/market_intelligence/domain/value_objects.py
"""Small deterministic value types. No I/O, no randomness, no clock access."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from plm_contracts import Tri

from .enums import FreshnessBucket, ScoringComponent, TaxonomyDimension

__all__ = [
    "ComponentScoresLike", "EvidenceSummary", "FreshnessAssessment", "ScoreComponents",
    "TaxonomyProfile", "classify_freshness", "fingerprint", "slug_token", "tri_from_optional",
]

_UNSET: Final[str] = "none"
_NON_TOKEN: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class TaxonomyProfile:
    """The 13 §14.2 dimensions as one comparable object; unset means unclassified."""

    genre: str | None = None
    subgenre: str | None = None
    mood: tuple[str, ...] = ()
    use_case: str | None = None
    language: str | None = None
    vocal_mode: str | None = None
    vocal_gender: str | None = None
    energy: str | None = None
    bpm_range: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None
    audience_intent: str | None = None
    title_archetype: str | None = None

    def value_for(self, dimension: TaxonomyDimension) -> str:
        """The canonical token for one dimension; `none` when unset; moods joined."""
        raw: Any = getattr(self, dimension.value)
        if raw is None or raw == () or raw == "":
            return _UNSET
        if isinstance(raw, tuple):
            return "+".join(raw)
        return str(raw)


def fingerprint(profile: TaxonomyProfile) -> str:
    """Canonical identity string, one `dimension=value` part per dimension, in enum order.

    Unset dimensions are explicit (`bpm_range=none`), never omitted: two arms that
    differ only by an unset dimension must not collapse into one identity.
    """
    return "|".join(f"{d.value}={profile.value_for(d)}" for d in TaxonomyDimension)


def slug_token(text: str, *, max_length: int = 48) -> str:
    """Lowercase identifier fragment: non-alphanumerics → `_`, collapsed, truncated."""
    slug = _NON_TOKEN.sub("_", text.strip().lower()).strip("_")
    return slug[:max_length].rstrip("_")


def tri_from_optional(value: float | None) -> Tri[float]:
    """`NULL` from the database becomes `Tri.unknown()`, never `0` (Review Focus 1)."""
    return Tri.unknown() if value is None else Tri.known(value)


@dataclass(frozen=True, slots=True)
class ScoreComponents:
    """§17.2 component scores, each 0–100; `confidence` is deliberately absent."""

    demand: float
    growth: float
    competitor_velocity: float
    competition: float
    audience_quality: float
    cross_platform: float
    production_fit: float

    def __post_init__(self) -> None:
        for component in ScoringComponent:
            value = getattr(self, component.value)
            if not 0.0 <= float(value) <= 100.0:
                raise ValueError(f"{component.value} must be within 0–100, got {value!r}")

    @classmethod
    def from_mapping(cls, values: Mapping[str, float]) -> ScoreComponents:
        missing = {c.value for c in ScoringComponent} - set(values)
        if missing:
            raise ValueError(f"missing component scores: {sorted(missing)}")
        return cls(**{c.value: float(values[c.value]) for c in ScoringComponent})

    def as_mapping(self) -> dict[ScoringComponent, float]:
        """The wire dict of §20 keys on `ScoringComponent` members."""
        return {c: float(getattr(self, c.value)) for c in ScoringComponent}


class ComponentScoresLike(Protocol):
    """The shape both component containers share: `ScoreComponents` (all-float) and
    the Tri-valued `ArmComponentScores` in `scoring/opportunity.py`. An
    `OpportunityScore` may hold either, so the domain type is the protocol."""

    def as_mapping(self) -> Mapping[ScoringComponent, Any]: ...


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    """§20 `evidence` object; counts are observations, not entity counts.

    `observation_ids` is the **full** input set behind a score (§30 reconstruction):
    the frozen C1 wire exposes only the first five as `top_observation_ids`, but a
    stored score whose evidence stopped at five could not be recomputed when more
    observations had fed it (review fix 2).
    """

    observation_count: int
    source_count: int
    observation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.observation_count < 0 or self.source_count < 0:
            raise ValueError("evidence counts cannot be negative")

    @property
    def top_observation_ids(self) -> tuple[str, ...]:
        """The wired view: the first five ids in deterministic input order."""
        return self.observation_ids[:5]


@dataclass(frozen=True, slots=True)
class FreshnessAssessment:
    bucket: FreshnessBucket
    age_seconds: float


def classify_freshness(
    *,
    as_of: datetime,
    now: datetime,
    fresh_hours: float = 48.0,
    normal_hours: float = 168.0,
    stale_hours: float = 720.0,
) -> FreshnessAssessment:
    """§21 buckets. Naive datetimes are refused — one timezone, always (P0 §3 item 5)."""
    if as_of.tzinfo is None or now.tzinfo is None:
        raise ValueError("freshness needs timezone-aware datetimes; use datetime.now(UTC)")
    age_seconds = (now - as_of).total_seconds()
    if age_seconds < 0:
        raise ValueError("as_of is in the future relative to now")
    hours = age_seconds / 3600
    if hours <= fresh_hours:
        bucket = FreshnessBucket.FRESH
    elif hours <= normal_hours:
        bucket = FreshnessBucket.NORMAL
    elif hours <= stale_hours:
        bucket = FreshnessBucket.STALE
    else:
        bucket = FreshnessBucket.EXPIRED
    return FreshnessAssessment(bucket=bucket, age_seconds=age_seconds)
