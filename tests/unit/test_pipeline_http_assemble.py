"""The pipeline ``assemble`` step over HTTP, against in-process ``httpx.MockTransport``s.

Proves the request shapes and the decisions the step makes: it asks P5 which candidates
are cleared (a 404 or an uncleared manifest is not offered to P6), groups the cleared
ones by P3 package via their track spec, sends P6 candidate ids (never manifest bodies)
with P3's playlist facts (the declared duration range, never a point), and fails on a P6
refusal or a package post-render QA blocked. A package short of its range minimum (no
cleared recording, or P6's ``playlist_supply_short``) is reported with its missing
seconds after the other packages were assembled, so the workflow can top it up. It
proves nothing about P3, P5 or P6 themselves; their own tests and the integration test
do.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from plm_platform.pipeline.http import (
    HttpPhaseSteps,
    PhaseClient,
    PhaseRequestRefused,
    PlaylistSupplyShort,
)
from plm_platform.pipeline.states import Step
from plm_platform.pipeline.types import PipelineOptions, StepRequest
from plm_platform.temporal import Phase, ValidationFailure

from tests.support.seams import release_manifest

TOKEN = "unit-token"
STEP_KEY = "pipeline.dir-1:assemble"
OVERRIDE = {
    "status": "CLEARED_BY_OVERRIDE", "quarantine_case_id": "qc", "auditor_id": "aud",
    "resolved_at": "2026-09-25T12:00:00+00:00", "reason_codes": ["UNKNOWN_COMMERCIAL_LICENSE"],
}
RANGE = {"min_seconds": 2700, "max_seconds": 4500}


def test_default_pipeline_timeout_covers_long_p6_render_with_headroom() -> None:
    options = PipelineOptions()
    assert options.step_start_to_close_seconds >= 3600
    assert 600 < HttpPhaseSteps.ASSEMBLE_TIMEOUT_SECONDS
    assert HttpPhaseSteps.ASSEMBLE_TIMEOUT_SECONDS <= (
        options.step_start_to_close_seconds - 120
    )
    assert options.step_heartbeat_seconds < options.step_start_to_close_seconds
    # Explicit scenario-specific activity deadlines remain owner-controlled.
    assert PipelineOptions(step_start_to_close_seconds=45).step_start_to_close_seconds == 45


def _package(package_id: str, specs: list[str]) -> dict[str, Any]:
    return {
        "package_id": package_id, "brand_id": "neon_seoul",
        "playlist_spec": {"duration_range_seconds": RANGE},
        "visual_spec": {"visual_world": "rainy_coffee_shop"},
        "title_spec": {"formatting_pattern": "{mood} {genre} for {use_case}"},
        "track_specs": [
            {"track_spec_id": spec, "hard_constraints": {"genre": "lofi"},
             "bounded_preferences": {"mood": ["peaceful"], "use_case": ["study"]},
             "duration_range": {"min_seconds": 150 + 10 * n, "max_seconds": 240}}
            for n, spec in enumerate(specs)
        ],
    }


P3_PACKAGES = {
    "pkg_1": _package("pkg_1", ["trk_0", "trk_1", "trk_2"]),
    "pkg_2": _package("pkg_2", ["trk_elsewhere"]),
    "pkg_empty": _package("pkg_empty", ["trk_nobody"]),
}


def _manifest(candidate_id: str, spec: str, **overrides: Any) -> dict[str, Any]:
    return json.loads(json.dumps(release_manifest(
        candidate_id=candidate_id, track_spec_id=spec, **overrides).to_wire()))


MANIFESTS = {
    "c0": _manifest("c0", "trk_0"),
    "c1": _manifest("c1", "trk_1", disposition="QUARANTINED", override=OVERRIDE),
    "c2": _manifest("c2", "trk_2"),
    "c_rejected": _manifest("c_rejected", "trk_2", disposition="REJECTED"),
    "c_other": _manifest("c_other", "trk_elsewhere"),
}


class _Phases:
    def __init__(self, p6_answers: dict[str, tuple[int, dict[str, Any]]] | None = None,
                 *, step_key: str = STEP_KEY) -> None:
        self.p6_bodies: list[dict[str, Any]] = []
        self.p6_keys: list[str] = []
        self.p6_answers = p6_answers or {}
        self.step_key = step_key

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.headers["X-PLM-Service-Token"] == TOKEN
        if path.startswith("/qa/release-manifests/"):
            assert request.headers["Idempotency-Key"] == self.step_key
            body = MANIFESTS.get(path.rsplit("/", 1)[-1])
            if body is None:
                return httpx.Response(404, json={"detail": {"code": "manifest_not_found",
                                                            "message": "", "field_paths": []}})
            return httpx.Response(200, json=body)
        if path.startswith("/experiments/v1/packages/"):
            return httpx.Response(200, json=P3_PACKAGES[path.rsplit("/", 1)[-1]])
        if path == "/media/packages/assemble":
            body = json.loads(request.content)
            self.p6_bodies.append(body)
            self.p6_keys.append(request.headers["Idempotency-Key"])
            answer = self.p6_answers.get(body["candidate_ids"][0])
            if answer is not None:
                return httpx.Response(answer[0], json=answer[1])
            return httpx.Response(200, json={"media_package_id": f"m_{body['candidate_ids'][0]}",
                                             "status": "ready_for_publish",
                                             "video_checksum": "a" * 64,
                                             "blocking_reason_codes": []})
        raise AssertionError(f"unexpected call {request.method} {path}")


def _steps(phases: _Phases) -> HttpPhaseSteps:
    transport = httpx.MockTransport(phases.handler)
    return HttpPhaseSteps({
        phase: PhaseClient(phase, httpx.Client(base_url="http://phase.test", transport=transport),
                           token=TOKEN)
        for phase in (Phase.QA, Phase.EXPERIMENTS, Phase.MEDIA)
    })


def _request(candidates: list[str], packages: list[str] | None = None) -> StepRequest:
    return StepRequest(
        workflow_id="pipeline.dir-1", directive_id="dir-1", brand_id="neon_seoul",
        step=Step.ASSEMBLE.value, idempotency_key=STEP_KEY,
        requested_at=datetime(2026, 10, 14, tzinfo=UTC),
        inputs={"policy_check": {"package_ids": packages or ["pkg_1"]},
                "generate": {"candidate_ids": candidates}},
    )


def test_cleared_candidates_go_to_p6_by_id_with_the_declared_duration_range() -> None:
    phases = _Phases()
    result = asyncio.run(_steps(phases).assemble(
        _request(["c0", "c1", "c2", "c_rejected", "c_missing", "c_other"])))
    assert phases.p6_bodies == [{
        "candidate_ids": ["c0", "c1", "c2"], "brand_id": "neon_seoul",
        "duration_range_seconds": RANGE, "visual_profile": "rainy_coffee_shop",
        "playlist_concept": "peaceful lofi for study",
    }]
    assert phases.p6_keys == [f"{STEP_KEY}/assemble/pkg_1"]
    assert result.outputs["media_package_ids"] == ["m_c0"]
    assert result.outputs["packages"][0]["p3_package_id"] == "pkg_1"
    assert result.provider_ids == {"media_package.0": "m_c0"}


def test_a_package_blocked_by_post_render_qa_fails_the_step() -> None:
    phases = _Phases({"c0": (200, {"media_package_id": "mpkg_1", "status": "failed",
                                   "video_checksum": "a" * 64,
                                   "blocking_reason_codes": ["loudness_out_of_tolerance"]})})
    with pytest.raises(ValidationFailure, match="loudness_out_of_tolerance"):
        asyncio.run(_steps(phases).assemble(_request(["c0", "c1", "c2"])))


@pytest.mark.parametrize("reason", [
    "stream_duration_unmeasured",
    "stream_duration_mismatch",
    "video_audio_duration_mismatch",
])
def test_stream_timing_failed_p6_http_response_blocks_pipeline_assembly(reason: str) -> None:
    """The real pipeline HTTP step refuses P6's new post-render reason codes."""
    phases = _Phases({"c0": (200, {
        "media_package_id": "m_timing_failed",
        "status": "failed",
        "video_checksum": "a" * 64,
        "blocking_reason_codes": [reason],
    })})
    with pytest.raises(ValidationFailure, match=reason):
        asyncio.run(_steps(phases).assemble(_request(["c0", "c1", "c2"])))
    # A non-retryable validation error stops the step before producing a package list.
    assert len(phases.p6_bodies) == 1
    assert phases.p6_keys == [f"{STEP_KEY}/assemble/pkg_1"]


def test_a_p6_refusal_fails_the_step_with_its_code() -> None:
    phases = _Phases({"c0": (422, {"detail": {"code": "render_failed", "message": "m",
                                              "field_paths": []}})})
    with pytest.raises(PhaseRequestRefused) as raised:
        asyncio.run(_steps(phases).assemble(_request(["c0", "c1", "c2"])))
    assert raised.value.code == "render_failed"


def test_a_short_supply_is_reported_with_its_missing_seconds_after_the_rest_assembled() -> None:
    """P6's ``detail.facts.missing_seconds`` sizes the top-up; a package with no cleared
    recording is short by the whole minimum. The other package is still assembled."""
    short = (422, {"detail": {"code": "playlist_supply_short", "field_paths": [],
                              "message": "all eligible tracks give 2400.0s",
                              "facts": {"missing_seconds": 301}}})
    phases = _Phases({"c0": short})
    with pytest.raises(PlaylistSupplyShort) as raised:
        asyncio.run(_steps(phases).assemble(
            _request(["c0", "c2", "c_other"], ["pkg_1", "pkg_2", "pkg_empty"])))
    details = raised.value.details
    assert details["short_packages"] == [
        {"p3_package_id": "pkg_1", "shortest_track_seconds": 150,
         "track_spec_ids": ["trk_0", "trk_1", "trk_2"], "cleared_count": 2,
         "missing_seconds": 301},
        {"p3_package_id": "pkg_empty", "shortest_track_seconds": 150,
         "track_spec_ids": ["trk_nobody"], "cleared_count": 0, "missing_seconds": 2700},
    ]
    assert [p["p3_package_id"] for p in details["assembled"]] == ["pkg_2"]
    assert raised.value.no_effect


def test_a_short_supply_without_the_missing_seconds_is_refused_not_guessed() -> None:
    phases = _Phases({"c0": (422, {"detail": {"code": "playlist_supply_short",
                                              "field_paths": [], "message": "short"}})})
    with pytest.raises(ValidationFailure, match="cannot be sized"):
        asyncio.run(_steps(phases).assemble(_request(["c0"])))


def test_a_playlist_spec_without_its_duration_range_is_refused() -> None:
    package = P3_PACKAGES["pkg_1"]
    P3_PACKAGES["pkg_1"] = {**package, "playlist_spec": {"total_duration_minutes": 60}}
    try:
        with pytest.raises(ValidationFailure, match="duration_range_seconds"):
            asyncio.run(_steps(_Phases()).assemble(_request(["c0"])))
    finally:
        P3_PACKAGES["pkg_1"] = package


def test_missing_inputs_are_refused() -> None:
    without_policy = _request(["c0"])
    without_policy.inputs.pop("policy_check")
    with pytest.raises(ValidationFailure, match="policy_check"):
        asyncio.run(_steps(_Phases()).assemble(without_policy))
