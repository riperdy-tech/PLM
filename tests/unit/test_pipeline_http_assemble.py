"""The pipeline ``assemble`` step over HTTP, against in-process ``httpx.MockTransport``s.

Proves the request shapes and the decisions the step makes: it asks P5 which candidates
are cleared (a 404 or an uncleared manifest is not offered to P6), groups the cleared
ones by P3 package via their track spec, sends P6 candidate ids (never manifest bodies)
with P3's playlist facts, and fails on a P6 refusal, on a package post-render QA blocked,
or when no package has a cleared recording. It proves nothing about P3, P5 or P6
themselves; their own tests and the integration test do.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from plm_platform.pipeline.http import HttpPhaseSteps, PhaseClient, PhaseRequestRefused
from plm_platform.pipeline.states import Step
from plm_platform.pipeline.types import StepRequest
from plm_platform.temporal import Phase, ValidationFailure

from tests.support.seams import release_manifest

TOKEN = "unit-token"
OVERRIDE = {
    "status": "CLEARED_BY_OVERRIDE", "quarantine_case_id": "qc", "auditor_id": "aud",
    "resolved_at": "2026-09-25T12:00:00+00:00", "reason_codes": ["UNKNOWN_COMMERCIAL_LICENSE"],
}
P3_PACKAGE = {
    "package_id": "pkg_1", "brand_id": "neon_seoul",
    "playlist_spec": {"total_duration_minutes": 60},
    "visual_spec": {"visual_world": "rainy_coffee_shop"},
    "title_spec": {"formatting_pattern": "{mood} {genre} for {use_case}"},
    "track_specs": [
        {"track_spec_id": f"trk_{n}", "hard_constraints": {"genre": "lofi"},
         "bounded_preferences": {"mood": ["peaceful"], "use_case": ["study"]}}
        for n in range(3)
    ],
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
    def __init__(self, p6_answer: tuple[int, dict[str, Any]] | None = None) -> None:
        self.p6_bodies: list[dict[str, Any]] = []
        self.p6_answer = p6_answer

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        assert request.headers["X-PLM-Service-Token"] == TOKEN
        assert request.headers["Idempotency-Key"] == "pipeline.dir-1:assemble"
        if path.startswith("/qa/release-manifests/"):
            body = MANIFESTS.get(path.rsplit("/", 1)[-1])
            if body is None:
                return httpx.Response(404, json={"detail": {"code": "manifest_not_found",
                                                            "message": "", "field_paths": []}})
            return httpx.Response(200, json=body)
        if path == "/experiments/v1/packages/pkg_1":
            return httpx.Response(200, json=P3_PACKAGE)
        if path == "/media/packages/assemble":
            self.p6_bodies.append(json.loads(request.content))
            if self.p6_answer is not None:
                return httpx.Response(self.p6_answer[0], json=self.p6_answer[1])
            return httpx.Response(200, json={"media_package_id": "mpkg_1",
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


def _request(candidates: list[str]) -> StepRequest:
    return StepRequest(
        workflow_id="pipeline.dir-1", directive_id="dir-1", brand_id="neon_seoul",
        step=Step.ASSEMBLE.value, idempotency_key="pipeline.dir-1:assemble",
        requested_at=datetime(2026, 10, 14, tzinfo=UTC),
        inputs={"policy_check": {"package_ids": ["pkg_1"]},
                "generate": {"candidate_ids": candidates}},
    )


def test_cleared_candidates_go_to_p6_by_id_with_p3_facts() -> None:
    phases = _Phases()
    result = asyncio.run(_steps(phases).assemble(
        _request(["c0", "c1", "c2", "c_rejected", "c_missing", "c_other"])))
    assert phases.p6_bodies == [{
        "candidate_ids": ["c0", "c1", "c2"], "brand_id": "neon_seoul",
        "required_duration_seconds": 3600.0, "visual_profile": "rainy_coffee_shop",
        "playlist_concept": "peaceful lofi for study",
    }]
    assert result.outputs["media_package_ids"] == ["mpkg_1"]
    assert result.provider_ids == {"media_package.0": "mpkg_1"}


def test_a_package_blocked_by_post_render_qa_fails_the_step() -> None:
    phases = _Phases((200, {"media_package_id": "mpkg_1", "status": "failed",
                            "video_checksum": "a" * 64,
                            "blocking_reason_codes": ["loudness_out_of_tolerance"]}))
    with pytest.raises(ValidationFailure, match="loudness_out_of_tolerance"):
        asyncio.run(_steps(phases).assemble(_request(["c0", "c1", "c2"])))


def test_a_p6_refusal_fails_the_step_with_its_code() -> None:
    phases = _Phases((422, {"detail": {"code": "required_duration_unreachable", "message": "m",
                                       "field_paths": []}}))
    with pytest.raises(PhaseRequestRefused) as raised:
        asyncio.run(_steps(phases).assemble(_request(["c0", "c1", "c2"])))
    assert raised.value.code == "required_duration_unreachable"


def test_no_cleared_recording_fails_without_calling_p6() -> None:
    phases = _Phases()
    with pytest.raises(ValidationFailure, match="has a recording P5 cleared"):
        asyncio.run(_steps(phases).assemble(_request(["c_rejected", "c_missing"])))
    assert phases.p6_bodies == []


def test_missing_inputs_are_refused() -> None:
    without_policy = _request(["c0"])
    without_policy.inputs.pop("policy_check")
    with pytest.raises(ValidationFailure, match="policy_check"):
        asyncio.run(_steps(_Phases()).assemble(without_policy))
