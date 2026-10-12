"""Observability metrics and logging for Phase P8 (P8 §27)."""

from .logging import configure_distribution_logger
from .metrics import (
    DISTRIBUTION_DELIVERIES_TOTAL,
    DISTRIBUTION_REJECTIONS_TOTAL,
    DISTRIBUTION_SUBMISSIONS_TOTAL,
)

__all__ = [
    "DISTRIBUTION_DELIVERIES_TOTAL",
    "DISTRIBUTION_REJECTIONS_TOTAL",
    "DISTRIBUTION_SUBMISSIONS_TOTAL",
    "configure_distribution_logger",
]
