"""Distribution orchestrator and state machine for Phase P8 (P8 §6, §11, §18, §71, Ruling R9, R10)."""

from __future__ import annotations

import logging
from datetime import datetime

from plm_contracts.seams import ExperimentLineage
from plm_platform.clock import require_aware
from plm_platform.execution import (
    ExternalWriteGuard,
    ExternalWriteRefusedError,
    GuardDecision,
    provider_mode_of,
)
from sqlalchemy.orm import Session

from .client.base import (
    DistributorClientProtocol,
)
from .domain.enums import (
    DeliveryStatus,
    ReleaseStatus,
    StorePlatform,
)
from .domain.models import (
    ArtistProfile,
    CanonicalReleasePackage,
    DeliveryTarget,
    DistributionRecordPayload,
    DistributionRelease,
)
from .preflight.validators import (
    DistributionPreflightError,
    validate_canonical_package,
)
from .storage.repositories import (
    get_release_by_fingerprint,
    get_release_by_id,
    get_release_identity,
    get_tracks_by_release_id,
    insert_release,
    insert_track,
    update_release_status,
)

logger = logging.getLogger(__name__)


class DistributionEngineResult:
    """Outcome of a distribution engine operation."""

    def __init__(
        self,
        *,
        release: DistributionRelease,
        payload: DistributionRecordPayload | None = None,
        is_duplicate: bool = False,
        error: str | None = None,
    ) -> None:
        self.release = release
        self.payload = payload
        self.is_duplicate = is_duplicate
        self.error = error

    @property
    def is_success(self) -> bool:
        return self.release.status in {
            ReleaseStatus.SUBMITTED,
            ReleaseStatus.PROCESSING,
            ReleaseStatus.PARTIALLY_DELIVERED,
            ReleaseStatus.DELIVERED,
            ReleaseStatus.TAKEN_DOWN,
        }


CAPABILITY_SUBMIT = "distribution.submit"
CAPABILITY_TAKEDOWN = "distribution.takedown"


class DistributionEngine:
    """Core distribution orchestrator managing lifecycle, idempotency, and DSP delivery.

    Submissions and takedowns first pass the platform
    :class:`~plm_platform.execution.ExternalWriteGuard`. A write the guard does not
    permit raises :class:`~plm_platform.execution.ExternalWriteRefusedError` before
    anything is persisted; the guard keeps the decision record. A funded write is settled
    with the guard: committed once the distributor call returned, released when the
    engine fails before calling it. A distributor call that raised leaves the
    reservation held (outcome uncertain) for operator reconciliation.
    """

    def __init__(
        self,
        client: DistributorClientProtocol,
        provider_name: str = "mock_distributor",
        *,
        write_guard: ExternalWriteGuard | None = None,
    ) -> None:
        self.client = client
        self.provider_name = provider_name
        self.write_guard = write_guard or ExternalWriteGuard()
        self.provider_mode = provider_mode_of(client)

    def _require_write(
        self, capability: str, idempotency_key: str, *, now: datetime
    ) -> GuardDecision:
        decision = self.write_guard.authorize_external_write(
            capability,
            provider_mode=self.provider_mode,
            idempotency_key=idempotency_key,
            now=now,
        )
        if not decision.decision.may_call_provider:
            raise ExternalWriteRefusedError(decision)
        return decision

    def submit_release(
        self,
        session: Session,
        package: CanonicalReleasePackage,
        *,
        artist: ArtistProfile | None = None,
        lineage: ExperimentLineage,
        media_package_id: str,
        now: datetime,
    ) -> DistributionEngineResult:
        """Submit a canonical release package to the distributor.

        Guarantees idempotency: duplicate submissions with matching fingerprint
        return the existing release without re-submitting to the distributor (P8 §19).
        """
        now = require_aware(now)

        # 1. Idempotency Check (P8 §19)
        existing = get_release_by_fingerprint(session, package.submission_fingerprint)
        if existing:
            logger.info("Found existing distribution release for fingerprint %s", package.submission_fingerprint)
            existing_tracks = get_tracks_by_release_id(session, existing.release_id)
            stored_lineage, stored_package_id = get_release_identity(
                session, existing.release_id
            )
            c7_payload = self.build_c7_payload(
                release=existing,
                isrcs=[t.isrc for t in existing_tracks],
                source_package_hash=package.submission_fingerprint,
                lineage=stored_lineage,
                media_package_id=stored_package_id,
            )
            return DistributionEngineResult(release=existing, payload=c7_payload, is_duplicate=True)

        # 2. Preflight Validation (P8 §12, §21)
        errors = validate_canonical_package(package, artist)
        if errors:
            raise DistributionPreflightError(errors)

        # 3. External-write gate: nothing is persisted for a write that will not happen.
        decision = self._require_write(
            CAPABILITY_SUBMIT, f"{package.submission_fingerprint}:submit", now=now
        )

        # 4. Persist Tracks
        try:
            for track in package.tracks:
                insert_track(session, track)
        except Exception:
            self.write_guard.settle_external_write(
                decision, performed=False, reason="tracks_not_persisted"
            )
            raise

        # 5. Submit to Distributor
        submission_res = self.client.submit_release(package)
        self.write_guard.settle_external_write(decision, performed=True)

        # 6. Build Initial Delivery Targets
        standard_stores = [
            StorePlatform.SPOTIFY,
            StorePlatform.APPLE_MUSIC,
            StorePlatform.YOUTUBE_MUSIC,
            StorePlatform.AMAZON_MUSIC,
            StorePlatform.TIDAL,
            StorePlatform.DEEZER,
        ]
        initial_deliveries = [
            DeliveryTarget(
                store=st,
                external_release_id=None,
                status=DeliveryStatus.PENDING,
                first_delivered_at=None,
            )
            for st in standard_stores
        ]

        # 7. Persist Release
        release = DistributionRelease(
            id=f"dist_rel_{package.release_id}",
            release_id=package.release_id,
            artist_id=package.artist_id,
            release_type=package.release_type,
            release_title=package.title,
            release_version=package.release_version,
            status=submission_res.status,
            provider=self.provider_name,
            provider_release_id=submission_res.provider_release_id,
            submission_fingerprint=package.submission_fingerprint,
            deliveries=initial_deliveries,
            created_at=now,
            updated_at=now,
        )

        insert_release(
            session,
            release,
            source_manifest_ids=package.source_manifest_ids,
            lineage=lineage,
            media_package_id=media_package_id,
        )

        c7_payload = self.build_c7_payload(
            release=release,
            isrcs=[t.isrc for t in package.tracks],
            source_package_hash=package.submission_fingerprint,
            lineage=lineage,
            media_package_id=media_package_id,
        )

        return DistributionEngineResult(release=release, payload=c7_payload, is_duplicate=False)

    def reconcile_status(
        self,
        session: Session,
        release_id: str,
        *,
        now: datetime,
    ) -> DistributionEngineResult:
        """Query remote distributor status and reconcile local delivery states."""
        now = require_aware(now)
        release = get_release_by_id(session, release_id)
        if not release:
            raise ValueError(f"Release not found: {release_id}")

        if not release.provider_release_id:
            raise ValueError(f"Release {release_id} has no provider_release_id")

        status_res = self.client.get_release_status(release.provider_release_id)

        update_release_status(
            session,
            release_id,
            status=status_res.status,
            deliveries=status_res.deliveries,
            updated_at=now,
        )

        updated_release = get_release_by_id(session, release_id)
        if not updated_release:
            raise RuntimeError("Failed to retrieve updated release")

        tracks = get_tracks_by_release_id(session, release_id)
        stored_lineage, stored_package_id = get_release_identity(session, release_id)
        c7_payload = self.build_c7_payload(
            release=updated_release,
            isrcs=[t.isrc for t in tracks],
            source_package_hash=updated_release.submission_fingerprint,
            lineage=stored_lineage,
            media_package_id=stored_package_id,
        )

        return DistributionEngineResult(release=updated_release, payload=c7_payload, is_duplicate=False)

    def request_takedown(
        self,
        session: Session,
        release_id: str,
        *,
        now: datetime,
    ) -> DistributionEngineResult:
        """Request takedown of a release from the distributor and mark taken down."""
        now = require_aware(now)
        release = get_release_by_id(session, release_id)
        if not release:
            raise ValueError(f"Release not found: {release_id}")

        if not release.provider_release_id:
            raise ValueError(f"Release {release_id} has no provider_release_id")

        decision = self._require_write(
            CAPABILITY_TAKEDOWN, f"{release.submission_fingerprint}:takedown", now=now
        )
        takedown_res = self.client.request_takedown(release.provider_release_id)
        self.write_guard.settle_external_write(decision, performed=True)

        update_release_status(
            session,
            release_id,
            status=takedown_res.status,
            updated_at=now,
        )

        updated_release = get_release_by_id(session, release_id)
        if not updated_release:
            raise RuntimeError("Failed to retrieve updated release")

        return DistributionEngineResult(release=updated_release, payload=None, is_duplicate=False)

    @staticmethod
    def build_c7_payload(
        release: DistributionRelease,
        isrcs: list[str],
        source_package_hash: str,
        *,
        lineage: ExperimentLineage,
        media_package_id: str,
    ) -> DistributionRecordPayload:
        """Construct the Seam C7 Leg 1 payload carrying all R9 identity fields (P8 §71, Ruling R9, R10)."""
        return DistributionRecordPayload.build(
            release_id=release.release_id,
            artist_id=release.artist_id,
            release_version=release.release_version,
            isrcs=tuple(isrcs),
            provider=release.provider,
            deliveries=tuple(
                {
                    "store": d.store.value,
                    "external_release_id": d.external_release_id,
                    "status": d.status.value,
                    "first_delivered_at": d.first_delivered_at,
                }
                for d in release.deliveries
            ),
            source_package_hash=source_package_hash,
            status=release.status.value,
            lineage=lineage,
            media_package_id=media_package_id,
        )
