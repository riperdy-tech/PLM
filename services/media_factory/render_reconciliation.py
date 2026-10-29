"""Read-only, bounded B02 render-artifact evidence inventory, NOT recovery.

The DB still cannot prove that a worker has terminated, and an object listing
cannot establish bytes/rights/QA or authorize cleanup. In particular a crashed
render can have no media package row; the operator brand hint is then UNKNOWN.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from .artifacts import ClaimObjectPage, MinioArtifactStore
from .domain.enums import RenderJobStatus
from .storage.render_claims import (
    RecoveryEvidenceKind,
    record_render_recovery_evidence,
    render_claim_namespace,
)
from .storage.repositories import get_media_package_by_id, get_render_job

__all__ = ["RenderClaimInspection", "inspect_render_claim", "record_claim_inventory"]


@dataclass(frozen=True, slots=True)
class RenderClaimInspection:
    job_id: str
    job_status: RenderJobStatus
    claim_namespace: str | None
    evidence: ClaimObjectPage | None
    brand_source: str
    reason: str


def inspect_render_claim(
    session: Session, store: MinioArtifactStore, *, job_id: str,
    brand_id: str, limit: int = 20,
) -> RenderClaimInspection:
    """List object *names* under an exact persisted claim; never change state.

    A missing package cannot authenticate a supplied brand ID. Empty results
    under that hint do not prove absence of orphan artifacts. A naive SQLite
    timestamp is rejected rather than silently being called UTC.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("render inspection limit must be within 1..100")
    if not brand_id or not brand_id.strip() or "/" in brand_id or "\\" in brand_id:
        raise ValueError("brand_id must be one exact nonempty segment")
    if not job_id or not job_id.strip():
        raise ValueError("job_id must not be empty")
    job = get_render_job(session, job_id)
    if job is None:
        raise ValueError("render job not found")
    pkg = get_media_package_by_id(session, job.media_package_id)
    if pkg is not None and pkg.brand_id != brand_id:
        raise ValueError("brand ID conflicts with the persisted media package")
    source = "persisted_package" if pkg is not None else "unverified_operator_hint"

    if not job.worker_id or not job.worker_id.strip() or job.started_at is None:
        return RenderClaimInspection(
            job.id, job.job_status, None, None, source,
            "no_committed_execution_identity",
        )
    if job.started_at.tzinfo is None or job.started_at.utcoffset() is None:
        return RenderClaimInspection(
            job.id, job.job_status, None, None, source,
            "execution_timezone_unknown",
        )
    namespace = render_claim_namespace(
        job_id=job.id, worker_id=job.worker_id,
        input_manifest_hash=job.input_manifest_hash, started_at=job.started_at,
    )
    if pkg is not None:
        persisted = pkg.render_report.get("artifact_claim_namespace")
        if persisted is not None and persisted != namespace:
            return RenderClaimInspection(
                job.id, job.job_status, namespace, None, source,
                "stored_claim_namespace_conflict",
            )
    page = store.list_claim_objects(
        brand_id=brand_id, media_package_id=job.media_package_id,
        claim_namespace=namespace, limit=limit,
    )
    return RenderClaimInspection(
        job.id, job.job_status, namespace, page, source,
        "metadata_only_unverified_bytes_and_worker_liveness",
    )


def record_claim_inventory(
    session: Session, inspection: RenderClaimInspection, *,
    recorded_by: str, recorded_at: datetime,
) -> bool:
    """Persist a complete claim listing as ``orphan_artifacts`` evidence (ADR-0043).

    Only a non-truncated listing of a QUARANTINED claim qualifies. The record
    keeps the object names and the brand source exactly as listed: key hashes
    are not byte verification, and an operator brand hint stays unverified.
    The caller commits; nothing is deleted or retried here.
    """
    if inspection.evidence is None or inspection.claim_namespace is None:
        raise ValueError(f"no claim listing to record ({inspection.reason})")
    if inspection.evidence.truncated:
        raise ValueError("a truncated listing is not an inventory; raise --limit")
    if inspection.job_status is not RenderJobStatus.QUARANTINED:
        # A live claim can still write after the listing was taken.
        raise ValueError("only a listing taken after the claim was quarantined is an inventory")
    if not inspection.evidence.objects and inspection.brand_source != "persisted_package":
        # Under an unverified brand hint an empty listing cannot tell "no
        # orphans" from "wrong prefix". A non-empty one proves the prefix, since
        # the claim namespace is derived from this execution's identity.
        raise ValueError("an empty listing under an unverified brand hint is not an inventory")
    job = get_render_job(session, inspection.job_id)
    if job is None or job.worker_id is None or job.started_at is None:
        raise ValueError("render job has no committed execution identity")
    objects = [
        {"uri": item.uri, "pinned_sha256": item.pinned_sha256, "size_bytes": item.size_bytes}
        for item in inspection.evidence.objects
    ]
    return record_render_recovery_evidence(
        session, job_id=job.id, kind=RecoveryEvidenceKind.ORPHAN_ARTIFACTS,
        method="claim_namespace_inventory", worker_id=job.worker_id,
        started_at=job.started_at, input_manifest_hash=job.input_manifest_hash,
        recorded_by=recorded_by, recorded_at=recorded_at,
        evidence_ref=f"render-inspect:{inspection.claim_namespace}",
        detail={
            "claim_namespace": inspection.claim_namespace,
            "brand_source": inspection.brand_source,
            "object_count": len(objects),
            "objects": objects,
            "truncated": False,
            "bytes_verified": False,
        },
    )
