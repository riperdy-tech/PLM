"""P1 measured-duration classification from stored observations through arm evidence.

The SQLite harness is not proof of PostgreSQL replay or P2 migration. The
source metrics are synthetic and all writes stay in an in-memory schema.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from plm_contracts import Tri

from services.market_intelligence.cli import _classify_pending, _recompute
from services.market_intelligence.config import load_market_settings
from services.market_intelligence.normalization.youtube import NormalizedObservationDraft
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.models import classifications

NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
TITLE = "45_75m lofi study playlist"
SETTINGS = load_market_settings(min_arm_support=2)


def _store(
    session: sa.orm.Session, *, suffix: str, seconds: float, when: datetime,
) -> str:
    observation_id, created = repositories.insert_normalized_observation(
        session, NormalizedObservationDraft(
            entity_id=repositories.deterministic_id("entity", suffix),
            source_id=repositories.deterministic_id("source", "measured-duration"),
            raw_observation_id=repositories.deterministic_id("raw", suffix),
            observed_at=when, published_at=when - timedelta(days=3),
            views=Tri.known(1500), likes=Tri.known(100), comments=Tri.known(10),
            subscriber_count=Tri.known(750),
            video_duration_seconds=Tri.known(seconds),
            playlist_item_count=Tri.unknown(),
            channel_video_count=Tri.unknown(),
            text_title=TITLE, text_description="rain night city",
            country_signal=None, language_signal="en",
        ),
    )
    assert created
    return observation_id


def test_measured_duration_pipeline_preserves_all_cached_observation_evidence(
    market_session: sa.orm.Session,
) -> None:
    long_a = _store(
        market_session, suffix="a", seconds=3600.0,
        when=NOW - timedelta(hours=3),
    )
    long_b = _store(
        market_session, suffix="b", seconds=3900.0,
        when=NOW - timedelta(hours=2),
    )
    short = _store(
        market_session, suffix="c", seconds=120.0,
        when=NOW - timedelta(hours=1),
    )
    future = _store(
        market_session, suffix="future", seconds=3700.0,
        when=NOW + timedelta(hours=1),
    )
    initial = _classify_pending(market_session, now=NOW, settings=SETTINGS, full=True)
    assert initial["classified"] == 2
    assert initial["cached"] == 2
    classified = market_session.execute(
        sa.select(classifications.c.dimensions_json).where(
            classifications.c.taxonomy_version == SETTINGS.taxonomy_version,
        ),
    ).scalars().all()
    assert {row["duration_bucket"][0] for row in classified} == {
        "45_75m", "60_180s",
    }
    first = _recompute(market_session, now=NOW, settings=SETTINGS)
    second = _recompute(market_session, now=NOW, settings=SETTINGS)
    assert first["arms"] == second["arms"] == 1
    arms = [
        row for row in repositories.load_arms(market_session)
        if row["arm_id"].startswith(SETTINGS.taxonomy_version.replace("-", "_") + "_")
    ]
    assert len(arms) == 1
    assert arms[0]["taxonomy_json"]["duration_bucket"] == "45_75m"
    assert arms[0]["support_count"] == 2
    assert set(arms[0]["source_observation_ids"]) == {long_a, long_b}
    assert short not in arms[0]["source_observation_ids"]
    assert future not in arms[0]["source_observation_ids"]
