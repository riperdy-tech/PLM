"""P9 settings: deployment values come from `plm_platform.config`; these are P9's knobs.

Constructed from code defaults plus explicit overrides; P9 never reads `os.environ`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from plm_contracts.money import DEFAULT_REPORTING_CURRENCY
from plm_platform.config import Settings

__all__ = ["AnalyticsSettings", "load_analytics_settings"]

_CURRENCY = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True, slots=True)
class AnalyticsSettings:
    platform: Settings | None = None
    reporting_currency: str = DEFAULT_REPORTING_CURRENCY
    """Every reported amount is converted into this currency (P9 §23; KRW by default)."""

    def __post_init__(self) -> None:
        if not _CURRENCY.match(self.reporting_currency):
            raise ValueError(f"reporting_currency must be an ISO 4217 code: {self.reporting_currency!r}")


def load_analytics_settings(
    *, platform: Settings | None = None, **overrides: object
) -> AnalyticsSettings:
    """Build P9 settings; unknown keyword overrides raise (a typo must not pass silently)."""
    unknown = set(overrides) - set(AnalyticsSettings.__dataclass_fields__)
    if unknown:
        raise TypeError(f"unknown analytics settings: {sorted(unknown)}")
    return AnalyticsSettings(platform=platform, **overrides)  # type: ignore[arg-type]
