# services/market_intelligence/features/competition.py
"""§13: competition and saturation — never "number of search results".

Concentration is an analytic indicator over channel-normalised activity, not a legal
statement; both raw and normalised values are persisted with the sample size.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "ArmCompetition",
    "competition_metrics",
    "herfindahl_index",
    "percentile",
    "top_n_share",
]


@dataclass(frozen=True, slots=True)
class ArmCompetition:
    active_channel_count: int
    active_video_count: int
    median_video_velocity: float
    p75_video_velocity: float
    p90_video_velocity: float
    median_channel_size: float
    top_10_channel_share: float
    new_upload_rate: float
    concentration_raw: float
    concentration_normalized: float
    sample_size: int


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile; an empty sample is 0.0 and says so via sample_size."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = min(max(q, 0.0), 1.0) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def herfindahl_index(shares: Sequence[float]) -> float:
    """§13 `HHI = sum(share_i ** 2)` over shares that should already sum to 1."""
    return float(sum(share * share for share in shares))


def top_n_share(values: Sequence[float], n: int) -> float:
    """Share of total activity held by the `n` largest values."""
    total = float(sum(values))
    if total <= 0:
        return 0.0
    return float(sum(sorted(values, reverse=True)[:n]) / total)


def competition_metrics(
    *,
    video_velocities: Sequence[float],
    channel_sizes: Sequence[float],
    channel_activities: Sequence[float],
    uploads_last_window: int,
    window_days: int,
) -> ArmCompetition:
    """One arm's competitive profile; `concentration_normalized` is HHI scaled to 0–1."""
    activity_total = float(sum(channel_activities))
    shares = [activity / activity_total for activity in channel_activities] \
        if activity_total > 0 else []
    raw = herfindahl_index(shares)
    normalized = 0.0 if not shares else (raw - 1.0 / len(shares)) / (1.0 - 1.0 / len(shares)) \
        if len(shares) > 1 else raw
    return ArmCompetition(
        active_channel_count=len(channel_activities),
        active_video_count=len(video_velocities),
        median_video_velocity=percentile(video_velocities, 0.5),
        p75_video_velocity=percentile(video_velocities, 0.75),
        p90_video_velocity=percentile(video_velocities, 0.9),
        median_channel_size=percentile(channel_sizes, 0.5),
        top_10_channel_share=top_n_share(channel_activities, 10),
        new_upload_rate=uploads_last_window / window_days if window_days > 0 else 0.0,
        concentration_raw=raw,
        concentration_normalized=max(0.0, min(normalized, 1.0)),
        sample_size=len(video_velocities),
    )
