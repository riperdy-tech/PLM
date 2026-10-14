"""Gate R3: P9's transactional outbox delivering to the running P2 container over real HTTP.

Against the compose stack: P9's outbox table in PostgreSQL, ``HttpTransport`` and
``HttpReconciler`` from :mod:`plm_platform.outbox`, and the ``api-ar`` container on
127.0.0.1:8082 (``PLM_SERVICE_URL__AR`` overrides) authenticating with
``PLM_SERVICE_TOKEN``. Nothing on the P2 side is stubbed: P2 parses, dedupes and stores
the fact in its own schema, and answers the reconciliation read.

Setup this test owns: an arm-state row for a fresh arm (P2 refuses results for arms it
does not know; P1 mints arms in production), the outbox rows, and P2's stored rows for
its fresh experiment, all removed afterwards. Skips with the reason when the token is
unset or ``api-ar`` is unreachable; fails when ``api-ar`` rejects the token (recreate it
with the token exported: ``docker compose up -d --wait api-ar``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, Settings
from plm_platform.outbox import (
    Dispatcher,
    HttpReconciler,
    HttpTransport,
    OutboxMessage,
    OutboxStatus,
    SendOutcome,
    SendResult,
)
from plm_platform.service_urls import LOOPBACK_HOST, service_url
from plm_platform.temporal import Phase
from sqlalchemy.orm import Session, sessionmaker

from services.analytics.evaluation.delivery import (
    AR_RESULTS_PATH,
    REWARD_TOPIC,
    build_reward_dispatcher,
    p2_acknowledges,
    p2_lookup_present,
    p2_lookup_query,
    reward_outbox,
)
from services.analytics.storage.models import OUTBOX_EVENTS
from services.autonomous_ar.storage.models import (
    ar_content_arm_state,
    ar_experiment_result,
    ar_reward_observation,
)
from services.autonomous_ar.storage.repositories import upsert_arm_state
from tests.integration.pipeline.support import REPO_ROOT
from tests.support.seams import unmeasured_raw_metrics

pytestmark = pytest.mark.integration

BRAND = "gate_brand"


@dataclass
class P2:
    client: httpx.Client
    token: str
    factory: sessionmaker[Session]
    experiment_id: str
    arm_id: str

    def payload(self, content_id: str) -> dict[str, Any]:
        return {
            "schema_version": "1.0", "experiment_id": self.experiment_id,
            "arm_id": self.arm_id, "content_id": content_id, "observation_window": "28d",
            "status": "mature", "observed_at": "2026-10-01T00:00:00+00:00",
            "raw_metrics": unmeasured_raw_metrics(), "normalized_reward": 0.8,
            "reward_version": "p9-reward-v1", "attribution_confidence": 0.9,
        }

    def stored_rows(self, content_id: str) -> tuple[int, int]:
        """(reward observations, experiment results) P2 holds for this fact."""
        with self.factory() as session:
            observations = session.execute(sa.select(sa.func.count()).select_from(
                ar_reward_observation).where(
                ar_reward_observation.c.experiment_id == self.experiment_id,
                ar_reward_observation.c.content_id == content_id)).scalar_one()
            results = session.execute(sa.select(sa.func.count()).select_from(
                ar_experiment_result).where(
                ar_experiment_result.c.experiment_id == self.experiment_id,
                ar_experiment_result.c.content_id == content_id)).scalar_one()
        return int(observations), int(results)


@pytest.fixture
def p2(settings: Settings, factory: sessionmaker[Session]) -> Iterator[P2]:
    if settings.service_token is None:
        pytest.skip("PLM_SERVICE_TOKEN is unset: export the dev token (.env.example) and "
                    "recreate api-ar with it (docker compose up -d --wait api-ar)")
    token = settings.service_token.get_secret_value()
    base_url = service_url(Phase.AR, dotenv_path=REPO_ROOT / ".env", default_host=LOOPBACK_HOST)
    client = httpx.Client(base_url=base_url, timeout=10.0)
    try:
        ready = client.get("/readyz")
    except httpx.HTTPError as exc:
        client.close()
        pytest.skip(f"api-ar not reachable at {base_url} - {exc}; "
                    "start it with: docker compose up -d --wait api-ar")
    assert ready.status_code == 200, f"api-ar is not ready: {ready.text}"
    probe = client.get(AR_RESULTS_PATH, headers={SERVICE_TOKEN_HEADER: token}, params={
        "experiment_id": "probe", "content_id": "probe", "observation_window": "28d",
        "status": "mature"})
    assert probe.status_code == 200, (
        f"api-ar refused the service token ({probe.status_code}: {probe.text}); recreate it "
        "with PLM_SERVICE_TOKEN exported: docker compose up -d --wait api-ar")

    suffix = uuid.uuid4().hex[:12]
    experiment_id, arm_id = f"gate-exp-{suffix}", f"gate-arm-{suffix}"
    with factory() as session:
        upsert_arm_state(session, brand_id=BRAND, arm_id=arm_id, now=datetime.now(UTC))
        session.commit()
    try:
        yield P2(client=client, token=token, factory=factory, experiment_id=experiment_id,
                 arm_id=arm_id)
    finally:
        client.close()
        with factory() as session:
            session.execute(OUTBOX_EVENTS.delete().where(
                OUTBOX_EVENTS.c.idempotency_key.like(f"%{experiment_id}%")))
            session.execute(ar_reward_observation.delete().where(
                ar_reward_observation.c.experiment_id == experiment_id))
            session.execute(ar_experiment_result.delete().where(
                ar_experiment_result.c.experiment_id == experiment_id))
            session.execute(ar_content_arm_state.delete().where(
                ar_content_arm_state.c.arm_id == arm_id))
            session.commit()


def _enqueue(p2: P2, content_id: str, now: datetime) -> OutboxMessage:
    """P9's side: the event is committed in P9's own transaction (never sent in it)."""
    with p2.factory() as session:
        message = reward_outbox.enqueue(
            session, topic=REWARD_TOPIC,
            idempotency_key=f"{REWARD_TOPIC}:{p2.experiment_id}|{content_id}|28d|mature",
            payload=p2.payload(content_id), destination=AR_RESULTS_PATH, now=now)
        session.commit()
    return message


def _message(p2: P2, key: str) -> OutboxMessage:
    with p2.factory() as session:
        message = reward_outbox.get(session, topic=REWARD_TOPIC, idempotency_key=key)
    assert message is not None
    return message


class LostResponse:
    """Sends for real, then reports a read timeout: the request landed, the answer did not."""

    def __init__(self, inner: HttpTransport) -> None:
        self.inner = inner
        self.sent = 0

    def send(self, message: OutboxMessage) -> SendResult:
        self.inner.send(message)
        self.sent += 1
        return SendResult(SendOutcome.AMBIGUOUS, code="ReadTimeout",
                          detail="simulated: the response was lost after P2 stored the fact")


def test_duplicate_delivery_is_stored_once_by_the_running_p2(p2: P2) -> None:
    """Proves over real HTTP: a re-enqueued fact is one outbox row; the dispatcher's
    delivery is acknowledged by P2 and marked DELIVERED; a duplicate delivery of the same
    fact (a resend after a crash before DELIVERED was committed) is acknowledged as
    ``stored: false`` and P2 still holds exactly one observation and one result."""
    clock = FixedClock(datetime.now(UTC))
    content_id = "content-dup"
    first = _enqueue(p2, content_id, clock.now())
    assert _enqueue(p2, content_id, clock.now()).id == first.id  # one key, one row

    report = build_reward_dispatcher(p2.client, token=p2.token, clock=clock).run_once(
        p2.factory)
    assert (report.claimed, report.delivered) == (1, 1)
    assert _message(p2, first.idempotency_key).status is OutboxStatus.DELIVERED
    assert p2.stored_rows(content_id) == (1, 1)

    duplicate = HttpTransport(p2.client, token=p2.token, acknowledges=p2_acknowledges)
    assert duplicate.send(_message(p2, first.idempotency_key)).outcome is SendOutcome.ACKNOWLEDGED
    raw = p2.client.post(AR_RESULTS_PATH, json=p2.payload(content_id),
                         headers={SERVICE_TOKEN_HEADER: p2.token,
                                  "Idempotency-Key": first.idempotency_key})
    assert raw.status_code == 200 and raw.json()["stored"] is False
    assert p2.stored_rows(content_id) == (1, 1)


def test_lost_response_reconciles_to_delivered_without_a_resend(p2: P2) -> None:
    """Proves over real HTTP: a send whose response is lost leaves the event UNCERTAIN
    (never assumed delivered); the next due pass asks P2 ``GET /ar/results``, which says
    the fact is stored, so the event becomes DELIVERED with no second send; P2 holds one
    copy."""
    clock = FixedClock(datetime.now(UTC))
    content_id = "content-lost"
    message = _enqueue(p2, content_id, clock.now())
    lost = LostResponse(HttpTransport(p2.client, token=p2.token, acknowledges=p2_acknowledges))
    dispatcher = Dispatcher(
        reward_outbox, transport=lost, clock=clock, topics=(REWARD_TOPIC,),
        reconciler=HttpReconciler(p2.client, token=p2.token, query=p2_lookup_query,
                                  present=p2_lookup_present))

    assert dispatcher.run_once(p2.factory).uncertain == 1
    uncertain = _message(p2, message.idempotency_key)
    assert uncertain.status is OutboxStatus.UNCERTAIN
    assert uncertain.last_error_code == "ReadTimeout"
    assert p2.stored_rows(content_id) == (1, 1)  # it did land

    clock.set(uncertain.next_attempt_at)
    report = dispatcher.run_once(p2.factory)
    assert report.reconciled_present == 1 and report.delivered == 0
    delivered = _message(p2, message.idempotency_key)
    assert delivered.status is OutboxStatus.DELIVERED
    assert (delivered.attempt_count, delivered.reconcile_count) == (1, 1)
    assert lost.sent == 1, "reconciliation must never resend"
    assert p2.stored_rows(content_id) == (1, 1)
