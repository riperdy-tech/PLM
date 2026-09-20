# tests/unit/market/test_seams_and_freshness.py
"""Review Focus 1 and §21, across the feature layer rather than inside one module."""

from datetime import UTC, datetime, timedelta

from plm_contracts import Tri

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.domain.enums import FreshnessBucket
from services.market_intelligence.domain.value_objects import classify_freshness
from services.market_intelligence.features.arm_features import compute_arm_features

SETTINGS = load_market_settings()


def test_freshness_buckets_follow_the_spec_boundaries() -> None:
    now = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
    for hours, expected in ((47.0, FreshnessBucket.FRESH), (49.0, FreshnessBucket.NORMAL),
                            (167.0, FreshnessBucket.NORMAL), (169.0, FreshnessBucket.STALE),
                            (719.0, FreshnessBucket.STALE), (721.0, FreshnessBucket.EXPIRED)):
        assessment = classify_freshness(as_of=now - timedelta(hours=hours), now=now)
        assert assessment.bucket is expected, hours


def test_an_observation_without_metrics_yields_unknown_features_and_lower_confidence() -> None:
    now = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
    thin = NormalizedObservation(
        entity_id="e1", source_id="youtube_data_api", raw_observation_id="raw-1",
        observed_at=now, published_at=now - timedelta(days=30),
    )
    features = compute_arm_features({"arm": (thin,)}, as_of=now, settings=SETTINGS)
    arm = features["arm"]
    assert arm.median_velocity_30d.is_unknown
    assert arm.demand.median_pull_per_day.is_unknown
    assert arm.cross_platform.is_unknown
    assert arm.confidence < 0.5, "a thin observation must lower confidence, not raise a score"


def test_a_verified_zero_stays_a_known_zero_and_is_not_swallowed_as_unknown() -> None:
    """Review Focus 1's other edge: an explicit `0` is data, not a hole.

    A zero-view sighting on a channel whose size is known yields a *verified* pull
    rate of 0.0 — `Tri.known(0.0)`, not `Tri.unknown()`. Missing data and verified
    zero must stay distinguishable all the way through the feature layer.
    """
    now = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
    empty = NormalizedObservation(
        entity_id="e1", source_id="youtube_data_api", raw_observation_id="raw-1",
        observed_at=now, published_at=now - timedelta(days=10),
        views=Tri.known(0), subscriber_count=Tri.known(1_000),
    )
    features = compute_arm_features({"arm": (empty,)}, as_of=now, settings=SETTINGS)
    pull = features["arm"].demand.median_pull_per_day
    assert pull.is_known and float(pull.unwrap()) == 0.0
