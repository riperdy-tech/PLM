# tests/fixtures/market/scenario.py
"""§35's deliberately adversarial fixture: raw popularity must not automatically win.

Channel A is the biggest and slowest; Channel B is small, fast and cheap to compete
with; Channel C is mid-sized and stable. The expected end state is asserted in
`test_scoring_properties.py` (unit) and `test_scenario_postgres.py` (integration,
through the real store), not assumed here.

**Deviation from the plan's fixture, with reasons (P1.13 ledger):**

* every sighting carries its channel's subscriber count — without it demand's
  per-subscriber pull rate is *unknowable* (`demand.py` skips sightings that cannot
  produce a rate), so the plan's fixture could not express §35 at all;
* sightings sit at 55/40/25/15/5 days rather than 20/10/0: the growth windows are
  current-30d vs previous-30d, and sightings clustered inside the last 20 days leave
  the baseline window empty, so growth was `unknown` for every arm;
* each channel has its *own* view trajectory and the same number of videos (seven,
  i.e. 21 total rather than the plan's 20). The plan's single `base - k*(base//20)`
  rule adds the same ~5% per step to every channel — the slow/fast contrast §35
  exists to test was not present — and its 7/7/6 cadence makes the arm with more
  sightings win the demand input's activity term regardless of its pull rate;
* all videos share one publication-age profile (`_PUBLISHED_DAYS`). The growth metric
  is the window median of *lifetime* views/day, whose denominator is the sighting's
  age; a channel whose videos are older decays more slowly and would otherwise look
  like it was growing (§12's windows compare rates, not video ages);
* the three channels are three arms (`MID_ARM_ID` added), not two: the plan mapped
  Channel A *and* Channel C onto one arm, which is not the three-channel ranking
  §35 describes (and which its own test then compared against Channel B).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from plm_contracts import Tri

from services.market_intelligence.domain.entities import NormalizedObservation
from services.market_intelligence.domain.enums import EntityType

SCENARIO_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
PRIMARY_ARM_ID = "lofi_study_focus"        # Channel B: small, fast, cheap to compete with
SECONDARY_ARM_ID = "rnb_late_night_drive"  # Channel A: the largest and slowest
MID_ARM_ID = "citypop_steady_mix"          # Channel C: mid-sized and stable

# The four inter-sighting steps back from the newest sighting (oldest first).
SIGHTING_DAYS_AGO = (55, 40, 25, 15, 5)
# One publication-age profile shared by every video (same index → same age), so the
# growth windows compare view *dynamics*, not the age of the video.
_PUBLISHED_DAYS = (365, 330, 280, 240, 200, 150, 120)


@dataclass(frozen=True, slots=True)
class ScenarioChannel:
    entity_id: str
    entity_type: EntityType
    subscriber_count: int | None
    video_count: int


@dataclass(frozen=True, slots=True)
class ScenarioVideo:
    video_id: str
    channel_id: str
    published_days_ago: int
    views: int  # the newest sighting's view count
    likes: int | None
    comments: int | None
    duration_seconds: float


# §35's per-channel data: subscribers, the seven newest view counts, and how much of
# the newest count each of the four inter-sighting steps contributed (oldest first).
# Channel A barely moves; Channel B accelerates hard; Channel C grows steadily.
_CHANNELS_DATA: dict[str, tuple[int, tuple[int, ...], tuple[float, ...]]] = {
    "ch-a": (10_000_000, (600_000, 590_000, 580_000, 570_000, 560_000, 540_000, 520_000),
             (0.001, 0.001, 0.001, 0.001)),
    "ch-b": (20_000, (54_000, 51_000, 48_000, 46_000, 44_000, 42_000, 45_000),
             (0.05, 0.10, 0.25, 0.50)),
    "ch-c": (80_000, (37_800, 37_400, 37_000, 36_500, 36_000, 35_000, 35_800),
             (0.05, 0.10, 0.15, 0.20)),
}

# Likes/comments/durations are per (channel, position); views come from _CHANNELS_DATA.
_ENGAGEMENT = {
    "ch-a": ((21_000, 1_100, 3_600.0), (20_400, 1_050, 2_800.0), (19_800, 990, 2_400.0),
             (19_100, 940, 3_000.0), (18_400, 900, 1_200.0), (17_000, 820, 3_600.0),
             (15_900, 760, 2_200.0)),
    "ch-b": ((3_100, 260, 1_800.0), (2_900, 240, 2_100.0), (3_050, 255, 2_400.0),
             (3_220, 270, 2_000.0), (3_400, 285, 1_900.0), (3_650, 305, 2_050.0),
             (3_900, 330, 2_150.0)),
    "ch-c": ((2_100, 180, 2_600.0), (2_160, 185, 2_700.0), (2_190, 188, 2_650.0),
             (2_220, 190, 2_800.0), (2_244, 192, 2_900.0), (2_268, 194, 2_750.0),
             (2_280, 195, 2_700.0)),
}


@dataclass(frozen=True, slots=True)
class MarketScenario:
    """§35's fixture: channels, videos, queries and the arm-keyed observations."""

    channels: tuple[ScenarioChannel, ...]
    videos: tuple[ScenarioVideo, ...]
    queries: tuple[str, ...]
    observations_by_arm: dict[str, tuple[NormalizedObservation, ...]]
    now: datetime


CHANNELS = (
    ScenarioChannel("ch-a", EntityType.CHANNEL, 10_000_000, 412),
    ScenarioChannel("ch-b", EntityType.CHANNEL, 20_000, 88),
    ScenarioChannel("ch-c", EntityType.CHANNEL, 80_000, 140),
)

_ARM_FOR_CHANNEL = {"ch-a": SECONDARY_ARM_ID, "ch-b": PRIMARY_ARM_ID, "ch-c": MID_ARM_ID}


def _view_series(channel_id: str, newest_views: int) -> tuple[int, ...]:
    """Oldest-first view counts: the newest sighting, walked backwards by fraction."""
    fractions = _CHANNELS_DATA[channel_id][2]
    series = [newest_views]
    for fraction in reversed(fractions):
        series.append(series[-1] - int(fraction * newest_views))
    return tuple(reversed(series))


def build_scenario() -> MarketScenario:
    """Twenty-one videos, three channels, five collection instants each — deterministic."""
    observations_by_arm: dict[str, list[NormalizedObservation]] = {
        arm: [] for arm in (PRIMARY_ARM_ID, SECONDARY_ARM_ID, MID_ARM_ID)
    }
    videos: list[ScenarioVideo] = []
    for channel, (subscribers, newest_views, _fractions) in _CHANNELS_DATA.items():
        arm = _ARM_FOR_CHANNEL[channel]
        for index, (views, published_days) in enumerate(zip(newest_views, _PUBLISHED_DAYS)):
            # 11 characters, so the id is also valid for the provider-id regex.
            video_id = f"vid{channel[-1]}{index:08d}"
            likes, comments, duration = _ENGAGEMENT[channel][index]
            videos.append(ScenarioVideo(video_id, channel, published_days, views, likes,
                                        comments, duration))
            for step, (days_ago, view_count) in enumerate(
                    zip(SIGHTING_DAYS_AGO, _view_series(channel, views))):
                observations_by_arm[arm].append(NormalizedObservation(
                    entity_id=video_id, source_id="youtube_data_api",
                    raw_observation_id=f"raw-{video_id}-{step}",
                    observed_at=SCENARIO_NOW - timedelta(days=days_ago),
                    published_at=SCENARIO_NOW - timedelta(days=published_days),
                    views=Tri.known(view_count),
                    likes=Tri.known(likes), comments=Tri.known(comments),
                    video_duration_seconds=Tri.known(duration),
                    subscriber_count=Tri.known(subscribers) if subscribers is not None
                    else Tri.unknown(),
                ))
    return MarketScenario(
        channels=CHANNELS, videos=tuple(videos), queries=("lofi", "study music", "rnb"),
        observations_by_arm={arm: tuple(rows) for arm, rows in observations_by_arm.items()},
        now=SCENARIO_NOW,
    )
