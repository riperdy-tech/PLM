"""Prometheus metrics for Phase P8 DSP / Music Distribution Engine."""

from __future__ import annotations

from prometheus_client import Counter

DISTRIBUTION_SUBMISSIONS_TOTAL = Counter(
    "plm_distribution_submissions_total",
    "Total release submissions to DSP distributors",
    ["provider", "status"],
)

DISTRIBUTION_DELIVERIES_TOTAL = Counter(
    "plm_distribution_deliveries_total",
    "Total successful store deliveries",
    ["store", "status"],
)

DISTRIBUTION_REJECTIONS_TOTAL = Counter(
    "plm_distribution_rejections_total",
    "Total rejected submissions by provider or store",
    ["provider", "reason"],
)
