"""The R5 pipeline steps over HTTP, against in-process ``httpx.MockTransport``s.

Proves, for each step, the requests it sends (paths, bodies, one idempotency key per
write, every instant from the request's ``requested_at``), how it reads the answers, and
how each refusal is classified: a write guard's 409 is :class:`ExternalWriteRefused`
naming its hold; work still in flight is :class:`StepNotReady` (retried); a missing fact
or configuration section is a non-retryable refusal; no amount is ever sent to P9's
measurement. It proves nothing about the phase services; their own tests and the
integration tests do.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from plm_platform.clock import stable_seed
from plm_platform.config import SettingsError
from plm_platform.pipeline.config import (
    PipelineConfig,
    PipelineConfigMissing,
    load_pipeline_config,
    parse_pipeline_config,
)
from plm_platform.pipeline.http import (
    ExternalWriteRefused,
    HttpPhaseSteps,
    PhaseClient,
    PhaseRequestRefused,
    StepNotReady,
)
from plm_platform.pipeline.states import Step
from plm_platform.pipeline.types import StepRequest
from plm_platform.pipeline.workflow import topup_count
from plm_platform.temporal import Phase, ValidationFailure

from tests.support.seams import media_package_fields, publication_fields

TOKEN = "unit-token"
NOW = datetime(2026, 11, 12, 9, 30, tzinfo=UTC)
PUBLISHED = "2026-10-15T09:30:00+00:00"
CONFIG = parse_pipeline_config({
    "publish": {"channel_id": "UC_test", "operating_mode": "private_only"},
    "release": {"artist_name": "A", "label_name": "L", "copyright_line": "C",
                "publisher_line": "P", "territories": "WORLDWIDE",
                "release_date_offset_days": 2},
    "fx_rates": [{"base": "USD", "quote": "KRW", "rate": "1380.00", "source": "dev",
                  "as_of": "2026-01-01T00:00:00+00:00"}],
    "learning": {"slot_count": 3, "budget": 30.0, "policy_version": "p2-v1",
                 "activation_state": "AUTONOMOUS"},
})
Handler = Callable[[httpx.Request], httpx.Response]
C6 = json.loads(json.dumps(media_package_fields("cand-1", "cand-2",
                                                media_package_id="m1")))
EVENT = publication_fields(media_package_id="m1", youtube_video_id="yt1",
                           published_at=PUBLISHED)


class World:
    """Records every request; answers from per-path handlers."""

    def __init__(self) -> None:
        self.requests: list[tuple[Phase, httpx.Request]] = []
        self.routes: dict[tuple[str, str], Callable[[httpx.Request], Any]] = {}

    def on(self, method: str, path: str, answer: Any) -> None:
        self.routes[(method, path)] = answer if callable(answer) else (lambda r, a=answer: a)

    def handler(self, phase: Phase) -> Handler:
        def handle(request: httpx.Request) -> httpx.Response:
            assert request.headers["X-PLM-Service-Token"] == TOKEN
            self.requests.append((phase, request))
            answer = self.routes.get((request.method, request.url.path))
            if answer is None:
                raise AssertionError(f"unexpected {request.method} {request.url.path}")
            result = answer(request)
            if isinstance(result, httpx.Response):
                return result
            return httpx.Response(200, json=result)
        return handle

    def steps(self, config: PipelineConfig = CONFIG) -> HttpPhaseSteps:
        return HttpPhaseSteps({
            phase: PhaseClient(phase, httpx.Client(base_url="http://phase.test",
                                                   transport=httpx.MockTransport(
                                                       self.handler(phase))), token=TOKEN)
            for phase in Phase
        }, config=config)

    def sent(self, method: str, path: str) -> list[httpx.Request]:
        return [r for _, r in self.requests if r.method == method and r.url.path == path]


def _request(step: Step, inputs: dict[str, Any], key: str | None = None) -> StepRequest:
    return StepRequest(workflow_id="pipeline.dir-1", directive_id="dir-1", brand_id="brand",
                       step=step.value, idempotency_key=key or f"pipeline.dir-1:{step.value}",
                       requested_at=NOW, inputs=inputs)


def _body(request: httpx.Request) -> Any:
    return json.loads(request.content)


ASSEMBLED = {"packages": [{"media_package_id": "m1", "p3_package_id": "p1"}],
             "media_package_ids": ["m1"]}
RECEIPTS = [{"capability": "youtube.upload", "idempotency_key": "k:youtube.upload",
             "decision": "simulate", "reason": "mock_provider"},
            {"capability": "youtube.thumbnail", "idempotency_key": "k:youtube.thumbnail",
             "decision": "simulate", "reason": "mock_provider"}]


# -- upload ----------------------------------------------------------------------------------


def _upload_world(answer: Any) -> World:
    world = World()
    world.on("GET", "/media/packages/m1/c6", C6)
    world.on("POST", "/youtube/publish", answer)
    return world


def test_upload_publishes_the_c6_package_on_the_configured_channel() -> None:
    world = _upload_world({"attempt_id": "att-1", "manifest_id": "man-1", "state": "verified",
                           "youtube_video_id": "yt1", "publication_event": EVENT,
                           "external_writes": RECEIPTS})
    result = asyncio.run(world.steps().upload(_request(Step.UPLOAD, {"assemble": ASSEMBLED})))
    (publish,) = world.sent("POST", "/youtube/publish")
    assert _body(publish) == {"media_package": C6, "channel_id": "UC_test",
                              "operating_mode": "private_only"}
    assert publish.headers["Idempotency-Key"] == "pipeline.dir-1:upload/publish/m1"
    (publication,) = result.outputs["publications"]
    assert publication["youtube_video_id"] == "yt1" and publication["attempt_id"] == "att-1"
    assert publication["published_at"] == PUBLISHED and publication["p3_package_id"] == "p1"
    assert result.outputs["external_writes"] == [{**r, "media_package_id": "m1"}
                                                 for r in RECEIPTS]
    assert result.cost is None  # P7 reports no money; the spend is in the ledger


def test_upload_turns_a_guard_refusal_into_its_hold() -> None:
    world = _upload_world(lambda r: httpx.Response(409, json={"detail": {
        "code": "external_write_deny", "field_paths": [],
        "message": "youtube.upload not performed: budget_exhausted"}}))
    with pytest.raises(ExternalWriteRefused) as raised:
        asyncio.run(world.steps().upload(_request(Step.UPLOAD, {"assemble": ASSEMBLED})))
    assert raised.value.details["hold_reason_code"] == "BUDGET_RESERVATION_REFUSED"

    intent = _upload_world({"attempt_id": "att-1", "manifest_id": "man-1",
                            "state": "intent_recorded", "external_writes": [
                                {**RECEIPTS[0], "decision": "record_intent",
                                 "reason": "policy_dry_run"}]})
    with pytest.raises(ExternalWriteRefused) as raised:
        asyncio.run(intent.steps().upload(_request(Step.UPLOAD, {"assemble": ASSEMBLED})))
    assert raised.value.details["hold_reason_code"] == "EXTERNAL_WRITE_NOT_AUTHORIZED"
    assert raised.value.guard_reason == "policy_dry_run"

    failed = _upload_world({"attempt_id": "att-1", "manifest_id": "man-1",
                            "state": "failed_permanent", "error": "boom"})
    with pytest.raises(ValidationFailure, match="did not publish"):
        asyncio.run(failed.steps().upload(_request(Step.UPLOAD, {"assemble": ASSEMBLED})))


def test_upload_and_submission_refuse_without_their_configuration() -> None:
    world = World()
    with pytest.raises(PipelineConfigMissing, match="publish"):
        asyncio.run(world.steps(PipelineConfig()).upload(
            _request(Step.UPLOAD, {"assemble": ASSEMBLED})))
    with pytest.raises(PipelineConfigMissing, match="release"):
        asyncio.run(world.steps(PipelineConfig()).submit_distribution(
            _request(Step.SUBMIT_DISTRIBUTION, {"upload": {"publications": []}})))
    assert world.requests == []


# -- submit_distribution -----------------------------------------------------------------------


PUBLICATIONS = {"publications": [{"media_package_id": "m1", "p3_package_id": "p1",
                                  "attempt_id": "att-1", "youtube_video_id": "yt1",
                                  "channel_id": "UC_test", "published_at": PUBLISHED,
                                  "publication_event": EVENT}]}
RECORD = {"release_id": "rel-1", "isrcs": ["USPLM2600001", "USPLM2600002"]}


def test_submission_sends_the_manifests_package_event_and_configured_release_facts() -> None:
    world = World()
    world.on("GET", "/media/packages/m1/c6", C6)
    for candidate in ("cand-1", "cand-2"):
        world.on("GET", f"/qa/release-manifests/{candidate}", {"manifest_of": candidate})
    world.on("POST", "/distribution/releases", lambda r: httpx.Response(201, json={
        "release_id": "rel-1", "status": "submitted", "isrcs": RECORD["isrcs"],
        "distribution_record": RECORD, "external_writes": [
            {"capability": "distribution.submit", "idempotency_key": "f:submit",
             "decision": "simulate", "reason": "mock_provider"}]}))
    result = asyncio.run(world.steps().submit_distribution(
        _request(Step.SUBMIT_DISTRIBUTION, {"upload": PUBLICATIONS})))
    (submit,) = world.sent("POST", "/distribution/releases")
    body = _body(submit)
    assert body["track_manifests"] == [{"manifest_of": "cand-1"}, {"manifest_of": "cand-2"}]
    assert body["media_package"] == C6 and body["publication"] == EVENT
    assert body["release_date"] == "2026-11-14"  # requested_at + the configured offset
    assert (body["artist_name"], body["label_name"], body["territories"]) == (
        "A", "L", "WORLDWIDE")
    assert submit.headers["Idempotency-Key"] == "pipeline.dir-1:submit_distribution/submit/m1"
    (release,) = result.outputs["releases"]
    assert release["isrcs"] == RECORD["isrcs"] and release["distribution_record"] == RECORD
    assert result.outputs["external_writes"][0]["media_package_id"] == "m1"


# -- await_processing ---------------------------------------------------------------------------


@pytest.mark.parametrize(("state", "error"), [
    ("processing", StepNotReady), ("thumbnail_pending", StepNotReady),
    ("failed_permanent", ValidationFailure), ("intent_recorded", ValidationFailure),
])
def test_processing_waits_for_in_flight_work_and_refuses_a_failed_attempt(
        state: str, error: type[Exception]) -> None:
    world = World()
    world.on("GET", "/youtube/attempts/att-1", {"state": state, "youtube_video_id": "yt1"})
    with pytest.raises(error):
        asyncio.run(world.steps().await_processing(
            _request(Step.AWAIT_PROCESSING, {"upload": PUBLICATIONS})))


def test_processing_reports_the_latest_publication_instant() -> None:
    world = World()
    second = {**PUBLICATIONS["publications"][0], "attempt_id": "att-2", "media_package_id": "m2"}
    world.on("GET", "/youtube/attempts/att-1", {"state": "verified", "youtube_video_id": "yt1",
                                                "published_at": PUBLISHED})
    world.on("GET", "/youtube/attempts/att-2", {"state": "verified", "youtube_video_id": "yt2",
                                                "published_at": "2026-10-16T00:00:00Z"})
    result = asyncio.run(world.steps().await_processing(_request(
        Step.AWAIT_PROCESSING,
        {"upload": {"publications": [*PUBLICATIONS["publications"], second]}})))
    assert result.outputs["published_at"] == "2026-10-16T00:00:00+00:00"
    assert [u["youtube_video_id"] for u in result.outputs["units"]] == ["yt1", "yt2"]


# -- evaluate_outcome ---------------------------------------------------------------------------


SNAPSHOT = {"provider_mode": "mock", "metrics": {
    "views": 3360, "watch_time_minutes": 21840.0, "average_view_duration_seconds": 390.0,
    "average_percentage_viewed": 18.0, "subscribers_gained": 13, "subscribers_lost": 1,
    "estimated_revenue": {"amount": "4.032000", "currency": "USD"}}}
STATEMENT = {"period": "2026-10", "period_start": PUBLISHED,
             "period_end": "2026-11-01T00:00:00+00:00", "currency": "USD",
             "raw_content": "isrc,release_id,streams,revenue\n",
             "source_reference": "mock_distributor:rel-1:2026-10",
             "rows": [{"isrc": "USPLM2600001", "streams": 1120, "revenue": "3.92"}]}
STATEMENTS = {"provider": "mock_distributor", "provider_mode": "mock",
              "statements": [STATEMENT, {**STATEMENT, "period": "2026-11"}]}
REWARD = {"experiment_id": "exp-1", "content_id": "yt1", "observation_window": "28d",
          "status": "mature", "arm_id": "arm-1", "normalized_reward": 0.81,
          "reward_version": "engagement-contribution-proxy-v1"}


def _cost(capability: str, amount: str | None, status: str, **context: str) -> dict[str, Any]:
    return {"capability": capability, "idempotency_key": f"k-{capability}",
            "decision": "execute" if status != "simulated" else "simulate", "status": status,
            "actual": None if amount is None else {"amount": amount, "currency": "USD"},
            "basis": None if amount is None else "provider_reported",
            "settled_at": "2026-10-14T00:00:00+00:00" if amount else None, "context": context}


def _evaluate_world() -> World:
    world = World()
    for path in ("/analytics/fx-rates", "/analytics/ingest/releases",
                 "/analytics/ingest/youtube", "/analytics/ingest/distributor",
                 "/analytics/ingest/cost"):
        world.on("POST", path, lambda r: httpx.Response(201, json={"ok": True}))
    world.on("GET", "/youtube/videos/yt1/analytics", SNAPSHOT)
    world.on("GET", "/distribution/releases/rel-1/statements", STATEMENTS)
    world.on("POST", "/analytics/evaluation/measure", REWARD)
    return world


def _evaluate_inputs(costs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"outcome_horizon": "28d", "upload": PUBLICATIONS,
            "submit_distribution": {"releases": [{"media_package_id": "m1",
                                                  "release_id": "rel-1",
                                                  "isrcs": RECORD["isrcs"],
                                                  "distribution_record": RECORD}]},
            "costs": costs}


def test_evaluation_feeds_p9_measured_facts_and_never_an_amount_to_measure() -> None:
    world = _evaluate_world()
    costs = [_cost("music.generate", "2.50", "committed", p3_package_id="p1"),
             _cost("youtube.upload", "0", "simulated", media_package_id="m1"),
             _cost("distribution.submit", "1.00", "committed", media_package_id="m1"),
             _cost("music.generate", "9.99", "committed", p3_package_id="other")]
    result = asyncio.run(world.steps().evaluate_outcome(
        _request(Step.EVALUATE_OUTCOME, _evaluate_inputs(costs))))

    (fx,) = world.sent("POST", "/analytics/fx-rates")
    assert _body(fx)["rates"][0]["quote"] == "KRW"
    (link,) = world.sent("POST", "/analytics/ingest/releases")
    assert _body(link) == {"record": RECORD}
    (analytics,) = world.sent("GET", "/youtube/videos/yt1/analytics")
    assert analytics.url.params["as_of"] == NOW.isoformat()
    assert analytics.url.params["window"] == "28d"
    (snapshot,) = world.sent("POST", "/analytics/ingest/youtube")
    assert _body(snapshot)["source"] == "synthetic"  # a mock source is labelled
    assert _body(snapshot)["observed_at"] == NOW.isoformat()
    assert _body(snapshot)["estimated_revenue"] == "4.032000"
    (statement,) = world.sent("GET", "/distribution/releases/rel-1/statements")
    assert statement.url.params["as_of"] == NOW.isoformat()
    ingested = world.sent("POST", "/analytics/ingest/distributor")
    assert [_body(r)["period"] for r in ingested] == ["2026-10", "2026-11"]
    assert {_body(r)["received_at"] for r in ingested} == {NOW.isoformat()}
    assert _body(ingested[0])["rows"] == STATEMENT["rows"]
    assert _body(ingested[0])["provider"] == "mock_distributor"

    cost_bodies = sorted((_body(r) for r in world.sent("POST", "/analytics/ingest/cost")),
                         key=lambda b: b["category"])
    assert [(b["category"], Decimal(b["amount"]), b["entity_id"], b["period"])
            for b in cost_bodies] == [
        ("distribution", Decimal("1.00"), "yt1", "2026-10"),
        ("third_party_api", Decimal("2.50"), "yt1", "2026-10")]

    (measure,) = world.sent("POST", "/analytics/evaluation/measure")
    body = _body(measure)
    assert body == {"publication": EVENT, "isrcs": RECORD["isrcs"], "window": "28d",
                    "observed_at": NOW.isoformat(), "closed_by_source": False,
                    "quality_status": None}
    (reward,) = result.outputs["rewards"]
    assert (reward["content_id"], reward["status"]) == ("yt1", "mature")
    assert reward["measurement_source"] == "synthetic"


@pytest.mark.parametrize("status", ["reserved", "missing", "unreported"])
def test_evaluation_refuses_a_write_whose_cost_is_unknown(status: str) -> None:
    world = _evaluate_world()
    costs = [_cost("youtube.upload", None, status, media_package_id="m1")]
    with pytest.raises(ValidationFailure, match="cost_unknown"):
        asyncio.run(world.steps().evaluate_outcome(
            _request(Step.EVALUATE_OUTCOME, _evaluate_inputs(costs))))
    assert world.sent("POST", "/analytics/evaluation/measure") == []


def test_evaluation_refuses_a_unit_with_no_reported_write() -> None:
    world = _evaluate_world()
    with pytest.raises(ValidationFailure, match="cost is unknown"):
        asyncio.run(world.steps().evaluate_outcome(
            _request(Step.EVALUATE_OUTCOME, _evaluate_inputs([]))))


# -- dispatch_reward and learn ------------------------------------------------------------------


def _fact(status: str | None) -> dict[str, Any]:
    return {**{k: REWARD[k] for k in ("experiment_id", "content_id", "observation_window",
                                      "status")}, "delivery_status": status}


@pytest.mark.parametrize(("status", "outcome"), [
    ("delivered", None), ("pending", StepNotReady), ("uncertain", StepNotReady),
    ("failed", ValidationFailure), (None, ValidationFailure),
])
def test_dispatch_completes_only_when_p2_acknowledged(status: str | None,
                                                      outcome: type[Exception] | None) -> None:
    world = World()
    world.on("POST", "/analytics/rewards/dispatch", {"report": {"delivered": 1},
                                                     "facts": [_fact(status)]})
    request = _request(Step.DISPATCH_REWARD, {"evaluate_outcome": {"rewards": [REWARD]}})
    if outcome is not None:
        with pytest.raises(outcome):
            asyncio.run(world.steps().dispatch_reward(request))
        return
    result = asyncio.run(world.steps().dispatch_reward(request))
    assert result.outputs["delivery_status"] == "DELIVERED"
    (sent,) = world.sent("POST", "/analytics/rewards/dispatch")
    assert _body(sent)["facts"] == [{k: REWARD[k] for k in (
        "experiment_id", "content_id", "observation_window", "status")}]


def test_learning_updates_the_posterior_then_allocates_again_at_the_same_instant() -> None:
    world = World()
    world.on("POST", "/ar/update", {"applied": 1, "skipped_duplicate": 0, "quarantined": 0,
                                    "failed": 0})
    world.on("POST", "/ar/cycles", {"cycle_id": "cycle-2", "status": "completed"})
    result = asyncio.run(world.steps().learn(_request(
        Step.LEARN, {"dispatch_reward": {"delivery_status": "DELIVERED"}})))
    (update,) = world.sent("POST", "/ar/update")
    assert update.url.params["as_of"] == NOW.isoformat()
    (cycle,) = world.sent("POST", "/ar/cycles")
    assert _body(cycle) == {"brand_id": "brand", "slot_count": 3, "budget": 30.0,
                            "as_of": NOW.isoformat(), "policy_version": "p2-v1",
                            "activation_state": "AUTONOMOUS",
                            "random_seed": stable_seed("pipeline.learn", "pipeline.dir-1")}
    assert [r.url.path for _, r in world.requests] == ["/ar/update", "/ar/cycles"]
    assert result.provider_ids == {"next_cycle_id": "cycle-2"}
    with pytest.raises(ValidationFailure):
        asyncio.run(world.steps().learn(_request(Step.LEARN, {"dispatch_reward": {}})))
    with pytest.raises(PipelineConfigMissing, match="learning"):
        asyncio.run(world.steps(PipelineConfig()).learn(_request(
            Step.LEARN, {"dispatch_reward": {"delivery_status": "DELIVERED"}})))


# -- extend -------------------------------------------------------------------------------------


def test_extend_asks_p3_for_more_specs_under_the_rounds_key() -> None:
    world = World()
    world.on("POST", "/experiments/v1/packages/p1/extend", {
        "package_id": "p1", "extension_id": "ext-1", "idempotency_key": "k",
        "track_specs": [{"track_spec_id": "ts-9"}, {"track_spec_id": "ts-10"}],
        "remaining_extension_track_count": 4})
    key = "pipeline.dir-1:extend.r1.0"
    result = asyncio.run(world.steps().extend(
        _request(Step.EXTEND, {"p3_package_id": "p1", "count": 2}, key=key)))
    (sent,) = world.sent("POST", "/experiments/v1/packages/p1/extend")
    assert _body(sent) == {"count": 2, "idempotency_key": key}
    assert sent.headers["Idempotency-Key"] == key
    assert result.outputs["track_spec_ids"] == ["ts-9", "ts-10"]

    limited = World()
    limited.on("POST", "/experiments/v1/packages/p1/extend", lambda r: httpx.Response(
        409, json={"detail": {"code": "package_extension_limit_exceeded", "field_paths": [],
                              "message": "bound"}}))
    with pytest.raises(PhaseRequestRefused) as raised:
        asyncio.run(limited.steps().extend(
            _request(Step.EXTEND, {"p3_package_id": "p1", "count": 2})))
    assert raised.value.code == "package_extension_limit_exceeded"
    with pytest.raises(ValidationFailure, match="count"):
        asyncio.run(world.steps().extend(_request(Step.EXTEND, {"p3_package_id": "p1",
                                                                "count": 0})))


def test_a_top_up_covers_the_missing_seconds_with_the_shortest_track_plus_spare() -> None:
    assert topup_count(301, 150, 1) == 4
    assert topup_count(300, 150, 0) == 2
    assert topup_count(2700, 160, 1) == 18
    with pytest.raises(ValueError):
        topup_count(0, 150, 1)


# -- configuration ------------------------------------------------------------------------------


def test_configuration_is_optional_but_never_malformed(tmp_path: Any) -> None:
    absent = tmp_path / "none.env"
    assert load_pipeline_config(env={}, dotenv_path=absent) == PipelineConfig()
    dev = load_pipeline_config(
        env={"PLM_PIPELINE_CONFIG_FILE": "config/dev/pipeline.json"}, dotenv_path=absent)
    assert dev.publish is not None and dev.release is not None and dev.learning is not None
    assert dev.fx_rates[0].wire()["quote"] == "KRW"
    for broken, match in (({"publish": {"channel_id": ""}}, "publish.channel_id"),
                          ({"fx_rates": [{"base": "USD", "quote": "KRW", "rate": "x",
                                          "source": "s", "as_of": "2026-01-01T00:00:00Z"}]},
                           "rate"),
                          ({"fx_rates": [{"base": "USD", "quote": "KRW", "rate": "1",
                                          "source": "s", "as_of": "2026-01-01T00:00:00"}]},
                           "timezone"),
                          ({"learning": {"slot_count": 3, "budget": 0, "policy_version": "p",
                                         "activation_state": "A"}}, "budget")):
        with pytest.raises(SettingsError, match=match):
            parse_pipeline_config(broken)
