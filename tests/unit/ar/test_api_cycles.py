# tests/unit/ar/test_api_cycles.py
"""P2.13: §71's cycle endpoints, the SHADOW serving rule and the frozen C2 wire.

The serving rule is the contract: under `SHADOW` activation `GET /ar/directives` serves
*nothing actionable* (ADR-0024), and when it does serve, each payload carries exactly the
seventeen frozen C2 fields — no more, no less.
"""

from fastapi.testclient import TestClient
from plm_contracts.seams import parse_seam
from plm_contracts.seams.c2 import ProductionDirectivePayload

from services.autonomous_ar.domain.directive import DIRECTIVE_WIRE_FIELDS

BRIEF_WIRE_FIELDS = ("genre", "mood", "use_case", "language", "vocal_mode",
                     "duration_bucket", "visual_world")
"""The frozen C2 brief keys (registry C2); `DirectiveBrief` must not drift from them."""


def _cycle_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "brand_id": "neon_seoul", "slot_count": 2, "budget": 2.0,
        "as_of": "2026-09-20T06:00:00+00:00", "policy_version": "p2-v1",
    }
    body.update(overrides)
    return body


def test_cycle_endpoints_and_the_shadow_directive_rule(client: TestClient) -> None:
    created = client.post("/ar/cycles", json=_cycle_body())
    assert created.status_code == 200, created.text
    summary = created.json()
    assert summary["selected_count"] == 2 and summary["replay"] is False
    cycle_id = summary["cycle_id"]
    assert client.get(f"/ar/cycles/{cycle_id}").json()["status"] == "completed"
    decisions = client.get(f"/ar/cycles/{cycle_id}/decisions").json()
    assert {row["policy_version"] for row in decisions} == {"p2-v1"}
    # SHADOW: computed, persisted, auditable — and GET /ar/directives serves nothing
    assert client.get("/ar/directives", params={"brand_id": "neon_seoul"}).json() == []
    # §71's `channel_id` is not a wire name (ADR-0019): refusing beats aliasing
    assert client.post("/ar/cycles", json={
        "channel_id": "x", "slot_count": 1, "budget": 1.0,
        "as_of": "2026-09-20T06:00:00+00:00", "policy_version": "p2-v1",
    }).status_code == 422


def test_a_served_directive_is_exactly_the_seventeen_frozen_c2_fields(client: TestClient) -> None:
    created = client.post("/ar/cycles", json=_cycle_body(activation_state="AUTONOMOUS"))
    assert created.status_code == 200, created.text
    served = client.get("/ar/directives", params={"brand_id": "neon_seoul"}).json()
    assert len(served) == 2, "ADR-0024: an AUTONOMOUS cycle's queued directives are actionable"
    for item in served:
        assert tuple(item) == DIRECTIVE_WIRE_FIELDS, "C2 fields, in registry order, no extras"
        assert tuple(item["brief"]) == BRIEF_WIRE_FIELDS
        # the payload parses with the shared seam model and round-trips unchanged
        assert parse_seam(ProductionDirectivePayload, item).to_wire() == item
    # another brand's surface stays empty — the brand_id coordinate is load-bearing
    assert client.get("/ar/directives", params={"brand_id": "somewhere_else"}).json() == []


def test_the_same_cycle_request_replays_instead_of_writing_a_second(client: TestClient) -> None:
    first = client.post("/ar/cycles", json=_cycle_body()).json()
    second = client.post("/ar/cycles", json=_cycle_body()).json()
    assert second["replay"] is True and second["cycle_id"] == first["cycle_id"]
    assert second["selected_count"] == first["selected_count"]
