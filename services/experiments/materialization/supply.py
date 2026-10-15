"""Playlist supply planning: how many track specs a package needs for its declared range.

Owner decision 2026-10-15: a playlist video's length is a declared **range** (the
directive bucket ``45_75m`` is 45–75 minutes); the target is only the planning midpoint.
P3 plans supply so that P6 can land inside that range after P5 has rejected some tracks:

* ``base_track_count = ceil(range.min_seconds / planned_track_seconds)``: enough tracks of
  the planned length (the track spec's ``requested_duration``) to reach the range
  minimum;
* ``spare_track_count = ceil(base_track_count * spare_ratio)``: declared spares for P5
  rejections;
* ``max_extension_track_count = ceil(track_count * max_extension_ratio)``: the most
  top-up specs ``POST /experiments/v1/packages/{id}/extend`` may add later, when P6
  still reports ``playlist_supply_short``.

The ratios are versioned data (``supply_policy.json``), not constants in code. The
package budget is split over every spec the package may ever hold (planned plus the
extension bound), so the directive's budget ceiling holds even after every top-up.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Final

from plm_contracts.seams.c3 import DurationRangeSeconds
from pydantic import BaseModel, ConfigDict, Field

from ..domain.genome import DurationRange

__all__ = [
    "SUPPLY_POLICY_PATH",
    "SupplyPlan",
    "SupplyPolicy",
    "default_supply_policy",
    "plan_supply",
    "playlist_midpoint_seconds",
]

SUPPLY_POLICY_PATH: Final[Path] = Path(__file__).resolve().parent / "supply_policy.json"


class SupplyPolicy(BaseModel):
    """The declared spare and extension ratios (versioned data)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    supply_policy_version: str = Field(min_length=1)
    spare_ratio: Decimal = Field(ge=0, le=1)
    """Spare specs per base spec, to absorb P5 rejections."""
    max_extension_ratio: Decimal = Field(ge=0, le=2)
    """The most top-up specs per planned spec that ``extend`` may add."""

    @classmethod
    def from_file(cls, path: Path) -> SupplyPolicy:
        with path.open(encoding="utf-8") as handle:
            return cls.model_validate(json.load(handle))


@lru_cache(maxsize=1)
def default_supply_policy() -> SupplyPolicy:
    """The shipped supply policy, loaded and validated once per process."""
    return SupplyPolicy.from_file(SUPPLY_POLICY_PATH)


class SupplyPlan(BaseModel):
    """How many specs one package plans, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    duration_range_seconds: DurationRangeSeconds
    target_duration_seconds: int = Field(ge=1)
    planned_track_duration_seconds: int = Field(ge=1)
    base_track_count: int = Field(ge=1)
    spare_track_count: int = Field(ge=0)
    track_count: int = Field(ge=1)
    max_extension_track_count: int = Field(ge=0)
    spare_ratio: Decimal
    supply_policy_version: str

    @property
    def budget_slots(self) -> int:
        """Every spec the package may ever hold: planned plus the extension bound."""
        return self.track_count + self.max_extension_track_count


def playlist_midpoint_seconds(playlist: DurationRangeSeconds) -> int:
    """The planning target: the midpoint of the declared range (owner, 2026-10-15)."""
    return round((playlist.min_seconds + playlist.max_seconds) / 2)


def _ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding="ROUND_CEILING"))


def plan_supply(
    playlist_duration: DurationRange,
    planned_track_seconds: int,
    policy: SupplyPolicy,
) -> SupplyPlan:
    """The package's supply plan for the genome's declared playlist range."""
    if planned_track_seconds < 1:
        raise ValueError(f"planned track duration must be positive; got {planned_track_seconds}")
    declared = DurationRangeSeconds(
        min_seconds=playlist_duration.min_seconds, max_seconds=playlist_duration.max_seconds
    )
    base = max(1, math.ceil(declared.min_seconds / planned_track_seconds))
    spare = _ceil(Decimal(base) * policy.spare_ratio)
    total = base + spare
    return SupplyPlan(
        duration_range_seconds=declared,
        target_duration_seconds=playlist_midpoint_seconds(declared),
        planned_track_duration_seconds=planned_track_seconds,
        base_track_count=base,
        spare_track_count=spare,
        track_count=total,
        max_extension_track_count=_ceil(Decimal(total) * policy.max_extension_ratio),
        spare_ratio=policy.spare_ratio,
        supply_policy_version=policy.supply_policy_version,
    )
