"""`POST /ar/cycles` honours `Idempotency-Key` (review #14).

The pipeline sends its run's request id as the key. A retry with the same key returns
the first cycle and writes nothing; the same key on a different body is a caller bug and
is refused with 409; a request without a key behaves as before.
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from services.autonomous_ar.storage.models import ar_allocation_cycle


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "brand_id": "longform_signal", "slot_count": 1, "budget": 1.0,
        "as_of": "2026-09-20T06:00:00+00:00", "policy_version": "p2-v2",
    }
    body.update(overrides)
    return body


def _cycles(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return int(session.execute(
            sa.select(sa.func.count()).select_from(ar_allocation_cycle)).scalar_one())


def test_a_repeat_with_the_same_key_returns_the_first_cycle(
        client: TestClient, ar_factory: sessionmaker[Session]) -> None:
    key = {"Idempotency-Key": "run-request-1"}
    first = client.post("/ar/cycles", json=_body(), headers=key)
    assert first.status_code == 200, first.text
    again = client.post("/ar/cycles", json=_body(), headers=key)
    assert again.status_code == 200, again.text
    assert again.json()["cycle_id"] == first.json()["cycle_id"]
    assert again.json() == {**first.json(), "replay": True}
    assert _cycles(ar_factory) == 1


def test_a_retry_stamped_later_with_the_same_key_is_refused_not_a_second_cycle(
        client: TestClient, ar_factory: sessionmaker[Session]) -> None:
    """The cycle id comes from the key, not the instant: a retry can never make a
    second cycle; one with another instant is a different request, hence 409."""
    key = {"Idempotency-Key": "run-request-3"}
    assert client.post("/ar/cycles", json=_body(), headers=key).status_code == 200
    later = client.post("/ar/cycles", headers=key, json=_body(slot_count=2))
    assert later.status_code == 409
    assert _cycles(ar_factory) == 1


def test_the_same_key_on_a_different_body_is_refused(
        client: TestClient, ar_factory: sessionmaker[Session]) -> None:
    key = {"Idempotency-Key": "run-request-2"}
    assert client.post("/ar/cycles", json=_body(), headers=key).status_code == 200
    other = client.post("/ar/cycles", headers=key,
                        json=_body(as_of="2026-09-21T06:00:00+00:00"))
    assert other.status_code == 409, other.text
    assert other.json()["detail"]["code"] == "idempotency_key_reused"
    assert _cycles(ar_factory) == 1, "the refused request wrote nothing"


def test_without_a_key_a_new_instant_is_a_new_cycle(
        client: TestClient, ar_factory: sessionmaker[Session]) -> None:
    assert client.post("/ar/cycles", json=_body()).status_code == 200
    assert client.post("/ar/cycles",
                       json=_body(as_of="2026-09-21T06:00:00+00:00")).status_code == 200
    assert _cycles(ar_factory) == 2
    blank = client.post("/ar/cycles", json=_body(), headers={"Idempotency-Key": "  "})
    assert blank.status_code == 422
