# tests/integration/market/test_e2e_acceptance.py
"""P1's acceptance run: mock-backed, full pipeline, real Postgres and MinIO.

Covers AC-01…AC-14 in one path: collect → raw → normalize → entities → classify → arms →
features → score → API → report, plus AC-03 (a second collection adds history), the run's
§42 cost counters (observed in the result, in the database and on the log line) and
AC-12 (a rate-limit response leaves the run partial and resumable).

The mock script mirrors the CI connector's shape: one search + one hydration per
bootstrap seed (20 pairs). The tests make the queries due before each cycle so the
acceptance does not depend on scheduler state left by an earlier run of this module.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from plm_platform.config import Secret, load_settings
from plm_platform.db import build_engine
from plm_platform.storage import build_client, ensure_bucket, from_uri, get_verified
from sqlalchemy.orm import Session, sessionmaker

from services.market_intelligence.api.app import create_app
from services.market_intelligence.cli import run_daily
from services.market_intelligence.collection.rate_limit import QuotaBudget, RequestBudget
from services.market_intelligence.collection.seeds import load_bootstrap_seeds
from services.market_intelligence.config import MarketSettings, load_market_settings
from services.market_intelligence.connectors.youtube import (
    BASE_URL,
    YouTubeCredentials,
    YouTubeDataAPIConnector,
)
from services.market_intelligence.reports.exports import render_markdown, store_report
from services.market_intelligence.reports.market_snapshot import build_snapshot
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.models import queries
from tests.support.c1_api import SyncASGIClient
from tests.support.http import MockHTTP

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "market" / "youtube"
# One database is shared across the suite's integration modules, and both this
# acceptance's corpus and §35's scenario (`tests/fixtures/market/scenario.py`) resolve
# the same arm slug (`lofi_study_focus`). The acceptance therefore owns instants
# strictly *before* the scenario's fixed instant (2026-09-20 06:00): the scenario's
# read-back asserts against the row it writes at its instant, and a score this module
# wrote at a later instant would be the newest row for that shared arm and displace it.
NOW = datetime(2026, 9, 10, 6, 0, tzinfo=UTC)
ACCEPTANCE_TOKEN = "acceptance-token"
# A test that replays the same fixed instants accumulates quota charges on the same
# run rows; the ceiling is exercised by its own unit tests, so the acceptance run
# lifts it rather than failing on its own history.
ACCEPTANCE_BUDGETS = {"quota_units_per_run": 100_000, "quota_units_per_day": 1_000_000}


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def stack() -> tuple[sa.Engine, object]:
    try:
        engine = build_engine()
        with engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
        client = build_client()
        ensure_bucket(client=client)
    except Exception as exc:  # noqa: BLE001 - skip with a remedy (repo convention)
        pytest.skip(f"postgres/minio unreachable ({type(exc).__name__}); "
                    "run: docker compose up -d --wait")
    yield engine, client
    engine.dispose()


def _mock_connector(*, rate_limits: int = 0) -> YouTubeDataAPIConnector:
    """The CI-mock shape: one search + one hydration (videos, channels) per bootstrap seed.

    `search_multi_p1.json` extends the repo's `search_lofi_p1.json` with four more
    videos so two taxonomy profiles reach §16's arm support floor (2) — with the
    original two-video fixture no arm can form at all (one video per profile).
    """
    mock = MockHTTP(repeat_last=True)
    for _ in range(rate_limits):
        mock.enqueue_rate_limit(retry_after="60")
    for _ in load_bootstrap_seeds():  # search + videos.list + channels.list per query
        mock.enqueue_json(_fixture("search_multi_p1.json"))
        mock.enqueue_json(_fixture("videos_multi_batch.json"))
        mock.enqueue_json(_fixture("channels_lofi_batch.json"))
    return YouTubeDataAPIConnector(
        client=mock.client(base_url=BASE_URL),
        credentials=YouTubeCredentials(api_key=Secret("acceptance")),
        quota=QuotaBudget(100_000), request_budget=RequestBudget(1_000),
        sleep=lambda _seconds: None, observed_at=lambda: NOW,
    )


def _make_queries_due(engine: sa.Engine, *, now: datetime,
                      settings: MarketSettings) -> int:
    """Clear every query's `next_run_at` — the state a fresh seed install leaves.

    `scheduler.reset_schedule` deliberately *defers* a query to its next cadence; a
    due query is `next_run_at IS NULL` (`query_planner.plan_refresh`), which is what
    this test needs for a deterministic replay.
    """
    _ = now, settings
    with Session(engine) as session:
        session.execute(sa.update(queries).values(next_run_at=None))
        session.commit()
        return len(repositories.load_queries(session))


def test_full_mock_cycle_produces_ranked_evidence_backed_arms(
    stack: tuple[sa.Engine, object], caplog: pytest.LogCaptureFixture
) -> None:
    engine, storage_client = stack
    factory = sessionmaker(bind=engine)
    settings = load_market_settings(**ACCEPTANCE_BUDGETS)
    # On the first run the seeds do not exist yet (run_daily installs them, due);
    # a replay resets whatever schedule an earlier run left behind.
    _make_queries_due(engine, now=NOW, settings=settings)

    with caplog.at_level(logging.INFO, logger="plm.market.run"):
        first = run_daily(now=NOW, session_factory=factory, connector=_mock_connector(),
                          settings=settings)
    assert first["status"] in {"succeeded", "partial"}

    # §42: the run emits its six cost counters, with units, as one structured line.
    costs = first["costs"]
    assert costs is not None, "a collection run must emit its cost counters"
    for counter in ("api_calls", "api_quota_units", "llm_calls", "llm_tokens",
                    "llm_cost_estimate", "storage_bytes"):
        assert counter in costs, counter
    assert costs["api_calls"] > 0 and costs["api_quota_units"] > 0
    # Storage bytes are charged only for payloads this run actually wrote; a replay of
    # identical content is a no-op, so the honest assertion is "a real count, never negative".
    assert costs["storage_bytes"] >= 0
    assert costs["api_quota_units_unit"] == "youtube_api_quota_units"
    assert costs["llm_cost_estimate_currency"] == "USD"
    assert costs["llm_calls"] == 0  # the rule-based classifier made no LLM calls: a real 0
    run_lines = [record for record in caplog.records
                 if record.getMessage() == "market run summary"]
    assert run_lines, "the run must log its structured summary line"
    assert run_lines[-1].api_quota_units == costs["api_quota_units"]

    with factory() as session:
        raw_rows = session.execute(sa.text(
            "SELECT payload_uri, payload_hash, request_context_hash "
            "FROM market.market_raw_observations"
        )).mappings().all()
        observations = session.execute(sa.text(
            "SELECT count(*) FROM market.market_observations")).scalar_one()
        arms = session.execute(sa.text("SELECT count(*) FROM market.market_arms")).scalar_one()
        scores = session.execute(sa.text(
            "SELECT count(*) FROM market.market_opportunity_scores")).scalar_one()
        run = repositories.load_run(session, str(first["costs"]["run_id"]))
    assert raw_rows and observations and arms and scores  # AC-01/AC-02/AC-07
    assert run is not None and run["status"] in {"succeeded", "partial"}
    assert run["cost_api_quota_units"] > 0  # the run's ledger carries the cost
    assert run["cost_api_calls"] > 0  # 41 HTTP attempts must not be recorded as zero calls
    assert run["cost_llm_calls"] == 0
    for row in raw_rows:
        assert str(row["payload_uri"]).startswith("plm://")  # R13/C9
        assert get_verified(from_uri(str(row["payload_uri"])), client=storage_client)
        assert row["request_context_hash"]  # provenance travels with the payload

    # AC-03: a second collection at a later instant adds history, never overwrites
    later = NOW + timedelta(days=3)
    _make_queries_due(engine, now=later, settings=settings)
    with factory() as session:
        before = session.execute(sa.text(
            "SELECT count(*) FROM market.market_observations")).scalar_one()
    run_daily(now=later, session_factory=factory, connector=_mock_connector(), settings=settings)
    with factory() as session:
        after = session.execute(sa.text(
            "SELECT count(*) FROM market.market_observations")).scalar_one()
    assert after > before

    # AC-09/AC-10: the API answers with ranked items and the frozen envelope
    base = {"DATABASE_URL": "postgresql://plm:x@localhost:5432/plm", "POSTGRES_PASSWORD": "x",
            "MINIO_ENDPOINT": "localhost:9000", "MINIO_ROOT_USER": "plm",
            "MINIO_ROOT_PASSWORD": "x", "MINIO_BUCKET": "plm-media",
            "TEMPORAL_HOST": "localhost:7233", "PLM_SERVICE_TOKEN": ACCEPTANCE_TOKEN}
    app = create_app(session_factory=factory,
                     settings=load_market_settings(platform=load_settings(env=base)))
    client = SyncASGIClient(app, base_url="http://testserver",
                            headers={"X-PLM-Service-Token": ACCEPTANCE_TOKEN})
    body = client.get("/market/v1/opportunities").json()
    assert set(body) == {"schema_version", "items", "next_cursor", "as_of", "score_version"}
    scores_seen = [item["market_opportunity_score"] for item in body["items"]]
    assert scores_seen and scores_seen == sorted(scores_seen, reverse=True)

    # §31: the report is built from stored data and stored content-addressed
    with factory() as session:
        snapshot = build_snapshot(session, as_of=later, settings=settings)
    assert snapshot.sections["top_opportunity_arms"], "the report must show the run's scores"
    assert snapshot.sections["market_snapshot"][0]["observations"] > 0
    assert "OBSERVED" in render_markdown(snapshot)
    stored = store_report(snapshot, client=storage_client)
    assert get_verified(stored.key, client=storage_client)


def test_a_rate_limited_run_is_partial_and_resumable(stack: tuple[sa.Engine, object]) -> None:
    """AC-12/§23/§27: the run records the failure class and reschedules, never blinds on."""
    engine, _ = stack
    factory = sessionmaker(bind=engine)
    settings = load_market_settings(**ACCEPTANCE_BUDGETS)
    now = NOW + timedelta(hours=1)
    _make_queries_due(engine, now=now, settings=settings)
    # The first query's three attempts all hit 429; the rest of the day's queries
    # proceed, so the run ends partial rather than failed.
    result = run_daily(now=now, session_factory=factory,
                       connector=_mock_connector(rate_limits=3), settings=settings)
    assert result["status"] == "partial"
    run_id = result["costs"]["run_id"] if result["costs"] else None
    assert run_id is not None
    with factory() as session:
        run = repositories.load_run(session, str(run_id))
        assert run is not None
        assert run["status"] == "partial"
        assert run["error_count"] == 1  # one query exhausted its retries
        scheduled = session.execute(sa.text(
            "SELECT count(*) FROM market.market_queries WHERE next_run_at IS NOT NULL "
            "AND active"
        )).scalar_one()
    assert scheduled == len(load_bootstrap_seeds())  # every query has a next run, none lost
