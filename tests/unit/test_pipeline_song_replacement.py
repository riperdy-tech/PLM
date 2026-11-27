"""Review 2026-11-26 #47: a short playlist first replaces the owner-made songs P5 rejected.

The ``extend`` step against the HTTP fakes of :mod:`tests.unit.test_pipeline_http_r5`:
for a run whose songs the owner makes (``suno_manual`` / ``flow_music_manual``), each
rejected candidate of the short package is replaced in P4 (its plan's next execute asks
for ``<track>-<variant>-r2.wav``) and its track spec is generated again; P3 is asked for
new specs only for what replacement does not cover. Proves the requests, not P3 or P4.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from plm_platform.pipeline.states import Step

from tests.unit.test_pipeline_http_r5 import World, _body, _request

KEY = "pipeline.dir-1:extend.r1.0"
PACKAGE = {"package_id": "p1", "track_specs": [{"track_spec_id": "ts-1"},
                                               {"track_spec_id": "ts-2"}]}


def _world(candidates: dict[str, str], *, extend_answer: Any = None) -> World:
    """P3 package p1 (ts-1, ts-2); P4 candidates id -> track spec; P3 extend answer."""
    world = World()
    world.on("GET", "/experiments/v1/packages/p1", PACKAGE)
    for candidate_id, spec in candidates.items():
        world.on("GET", f"/music/candidates/{candidate_id}",
                 {"candidate": {"candidate_id": candidate_id, "track_spec_id": spec,
                                "status": "EXPORTED_TO_P5"}})
        world.on("POST", f"/music/candidates/{candidate_id}/replace",
                 {"candidate_id": candidate_id, "status": "REJECTED"})
    world.on("POST", "/experiments/v1/packages/p1/extend", extend_answer or {
        "extension_id": "ext-1", "track_specs": [{"track_spec_id": "ts-new"}],
        "remaining_extension_track_count": 3})
    return world


def _extend(world: World, count: int, rejected: list[str],
            provider: str | None = "flow_music_manual") -> Any:
    inputs: dict[str, Any] = {"p3_package_id": "p1", "count": count,
                              "rejected_candidate_ids": rejected}
    if provider is not None:
        inputs["music_provider"] = provider
    return asyncio.run(world.steps().extend(_request(Step.EXTEND, inputs, key=KEY)))


def test_rejected_owner_made_songs_are_replaced_before_p3_is_asked_for_more() -> None:
    world = _world({"c-1": "ts-1", "c-2": "ts-2", "c-other": "ts-99"})
    result = _extend(world, 3, ["c-1", "c-2", "c-other"])

    replaced = world.sent("POST", "/music/candidates/c-1/replace")
    assert len(replaced) == 1
    assert replaced[0].headers["Idempotency-Key"] == f"{KEY}/replace/c-1"
    assert world.sent("POST", "/music/candidates/c-other/replace") == []  # other package
    (extended,) = world.sent("POST", "/experiments/v1/packages/p1/extend")
    assert _body(extended) == {"count": 1, "idempotency_key": KEY}  # 3 - 2 replaced
    assert result.outputs["track_spec_ids"] == ["ts-1", "ts-2", "ts-new"]
    assert result.outputs["replaced_candidate_ids"] == ["c-1", "c-2"]


def test_replacement_covering_the_shortage_asks_p3_for_nothing() -> None:
    world = _world({"c-1": "ts-1"})
    result = _extend(world, 1, ["c-1"])
    assert world.sent("POST", "/experiments/v1/packages/p1/extend") == []
    assert result.outputs["track_spec_ids"] == ["ts-1"]


def test_a_spec_replaced_twice_falls_back_to_new_specs() -> None:
    """At most two replacements per track spec: its third rejected song is not asked
    for again (``-r3`` would be the third replacement); P3 adds a new spec instead."""
    world = _world({"c-1": "ts-1", "c-1r2": "ts-1", "c-1r3": "ts-1"})
    result = _extend(world, 1, ["c-1", "c-1r2", "c-1r3"])
    assert not any(r.url.path.endswith("/replace") for r in [r for _, r in world.requests])
    assert result.outputs["track_spec_ids"] == ["ts-new"]
    assert result.outputs["replaced_candidate_ids"] == []


def test_a_paid_or_unknown_song_tool_never_replaces() -> None:
    """Replacing a paid provider's song would re-buy it (P4 refuses that anyway); with no
    owner-made tool named the step is the plain top-up."""
    for provider in ("elevenlabs", None):
        world = _world({"c-1": "ts-1"})
        result = _extend(world, 1, ["c-1"], provider=provider)
        assert [r.url.path for r in [r for _, r in world.requests]] == [
            "/experiments/v1/packages/p1/extend"]
        assert result.outputs["track_spec_ids"] == ["ts-new"]


def test_p3s_extension_bound_after_a_replacement_keeps_the_replacements() -> None:
    world = _world({"c-1": "ts-1"}, extend_answer=lambda r: httpx.Response(
        409, json={"detail": {"code": "package_extension_limit_exceeded",
                              "field_paths": [], "message": "bound"}}))
    result = _extend(world, 2, ["c-1"])
    assert result.outputs["track_spec_ids"] == ["ts-1"]
    assert result.outputs["extension_limit_reached"] is True
