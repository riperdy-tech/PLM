"""The live dashboard's six PART XVII views (R5) over seeded SQLite and in-process APIs.

What these prove, and what they do not:

* Each view's API reads what the owning phase stored: ``ops`` rows written through
  :class:`PipelineStore` and :class:`ExceptionQueue`; P9 numbers through the **real** P9
  app (``analytics.api.app``) on the same database, seeded through P9's own ingest
  routes; the Market Map through the **real** P1 app (``open_c1_client``). P3's list is a
  controlled mock of its documented response (the P3 app is not driven here).
* Unknown values stay ``null`` and an unreachable source makes its panel
  ``unavailable``; nothing turns into a zero.
* Live mode never serves demo data or demo routes; ``--demo`` labels everything demo.

They do not prove the views against the compose database: that is
``tests/integration/pipeline/test_dashboard_live.py``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from analytics.api.app import create_app as create_p9_app
from analytics.config import AnalyticsSettings
from fastapi.testclient import TestClient
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.dashboard import DEMO_DATA_SOURCE, LIVE_DATA_SOURCE
from plm_platform.dashboard.actions import ExceptionActions, ReviewerConfig
from plm_platform.dashboard.app import (
    DATA_SOURCE_HEADER,
    build_live_components,
    create_app,
    create_live_app,
)
from plm_platform.dashboard.live import VIEW_NAMES, LiveDashboard
from plm_platform.dashboard.sources import PhaseApis, ReadOnlyDatabase
from plm_platform.exceptions import ExceptionQueue, ReviewDecision
from plm_platform.temporal import Phase
from sqlalchemy.orm import Session, sessionmaker

from tests.support.c1_api import PLATFORM_ENV, SEAM_TOKEN, SyncASGITransport, open_c1_client
from tests.support.dashboard_db import (
    create_dashboard_engine,
    raise_hold,
    record_run,
    seed_arms,
    seed_lineage,
)

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
AUTH = {SERVICE_TOKEN_HEADER: SEAM_TOKEN}
UNREACHABLE = {phase: "http://127.0.0.1:9" for phase in Phase}


def _refuse_connection(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


NO_NETWORK = httpx.MockTransport(_refuse_connection)
"""Every phase API this test does not inject is down (no socket is ever opened)."""
DEMO_LITERALS = ("arm_synthwave_night", "Starlight Highway", "case_001", "1845.2")

EXPERIMENTS = [
    {"experiment_id": "exp_live", "content_arm_id": "arm_hi", "production_directive_id": "dir_1",
     "experiment_type": "single_variant", "status": "executing", "treatments": [{}, {}],
     "budget_limit": 12.5, "created_at": "2026-10-14T00:00:00+00:00",
     "measurement_plan": {"outcome_horizon": "7d"}},
    {"experiment_id": "exp_done", "content_arm_id": "arm_lo", "production_directive_id": "dir_2",
     "experiment_type": "single_variant", "status": "completed", "treatments": [{}],
     "budget_limit": 5.0, "created_at": "2026-10-01T00:00:00+00:00",
     "measurement_plan": {"outcome_horizon": "24h"}},
]


def _p3_transport(experiments: list[dict[str, Any]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers[SERVICE_TOKEN_HEADER] == SEAM_TOKEN
        if request.url.path == "/experiments/v1/experiments":
            return httpx.Response(200, json={"experiments": experiments,
                                             "total": len(experiments)})
        return httpx.Response(404, json={"detail": {"code": "not_found", "field_paths": [],
                                                    "message": "no route"}})

    return httpx.MockTransport(handler)


def _p9(engine_factory: sessionmaker[Session]) -> TestClient:
    app = create_p9_app(
        session_factory=engine_factory, settings=load_settings(env=PLATFORM_ENV),
        clock=FixedClock(NOW), analytics_settings=AnalyticsSettings(reporting_currency="USD"))
    return TestClient(app)


def _post(client: TestClient, path: str, body: dict[str, Any]) -> None:
    response = client.post(path, json=body, headers=AUTH)
    assert response.status_code == 201, response.text


def _snapshot(video_id: str, window: str, observed_at: datetime, **metrics: Any) -> dict[str, Any]:
    return {"video_id": video_id, "channel_id": metrics.pop("channel_id", "chan_1"),
            "date_str": observed_at.date().isoformat(), "window": window,
            "observed_at": observed_at.isoformat(), **metrics}


def _seed_p9(p9: TestClient) -> None:
    """Two videos on two channels, one track with a statement and a cost, one blocked."""
    day = NOW - timedelta(days=1)
    _post(p9, "/analytics/ingest/youtube", _snapshot(
        "vid_a", "lifetime", day - timedelta(days=5), views=100, subscribers_gained=50,
        subscribers_lost=40, estimated_revenue="9.99", revenue_currency="USD"))
    _post(p9, "/analytics/ingest/youtube", _snapshot(
        "vid_a", "7d", day, views=999, subscribers_gained=999, subscribers_lost=0,
        estimated_revenue="99.00", revenue_currency="USD"))
    _post(p9, "/analytics/ingest/youtube", _snapshot(
        "vid_a", "lifetime", day, views=1200, subscribers_gained=60, subscribers_lost=5,
        estimated_revenue="0.10", revenue_currency="USD"))
    _post(p9, "/analytics/ingest/youtube", _snapshot(
        "vid_b", "lifetime", day, channel_id="chan_2", views=800, estimated_revenue="0.20",
        revenue_currency="USD"))
    _post(p9, "/analytics/ingest/distributor", {
        "provider": "spotify", "period": "2026-09", "currency": "USD", "source_reference": "s.csv",
        "raw_content": "isrc,track_id,streams,revenue\nUSPLM2600001,trk_a,5000,12.50\n"})
    _post(p9, "/analytics/ingest/cost", {
        "entity_id": "trk_a", "entity_type": "track", "category": "compute", "amount": "2.50",
        "currency": "USD", "period": "2026-09", "incurred_at": day.isoformat()})
    _post(p9, "/analytics/ingest/cost", {
        "entity_id": "trk_fx", "entity_type": "track", "category": "compute", "amount": "3.00",
        "currency": "EUR", "period": "2026-09", "incurred_at": day.isoformat()})


@dataclass
class Live:
    factory: sessionmaker[Session]
    client: TestClient
    dashboard: LiveDashboard


def _live(factory: sessionmaker[Session] | None, *, clients: dict[Phase, Any] | None = None,
          transport: httpx.BaseTransport | None = None, token: str | None = SEAM_TOKEN,
          reviewers: ReviewerConfig | None = None) -> tuple[LiveDashboard, TestClient]:
    dashboard = LiveDashboard(
        ReadOnlyDatabase(factory),
        PhaseApis(UNREACHABLE, token=token, clients=clients,
                  transport=transport if transport is not None else NO_NETWORK),
        reviewers=reviewers, clock=FixedClock(NOW))
    actions = ExceptionActions(factory, reviewers=reviewers, signaller=None, clock=FixedClock(NOW))
    platform = load_settings(env=PLATFORM_ENV)
    return dashboard, TestClient(create_live_app(dashboard, actions, platform=platform))


@pytest.fixture
def live() -> Iterator[Live]:
    engine = create_dashboard_engine()
    factory = sessionmaker(bind=engine)
    seed_arms(factory, NOW, [("arm_hi", "active", 0.81, 6), ("arm_mid", "active", 0.55, 3),
                             ("arm_lo", "active", 0.20, 2), ("arm_prior", "candidate", 0.5, 0),
                             ("arm_old", "retired", 0.9, 9), ("arm_new", "candidate", None, 0)])
    seed_lineage(factory, NOW, videos={"vid_a": "arm_hi", "vid_b": "arm_lo"},
                 tracks={"trk_a": "arm_hi"}, genres={"arm_hi": "lofi", "arm_lo": "ambient"})
    p9 = _p9(factory)
    _seed_p9(p9)
    with open_c1_client() as p1:
        market = httpx.Client(transport=SyncASGITransport(p1.app), base_url="http://p1")
        dashboard, client = _live(factory, clients={Phase.ANALYTICS: p9, Phase.MARKET: market},
                                  transport=_p3_transport(EXPERIMENTS))
        yield Live(factory=factory, client=client, dashboard=dashboard)


def _view(client: TestClient, name: str) -> dict[str, Any]:
    response = client.get(f"/api/views/{name}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_source"] == LIVE_DATA_SOURCE
    assert response.headers[DATA_SOURCE_HEADER] == LIVE_DATA_SOURCE
    return body


def _ok(view: dict[str, Any], panel: str) -> Any:
    entry = view["panels"][panel]
    assert entry["status"] == "ok", entry
    return entry["data"]


# ------------------------------------------------------------------- 1. command center


def test_command_center_keeps_realized_and_estimated_revenue_apart_with_currency(
        live: Live) -> None:
    revenue = _ok(_view(live.client, "command-center"), "revenue")
    # trk_fx is blocked by P9 (no FX rate): its revenue is unknown, not zero.
    assert revenue["realized"] == {
        "value": [{"amount": "12.500000", "currency": "USD"}],
        "entities_known": 1, "entities_unknown": 1}
    # Latest lifetime snapshot per video (0.10, not 9.99 or the 7d 99.00), summed across
    # two distinct videos exactly: 0.10 + 0.20.
    assert revenue["estimated"]["value"] == [{"amount": "0.300000", "currency": "USD"}]
    assert revenue["estimated"]["entities_known"] == 2
    assert [b["code"] for b in revenue["blocked_entities"]] == ["fx_rate_missing"]


def test_command_center_audience_uses_one_lifetime_snapshot_per_video(live: Live) -> None:
    audience = _ok(_view(live.client, "command-center"), "audience")
    assert audience["views"] == {"value": 2000, "entities_known": 2, "entities_unknown": 0}
    assert audience["streams"] == {"value": 5000, "entities_known": 1, "entities_unknown": 0}
    # vid_a: latest lifetime 60 - 5 = 55 (not the 7d 999); vid_b never measured: unknown.
    assert audience["subscribers_net"] == {"value": 55, "entities_known": 1,
                                           "entities_unknown": 1}


def test_command_center_catalog_and_arms_by_posterior(live: Live) -> None:
    view = _view(live.client, "command-center")
    assert _ok(view, "catalog") == {"published_videos": 2, "distributed_tracks": 1,
                                    "releases_by_status": {"submitted": 1}}
    arms = _ok(view, "arms")
    assert [a["arm_id"] for a in arms["best"]] == ["arm_old", "arm_hi", "arm_mid"]
    assert [a["arm_id"] for a in arms["worst"]] == ["arm_lo", "arm_mid", "arm_hi"]
    assert arms["arms_prior_only"] == 1 and arms["arms_without_posterior"] == 1
    assert "not expected economic value" in arms["reward_label"]


# ----------------------------------------------------------------------- 2. market map


def test_market_map_reads_p1_and_keeps_unknown_components_unknown(live: Live) -> None:
    data = _ok(_view(live.client, "market-map"), "opportunities")
    with open_c1_client() as p1:
        expected = p1.get("/market/v1/opportunities", params={"limit": 200}).json()["items"]
    assert [i["arm_id"] for i in data["items"]] == [e["arm_id"] for e in expected]
    for item, wire in zip(data["items"], expected, strict=True):
        for name in ("demand", "competition", "growth"):
            assert item[name] == wire["components"][name]
        assert item["unknown_components"] == [
            n for n in ("demand", "competition", "growth") if wire["components"][n] is None]


def test_market_map_shows_a_null_component_as_unknown_not_zero() -> None:
    page = {"schema_version": "1.0", "next_cursor": None, "as_of": NOW.isoformat(),
            "score_version": "v1", "items": [{
                "arm_id": "arm_x", "market_opportunity_score": 61.0, "confidence": 0.7,
                "as_of": NOW.isoformat(),
                "taxonomy": {"genre": "lofi", "subgenre": None, "mood": [], "use_case": [],
                             "language": None, "vocal_mode": None, "duration_bucket": None,
                             "visual_world": None, "title_archetype": None},
                "components": {"demand": 0.8, "growth": None, "competitor_velocity": None,
                               "competition": None, "audience_quality": 0.4,
                               "cross_platform": None, "production_fit": 0.9},
                "evidence": {"observation_count": 4, "source_count": 1,
                             "top_observation_ids": []},
                "score_version": "v1"}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=page))
    _, client = _live(None, transport=transport)
    (item,) = _ok(_view(client, "market-map"), "opportunities")["items"]
    assert item["demand"] == 0.8
    assert item["growth"] is None and item["competition"] is None
    assert item["unknown_components"] == ["competition", "growth"]


# ------------------------------------------------------------------ 3. production queue


def test_production_queue_shows_runs_by_state_with_hold_reasons(live: Live) -> None:
    held = raise_hold(live.factory, "UNKNOWN_COMMERCIAL_LICENSE", NOW,
                      subject_ref="pipeline.dir_held:run-1:qa:1",
                      raised_by="pipeline:pipeline.dir_held")
    record_run(live.factory, "pipeline.dir_held", NOW, hold_case_ids=[held.id])
    record_run(live.factory, "pipeline.dir_failed", NOW, path=("IDEA",),
               fail="provider_timeout")
    data = _ok(_view(live.client, "production-queue"), "runs")
    assert data["by_state"] == [{"state": "HELD", "count": 1}, {"state": "FAILED", "count": 1}]
    (run,) = data["active"]
    assert run["state"] == "HELD" and run["held_from"] == "QA" and run["blocked"]
    assert run["last_error_code"] == "qa_quarantined"
    assert run["holds"] == [{
        "case_id": held.id, "reason_code": "UNKNOWN_COMMERCIAL_LICENSE",
        "hold_class": "REVIEWABLE_QUARANTINE", "status": "OPEN",
        "reviewer_role": "rights_reviewer", "required_evidence": list(held.required_evidence)}]
    assert run["awaiting_signal"] is False
    (failed,) = data["recent_finished"]
    assert failed["state"] == "FAILED" and failed["last_error_code"] == "provider_timeout"

    with live.factory() as session:  # the hold closes but nothing signalled the run yet
        ExceptionQueue().resolve(session, held.id, decision=ReviewDecision.WITHDRAW,
                                 actor="ops", role=None, evidence={}, notes="", now=NOW)
        session.commit()
    (run,) = _ok(_view(live.client, "production-queue"), "runs")["active"]
    assert run["awaiting_signal"] is True


def test_production_queue_with_no_runs_is_empty_not_unavailable(live: Live) -> None:
    data = _ok(_view(live.client, "production-queue"), "runs")
    assert data == {"by_state": [], "unknown_states": [], "active": [],
                    "active_truncated": False, "recent_finished": [], "total": 0}


# ------------------------------------------------------------------ 4. experiment board


def test_experiment_board_lists_live_arms_and_active_experiments(live: Live) -> None:
    view = _view(live.client, "experiment-board")
    arms = _ok(view, "arms")
    assert [a["arm_id"] for a in arms["arms"]] == [
        "arm_hi", "arm_lo", "arm_mid", "arm_new", "arm_prior"]
    assert arms["arms_excluded"] == 1  # arm_old is retired
    hi = arms["arms"][0]["posterior"]
    assert hi["mean"] == pytest.approx(0.81) and hi["variance"] == pytest.approx(0.016)
    assert hi["observation_count"] == 6
    assert arms["arms"][3]["posterior"] is None  # no posterior is unknown, not zero
    experiments = _ok(view, "experiments")
    assert [e["experiment_id"] for e in experiments["active"]] == ["exp_live"]
    assert experiments["by_status"] == {"completed": 1, "executing": 1}
    assert experiments["active"][0]["outcome_horizon"] == "7d"


# ------------------------------------------------------------------------- 5. economics


def test_economics_groups_p9_unit_economics_by_track_channel_and_genre(live: Live) -> None:
    data = _ok(_view(live.client, "economics"), "economics")
    assert data["reporting_currencies"] == ["USD"]
    (track,) = data["by_track"]
    assert track["entity_id"] == "trk_a" and track["genre"] == "lofi"
    assert track["net_contribution"] == {"amount": "10.000000", "currency": "USD"}
    assert track["roi"] == "4.000000"  # P9's own net / cost
    channels = {g["channel_id"]: g for g in data["by_channel"]}
    assert set(channels) == {"chan_1", "chan_2"}
    assert channels["chan_1"]["views"]["value"] == 1200
    # A video has no realized revenue: its net is blocked, never estimated revenue.
    assert channels["chan_1"]["net_contribution"] == {"value": None, "entities_blocked": 1}
    genres = {g["genre"]: g for g in data["by_genre"]}
    assert set(genres) == {"lofi", "ambient"}
    assert genres["lofi"]["entities"] == 2
    assert genres["lofi"]["realized_revenue"]["value"] == [
        {"amount": "12.500000", "currency": "USD"}]
    assert genres["lofi"]["estimated_revenue"]["value"] == [
        {"amount": "0.100000", "currency": "USD"}]
    assert genres["lofi"]["roi"] is None  # one member's net is blocked: no group ROI
    (blocked,) = data["blocked"]
    assert blocked["entity_id"] == "trk_fx" and blocked["code"] == "fx_rate_missing"


def test_a_group_roi_needs_every_net_and_cost_known_in_one_currency() -> None:
    def track(entity_id: str, net: str, cost: str, currency: str = "USD") -> dict[str, Any]:
        return {"entity_id": entity_id, "entity_type": "track", "genre": "lofi",
                "realized_revenue": {"amount": str(float(net) + float(cost)),
                                     "currency": currency},
                "estimated_revenue": None,
                "allocated_cost": {"amount": cost, "currency": currency},
                "net_contribution": {"amount": net, "currency": currency},
                "views": None, "streams": 10}

    (group,) = LiveDashboard._group([track("a", "10.00", "2.50"), track("b", "5.00", "2.50")],
                                    "genre")
    assert group["net_contribution"] == {
        "value": [{"amount": "15.00", "currency": "USD"}], "entities_blocked": 0}
    assert group["roi"] == "3.0000"
    assert group["streams"] == {"value": 20, "entities_known": 2, "entities_unknown": 0}
    (mixed,) = LiveDashboard._group([track("a", "10.00", "2.50"),
                                     track("b", "5.00", "2.50", currency="EUR")], "genre")
    assert mixed["net_contribution"]["value"] == [
        {"amount": "5.00", "currency": "EUR"}, {"amount": "10.00", "currency": "USD"}]
    assert mixed["roi"] is None  # currencies are never added together


# ------------------------------------------------------------------------ 6. exceptions


def test_exceptions_view_lists_only_open_holds_with_their_hold_class_rules(live: Live) -> None:
    hard = raise_hold(live.factory, "COPYRIGHT_DISPUTE", NOW)
    review = raise_hold(live.factory, "UNKNOWN_COMMERCIAL_LICENSE", NOW)
    closed = raise_hold(live.factory, "SEVERE_CLIPPING_DETECTED", NOW)
    with live.factory() as session:
        ExceptionQueue().resolve(session, closed.id, decision=ReviewDecision.REJECT,
                                 actor="ops", role=None, evidence={}, notes="", now=NOW)
        session.commit()
    data = _ok(_view(live.client, "exceptions"), "open")
    items = {i["case_id"]: i for i in data["items"]}
    assert set(items) == {hard.id, review.id}
    assert items[hard.id]["hold_class"] == "HARD_STOP"
    assert items[hard.id]["allowed_actions"] == ["record_external_resolution", "reject"]
    assert items[hard.id]["approve_available"] is False
    assert items[hard.id]["required_evidence"] == ["dispute_id", "dispute_outcome_ref"]
    assert items[hard.id]["reviewer_role"] == "rights_counsel"
    assert items[review.id]["allowed_actions"] == ["resume", "reject"]
    assert items[review.id]["reviewer_role"] == "rights_reviewer"
    assert items[review.id]["description"]


# ----------------------------------------------------------- unknown and unavailable


def test_every_panel_is_unavailable_without_its_sources_never_zero() -> None:
    engine = create_dashboard_engine(schemas=())
    _, client = _live(sessionmaker(bind=engine))
    for name in VIEW_NAMES:
        view = _view(client, name)
        for panel_name, panel in view["panels"].items():
            if panel_name == "reviewers":
                continue
            assert panel["status"] == "unavailable", (name, panel_name, panel)
            assert panel["reason"]
            assert "data" not in panel


def test_unconfigured_database_and_token_are_named_as_the_reason() -> None:
    _, client = _live(None, token=None)
    queue = _view(client, "production-queue")["panels"]["runs"]
    assert queue == {"status": "unavailable", "source": "db:ops",
                     "reason": "database not configured (DATABASE_URL)"}
    market = _view(client, "market-map")["panels"]["opportunities"]
    assert market["status"] == "unavailable" and "PLM_SERVICE_TOKEN" in market["reason"]


def test_an_unreachable_phase_api_is_unavailable(live: Live) -> None:
    _, client = _live(live.factory)  # no injected clients: every phase API is down
    view = _view(client, "experiment-board")
    assert view["panels"]["arms"]["status"] == "ok"
    experiments = view["panels"]["experiments"]
    assert experiments["status"] == "unavailable" and "ConnectError" in experiments["reason"]


def test_a_phase_refusal_is_unavailable_with_its_code() -> None:
    refusal = {"detail": {"code": "invalid_service_token", "field_paths": [],
                          "message": "missing or invalid service token"}}
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json=refusal))
    _, client = _live(None, transport=transport)
    panel = _view(client, "market-map")["panels"]["opportunities"]
    assert panel["status"] == "unavailable" and "invalid_service_token" in panel["reason"]


# ------------------------------------------------------------------ live is never demo


def test_live_mode_never_serves_demo_data_or_demo_routes(live: Live) -> None:
    raise_hold(live.factory, "COPYRIGHT_DISPUTE", NOW)
    bodies = []
    for name in VIEW_NAMES:
        response = live.client.get(f"/api/views/{name}")
        assert response.headers[DATA_SOURCE_HEADER] == LIVE_DATA_SOURCE
        assert response.json()["data_source"] == LIVE_DATA_SOURCE
        bodies.append(response.text)
    for path in ("/api/stats", "/api/arms", "/api/candidates", "/api/releases",
                 "/api/audio/cand_synth_01", "/static/demo/index.html"):
        response = live.client.get(path)
        assert response.status_code == 404, path
        assert response.headers[DATA_SOURCE_HEADER] == LIVE_DATA_SOURCE
    assert live.client.post("/api/qa/resolve", json={"case_id": "case_001"},
                            headers=AUTH).status_code == 404
    page = live.client.get("/")
    assert "DEMO" not in page.text
    for literal in DEMO_LITERALS:
        assert all(literal not in body for body in bodies), literal


def test_an_unknown_view_is_404() -> None:
    _, client = _live(None)
    response = client.get("/api/views/stats")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "unknown_view"


def test_create_app_is_live_unless_demo_is_asked_for() -> None:
    platform = load_settings(env={**PLATFORM_ENV, "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm"})
    live_client = TestClient(create_app(platform=platform))
    page = live_client.get("/")
    assert page.headers[DATA_SOURCE_HEADER] == LIVE_DATA_SOURCE and "DEMO" not in page.text
    assert live_client.get("/api/stats").status_code == 404

    demo_client = TestClient(create_app(demo=True, platform=platform))
    stats = demo_client.get("/api/stats")
    assert stats.json()["data_source"] == DEMO_DATA_SOURCE
    assert stats.headers[DATA_SOURCE_HEADER] == DEMO_DATA_SOURCE
    assert "DEMO DATA" in demo_client.get("/").text
    assert demo_client.get("/api/views/exceptions").status_code == 404


def test_build_live_components_without_settings_is_unavailable_and_closed(tmp_path: Any) -> None:
    reviewers = tmp_path / "reviewers.json"
    reviewers.write_text(json.dumps({"rita": ["rights_reviewer"]}), encoding="utf-8")
    dashboard, actions = build_live_components(None, reviewers_file=reviewers, service_urls_env={})
    assert dashboard.production_queue()["panels"]["runs"]["status"] == "unavailable"
    assert actions.reviewers is not None
    assert actions.reviewers.roles_of("rita") == frozenset({"rights_reviewer"})


def test_a_malformed_reviewer_file_is_refused_at_startup(tmp_path: Any) -> None:
    bad = tmp_path / "reviewers.json"
    bad.write_text(json.dumps({"rita": "rights_reviewer"}), encoding="utf-8")
    with pytest.raises(ValueError, match="expected an object mapping actor"):
        build_live_components(None, reviewers_file=bad, service_urls_env={})


def test_cli_serves_live_by_default_and_demo_only_behind_the_flag() -> None:
    from click import unstyle
    from plm_platform.cli import app
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["dashboard", "serve", "--help"])
    assert result.exit_code == 0
    # Typer forces Rich styling under GITHUB_ACTIONS; compare plain text.
    output = unstyle(result.output)
    assert "--demo" in output and "--reviewers-file" in output
