"""The weekly P2 schedule never starts an unrequested AUTONOMOUS cycle (review 2026-11-26).

A cycle's activation state used to come from the stored channel state when the payload
named none, so the weekly schedule turned AUTONOMOUS the moment the channel was. A
scheduled cycle is SHADOW unless its payload names a state explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from autonomous_ar.jobs.cycle import cycle_request_from_payload
from autonomous_ar.jobs.schedules import WEEKLY_ALLOCATION_PAYLOAD
from plm_contracts import ChannelActivationState

NOW = datetime(2026, 11, 27, 6, 0, tzinfo=UTC)


def test_the_weekly_schedule_asks_for_shadow() -> None:
    assert WEEKLY_ALLOCATION_PAYLOAD["activation_state"] == ChannelActivationState.SHADOW.value
    request = cycle_request_from_payload(dict(WEEKLY_ALLOCATION_PAYLOAD), now=NOW)
    assert request.activation_state is ChannelActivationState.SHADOW
    assert request.as_of == NOW


def test_a_payload_without_a_state_is_shadow_not_the_channel_state() -> None:
    payload = {"brand_id": "longform_signal", "slot_count": 1, "budget": 1.0,
               "policy_version": "p2-v2"}
    assert cycle_request_from_payload(payload, now=NOW).activation_state \
        is ChannelActivationState.SHADOW


def test_an_explicit_state_is_honoured_and_parsed() -> None:
    payload = {"brand_id": "longform_signal", "slot_count": 1, "budget": 1.0,
               "policy_version": "p2-v2", "activation_state": "AUTONOMOUS", "as_of": NOW}
    request = cycle_request_from_payload(payload, now=datetime(2030, 1, 1, tzinfo=UTC))
    assert request.activation_state is ChannelActivationState.AUTONOMOUS
    assert request.as_of == NOW
    with pytest.raises(ValueError):
        cycle_request_from_payload({**payload, "activation_state": "autonomous!"}, now=NOW)


def test_the_prototype_loop_worker_asks_p2_for_shadow_only() -> None:
    """``scripts/autonomous_loop_worker.py`` is disabled, but its helper is importable;
    the cycle it would post is SHADOW, never AUTONOMOUS."""
    from scripts import autonomous_loop_worker

    assert autonomous_loop_worker.CYCLE_ACTIVATION_STATE == "SHADOW"
