# services/market_intelligence/collection/retries.py
"""§23 retry rules: exponential backoff with jitter, provider guidance honoured,
permanent failures never retried. Transport-level only — a creative failure is
never retried as a transport failure (P0 §2)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from random import Random
from typing import Final

from ..connectors.base import RETRYABLE_ERROR_CLASSES, ConnectorError
from ..domain.enums import ConnectorErrorClass

__all__ = [
    "RetryExhaustedError", "RetryPolicy", "classify_http_status", "delay_for_attempt",
    "execute_with_retries", "is_retryable",
]

_MAX_RETRY_AFTER_SECONDS: Final[float] = 60.0
_STATUS_TO_CLASS: Final[dict[int, ConnectorErrorClass]] = {
    400: ConnectorErrorClass.INVALID_REQUEST,
    401: ConnectorErrorClass.AUTH_FAILURE,
    403: ConnectorErrorClass.AUTH_FAILURE,
    404: ConnectorErrorClass.NOT_FOUND,
    429: ConnectorErrorClass.RATE_LIMIT,
}


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    jitter_seconds: float = 0.25


class RetryExhaustedError(ConnectorError):
    """Every attempt failed with a retryable class; the last cause is chained."""


def classify_http_status(status: int) -> ConnectorErrorClass:
    """Map an HTTP status to §23's classes; anything unexpected 5xx is PROVIDER_5XX."""
    if status in _STATUS_TO_CLASS:
        return _STATUS_TO_CLASS[status]
    if 500 <= status < 600:
        return ConnectorErrorClass.PROVIDER_5XX
    raise ValueError(f"unclassified HTTP status {status}: add it to _STATUS_TO_CLASS")


def is_retryable(error_class: ConnectorErrorClass) -> bool:
    return error_class in RETRYABLE_ERROR_CLASSES


def delay_for_attempt(
    policy: RetryPolicy,
    attempt: int,
    *,
    rng: Random,
    retry_after_seconds: float | None = None,
) -> float:
    """Backoff for the gap *before* attempt `attempt` (1-based); jitter is additive.

    A provider `Retry-After` is honoured as given, capped so a hostile value cannot
    stall a run; it is never reduced by the backoff curve (§23).
    """
    if retry_after_seconds is not None:
        return min(max(retry_after_seconds, 0.0), _MAX_RETRY_AFTER_SECONDS)
    exponential = policy.base_delay_seconds * (2 ** (attempt - 1))
    capped = min(exponential, policy.max_delay_seconds)
    return capped + (rng.random() * policy.jitter_seconds if policy.jitter_seconds else 0.0)


def execute_with_retries[T](
    attempt_fn: Callable[[], T],
    *,
    policy: RetryPolicy,
    sleep: Callable[[float], None] = time.sleep,
    rng: Random | None = None,
) -> T:
    """Call `attempt_fn` until it succeeds, a permanent class appears, or attempts run out."""
    generator = rng if rng is not None else Random()
    last: ConnectorError | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return attempt_fn()
        except ConnectorError as exc:
            last = exc
            if not exc.retryable or attempt == policy.max_attempts:
                if exc.retryable:
                    raise RetryExhaustedError(
                        exc.error_class, f"retries exhausted after {attempt} attempts: {exc.detail}"
                    ) from exc
                raise
            sleep(delay_for_attempt(
                policy, attempt, rng=generator, retry_after_seconds=exc.retry_after_seconds
            ))
    raise RetryExhaustedError(  # pragma: no cover - unreachable with max_attempts >= 1
        last.error_class if last else ConnectorErrorClass.TRANSIENT_NETWORK, "no attempts made"
    )
