# tests/unit/market/test_change_detection.py
"""§32: change events are derived from two stored snapshots, never re-invented.

`detect_changes` is pure (two snapshots in, drafts out); `persist_snapshot` stores
exactly the events the report showed, so the stored history and the rendered report
cannot disagree.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.entities import EvidenceSummary, OpportunityScore
from services.market_intelligence.domain.enums import ChangeEventType, ScoringComponent
from services.market_intelligence.domain.value_objects import ScoreComponents
from services.market_intelligence.reports.market_snapshot import (
    MarketSnapshot,
    build_snapshot,
    detect_changes,
    persist_snapshot,
    previous_snapshot,
)
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.models import change_events
from tests.support.c1_api import ARM_ID, seed_market

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()


def _snapshot(*, score: float, growth: float | None, as_of: datetime = AS_OF) -> MarketSnapshot:
    return MarketSnapshot(
        as_of=as_of, generated_at=as_of,
        sections={
            "top_opportunity_arms": [{"arm_id": "arm-a", "score": score, "confidence": 0.8,
                                      "as_of": as_of.isoformat()}],
            "fastest_growing_concepts": [{"arm_id": "arm-a", "growth": growth}],
        },
        evidence_classes={"top_opportunity_arms": "DERIVED",
                          "fastest_growing_concepts": "DERIVED"},
    )


def test_a_score_move_beyond_the_threshold_is_one_draft_per_arm() -> None:
    previous = _snapshot(score=40.0, growth=20.0)
    current = _snapshot(score=61.0, growth=20.0, as_of=AS_OF + timedelta(days=1))
    drafts = detect_changes(previous, current, settings=SETTINGS)
    assert [draft.event_type for draft in drafts] == [ChangeEventType.OPPORTUNITY_SCORE_CHANGE]
    assert drafts[0].arm_id == "arm-a"
    assert drafts[0].previous_value == 40.0 and drafts[0].current_value == 61.0
    assert drafts[0].detail["delta"] == 21.0


def test_small_moves_and_new_arms_are_not_changes() -> None:
    previous = _snapshot(score=40.0, growth=20.0)
    within = _snapshot(score=45.0, growth=25.0, as_of=AS_OF + timedelta(days=1))
    assert detect_changes(previous, within, settings=SETTINGS) == []
    no_previous = MarketSnapshot(as_of=AS_OF, generated_at=AS_OF, sections={},
                                 evidence_classes={})
    assert detect_changes(None, no_previous, settings=SETTINGS) == []


def test_a_growth_move_beyond_the_threshold_is_its_own_event() -> None:
    previous = _snapshot(score=40.0, growth=10.0)
    current = _snapshot(score=40.0, growth=40.0, as_of=AS_OF + timedelta(days=1))
    drafts = detect_changes(previous, current, settings=SETTINGS)
    assert [draft.event_type for draft in drafts] == [ChangeEventType.GROWTH_COMPONENT_CHANGE]


def test_an_unknown_growth_reading_never_becomes_a_change(market_session) -> None:
    """Unknown is not zero: an arm with no growth reading cannot "move"."""
    previous = _snapshot(score=40.0, growth=None)
    current = _snapshot(score=40.0, growth=99.0, as_of=AS_OF + timedelta(days=1))
    assert detect_changes(previous, current, settings=SETTINGS) == []


def test_persist_snapshot_round_trips_and_stores_exactly_the_shown_events(market_session) -> None:
    seed_market(market_session)
    first = build_snapshot(market_session, as_of=AS_OF, settings=SETTINGS)
    assert first.changes == ()
    persist_snapshot(market_session, first, artifact_uri="plm://plm-media/market/reports/a",
                     artifact_hash="a" * 64)
    market_session.commit()

    stored = previous_snapshot(market_session)
    assert stored is not None
    assert stored["as_of"].replace(tzinfo=UTC) == AS_OF
    assert set(stored["sections_json"]) == set(first.sections)

    # A much lower recomputation the next day: one score move, one stored event.
    baseline = repositories.latest_scores(market_session)[0]
    later_at = AS_OF + timedelta(days=1)
    repositories.insert_score(
        market_session, arm_id=ARM_ID,
        score=OpportunityScore(
            arm_id=ARM_ID,
            market_opportunity_score=max(0.0, float(baseline["market_opportunity_score"]) - 25.0),
            confidence=float(baseline["confidence"]), basis_weight=float(baseline["basis_weight"]),
            components=ScoreComponents(demand=50.0, growth=50.0, competitor_velocity=50.0,
                                       competition=50.0, audience_quality=50.0,
                                       cross_platform=50.0, production_fit=50.0),
            evidence=EvidenceSummary(observation_count=1, source_count=1,
                                     observation_ids=("obs-1",)),
            as_of=later_at, score_version=SETTINGS.score_version,
            feature_version=SETTINGS.feature_version,
            normalization_version=SETTINGS.normalization_version,
        ),
        computed_at=later_at,
        components={component.value: 50.0 for component in ScoringComponent},
        evidence={"observation_count": 1, "source_count": 1, "observation_ids": ["obs-1"],
                  "top_observation_ids": ["obs-1"]},
    )
    market_session.commit()

    later = build_snapshot(market_session, as_of=later_at, settings=SETTINGS)
    assert [draft.event_type for draft in later.changes] == \
        [ChangeEventType.OPPORTUNITY_SCORE_CHANGE]
    assert later.sections["changes_vs_previous_snapshot"] == [{
        "event_type": "opportunity_score_change", "arm_id": ARM_ID,
        "previous": float(baseline["market_opportunity_score"]),
        "current": max(0.0, float(baseline["market_opportunity_score"]) - 25.0),
    }]
    persist_snapshot(market_session, later, artifact_uri="plm://plm-media/market/reports/b",
                     artifact_hash="b" * 64)
    market_session.commit()
    stored_events = market_session.execute(
        sa.select(sa.func.count()).select_from(change_events)
    ).scalar_one()
    assert stored_events == len(later.changes) == 1
