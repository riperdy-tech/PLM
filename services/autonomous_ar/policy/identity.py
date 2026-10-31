# services/autonomous_ar/policy/identity.py
"""Brand identity (ADR-0044): which P1 arms a brand may ever produce.

The identity is versioned data (`brand_identity.json`), not a portfolio knob. An arm
outside it is set `blocked` with reason `out_of_identity:<version>` before eligibility
is computed, so the refusal is persisted and auditable on the arm's state row. An arm a
later identity admits again goes back to `candidate`; an arm blocked for any other
reason is never touched. A brand the file does not declare is not filtered, and the
cycle says so with `IDENTITY_UNDECLARED`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..domain.arm import ArmDimensions, ContentArm
from ..domain.enums import ArmLifecycleStatus
from ..storage import repositories

__all__ = [
    "IDENTITY_FILE", "OUT_OF_IDENTITY_PREFIX", "BrandIdentity", "apply_brand_identity",
    "load_brand_identities",
]

IDENTITY_FILE = Path(__file__).with_name("brand_identity.json")
OUT_OF_IDENTITY_PREFIX = "out_of_identity:"
_UNSET = frozenset({"", "none"})
"""P1 writes an unmeasured dimension as `none`; the wire may also send nothing."""


@dataclass(frozen=True, slots=True)
class BrandIdentity:
    version: str
    use_cases: frozenset[str]
    genres: frozenset[str]
    allow_unset_genre: bool
    excluded_vocal_modes: frozenset[str]

    def admits(self, dims: ArmDimensions) -> bool:
        """In identity: a focus use case, a focus (or unset) genre, and not excluded vocals.

        An arm whose genre is one of the identity's own genres may leave its use case
        unset (its genre already names the use); an unset genre needs the use case.
        """
        genre_unset = dims.genre is None or dims.genre in _UNSET
        if genre_unset:
            if not self.allow_unset_genre:
                return False
        elif dims.genre not in self.genres:
            return False
        use_cases = {u for u in dims.use_cases if u not in _UNSET}
        if use_cases:
            if not self.use_cases.intersection(use_cases):
                return False
        elif genre_unset:
            return False
        return dims.vocal_mode not in self.excluded_vocal_modes

    @property
    def block_reason(self) -> str:
        return f"{OUT_OF_IDENTITY_PREFIX}{self.version}"


def load_brand_identities(path: Path = IDENTITY_FILE) -> dict[str, BrandIdentity]:
    """Parse the identity file; a malformed file is an error, never an open identity."""
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    version = str(raw["identity_version"])
    identities: dict[str, BrandIdentity] = {}
    for brand_id, spec in raw["brands"].items():
        use_cases = frozenset(str(u) for u in spec["use_cases"])
        if not use_cases:
            raise ValueError(f"brand identity {brand_id!r} declares no use case")
        identities[str(brand_id)] = BrandIdentity(
            version=version, use_cases=use_cases,
            genres=frozenset(str(g) for g in spec["genres"]),
            allow_unset_genre=bool(spec["allow_unset_genre"]),
            excluded_vocal_modes=frozenset(str(v) for v in spec["excluded_vocal_modes"]),
        )
    return identities


def apply_brand_identity(
    session: Any, identity: BrandIdentity, arms: Iterable[ContentArm],
    states: Mapping[str, Mapping[str, Any]], *, now: datetime,
) -> tuple[int, int]:
    """Block arms outside the identity and unblock identity-blocked arms it admits.

    Returns `(blocked, unblocked)`. `states` is `load_arm_states` keyed by arm id.
    """
    blocked = unblocked = 0
    for arm in arms:
        row = states.get(arm.arm_id)
        if row is None:
            continue
        status = ArmLifecycleStatus(row["status"])
        reason = row.get("retirement_reason") or ""
        if not identity.admits(arm.dimensions):
            if status is not ArmLifecycleStatus.BLOCKED:
                repositories.set_arm_status(session, arm_id=arm.arm_id,
                                            status=ArmLifecycleStatus.BLOCKED, now=now,
                                            reason=identity.block_reason)
                blocked += 1
        elif status is ArmLifecycleStatus.BLOCKED and reason.startswith(OUT_OF_IDENTITY_PREFIX):
            repositories.set_arm_status(session, arm_id=arm.arm_id,
                                        status=ArmLifecycleStatus.CANDIDATE, now=now)
            unblocked += 1
    return blocked, unblocked
