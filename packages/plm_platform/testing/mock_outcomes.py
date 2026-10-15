"""A deterministic mock of the outside world's response to a published unit (R5).

There are no live YouTube Analytics or distributor statements before R6, so P7's mock
YouTube Analytics source and P8's mock distributor statements read their numbers from
this model. It is labelled ``mock`` everywhere it surfaces (``provider_mode`` on every
answer, ``source = synthetic`` when P9 stores it, ``mock`` in ``docs/STATUS.md``) and is
never evidence about a real audience.

* **Deterministic.** Every number is a function of stable identifiers (video id, ISRC,
  arm id, window, period) through :func:`~plm_platform.clock.stable_seed` and of the
  instants the caller passes; nothing reads a clock or uses ``hash()``. The same inputs
  give the same metrics in every process.
* **Arm-dependent.** Each arm has a *true effect*, a multiplier on views and streams:
  the configured ``arm_effects[arm_id]``, else a seeded draw inside ``effect_range``.
  Arms therefore earn different rewards and an allocator that learns can be seen to
  shift toward the better ones.
* **Elapsed time.** A metric accrues with the time elapsed since publication, capped at
  the window's length (``lifetime`` is uncapped). Per-identifier jitter of at most
  ``±noise`` keeps units of one arm from being identical.

Configuration is a JSON file named by ``PLM_MOCK_OUTCOMES_FILE`` (see
:func:`load_mock_outcome_model`); every parameter is required.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from plm_contracts import ObservationWindow, ProviderMode
from plm_contracts.measurement import window_duration
from plm_contracts.money import Money

from ..clock import SEED_MODULUS, require_aware, stable_seed
from ..config import SettingsError
from ..dotenv import load_env_file

__all__ = [
    "MOCK_OUTCOMES_FILE_VAR",
    "MockOutcomeModel",
    "MockStatementLine",
    "MockVideoSnapshot",
    "load_mock_outcome_model",
    "mock_outcome_model_from_env",
    "monthly_periods",
    "parse_mock_outcome_model",
]

MOCK_OUTCOMES_FILE_VAR: Final[str] = "PLM_MOCK_OUTCOMES_FILE"
PROVIDER_MODE: Final[ProviderMode] = ProviderMode.MOCK
_DAY_SECONDS: Final[Decimal] = Decimal(86400)
_MONEY_QUANTUM: Final[Decimal] = Decimal("0.000001")
_RATIO_QUANTUM: Final[Decimal] = Decimal("0.0001")


@dataclass(frozen=True, slots=True)
class MockVideoSnapshot:
    """One cumulative snapshot of a video for one window, as YouTube Analytics would give it."""

    views: int
    watch_time_minutes: Decimal
    average_view_duration_seconds: Decimal | None
    average_percentage_viewed: Decimal
    subscribers_gained: int
    subscribers_lost: int
    estimated_revenue: Money


@dataclass(frozen=True, slots=True)
class MockStatementLine:
    """One distributor statement line: a recording's streams and revenue for a period."""

    isrc: str
    streams: int
    revenue: Money


def _unit(*parts: str) -> Decimal:
    """A deterministic number in ``[0, 1)`` from identifiers."""
    return Decimal(stable_seed("plm.mock-outcomes", *parts)) / Decimal(SEED_MODULUS)


@dataclass(frozen=True, slots=True)
class MockOutcomeModel:
    model_version: str
    currency: str
    views_per_day: Decimal
    watch_minutes_per_view: Decimal
    average_percentage_viewed: Decimal
    subscribers_per_thousand_views: Decimal
    unsubscribe_ratio: Decimal
    estimated_rpm: Decimal
    """Estimated revenue per 1 000 views, in ``currency`` (a platform estimate)."""
    streams_per_track_per_day: Decimal
    revenue_per_stream: Decimal
    noise: Decimal
    arm_effects: Mapping[str, Decimal]
    effect_range: tuple[Decimal, Decimal]

    provider_mode: ProviderMode = PROVIDER_MODE

    def effect(self, arm_id: str) -> Decimal:
        """The arm's true effect: configured, else seeded inside ``effect_range``."""
        if not arm_id.strip():
            raise ValueError("an arm id is required")
        configured = self.arm_effects.get(arm_id)
        if configured is not None:
            return configured
        low, high = self.effect_range
        return (low + (high - low) * _unit("effect", arm_id)).quantize(_RATIO_QUANTUM)

    def _jitter(self, *parts: str) -> Decimal:
        return Decimal(1) + self.noise * (Decimal(2) * _unit("jitter", *parts) - Decimal(1))

    @staticmethod
    def _elapsed_days(start: datetime, end: datetime, cap: timedelta | None) -> Decimal:
        start, end = require_aware(start), require_aware(end)
        if end < start:
            raise ValueError("an observation cannot predate the publication it measures")
        elapsed = end - start
        if cap is not None and elapsed > cap:
            elapsed = cap
        return Decimal(int(elapsed.total_seconds())) / _DAY_SECONDS

    def video_snapshot(self, *, video_id: str, arm_id: str, published_at: datetime,
                       window: ObservationWindow, observed_at: datetime) -> MockVideoSnapshot:
        """The video's cumulative metrics for ``window`` as of ``observed_at``."""
        days = self._elapsed_days(published_at, observed_at, window_duration(window))
        scale = self.effect(arm_id) * self._jitter("views", video_id, window.value)
        views = int((self.views_per_day * days * scale).to_integral_value(ROUND_HALF_EVEN))
        watch = (Decimal(views) * self.watch_minutes_per_view
                 * self._jitter("watch", video_id, window.value)).quantize(_RATIO_QUANTUM)
        pct = min(Decimal(100), self.average_percentage_viewed
                  * self._jitter("retention", video_id, window.value)).quantize(_RATIO_QUANTUM)
        gained = int((Decimal(views) * self.subscribers_per_thousand_views / Decimal(1000))
                     .to_integral_value(ROUND_HALF_EVEN))
        lost = int((Decimal(gained) * self.unsubscribe_ratio).to_integral_value(ROUND_HALF_EVEN))
        revenue = (Decimal(views) * self.estimated_rpm / Decimal(1000)).quantize(_MONEY_QUANTUM)
        return MockVideoSnapshot(
            views=views, watch_time_minutes=watch,
            average_view_duration_seconds=(
                None if views == 0 else (watch * 60 / Decimal(views)).quantize(_RATIO_QUANTUM)),
            average_percentage_viewed=pct, subscribers_gained=gained, subscribers_lost=lost,
            estimated_revenue=Money.of(revenue, self.currency),
        )

    def statement_lines(self, *, isrcs: Sequence[str], arm_id: str, period_start: datetime,
                        period_end: datetime, period: str | None = None
                        ) -> list[MockStatementLine]:
        """One line per recording for ``[period_start, period_end)``; ``period`` labels
        the statement (its jitter key), the ISO interval when not given."""
        days = self._elapsed_days(period_start, period_end, None)
        period = period or f"{period_start.isoformat()}/{period_end.isoformat()}"
        lines: list[MockStatementLine] = []
        for isrc in isrcs:
            scale = self.effect(arm_id) * self._jitter("streams", isrc, period)
            streams = int((self.streams_per_track_per_day * days * scale)
                          .to_integral_value(ROUND_HALF_EVEN))
            revenue = (Decimal(streams) * self.revenue_per_stream).quantize(_MONEY_QUANTUM)
            lines.append(MockStatementLine(isrc=isrc, streams=streams,
                                           revenue=Money.of(revenue, self.currency)))
        return lines


def monthly_periods(start: datetime, end: datetime) -> list[tuple[str, datetime, datetime]]:
    """The calendar months (UTC) that ``[start, end)`` touches, each clipped to it:
    ``(label "YYYY-MM", period start, period end)``. Distributors report per month, so a
    statement period never overlaps another (P9 sums statement lines per period)."""
    start, end = require_aware(start).astimezone(UTC), require_aware(end).astimezone(UTC)
    if end < start:
        raise ValueError("a statement period cannot end before it starts")
    periods: list[tuple[str, datetime, datetime]] = []
    month = datetime(start.year, start.month, 1, tzinfo=UTC)
    while month <= end:
        following = (datetime(month.year + 1, 1, 1, tzinfo=UTC) if month.month == 12
                     else datetime(month.year, month.month + 1, 1, tzinfo=UTC))
        periods.append((f"{month.year:04d}-{month.month:02d}", max(month, start),
                        min(following, end)))
        month = following
    return periods


def _decimal(raw: Mapping[str, Any], key: str, *, minimum: Decimal = Decimal(0)) -> Decimal:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, str | int):
        raise SettingsError(f"mock outcomes: {key} must be a decimal string or an integer")
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise SettingsError(f"mock outcomes: {key} is not a decimal: {value!r}") from None
    if not number.is_finite() or number < minimum:
        raise SettingsError(f"mock outcomes: {key} must be >= {minimum}")
    return number


def parse_mock_outcome_model(raw: Mapping[str, Any]) -> MockOutcomeModel:
    """Validate a parsed configuration; every parameter is required."""
    version = raw.get("model_version")
    currency = raw.get("currency")
    if not isinstance(version, str) or not version.strip():
        raise SettingsError("mock outcomes: model_version is required")
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
        raise SettingsError("mock outcomes: currency must be an ISO 4217 code")
    effects_raw = raw.get("arm_effects")
    if not isinstance(effects_raw, dict):
        raise SettingsError("mock outcomes: arm_effects must be an object (it may be empty)")
    effects = {str(arm): _decimal(effects_raw, arm) for arm in effects_raw}
    bounds = raw.get("effect_range")
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise SettingsError("mock outcomes: effect_range must be [low, high]")
    low = _decimal({"low": bounds[0]}, "low")
    high = _decimal({"high": bounds[1]}, "high")
    if high < low:
        raise SettingsError("mock outcomes: effect_range low must not exceed high")
    noise = _decimal(raw, "noise")
    if noise >= 1:
        raise SettingsError("mock outcomes: noise must be below 1")
    return MockOutcomeModel(
        model_version=version.strip(), currency=currency.upper(),
        views_per_day=_decimal(raw, "views_per_day"),
        watch_minutes_per_view=_decimal(raw, "watch_minutes_per_view"),
        average_percentage_viewed=_decimal(raw, "average_percentage_viewed"),
        subscribers_per_thousand_views=_decimal(raw, "subscribers_per_thousand_views"),
        unsubscribe_ratio=_decimal(raw, "unsubscribe_ratio"),
        estimated_rpm=_decimal(raw, "estimated_rpm"),
        streams_per_track_per_day=_decimal(raw, "streams_per_track_per_day"),
        revenue_per_stream=_decimal(raw, "revenue_per_stream"),
        noise=noise, arm_effects=effects, effect_range=(low, high),
    )


def load_mock_outcome_model(path: str | Path) -> MockOutcomeModel:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SettingsError(f"mock outcomes file {str(path)!r} is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise SettingsError(f"mock outcomes file {str(path)!r} must hold a JSON object")
    return parse_mock_outcome_model(raw)


def mock_outcome_model_from_env(env: Mapping[str, str] | None = None,
                                dotenv_path: str | Path | None = None
                                ) -> MockOutcomeModel | None:
    """The model named by ``PLM_MOCK_OUTCOMES_FILE``; unset: ``None`` (the mock sources
    then refuse rather than answer with made-up numbers)."""
    process_env: Mapping[str, str] = os.environ if env is None else env
    dotenv_values = load_env_file(Path(".env") if dotenv_path is None else dotenv_path)
    path = (process_env.get(MOCK_OUTCOMES_FILE_VAR)
            or dotenv_values.get(MOCK_OUTCOMES_FILE_VAR) or "").strip()
    return None if not path else load_mock_outcome_model(path)
