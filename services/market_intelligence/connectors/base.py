# services/market_intelligence/connectors/base.py
"""Provider-neutral connector vocabulary: what a fetch returns and how it fails."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Protocol, runtime_checkable

from ..domain.enums import ConnectorErrorClass, ProviderName, ProviderObjectType

__all__ = [
    "RETRYABLE_ERROR_CLASSES", "ConnectorError", "FetchResult", "FetchedObject",
    "SourceConnector", "hash_request_context",
]

RETRYABLE_ERROR_CLASSES: Final[frozenset[ConnectorErrorClass]] = frozenset(
    {
        ConnectorErrorClass.RATE_LIMIT,
        ConnectorErrorClass.TRANSIENT_NETWORK,
        ConnectorErrorClass.PROVIDER_5XX,
    }
)
"""§23: retry only these. QUOTA_EXCEEDED is *not* retried — it is rescheduled (§27)."""


class ConnectorError(Exception):
    """A classified connector failure. Never a bare `requests`/`httpx` exception."""

    def __init__(
        self,
        error_class: ConnectorErrorClass,
        message: str,
        *,
        retryable: bool | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(f"{error_class.value}: {message}")
        self.error_class = error_class
        self.detail = message
        self.retry_after_seconds = retry_after_seconds
        self._retryable = retryable

    @property
    def retryable(self) -> bool:
        """Explicit override wins; otherwise the §23 class table decides."""
        return self._retryable if self._retryable is not None else self.error_class in (
            RETRYABLE_ERROR_CLASSES
        )


@dataclass(frozen=True, slots=True)
class FetchedObject:
    """One provider object exactly as received — the unit of raw provenance (§7.4)."""

    provider_object_type: ProviderObjectType
    provider_object_id: str
    observed_at: datetime
    payload: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class FetchResult:
    """What one logical fetch produced: objects, its request context and its cost."""

    objects: tuple[FetchedObject, ...] = field(default_factory=tuple)
    request_context_hash: str = ""
    quota_units: int = 0
    next_page_token: str | None = None


def hash_request_context(provider: str, endpoint: str, params: Mapping[str, Any]) -> str:
    """sha256 over the canonical request (provider, endpoint, sorted params).

    Secrets are never part of the context: pass the *public* params only. The hash is
    stable across runs so two observations of one object are comparable (§7.4).
    """
    if any(key.lower() in {"key", "access_token", "authorization"} for key in params):
        raise ValueError("request context must not carry credentials")
    canonical = json.dumps(
        {"provider": provider, "endpoint": endpoint,
         "params": {k: str(v) for k, v in sorted(params.items()) if v is not None}},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@runtime_checkable
class SourceConnector(Protocol):
    """§3.7: every source sits behind this shape so scoring never sees a provider."""

    provider_name: ProviderName
