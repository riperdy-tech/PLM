"""Delivery of P9's C7 leg-2 reward to P2 through the transactional outbox (stage R3).

The reward is recorded and its event enqueued in one transaction
(`feedback.record_and_enqueue_reward`); nothing is sent in the request that produced it.
A dispatcher pass (`dispatch_rewards`, run by ``plm analytics dispatch`` or later a
Temporal activity) sends due events to P2's ``POST /ar/results``:

* P2 acknowledges with ``200 {"observation_id", "experiment_result_id", "stored"}``. A
  replay of a stored fact is ``stored: false``, which is still an acknowledgement.
* A ``422`` with an ErrorShape code is P2 refusing the payload: the event is ``FAILED``
  with that code and never resent.
* A timeout leaves the event ``UNCERTAIN``. Reconciliation asks
  ``GET /ar/results?experiment_id=&content_id=&observation_window=&status=`` whether the
  fact is stored, and never guesses.

The reward counts as posted only when its outbox row is ``DELIVERED``
(`reward_delivery_status`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Final

import httpx
from plm_platform.clock import Clock
from plm_platform.outbox import (
    BackoffPolicy,
    Dispatcher,
    DispatchReport,
    HttpReconciler,
    HttpTransport,
    Outbox,
    OutboxMessage,
    OutboxStatus,
)
from sqlalchemy.orm import Session

from ..domain.models import NormalizedRewardPayload
from ..storage.models import OUTBOX_EVENTS

__all__ = [
    "AR_RESULTS_PATH",
    "REWARD_TOPIC",
    "build_reward_dispatcher",
    "dispatch_rewards",
    "enqueue_reward",
    "p2_acknowledges",
    "p2_lookup_present",
    "p2_lookup_query",
    "reward_delivery_status",
    "reward_fact_key",
    "reward_outbox",
]

REWARD_TOPIC: Final[str] = "c7.leg2.reward_result"
AR_RESULTS_PATH: Final[str] = "/ar/results"
"""P2's result endpoint (§71); relative to the P2 base URL the HTTP client carries."""

reward_outbox: Final[Outbox] = Outbox(OUTBOX_EVENTS)


def reward_fact_key(experiment_id: str, content_id: str, window: str, maturity: str) -> str:
    """One fact, as P2 dedupes it: (experiment, content, window, maturity) (ADR-0031).

    Sent as the ``Idempotency-Key``. P2 derives its own key from the payload, so this
    string is P9's identity for the event, not a shared wire format.
    """
    return f"{REWARD_TOPIC}:{experiment_id}|{content_id}|{window}|{maturity}"


def _payload_key(payload: NormalizedRewardPayload) -> str:
    return reward_fact_key(payload.experiment_id, payload.content_id,
                           payload.observation_window.value, payload.status.value)


def enqueue_reward(session: Session, payload: NormalizedRewardPayload, *,
                   now: datetime) -> OutboxMessage:
    """Queue the reward in the caller's transaction (never commits, never sends).

    Re-enqueueing the identical payload is a no-op; a different payload for the same fact
    raises `plm_platform.outbox.OutboxConflictError`.
    """
    return reward_outbox.enqueue(
        session, topic=REWARD_TOPIC, idempotency_key=_payload_key(payload),
        payload=payload.to_wire(), destination=AR_RESULTS_PATH, now=now,
    )


def reward_delivery_status(session: Session,
                           payload: NormalizedRewardPayload) -> OutboxStatus | None:
    """``DELIVERED`` is the only status under which the reward counts as posted to P2.

    ``None`` means no event was ever queued for this fact.
    """
    message = reward_outbox.get(session, topic=REWARD_TOPIC,
                                idempotency_key=_payload_key(payload))
    return None if message is None else message.status


def p2_acknowledges(body: Any) -> bool:
    """P2's ``ResultAccepted``: an observation id and an explicit ``stored`` boolean.

    ``stored: false`` (a replay of a fact P2 already holds) is an acknowledgement too.
    """
    return (
        isinstance(body, dict)
        and isinstance(body.get("observation_id"), str)
        and bool(body["observation_id"])
        and isinstance(body.get("stored"), bool)
    )


def p2_lookup_query(message: OutboxMessage) -> tuple[str, Mapping[str, str]]:
    payload = message.payload
    return AR_RESULTS_PATH, {
        "experiment_id": str(payload["experiment_id"]),
        "content_id": str(payload["content_id"]),
        "observation_window": str(payload["observation_window"]),
        "status": str(payload["status"]),
    }


def p2_lookup_present(body: Any) -> bool | None:
    """``True``/``False`` from P2's lookup ``{"stored": bool}``; ``None`` if not understood."""
    if isinstance(body, dict) and isinstance(body.get("stored"), bool):
        return bool(body["stored"])
    return None


def build_reward_dispatcher(client: httpx.Client, *, token: str, clock: Clock,
                            backoff: BackoffPolicy | None = None) -> Dispatcher:
    """A dispatcher for P9's reward topic over ``client`` (base URL = P2, with timeout)."""
    return Dispatcher(
        reward_outbox,
        transport=HttpTransport(client, token=token, acknowledges=p2_acknowledges),
        reconciler=HttpReconciler(client, token=token, query=p2_lookup_query,
                                  present=p2_lookup_present),
        clock=clock,
        backoff=backoff,
        topics=(REWARD_TOPIC,),
    )


def dispatch_rewards(session_factory: Callable[[], Session], *, client: httpx.Client,
                     token: str, clock: Clock, limit: int = 100,
                     backoff: BackoffPolicy | None = None) -> DispatchReport:
    """One dispatcher pass over due reward events: the entry point for the CLI and for a
    later Temporal activity. Every input is injected; nothing here reads the clock."""
    dispatcher = build_reward_dispatcher(client, token=token, clock=clock, backoff=backoff)
    return dispatcher.run_once(session_factory, limit=limit)
