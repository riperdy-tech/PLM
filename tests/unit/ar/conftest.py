"""Shared AR unit fixtures: the authenticated app client and the C1 injection seam.

`market_source` is the seam `create_app` takes for P1: production passes `None` and the
route builds a real `P1Client`; tests inject this callable, which returns a
`MarketSnapshot` built from `CycleRequest.as_of` — no HTTP, no clock.

Deviation from the plan's draft fixture (reported to the controller): the draft gave all
four arms the same `use_case` and `language`, which the §15 use-case and language caps
hold to a single selection (0.85 × 2 slots ⇒ floor 1), contradicting its own expectation
of two selections and a `completed` cycle. The arms below differ in genre, use case and
language, so a two-slot cycle can fill both slots exactly as the test expects.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from plm_platform.config import load_settings
from sqlalchemy.orm import Session, sessionmaker

from services.autonomous_ar.api.app import create_app
from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.decisioning.cycle import CycleRequest, MarketSnapshot
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.storage import repositories
from tests.support.ar_db import create_ar_engine

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
AR_TOKEN = "ar-seam-token"
ENV_WITH_TOKEN = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": AR_TOKEN,
}

_COMPONENTS = {name: 60.0 for name in
               ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
                "cross_platform", "production_fit")}


def _market_source(request: CycleRequest) -> MarketSnapshot:
    """Four in-identity P1 arms (ADR-0044), one per genre, each with its own language.

    Each also carries its own use case first, so the portfolio caps see four use cases."""
    arms = tuple(
        ContentArm(
            arm_id=f"{genre}-{index}",
            dimensions=ArmDimensions(genre=genre,
                                     use_cases=(f"use-case-{index}", "study_focus"),
                                     language=f"language-{index}", vocal_mode="instrumental"),
            p1_score=80.0 - index * 5.0, p1_confidence=0.7,
            component_scores=dict(_COMPONENTS), observation_count=2, source_count=1,
            as_of=request.as_of, score_version="p1-v1", freshness_seconds=0.0,
        )
        for index, genre in enumerate(("focus_electronic", "ambient", "synthwave", "deep_house"))
    )
    return MarketSnapshot(arms=arms, as_of=request.as_of, source="live", score_version="p1-v1")


@pytest.fixture
def ar_engine() -> sa.Engine:
    """One in-memory `ar` schema per test; shared across threads for the TestClient."""
    return create_ar_engine(static_pool=True)


@pytest.fixture
def ar_factory(ar_engine: sa.Engine) -> sessionmaker[Session]:
    """Sessions bound to `ar_engine`, with the default policy inserted and activated."""
    factory: sessionmaker[Session] = sessionmaker(bind=ar_engine)
    with factory() as session:
        repositories.insert_policy(session, DEFAULT_POLICY, brand_id="neon_seoul",
                                   created_at=NOW)
        repositories.activate_policy(session, DEFAULT_POLICY.version)
        session.commit()
    return factory


@pytest.fixture
def market_source() -> Callable[[CycleRequest], MarketSnapshot]:
    return _market_source


@pytest.fixture
def client(ar_factory: sessionmaker[Session],
           market_source: Callable[[CycleRequest], MarketSnapshot]) -> Iterator[TestClient]:
    """The real app over the SQLite fixture, authenticated with the seam token."""
    settings = load_ar_settings(platform=load_settings(env=ENV_WITH_TOKEN))
    app = create_app(session_factory=ar_factory, settings=settings, market_source=market_source)
    with TestClient(app, headers={"X-PLM-Service-Token": AR_TOKEN}) as test_client:
        yield test_client
