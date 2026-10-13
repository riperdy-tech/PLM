"""Two separate switches: which implementation serves a call, and whether it may write.

These are deliberately two enums, not one. ``ProviderMode`` says *who answers* (an
in-process mock or the real provider); ``ExecutionPolicy`` says *what a side-effecting
capability is allowed to do* in the outside world. Combinations such as "read real
analytics, evaluate a real publish decision, but only record the upload intent" need
both. No component may infer write permission from ``ProviderMode.REAL``.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["ExecutionPolicy", "ProviderMode"]


class ProviderMode(StrEnum):
    """Which implementation serves an integration. Set per integration."""

    MOCK = "mock"
    """In-process simulation. Calls have no effect outside the process."""

    REAL = "real"
    """The external provider. Writes still need an ``ExecutionPolicy.LIVE`` grant."""


class ExecutionPolicy(StrEnum):
    """What a side-effecting capability may do externally. Set per capability.

    Ordered from most to least restrictive; anything short of ``LIVE`` never causes an
    external write.
    """

    READ_ONLY = "read_only"
    """Reads only; any write attempt is refused."""

    DRY_RUN = "dry_run"
    """The write is evaluated and its intent recorded, but not sent."""

    SHADOW = "shadow"
    """Live reads and decisions are evaluated and recorded; they do not control production."""

    LIVE = "live"
    """External writes are permitted, subject to authorization, budget and idempotency."""
