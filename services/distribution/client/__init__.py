"""Distributor client interface and adapters for Phase P8 (P8 §5, §18)."""

from .base import (
    DistributorAuthError,
    DistributorCapability,
    DistributorCapabilityError,
    DistributorClientError,
    DistributorClientProtocol,
    DistributorRejectionError,
    DistributorStatusResult,
    DistributorSubmissionResult,
    DistributorTakedownResult,
)
from .mock import MockDistributorClient

__all__ = [
    "DistributorAuthError",
    "DistributorCapability",
    "DistributorCapabilityError",
    "DistributorClientError",
    "DistributorClientProtocol",
    "DistributorRejectionError",
    "DistributorStatusResult",
    "DistributorSubmissionResult",
    "DistributorTakedownResult",
    "MockDistributorClient",
]
