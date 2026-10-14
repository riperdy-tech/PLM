# services/market_intelligence/features/competition.py
"""§13: competition and saturation — never "number of search results".

Concentration is an analytic indicator over channel-normalised activity, not a legal
statement; both raw and normalised values are persisted with the sample size.

The channel-keyed metrics (channel count, top-10 share, concentration, channel size)
are `Tri`: when the arm's activity cannot be attributed to channels they are unknown.
They are never computed over video ids, which made every arm look perfectly
fragmented (review R2).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from plm_contracts import Tri

__all__ = [
    "ArmCompetition",
    "competition_metrics",
    "herfindahl_index",
    "percentile",
    "top_n_share",
]


@dataclass(frozen=True, slots=True)
class ArmCompetition:
    active_channel_count: Tri[int]
    active_video_count: int
    median_video_velocity: float
    p75_video_velocity: float
    p90_video_velocity: float
    median_channel_size: Tri[float]
    top_10_channel_share: Tri[float]
    new_upload_rate: float
    concentration_raw: Tri[float]
    concentration_normalized: Tri[float]
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
    channel_activities: Sequence[float] | None,
    uploads_last_window: int,
    window_days: int,
) -> ArmCompetition:
    """One arm's competitive profile; `concentration_normalized` is HHI scaled to 0–1.

    `video_velocities` has one entry per video, `channel_activities` and
    `channel_sizes` one entry per channel. `channel_activities=None` means the
    activity could not be attributed to channels: every channel-keyed metric is then
    unknown. No activity at all (an empty or all-zero list) has no shares, so
    concentration is unknown too — never a measured 0.
    """
    concentration_raw: Tri[float] = Tri.unknown()
    concentration_normalized: Tri[float] = Tri.unknown()
    top_share: Tri[float] = Tri.unknown()
    channel_count: Tri[int] = Tri.unknown()
    if channel_activities is not None:
        channel_count = Tri.known(len(channel_activities))
        activity_total = float(sum(channel_activities))
        if activity_total > 0:
            shares = [activity / activity_total for activity in channel_activities]
            raw = herfindahl_index(shares)
            if len(shares) == 1:
                normalized = raw
            else:
                normalized = (raw - 1.0 / len(shares)) / (1.0 - 1.0 / len(shares))
            concentration_raw = Tri.known(raw)
            concentration_normalized = Tri.known(max(0.0, min(normalized, 1.0)))
            top_share = Tri.known(top_n_share(channel_activities, 10))
    median_size: Tri[float] = (
        Tri.known(percentile(channel_sizes, 0.5)) if channel_sizes else Tri.unknown()
    )
    return ArmCompetition(
        active_channel_count=channel_count,
        active_video_count=len(video_velocities),
        median_video_velocity=percentile(video_velocities, 0.5),
        p75_video_velocity=percentile(video_velocities, 0.75),
        p90_video_velocity=percentile(video_velocities, 0.9),
        median_channel_size=median_size,
        top_10_channel_share=top_share,
        new_upload_rate=uploads_last_window / window_days if window_days > 0 else 0.0,
        concentration_raw=concentration_raw,
        concentration_normalized=concentration_normalized,
        sample_size=len(video_velocities),
    )
