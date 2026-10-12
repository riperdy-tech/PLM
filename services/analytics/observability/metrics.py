"""Prometheus metrics for Phase P9 Analytics & Economics Engine."""

from __future__ import annotations

from prometheus_client import Counter

ANALYTICS_INGESTIONS_TOTAL = Counter(
    "plm_analytics_ingestions_total",
    "Total metric and statement ingestions by source",
    ["source", "status"],
)

ANALYTICS_REWARDS_POSTED_TOTAL = Counter(
    "plm_analytics_rewards_posted_total",
    "Total feedback rewards posted to P2 autonomous A&R",
    ["arm_id", "status"],
)

ANALYTICS_COSTS_RECORDED_TOTAL = Counter(
    "plm_analytics_costs_recorded_total",
    "Total allocated cost entries recorded",
    ["category"],
)
