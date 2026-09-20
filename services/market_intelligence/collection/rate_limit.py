# services/market_intelligence/collection/rate_limit.py
"""§27 quota safety: per-run unit and request budgets plus a request-rate limiter.

Budgets are consumed *before* the HTTP call, so the call that would exceed a limit
never leaves the process. Exhaustion is a first-class outcome: the scheduler stops
low-priority work and reschedules (§27), the run is marked `partial`.
"""

from __future__ import annotations

import time
from collections.abc import Callable

__all__ = [
    "QuotaBudget", "QuotaExceededError", "RateLimiter", "RequestBudget",
    "RequestBudgetExceededError",
]


class QuotaExceededError(RuntimeError):
    """The provider quota budget for this run is exhausted."""

    error_class_token = "QUOTA_EXCEEDED"


class RequestBudgetExceededError(RuntimeError):
    """The per-run request budget is exhausted."""


class QuotaBudget:
    """Provider quota units for one run (`search.list` = 100, list endpoints = 1)."""

    def __init__(self, limit_units: int) -> None:
        if limit_units < 0:
            raise ValueError("quota limit cannot be negative")
        self.limit_units = limit_units
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.limit_units - self.used

    def consume(self, units: int, *, operation: str) -> None:
        if units < 0:
            raise ValueError("cannot consume a negative number of units")
        if units > self.remaining:
            raise QuotaExceededError(
                f"operation {operation!r} needs {units} quota units but only "
                f"{self.remaining} remain of {self.limit_units}; reschedule instead of "
                "retrying (PHASE-P1 §27)"
            )
        self.used += units

    def release(self, units: int, *, operation: str) -> None:
        """Un-charge one attempt that the provider cannot have billed (no response).

        Charging happens before the HTTP call so a call that would exceed the
        budget never leaves the process; a transport failure afterwards is
        reconciled here instead of leaving phantom units on the ledger.
        """
        if units < 0:
            raise ValueError("cannot release a negative number of units")
        if units > self.used:
            raise ValueError(
                f"cannot release {units} units for {operation!r}: only {self.used} are charged"
            )
        self.used -= units


class RequestBudget:
    """Per-run request count cap, independent of provider quota cost."""

    def __init__(self, limit_requests: int) -> None:
        if limit_requests < 0:
            raise ValueError("request limit cannot be negative")
        self.limit_requests = limit_requests
        self.used = 0

    def consume(self, *, operation: str) -> None:
        if self.used >= self.limit_requests:
            raise RequestBudgetExceededError(
                f"operation {operation!r} exceeds the per-run request budget "
                f"({self.limit_requests}); stop low-priority work first (PHASE-P1 §27)"
            )
        self.used += 1

    def release(self, *, operation: str) -> None:
        """Give back the count for an attempt that never reached the provider."""
        if self.used <= 0:
            raise ValueError(f"cannot release a request for {operation!r}: none is charged")
        self.used -= 1


class RateLimiter:
    """Token-bucket pacing: at most `requests_per_second`, measured on an injected clock."""

    def __init__(
        self,
        requests_per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._interval = 1.0 / requests_per_second
        self._clock = clock
        self._sleep = sleep
        self._last_request_at: float | None = None

    def wait_time(self) -> float:
        """Seconds until the next request may start (0.0 when it may start now)."""
        if self._last_request_at is None:
            return 0.0
        return max(0.0, self._interval - (self._clock() - self._last_request_at))

    def acquire(self) -> None:
        wait = self.wait_time()
        if wait > 0:
            self._sleep(wait)
        self._last_request_at = self._clock()
