# tests/unit/market/test_demand.py
"""Review fix 6: demand measures pull, not bigness.

The architecture is explicit that raw view count is not sufficient — "a small
channel with unusually fast growth can be a more useful signal than a very large
established channel with slow growth" — so the demand input is a size-independent
per-subscriber rate, never a raw cumulative view count.
"""

from datetime import UTC, datetime, timedelta

import pytest
from plm_contracts import Tri

from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.features.demand import demand_inputs

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def _sighting(*, days_ago: int, views: int, subscribers: int | None, age_days: int,
              entity_id: str = "channel") -> NormalizedObservation:
    observed_at = AS_OF - timedelta(days=days_ago)
    return NormalizedObservation(
        entity_id=entity_id, source_id="youtube_data_api", raw_observation_id=f"r{days_ago}",
        observed_at=observed_at, published_at=observed_at - timedelta(days=age_days),
        views=Tri.known(views),
        subscriber_count=Tri.known(subscribers) if subscribers is not None else Tri.unknown(),
    )


def test_demand_is_pull_per_subscriber_not_raw_views() -> None:
    """The mega channel has ~120x the views and ~500x the subscribers: its pull rate
    is lower. A raw-view median would rank it top — the defect this pins."""
    mega = _sighting(days_ago=15, views=2_001_500, subscribers=10_000_000, age_days=1185)
    small = _sighting(days_ago=15, views=17_000, subscribers=20_000, age_days=45)

    mega_pull = demand_inputs([mega], as_of=AS_OF, window_days=30).median_pull_per_day
    small_pull = demand_inputs([small], as_of=AS_OF, window_days=30).median_pull_per_day
    assert mega_pull.is_known and small_pull.is_known
    assert float(small_pull.unwrap()) > float(mega_pull.unwrap())
    # views per day per subscriber: small = 17000/45/20000, mega = 2001500/1185/10M
    assert float(small_pull.unwrap()) == pytest.approx(17_000 / 45 / 20_000)
    assert float(mega_pull.unwrap()) == pytest.approx(2_001_500 / 1185 / 10_000_000)


def test_missing_subscriber_count_leaves_pull_unknown_not_a_bare_view_rate() -> None:
    """§3.6/§11.3: without an audience there is no relative measure — mark it
    unstable (unknown), never substitute the raw rate."""
    hidden = _sighting(days_ago=10, views=50_000, subscribers=None, age_days=30)
    demand = demand_inputs([hidden], as_of=AS_OF, window_days=30)
    assert demand.median_pull_per_day.is_unknown
    # the raw signals are still reported as context
    assert demand.recent_activity_count == 1