"""Domain enums for Phase P9 Analytics & Economics Engine (P9 §14, §15, §16, §57)."""

from __future__ import annotations

from enum import Enum


class MetricSource(str, Enum):
    """Source provider for metric observations."""

    YOUTUBE_ANALYTICS = "youtube_analytics"
    YOUTUBE_REPORTING = "youtube_reporting"
    DISTRIBUTOR_STATEMENT = "distributor_statement"
    INTERNAL_COST = "internal_cost"
    SYNTHETIC = "synthetic"


class MetricStatus(str, Enum):
    """Quality and verification state of an ingested metric."""

    VALID = "valid"
    ESTIMATED = "estimated"
    REVISED = "revised"
    ANOMALOUS = "anomalous"
    QUARANTINED = "quarantined"


class ObservationWindow(str, Enum):
    """Canonical observation time window (P9 §14, §15)."""

    W_1H = "1h"
    W_24H = "24h"
    W_7D = "7d"
    W_14D = "14d"
    W_28D = "28d"
    W_90D = "90d"
    LIFETIME = "lifetime"


class RevenueStatus(str, Enum):
    """Monetary accounting finality state."""

    ESTIMATED = "estimated"
    REPORTED = "reported"
    FINAL = "final"


class PaybackStatus(str, Enum):
    """Status of cost recovery for an asset or release."""

    NOT_REPAID = "not_repaid"
    REPAID = "repaid"
    NOT_APPLICABLE = "not_applicable"


class DataFreshness(str, Enum):
    """Freshness rating of source observations."""

    FRESH = "fresh"
    ACCEPTABLE = "acceptable"
    STALE = "stale"
    CRITICAL = "critical"


class CostCategory(str, Enum):
    """Category of allocated production or operating costs."""

    COMPUTE = "compute"
    STORAGE = "storage"
    DISTRIBUTION = "distribution"
    MARKETING = "marketing"
    THIRD_PARTY_API = "third_party_api"
    OVERHEAD = "overhead"
