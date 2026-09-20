"""The decision unit: P1's arm, as P2 sees it over the frozen C1 wire (R15).

P2 never mints an arm and never rewrites taxonomy; `arm_id` arrives from P1 verbatim.
The fingerprint below is P2's *own* canonical form, built from exactly the nine taxonomy
keys the C1 payload carries — P1's `market_arms.fingerprint` is not on the wire.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

__all__ = [
    "DIMENSION_ORDER", "MEASURED_COMPONENT_KEYS", "ArmDimensions", "ContentArm",
    "canonical_fingerprint",
]

DIMENSION_ORDER: tuple[str, ...] = (
    "genre", "subgenre", "mood", "use_case", "language", "vocal_mode",
    "duration_bucket", "visual_world", "title_archetype",
)
"""The nine taxonomy keys of the frozen C1 payload (registry C1), in fingerprint order."""

MEASURED_COMPONENT_KEYS: tuple[str, ...] = (
    "demand", "growth", "competitor_velocity", "competition", "audience_quality",
    "cross_platform", "production_fit",
)
"""§20's seven component keys; a `null` component was not measured — never a zero."""


@dataclass(frozen=True, slots=True)
class ArmDimensions:
    """The frozen nine C1 taxonomy keys. Multi-valued keys stay as sorted token tuples."""

    genre: str | None = None
    subgenre: str | None = None
    moods: tuple[str, ...] = ()
    use_cases: tuple[str, ...] = ()
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None
    title_archetype: str | None = None

    def tokens(self, name: str) -> tuple[str, ...] | None:
        """The tokens present for one dimension, or None when the wire sent nothing."""
        if name == "mood":
            return tuple(sorted(self.moods)) or None
        if name == "use_case":
            return tuple(sorted(self.use_cases)) or None
        value = getattr(self, name)
        return None if value is None else (value,)


def canonical_fingerprint(dims: ArmDimensions) -> str:
    """`genre=rnb|mood=dreamy,melancholic|…` — deterministic, order-insensitive per (§7)."""
    parts: list[str] = []
    for name in DIMENSION_ORDER:
        tokens = dims.tokens(name)
        parts.append(f"{name}={'+'.join(tokens) if tokens else ''}")
    return "|".join(parts)


@dataclass(frozen=True, slots=True)
class ContentArm:
    """One ranked P1 opportunity, adapted to P2's domain (P2.2 builds these).

    `component_scores` maps the seven frozen C1 component keys to a measured value or
    `None`; a value that was never observed stays `None` all the way into features and
    decisions (Review Focus 1). `freshness_seconds` is P2-derived from `as_of`.
    """

    arm_id: str
    dimensions: ArmDimensions
    p1_score: float
    p1_confidence: float
    component_scores: Mapping[str, float | None]
    observation_count: int
    source_count: int
    as_of: datetime
    score_version: str
    freshness_seconds: float
    _extra: Mapping[str, object] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (registry C1 derives freshness from it)")
        if not self.arm_id:
            raise ValueError("arm_id must not be empty")
        if not 0.0 <= self.p1_confidence <= 1.0:
            raise ValueError(f"p1_confidence must be in [0, 1], got {self.p1_confidence}")
        if not 0.0 <= self.p1_score <= 100.0:
            raise ValueError(f"p1_score must be in [0, 100], got {self.p1_score}")
        expected = frozenset(MEASURED_COMPONENT_KEYS)
        if frozenset(self.component_scores) != expected:
            raise ValueError(
                "component_scores must carry exactly the seven frozen C1 keys "
                f"(missing={sorted(expected - frozenset(self.component_scores))}, "
                f"unknown={sorted(frozenset(self.component_scores) - expected)})"
            )
        if self.freshness_seconds < 0.0:
            raise ValueError("freshness_seconds must not be negative")

    @property
    def measured_component_count(self) -> int:
        """How many of the seven components were measured (non-null)."""
        return sum(1 for value in self.component_scores.values() if value is not None)

    def is_rankable(self, *, minimum: int = 2) -> bool:
        """ADR-0023 #5: ranked only with at least `minimum` measured components."""
        return self.measured_component_count >= minimum
