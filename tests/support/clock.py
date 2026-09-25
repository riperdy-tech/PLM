"""Frozen-clock and seeding helpers (time is a documented hazard, P0 §11.5).

The program's arithmetic is full of time boundaries — 24h/72h/7d/28d/90d
windows (P9), schedules (P1, P2), accounting periods (P9) — so tests must be
able to stop the clock and move it in deliberate steps, and runs must be
reproducible from a seed. Two rules:

1. **Inject the clock where the spec allows it.** Phase code should take its
   observation instant (``as_of`` and friends) as a parameter. For code that
   still reads ``datetime.datetime.now()`` directly, :func:`frozen_time`
   stops that clock process-wide for the duration of the block: every call
   returns the same instant until :meth:`FrozenClock.advance` moves it. A
   window test then reads as: freeze at t0, produce, advance 24h, observe.
2. **Seed every RNG.** :func:`seed` pins ``random`` (and ``numpy`` when the
   environment has it). The reproducibility expectation this documents: the
   *same seed plus the same inputs produce identical outputs*, so a failing
   test is reproducible from its seed alone — but seeding is not a substitute
   for determinism by construction: anything a spec requires to be
   reproducible (scoring, sequencing, feature calculation) must derive from
   stored inputs plus a version string, never from a seeded RNG whose call
   order can change. IDs must never depend on the seed (they derive from
   business identity, see ``plm_platform.temporal`` and the contract ids).

Caveats, stated so tests do not rely on them: freezegun freezes
``datetime``/``time.time`` but **not** ``time.monotonic()`` or real sleeps —
never make a test depend on elapsed real time. Subprocesses and container
clocks are not frozen; restart/durability tests (W0.7-style) must key off
data, not the test clock.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import ModuleType
from typing import Final

from freezegun import freeze_time
from freezegun.api import FrozenDateTimeFactory

DEFAULT_START: Final[str] = "2026-01-01T00:00:00+00:00"
"""Default frozen instant: a neutral epoch no phase's production data lands on."""

DEFAULT_SEED: Final[int] = 0
"""Default RNG seed. Use a different literal when a test needs a different stream."""


class FrozenClock:
    """Handle on a frozen clock: read it, advance it deliberately."""

    def __init__(self, factory: FrozenDateTimeFactory) -> None:
        self._factory = factory

    @property
    def now(self) -> datetime:
        """The current frozen instant (UTC)."""
        return datetime.now(UTC)

    def advance(self, delta: timedelta) -> datetime:
        """Move the clock forward by ``delta``; returns the new instant.

        ``delta`` may be negative; use :meth:`set` for a deliberate jump back.
        """
        self._factory.tick(delta)
        return self.now

    def set(self, moment: str | datetime) -> datetime:
        """Move the clock to ``moment``; returns the new instant."""
        self._factory.move_to(moment)
        return self.now


@contextmanager
def frozen_time(start: str | datetime = DEFAULT_START) -> Iterator[FrozenClock]:
    """Stop the clock at ``start`` for the duration of the ``with`` block.

    ::

        with frozen_time("2026-03-01T00:00:00+00:00") as clock:
            ...                       # now() is 2026-03-01T00:00:00Z everywhere
            clock.advance(timedelta(hours=24))
            ...                       # the 24h window boundary, exactly
    """
    with freeze_time(start) as factory:
        yield FrozenClock(factory)  # type: ignore[arg-type]


def seed(value: int = DEFAULT_SEED) -> int:
    """Seed every RNG the program can reach and return ``value``.

    Seeds ``random`` always and ``numpy.random`` when numpy is importable
    (the media/analysis phases' images may carry it; this repo does not).
    Returns the value so a fixture can ``return seed(value)`` in one line.
    """
    random.seed(value)
    numpy = _numpy()
    if numpy is not None:
        numpy.random.seed(value)
    return value


def _numpy() -> ModuleType | None:
    try:
        import numpy  # type: ignore[import-not-found]
    except ImportError:
        return None
    return numpy  # type: ignore[no-any-return]
