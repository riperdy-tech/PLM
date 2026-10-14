# services/market_intelligence/features/velocity.py
"""§11 derived YouTube metrics. Every function is pure, deterministic and takes its
observation instants as arguments — no clock access, no randomness (P0 §2)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Final

from plm_contracts import Tri

__all__ = [
    "MIN_AGE_EPSILON_HOURS",
    "channel_relative_views",
    "comment_rate",
    "lifetime_views_per_day",
    "like_rate",
    "recent_views_per_day",
    "video_age_hours",
    "windowed_views_per_day",
]

MIN_AGE_EPSILON_HOURS: Final[float] = 1.0 / 60.0  # one minute
_EPSILON: Final[float] = 1e-9


def _require_aware(*moments: datetime) -> None:
    if any(moment.tzinfo is None for moment in moments):
        raise ValueError("timezone-aware datetimes required; use datetime.now(UTC)")


def video_age_hours(*, observed_at: datetime, published_at: datetime) -> float:
    """§11.1, with the epsilon that keeps a just-published video out of division by zero."""
    _require_aware(observed_at, published_at)
    delta = (observed_at - published_at).total_seconds() / 3600.0
    return max(delta, MIN_AGE_EPSILON_HOURS)


def lifetime_views_per_day(views: Tri[float], *, observed_at: datetime,
                           published_at: datetime) -> Tri[float]:
    """§11.2. Explicitly the *lifetime* measure: the caller must prefer windowed velocity.

    Old videos dominate cumulative averages, which is why §11.5 exists and why every
    consumer records which basis it used.
    """
    if not views.is_known:
        return Tri.unknown()
    return Tri.known(views.unwrap() / (video_age_hours(
        observed_at=observed_at, published_at=published_at) / 24.0))


def windowed_views_per_day(
    views_before: Tri[float], views_after: Tri[float], *, observed_at_before: datetime,
    observed_at_after: datetime,
) -> Tri[float]:
    """§11.5's preferred measure: the observed delta between two real sightings."""
    if not (views_before.is_known and views_after.is_known):
        return Tri.unknown()
    _require_aware(observed_at_before, observed_at_after)
    window_days = (observed_at_after - observed_at_before).total_seconds() / 86400.0
    if window_days <= 0:
        raise ValueError("the later observation must be later than the earlier one")
    delta = views_after.unwrap() - views_before.unwrap()
    if delta < 0:
        return Tri.unknown()  # a provider correction, not negative growth: call it unknown
    return Tri.known(delta / window_days)


def recent_views_per_day(
    sightings: Sequence[tuple[datetime, Tri[float]]], *, now: datetime, window_hours: float,
    min_span_fraction: float,
) -> Tri[float]:
    """§11.5 over a short trailing horizon (24h/72h): views/day of one entity.

    The window is `(now - window_hours, now]` relative to the injected `now`; a
    sighting after `now` is not evidence yet and is ignored. The rate is the delta
    between the earliest and latest sightings with known views inside the window.

    Insufficient evidence is `unknown`, never zero and never a lifetime fallback:
    fewer than two known sightings, sightings spanning less than
    `min_span_fraction` of the window, or a negative delta (a provider correction).
    """
    if window_hours <= 0:
        raise ValueError("window_hours must be positive")
    if not 0.0 < min_span_fraction <= 1.0:
        raise ValueError("min_span_fraction must be in (0, 1]")
    _require_aware(now, *(moment for moment, _views in sightings))
    start = now - timedelta(hours=window_hours)
    inside = sorted(
        ((moment, float(views.unwrap())) for moment, views in sightings
         if start < moment <= now and views.is_known),
        key=lambda pair: pair[0],
    )
    if len(inside) < 2:
        return Tri.unknown()
    (first_at, first_views), (last_at, last_views) = inside[0], inside[-1]
    span_hours = (last_at - first_at).total_seconds() / 3600.0
    if span_hours <= 0 or span_hours < window_hours * min_span_fraction:
        return Tri.unknown()
    delta = last_views - first_views
    if delta < 0:
        return Tri.unknown()
    return Tri.known(delta / (span_hours / 24.0))


def like_rate(likes: Tri[float], views: Tri[float]) -> Tri[float]:
    """§11.4 descriptive proxy. Zero views gives no rate — never a division artefact."""
    if not (likes.is_known and views.is_known) or views.unwrap() <= 0:
        return Tri.unknown()
    return Tri.known(likes.unwrap() / max(views.unwrap(), _EPSILON))


def comment_rate(comments: Tri[float], views: Tri[float]) -> Tri[float]:
    if not (comments.is_known and views.is_known) or views.unwrap() <= 0:
        return Tri.unknown()
    return Tri.known(comments.unwrap() / max(views.unwrap(), _EPSILON))


def channel_relative_views(views: Tri[float], subscriber_count: Tri[float]) -> Tri[float]:
    """§11.3: unstable when subscriber count is missing **or zero** → unknown."""
    if not (views.is_known and subscriber_count.is_known):
        return Tri.unknown()
    if subscriber_count.unwrap() <= 0:
        return Tri.unknown()
    return Tri.known(views.unwrap() / subscriber_count.unwrap())
