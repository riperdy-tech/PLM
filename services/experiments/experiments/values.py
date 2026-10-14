"""Ontology-driven treatment value generation (P3 §14, §15, §25, §36, §37, §73).

Every experimentable genome dimension has a registered generator that returns the
*distinct, valid* alternatives to the baseline, in preference order:

* ``bpm``: target shifts within the envelope's ``bpm_variance_pct`` of the baseline
  target, largest first, alternating up/down; each keeps the baseline range's width.
* ``energy``: neighbouring ``EnergyLevel`` steps within ``energy_steps``, nearest first.
* categorical dimensions (visual world, packaging, hook, structure, production, lyric
  density): the genre ontology's alternatives (the genome template's ``alternatives``),
  minus the baseline, in a seeded order.

A variable with no generator, a hard constraint, a variable outside the exploration
level's creative latitude, or one with no alternative left is refused with a reason code.
Nothing is ever filled with a placeholder.

Randomness is only used to order categorical alternatives. It is derived from
``stable_seed(namespace, directive_id, seed, variable)``, never from ``hash()`` or time.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from plm_platform.clock import stable_seed

from ..domain.enums import ConstraintType, EnergyLevel
from ..domain.errors import ExperimentDesignError
from ..domain.genome import DEFAULT_CONSTRAINT_HIERARCHY, ContentGenome
from ..genome.engine import ALTERNATIVE_DIMENSIONS, CreativeLatitudeEnvelope

__all__ = [
    "RANDOMIZATION_ALGORITHM",
    "RANDOMIZATION_VERSION",
    "VALUE_GENERATORS",
    "GenerationContext",
    "baseline_value",
    "candidate_values",
    "canonical",
]

RANDOMIZATION_NAMESPACE: Final[str] = "p3.treatment_values"
RANDOMIZATION_ALGORITHM: Final[str] = "stable_seed(sha256)+random.Random.sample"
RANDOMIZATION_VERSION: Final[str] = "p3-treatment-values-v1"

_BPM_FLOOR: Final[int] = 20
_BPM_CEILING: Final[int] = 300


@dataclass(frozen=True)
class GenerationContext:
    genome: ContentGenome
    envelope: CreativeLatitudeEnvelope
    alternatives: Mapping[str, tuple[str, ...]]
    rng: random.Random


ValueGenerator = Callable[[str, GenerationContext], tuple[Any, ...]]


def canonical(value: Any) -> str:
    """Canonical JSON used to compare treatment values and assignments."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def baseline_value(genome: ContentGenome, variable: str) -> Any:
    """The control's value for ``variable``, in the JSON form treatments carry."""
    return genome.model_dump(mode="json")[variable]


def _bpm(variable: str, ctx: GenerationContext) -> tuple[Any, ...]:
    base = ctx.genome.bpm
    centre = base.target_bpm if base.target_bpm is not None else round((base.min_bpm + base.max_bpm) / 2)
    below, above = centre - base.min_bpm, base.max_bpm - centre
    magnitudes: list[int] = []
    step = int(centre * ctx.envelope.bpm_variance_pct)
    while step >= 1:
        magnitudes.append(step)
        step //= 2
    out: list[dict[str, int]] = []
    for magnitude in magnitudes:
        for sign in (1, -1):
            target = centre + sign * magnitude
            if not _BPM_FLOOR <= target <= _BPM_CEILING:
                continue
            out.append(
                {
                    "min_bpm": max(_BPM_FLOOR, target - below),
                    "max_bpm": min(_BPM_CEILING, target + above),
                    "target_bpm": target,
                }
            )
    return tuple(out)


def _energy(variable: str, ctx: GenerationContext) -> tuple[Any, ...]:
    ladder = list(EnergyLevel)
    index = ladder.index(ctx.genome.energy)
    out: list[str] = []
    for distance in range(1, ctx.envelope.energy_steps + 1):
        for direction in (1, -1):
            position = index + direction * distance
            if 0 <= position < len(ladder):
                out.append(ladder[position].value)
    return tuple(out)


def _categorical(variable: str, ctx: GenerationContext) -> tuple[Any, ...]:
    pool = sorted(set(ctx.alternatives.get(variable, ())) - {baseline_value(ctx.genome, variable)})
    return tuple(ctx.rng.sample(pool, len(pool)))


VALUE_GENERATORS: Final[Mapping[str, ValueGenerator]] = MappingProxyType(
    {
        "bpm": _bpm,
        "energy": _energy,
        **{dimension: _categorical for dimension in sorted(ALTERNATIVE_DIMENSIONS)},
    }
)


def candidate_values(
    variable: str,
    *,
    genome: ContentGenome,
    envelope: CreativeLatitudeEnvelope,
    alternatives: Mapping[str, tuple[str, ...]],
    directive_id: str,
    seed: int,
) -> tuple[Any, ...]:
    """Distinct alternatives to the baseline for ``variable``, or a typed refusal."""
    if DEFAULT_CONSTRAINT_HIERARCHY.get(variable) is ConstraintType.HARD:
        raise ExperimentDesignError(
            "variable_is_hard_constraint",
            f"{variable!r} is a hard constraint and cannot be an experimental variable",
            field_paths=[variable],
        )
    generator = VALUE_GENERATORS.get(variable)
    if generator is None:
        raise ExperimentDesignError(
            "no_value_generator",
            f"No treatment value generator is registered for {variable!r}; "
            f"registered: {', '.join(sorted(VALUE_GENERATORS))}",
            field_paths=[variable],
        )
    if not envelope.permits(variable):
        raise ExperimentDesignError(
            "variable_outside_creative_latitude",
            f"{variable!r} may not vary at exploration level "
            f"{envelope.exploration_level.value!r}",
            field_paths=[variable],
        )
    rng = random.Random(stable_seed(RANDOMIZATION_NAMESPACE, directive_id, seed, variable))
    context = GenerationContext(genome=genome, envelope=envelope, alternatives=alternatives, rng=rng)
    baseline = canonical(baseline_value(genome, variable))
    seen = {baseline}
    distinct: list[Any] = []
    for value in generator(variable, context):
        key = canonical(value)
        if key not in seen:
            seen.add(key)
            distinct.append(value)
    if not distinct:
        raise ExperimentDesignError(
            "no_candidate_values",
            f"{variable!r} has no valid value distinct from control for genre "
            f"{genome.genre!r} at exploration level {envelope.exploration_level.value!r}",
            field_paths=[variable],
        )
    return tuple(distinct)
