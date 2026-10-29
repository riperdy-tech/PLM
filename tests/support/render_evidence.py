"""Test-only recovery evidence records for B02 (ADR-0043).

These simulate what a supervisor would persist; they prove the SQL gates, not
that any real worker stopped or that any real object store was inventoried.
"""
from __future__ import annotations

from datetime import datetime

from media_factory.storage.render_claims import (
    RecoveryEvidenceKind,
    record_render_recovery_evidence,
)
from sqlalchemy.orm import Session

__all__ = ["attest_inventory", "attest_termination"]


def attest_termination(session: Session, job_id: str, *, worker_id: str,
                       started_at: datetime, sha: str, at: datetime) -> bool:
    return record_render_recovery_evidence(
        session, job_id=job_id, kind=RecoveryEvidenceKind.WORKER_TERMINATION,
        method="supervisor_attestation", worker_id=worker_id,
        started_at=started_at, input_manifest_hash=sha,
        recorded_by="test-supervisor", recorded_at=at,
        detail={"simulated": True}, evidence_ref="test://simulated-termination",
    )


def attest_inventory(session: Session, job_id: str, *, worker_id: str,
                     started_at: datetime, sha: str, at: datetime) -> bool:
    return record_render_recovery_evidence(
        session, job_id=job_id, kind=RecoveryEvidenceKind.ORPHAN_ARTIFACTS,
        method="claim_namespace_inventory", worker_id=worker_id,
        started_at=started_at, input_manifest_hash=sha,
        recorded_by="test-supervisor", recorded_at=at,
        detail={"object_count": 0, "truncated": False, "simulated": True},
        evidence_ref="test://simulated-inventory",
    )
