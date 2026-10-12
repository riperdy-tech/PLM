"""Mock distributor client implementation for hermetic testing (P8 §5.1, §18)."""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain.enums import DeliveryStatus, ReleaseStatus, StorePlatform
from ..domain.models import CanonicalReleasePackage, DeliveryTarget
from .base import (
    DistributorAuthError,
    DistributorCapability,
    DistributorCapabilityError,
    DistributorClientProtocol,
    DistributorRejectionError,
    DistributorStatusResult,
    DistributorSubmissionResult,
    DistributorTakedownResult,
)


class MockDistributorClient(DistributorClientProtocol):
    """Deterministic in-memory distributor adapter simulating DSP submissions."""

    def __init__(
        self,
        *,
        capabilities_override: set[DistributorCapability] | None = None,
        should_fail_auth: bool = False,
        should_reject: bool = False,
        rejection_reason: str = "Metadata policy violation",
        simulate_partial_delivery: bool = False,
    ) -> None:
        self._capabilities = (
            capabilities_override
            if capabilities_override is not None
            else {
                DistributorCapability.CREATE_RELEASE,
                DistributorCapability.SUBMIT_RELEASE,
                DistributorCapability.UPDATE_METADATA,
                DistributorCapability.TAKEDOWN,
                DistributorCapability.REDELIVERY,
                DistributorCapability.STORE_STATUS,
                DistributorCapability.RAW_REPORTS,
            }
        )
        self.should_fail_auth = should_fail_auth
        self.should_reject = should_reject
        self.rejection_reason = rejection_reason
        self.simulate_partial_delivery = simulate_partial_delivery
        self.submitted_packages: dict[str, CanonicalReleasePackage] = {}

    def capabilities(self) -> set[DistributorCapability]:
        return set(self._capabilities)

    def has_capability(self, cap: DistributorCapability) -> bool:
        return cap in self._capabilities

    def submit_release(self, package: CanonicalReleasePackage) -> DistributorSubmissionResult:
        if self.should_fail_auth:
            raise DistributorAuthError("Invalid distributor credentials or expired token")

        if not self.has_capability(DistributorCapability.SUBMIT_RELEASE):
            raise DistributorCapabilityError("Provider does not support SUBMIT_RELEASE")

        if self.should_reject:
            raise DistributorRejectionError(self.rejection_reason)

        provider_release_id = f"prov_{package.release_id}"
        self.submitted_packages[provider_release_id] = package

        return DistributorSubmissionResult(
            provider_release_id=provider_release_id,
            status=ReleaseStatus.SUBMITTED,
            raw_response={
                "id": provider_release_id,
                "status": "submitted",
                "upc": f"UPC_{package.release_id}",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    def get_release_status(self, provider_release_id: str) -> DistributorStatusResult:
        if self.should_fail_auth:
            raise DistributorAuthError("Invalid distributor credentials")

        if not self.has_capability(DistributorCapability.STORE_STATUS):
            raise DistributorCapabilityError("Provider does not support STORE_STATUS")

        now = datetime.now(UTC)
        stores = [
            StorePlatform.SPOTIFY,
            StorePlatform.APPLE_MUSIC,
            StorePlatform.YOUTUBE_MUSIC,
            StorePlatform.AMAZON_MUSIC,
            StorePlatform.TIDAL,
            StorePlatform.DEEZER,
        ]

        deliveries: list[DeliveryTarget] = []
        if self.simulate_partial_delivery:
            # First half delivered, second half pending/failed
            for idx, st in enumerate(stores):
                if idx < 3:
                    deliveries.append(
                        DeliveryTarget(
                            store=st,
                            external_release_id=f"ext_{st.value}_{provider_release_id}",
                            status=DeliveryStatus.DELIVERED,
                            first_delivered_at=now,
                        )
                    )
                else:
                    deliveries.append(
                        DeliveryTarget(
                            store=st,
                            external_release_id=None,
                            status=DeliveryStatus.PROCESSING,
                            first_delivered_at=None,
                        )
                    )
            overall_status = ReleaseStatus.PARTIALLY_DELIVERED
        else:
            for st in stores:
                deliveries.append(
                    DeliveryTarget(
                        store=st,
                        external_release_id=f"ext_{st.value}_{provider_release_id}",
                        status=DeliveryStatus.DELIVERED,
                        first_delivered_at=now,
                    )
                )
            overall_status = ReleaseStatus.DELIVERED

        return DistributorStatusResult(
            provider_release_id=provider_release_id,
            status=overall_status,
            deliveries=deliveries,
            raw_response={"status": overall_status.value, "provider_id": provider_release_id},
        )

    def request_takedown(self, provider_release_id: str) -> DistributorTakedownResult:
        if self.should_fail_auth:
            raise DistributorAuthError("Invalid distributor credentials")

        if not self.has_capability(DistributorCapability.TAKEDOWN):
            raise DistributorCapabilityError("Provider does not support TAKEDOWN")

        return DistributorTakedownResult(
            provider_release_id=provider_release_id,
            status=ReleaseStatus.TAKEN_DOWN,
            confirmed=True,
            raw_response={"status": "takedown_complete", "provider_id": provider_release_id},
        )
