"""The small HTTP client every pipeline step uses, and how it classifies an answer.

:class:`PhaseClient` sends every request with the service token
(``X-PLM-Service-Token``) and an ``Idempotency-Key``, and classifies the answer:

* 2xx: the JSON body.
* A refusal by a phase's external-write guard (``409`` with an ErrorShape code
  ``external_write_<decision>``): :class:`ExternalWriteRefused`, non-retryable. It names
  the capability, the guard's decision and reason, and the hold that resolves it: a
  budget refusal is ``BUDGET_RESERVATION_REFUSED``, a missing authorization or a
  non-live policy ``EXTERNAL_WRITE_NOT_AUTHORIZED``; a reason the guard does not declare
  is left unclassified, so the hold policy treats it as an unmapped hard stop.
* Any other definite refusal (4xx with the receiver's ErrorShape ``code``):
  :class:`PhaseRequestRefused`, non-retryable; the request itself must change.
* Anything that may clear by itself (connection refused, timeouts, 5xx, and 401 / 403 /
  408 / 425 / 429 as in :mod:`plm_platform.outbox`): :class:`PhaseRequestFailed`,
  retried by the Temporal retry policy.

**Idempotency keys.** A step is one unit of retry with one key
(``idempotency_key(workflow_id, step)``), but a step may make several writes (one
plan per track spec, one evaluation per candidate). Each write carries its own key,
:func:`request_key` ``(step_key, operation, entity)``, so a phase that deduplicates on
the key replays exactly the write it already made and never answers a different write
with a stored response. Reads carry the step key.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Final

import httpx

from ..config import SERVICE_TOKEN_HEADER, SERVICE_TOKEN_VAR
from ..ledger import Reason
from ..outbox import IDEMPOTENCY_KEY_HEADER
from ..temporal import Phase, ValidationFailure

__all__ = [
    "AUTHORIZATION_HOLD_REASON",
    "BUDGET_HOLD_REASON",
    "EXTERNAL_WRITE_CODE_PREFIX",
    "UNCLASSIFIED_WRITE_HOLD_REASON",
    "ExternalWriteRefused",
    "PhaseClient",
    "PhaseRequestFailed",
    "PhaseRequestRefused",
    "StepNotReady",
    "request_key",
    "require",
]

_DETAIL_LIMIT: Final[int] = 512
_RETRYABLE_4XX: Final[frozenset[int]] = frozenset({401, 403, 408, 425, 429})

EXTERNAL_WRITE_CODE_PREFIX: Final[str] = "external_write_"
"""P7 and P8 answer a write their guard did not permit with ``409`` and the code
``external_write_<decision>`` and the message ``"<capability> not performed: <reason>"``."""

BUDGET_HOLD_REASON: Final[str] = "BUDGET_RESERVATION_REFUSED"
AUTHORIZATION_HOLD_REASON: Final[str] = "EXTERNAL_WRITE_NOT_AUTHORIZED"
UNCLASSIFIED_WRITE_HOLD_REASON: Final[str] = "EXTERNAL_WRITE_REFUSED"
"""Not in the hold policy on purpose: a guard reason nobody declared holds as an
unmapped hard stop (fail closed, ``policy_mapped = False``)."""

_BUDGET_REASONS: Final[frozenset[str]] = frozenset(
    {reason.value for reason in Reason if reason is not Reason.RESERVED}
    | {"budget_ledger_unavailable", "budget_ledger_not_configured",
       "budget_reservation_replayed"}
)
"""Ledger refusal codes plus missing-ledger and replayed-reservation guards.
All stop real writes and require evidence before a workflow is resumed."""

_AUTHORIZATION_REASONS: Final[frozenset[str]] = frozenset({
    "missing_idempotency_key", "policy_read_only", "policy_dry_run", "policy_shadow",
    "no_authorization", "authorization_expired",
})
"""Every non-budget reason ``ExternalWriteGuard._decide`` can answer with."""

_REFUSAL_MESSAGE: Final = re.compile(r"^(?P<capability>[\w.]+) not performed: (?P<reason>[\w.\-]+)")


def request_key(step_key: str, operation: str, entity: str = "") -> str:
    """The idempotency key of one write inside a step (see the module docstring)."""
    if not step_key.strip() or not operation.strip():
        raise ValueError("a request key needs the step key and an operation")
    return f"{step_key}/{operation}" + (f"/{entity}" if entity else "")


def require(mapping: Any, key: str, where: str) -> Any:
    """A required fact: missing, ``None`` or ``""`` is refused, never defaulted."""
    if not isinstance(mapping, dict) or key not in mapping or mapping[key] in (None, ""):
        raise ValidationFailure(f"{where}: required fact {key!r} is missing")
    return mapping[key]


class PhaseRequestRefused(ValidationFailure):
    """The phase definitely refused the request (a 4xx with its reason code)."""

    def __init__(self, phase: Phase, path: str, status: int, code: str, message: str,
                 *, body: Mapping[str, Any] | None = None) -> None:
        self.phase = phase
        self.path = path
        self.status = status
        self.code = code
        self.message = message
        self.body: dict[str, Any] = dict(body or {})
        """The receiver's error detail as sent (for facts beyond the ErrorShape)."""
        super().__init__(f"{phase.name} {path} refused ({status} {code}): {message}")


class ExternalWriteRefused(PhaseRequestRefused):
    """A phase's external-write guard did not permit the write; nothing was written.

    ``details`` travels on the Temporal failure: the workflow raises the hold named by
    ``hold_reason_code`` and, once it is cleared, retries the step with the same key,
    so the service's guard reserves again against the new headroom.
    """

    no_effect = True
    """The guard refuses before the service persists or sends anything."""

    def __init__(self, phase: Phase, path: str, status: int, code: str, message: str,
                 *, body: Mapping[str, Any] | None = None) -> None:
        super().__init__(phase, path, status, code, message, body=body)
        match = _REFUSAL_MESSAGE.match(message)
        self.capability = match["capability"] if match else None
        self.guard_decision = code.removeprefix(EXTERNAL_WRITE_CODE_PREFIX)
        self.guard_reason = match["reason"] if match else None
        if self.guard_reason in _BUDGET_REASONS:
            self.hold_reason_code = BUDGET_HOLD_REASON
        elif self.guard_reason in _AUTHORIZATION_REASONS:
            self.hold_reason_code = AUTHORIZATION_HOLD_REASON
        else:
            self.hold_reason_code = UNCLASSIFIED_WRITE_HOLD_REASON

    @property
    def details(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "hold_reason_code": self.hold_reason_code,
            "capability": self.capability,
            "guard_decision": self.guard_decision,
            "guard_reason": self.guard_reason,
            "refusing_phase": self.phase.name,
        }
        if self.hold_reason_code == BUDGET_HOLD_REASON:
            details["ledger_reason"] = self.guard_reason
        return details


class PhaseRequestFailed(RuntimeError):
    """The request may succeed later (transport failure, 5xx, auth or rate limit)."""

    def __init__(self, phase: Phase, path: str, code: str, message: str) -> None:
        self.phase = phase
        self.code = code
        super().__init__(f"{phase.name} {path} failed ({code}): {message}")


class StepNotReady(RuntimeError):
    """The phase has not finished yet (a video still processing, a reward still queued):
    retryable, so the Temporal retry policy polls with backoff."""

    def __init__(self, message: str, *, code: str = "not_ready") -> None:
        self.code = code
        super().__init__(message)


def _error_shape(response: httpx.Response) -> tuple[str, str, dict[str, Any]]:
    try:
        body = response.json()
    except ValueError:
        return f"http_{response.status_code}", response.text[:_DETAIL_LIMIT], {}
    shape = body.get("detail", body) if isinstance(body, dict) else None
    if isinstance(shape, dict) and isinstance(shape.get("code"), str):
        return shape["code"], str(shape.get("message", ""))[:_DETAIL_LIMIT], dict(shape)
    if isinstance(shape, str):
        return f"http_{response.status_code}", shape[:_DETAIL_LIMIT], {}
    return f"http_{response.status_code}", json.dumps(body)[:_DETAIL_LIMIT], {}


class PhaseClient:
    """A synchronous client for one phase API (``client`` owns base URL and timeout)."""

    def __init__(self, phase: Phase, client: httpx.Client, *, token: str | None) -> None:
        self.phase = phase
        self._client = client
        self._token = token

    def get(self, path: str, *, idempotency_key: str,
            params: Mapping[str, str] | None = None) -> Any:
        return self._request("GET", path, idempotency_key=idempotency_key, params=params)

    def post(self, path: str, *, idempotency_key: str, body: Any = None,
             params: Mapping[str, str] | None = None,
             timeout_seconds: float | None = None) -> Any:
        """``timeout_seconds`` overrides the client's timeout for a long call (a render)."""
        return self._request("POST", path, idempotency_key=idempotency_key, body=body,
                             params=params, timeout_seconds=timeout_seconds)

    def _request(self, method: str, path: str, *, idempotency_key: str,
                 params: Mapping[str, str] | None = None, body: Any = None,
                 timeout_seconds: float | None = None) -> Any:
        if not self._token:
            raise PhaseRequestRefused(
                self.phase, path, 503, "blocked_pending_credentials",
                f"no service token: set {SERVICE_TOKEN_VAR} for the pipeline worker",
            )
        if not idempotency_key.strip():
            raise ValueError("every phase request carries an idempotency key")
        headers = {SERVICE_TOKEN_HEADER: self._token, IDEMPOTENCY_KEY_HEADER: idempotency_key}
        try:
            response = self._client.request(
                method, path, headers=headers,
                params=None if params is None else dict(params),
                json=body if method == "POST" else None,
                timeout=(httpx.USE_CLIENT_DEFAULT if timeout_seconds is None
                         else httpx.Timeout(timeout_seconds)),
            )
        except httpx.HTTPError as exc:
            raise PhaseRequestFailed(self.phase, path, type(exc).__name__, str(exc)) from exc
        status = response.status_code
        if 200 <= status < 300:
            try:
                return response.json()
            except ValueError as exc:
                raise PhaseRequestFailed(self.phase, path, "unreadable_body",
                                         response.text[:_DETAIL_LIMIT]) from exc
        code, message, detail = _error_shape(response)
        if status >= 500 or status in _RETRYABLE_4XX:
            raise PhaseRequestFailed(self.phase, path, code, message)
        if status == 409 and code.startswith(EXTERNAL_WRITE_CODE_PREFIX):
            raise ExternalWriteRefused(self.phase, path, status, code, message, body=detail)
        raise PhaseRequestRefused(self.phase, path, status, code, message, body=detail)
