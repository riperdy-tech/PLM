"""Domain enums for Phase P9 Analytics & Economics Engine (P9 §14, §15, §16, §57)."""

from __future__ import annotations

from enum import Enum

from plm_contracts import ObservationWindow as SharedObservationWindow


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


ObservationWindow = SharedObservationWindow
"""Observation windows; the shared vocabulary P2 parses (ADR-0030). P9's own
``1h``/``14d`` windows were refused by P2 and are gone; ``72h`` is now available."""


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
