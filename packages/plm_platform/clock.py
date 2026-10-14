"""Injected time and derived randomness, the two halves of replayable computation.

A computation that must replay (ids, seeds, checksums, gate decisions, allocation draws,
measurement windows) never reads the wall clock and never uses process-randomised
`hash()`. Time arrives as a `Clock` handed in at the edge (API route, CLI, worker), and
every seed is derived from stable identity with `stable_seed`. Domain code takes an
explicit `now: datetime` or a `Clock`; only edge code constructs `SystemClock`.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Protocol

__all__ = ["Clock", "FixedClock", "SystemClock", "require_aware", "stable_seed"]

SEED_MODULUS = 2**31 - 1


class Clock(Protocol):
    """Source of the current instant. Always timezone-aware UTC."""

    def now(self) -> datetime: ...


class SystemClock:
    """The wall clock. Constructed only at the edge of a service."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock that returns a set instant until moved. For replay and tests."""

    def __init__(self, at: datetime) -> None:
        self._at = require_aware(at)

    def now(self) -> datetime:
        return self._at

    def advance(self, delta: timedelta) -> datetime:
        self._at = self._at + delta
        return self._at

    def set(self, at: datetime) -> None:
        self._at = require_aware(at)


def require_aware(moment: datetime) -> datetime:
    """Refuse naive datetimes and normalise to UTC."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"timezone-aware datetime required, got naive {moment!r}")
    return moment.astimezone(UTC)


def stable_seed(namespace: str, *parts: str | int) -> int:
    """A seed derived from identity, identical in every process.

    `hash()` is randomised per interpreter (PYTHONHASHSEED), so a seed built from it
    differs between a run and its replay. This one is the first 8 bytes of
    sha256(namespace|part|...) reduced into [0, 2**31 - 1).
    """
    if not namespace:
        raise ValueError("stable_seed needs a namespace")
    material = "|".join([namespace, *(str(part) for part in parts)]).encode("utf-8")
    digest = hashlib.sha256(material).digest()
    return int.from_bytes(digest[:8], "big") % SEED_MODULUS
