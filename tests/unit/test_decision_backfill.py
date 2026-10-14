"""P9 attaches realized outcomes to P2's logged allocation decisions (PART XXI).

In-process: P9's real FastAPI app on SQLite with the platform decision log attached.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from analytics.api.app import create_app
from analytics.config import AnalyticsSettings
from fastapi.testclient import TestClient
from plm_platform.clock import FixedClock
from plm_platform.config import load_settings
from plm_platform.decision_log import allocation_decision_key, load_decision
from sqlalchemy.orm import sessionmaker

from tests.support.analytics_db import create_analytics_engine
from tests.support.decisions import log_allocation_decision
from tests.unit.test_analytics_api import HEADERS, MINIMAL_ENV, _feedback

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
DIRECTIVE = "dir_test_01"


def _app(engine: sa.Engine) -> TestClient:
    return TestClient(create_app(
        session_factory=sessionmaker(bind=engine), settings=load_settings(env=MINIMAL_ENV),
        clock=FixedClock(NOW), analytics_settings=AnalyticsSettings(reporting_currency="USD"),
    ))


def _decision(engine: sa.Engine) -> Any:
    with sessionmaker(bind=engine)() as session:
        return load_decision(session, allocation_decision_key(DIRECTIVE))


def test_a_mature_outcome_becomes_the_decisions_result_for_its_window() -> None:
    engine = create_analytics_engine(static_pool=True)
    log_allocation_decision(engine, directive_id=DIRECTIVE, arm_id="arm_api_01",
                            decided_at=NOW)
    response = _app(engine).post("/analytics/evaluation/feedback", json=_feedback(),
                                 headers=HEADERS)
    assert response.status_code == 200, response.text
    decision = _decision(engine)
    assert decision is not None and set(decision.result) == {"7d"}
    result = decision.result["7d"]
    assert result["maturity"] == "mature"
    assert result["net_contribution"] == {"amount": "25.0", "currency": "USD"}
    assert result["reward_version"] == "engagement-contribution-proxy-v1"


def test_an_outcome_without_a_logged_decision_is_refused() -> None:
    engine = create_analytics_engine(static_pool=True)
    response = _app(engine).post("/analytics/evaluation/feedback", json=_feedback(),
                                 headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "decision_not_logged"


def test_a_provisional_outcome_is_not_a_realized_result() -> None:
    engine = create_analytics_engine(static_pool=True)
    log_allocation_decision(engine, directive_id=DIRECTIVE, arm_id="arm_api_01",
                            decided_at=NOW)
    provisional = _feedback(window="28d")  # published 2026-09-20: 28d has not elapsed
    response = _app(engine).post("/analytics/evaluation/feedback", json=provisional,
                                 headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "provisional"
    assert _decision(engine).result is None
