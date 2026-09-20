# tests/support/c1_api.py
"""A fixture-backed P1 API client for the C1 seam and the API unit tests.

No docker, no credentials: SQLite with the `market` schema attached, seeded with one
scored arm, wrapped in the real FastAPI app behind the service token. The seam test
(`tests/contract/test_c1_market_to_ar.py`) and the unit endpoint tests share this one
factory — conftest fixtures do not cross test trees, so the fixture wrappers are thin.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Self

import httpx
import sqlalchemy as sa
from plm_contracts import Tri
from plm_platform.config import load_settings
from sqlalchemy.orm import Session, sessionmaker

from services.market_intelligence.api.app import create_app
from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.domain.value_objects import TaxonomyProfile
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.scoring.opportunity import persist_scores, score_all_arms
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.models import MARKET_METADATA
from services.market_intelligence.taxonomy.arms import ArmDraft

__all__ = ["ARM_ID", "AS_OF", "SEAM_TOKEN", "SyncASGIClient", "open_c1_client",
           "seed_market"]

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SEAM_TOKEN = "seam-test-token"
ARM_ID = "rnb_late_night_drive_english_vocal_45_75m"

PLATFORM_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": SEAM_TOKEN,
}


def seed_market(session: Session) -> None:
    """One scored arm over six observations, all defaults from `MarketSettings`."""
    profile = TaxonomyProfile(genre="rnb", use_case="late_night_drive", language="english",
                              vocal_mode="vocal", duration_bucket="45_75m")
    repositories.upsert_arm(
        session,
        ArmDraft(arm_id=ARM_ID, fingerprint="seam-fixture", profile=profile,
                 source_observation_ids=("raw-1",), support_count=2),
        brand_id="neon_seoul",
    )
    observations = [
        NormalizedObservation(
            entity_id="e1", source_id="youtube_data_api", raw_observation_id=f"raw-{index}",
            observed_at=AS_OF - timedelta(days=30 - index * 5),
            published_at=AS_OF - timedelta(days=60),
            views=Tri.known(1000 + index * 900), subscriber_count=Tri.known(20_000),
        )
        for index in range(6)
    ]
    settings = load_market_settings()
    features = compute_arm_features(
        {"seam-fixture": observations}, as_of=AS_OF, settings=settings,
        arm_ids={"seam-fixture": ARM_ID}, profiles={"seam-fixture": profile},
        taxonomy_confidence={"seam-fixture": 0.8},
    )
    persist_scores(session, score_all_arms(features, as_of=AS_OF, settings=settings),
                   computed_at=AS_OF)


def _market_engine() -> sa.Engine:
    """SQLite `market` for one app instance; StaticPool so FastAPI's threadpool sees it.

    The app runs its sync endpoints in worker threads, and an in-memory database is
    per-connection: one shared connection (``StaticPool`` + ``check_same_thread=False``)
    is what makes the seeded rows visible to requests. Unit-storage tests keep the
    plain per-thread engine (``tests.support.market_db.create_market_engine``).
    """
    engine = sa.create_engine(
        "sqlite+pysqlite:///:memory:", poolclass=sa.pool.StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS market")
    MARKET_METADATA.create_all(engine)
    return engine


class SyncASGIClient:
    """`httpx.ASGITransport` is async-only; this drives it synchronously for tests.

    Same wire the seam requires (real app, real JSON, no port bound); each request runs
    one ``asyncio.run`` around a fresh async client.
    """

    def __init__(self, app: Any, *, base_url: str, headers: Mapping[str, str]) -> None:
        self._app = app
        self._base_url = base_url
        self._headers = dict(headers)

    def request(self, method: str, path: str, *, params: Any = None,
                headers: Mapping[str, str] | None = None) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self._app)
            async with httpx.AsyncClient(transport=transport, base_url=self._base_url,
                                         headers=self._headers) as client:
                return await client.request(method, path, params=params, headers=headers)

        return asyncio.run(send())

    def get(self, path: str, *, params: Any = None,
            headers: Mapping[str, str] | None = None) -> httpx.Response:
        return self.request("GET", path, params=params, headers=headers)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@contextmanager
def open_c1_client(seed: Callable[[Session], None] | None = None) -> Iterator[SyncASGIClient]:
    """The real app over a seeded SQLite `market` schema, authenticated.

    `seed` replaces the default `seed_market` fixture when a test needs extra rows
    (e.g. an under-covered arm beside the fully covered one).
    """
    engine = _market_engine()
    factory = sessionmaker(bind=engine)
    with factory() as session:
        (seed or seed_market)(session)
        session.commit()
    platform = load_settings(env=PLATFORM_ENV)
    app = create_app(session_factory=factory,
                     settings=load_market_settings(platform=platform))
    with SyncASGIClient(app, base_url="http://testserver",
                        headers={"X-PLM-Service-Token": SEAM_TOKEN}) as client:
        yield client
