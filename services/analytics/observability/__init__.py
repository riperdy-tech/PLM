"""Observability package for Phase P9 Analytics & Economics Engine."""

from .logging import configure_analytics_logger
from .metrics import (
    ANALYTICS_COSTS_RECORDED_TOTAL,
    ANALYTICS_INGESTIONS_TOTAL,
    ANALYTICS_REWARDS_POSTED_TOTAL,
)

__all__ = [
    "ANALYTICS_COSTS_RECORDED_TOTAL",
    "ANALYTICS_INGESTIONS_TOTAL",
    "ANALYTICS_REWARDS_POSTED_TOTAL",
    "configure_analytics_logger",
]
