"""Provider-neutral distributor protocol and error definitions for Phase P8 (P8 §5, §18)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from ..domain.enums import ReleaseStatus
from ..domain.models import CanonicalReleasePackage, DeliveryTarget


class DistributorCapability(str, Enum):
    """Distributor capability flags (P8 §5.1)."""

    CREATE_RELEASE = "create_release"
    SUBMIT_RELEASE = "submit_release"
    UPDATE_METADATA = "update_metadata"
    TAKEDOWN = "takedown"
    REDELIVERY = "redelivery"
    STORE_STATUS = "store_status"
    RAW_REPORTS = "raw_reports"


class DistributorClientError(Exception):
    """Base exception for distributor communication failures."""


class DistributorCapabilityError(DistributorClientError):
    """Raised when an operation is attempted that the distributor does not support."""


class DistributorAuthError(DistributorClientError):
    """Raised when authentication with the distributor fails."""


class DistributorRejectionError(DistributorClientError):
    """Raised when a distributor or store explicitly rejects a release submission."""


@dataclass(frozen=True)
class DistributorSubmissionResult:
    """Outcome of submitting a release to a distributor."""

    provider_release_id: str
    status: ReleaseStatus
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DistributorStatusResult:
    """Remote status query result from a distributor."""

    provider_release_id: str
    status: ReleaseStatus
    deliveries: list[DeliveryTarget] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DistributorTakedownResult:
    """Outcome of requesting a release takedown from a distributor."""

    provider_release_id: str
    status: ReleaseStatus
    confirmed: bool
    raw_response: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DistributorClientProtocol(Protocol):
    """Common distributor client interface across all provider adapters (P8 §5.1)."""

    def capabilities(self) -> set[DistributorCapability]:
        """Return the set of capabilities supported by this provider."""
        ...

    def has_capability(self, cap: DistributorCapability) -> bool:
        """Check if provider supports a specific capability."""
        ...

    def submit_release(self, package: CanonicalReleasePackage) -> DistributorSubmissionResult:
        """Submit a compiled canonical release package to the distributor."""
        ...

    def get_release_status(self, provider_release_id: str) -> DistributorStatusResult:
        """Retrieve current delivery and store statuses for a release."""
        ...

    def request_takedown(self, provider_release_id: str) -> DistributorTakedownResult:
        """Request takedown of a submitted or delivered release."""
        ...
