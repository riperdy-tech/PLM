"""Atomic pre-render job claim and fenced terminal transitions (B02 partial).

P6 HTTP and CLI assembly now commit a QUEUED job before rendering and
commit a worker claim before starting FFmpeg. They may persist the final
package only after fenced completion. Direct in-process assembly callers
without a render session factory retain legacy insert-after-render behavior.
These SQL operations do not implement heartbeat, timeouts, reclaim or blob
reconciliation and must not be mistaken for a complete durable worker.

Quarantine and retry staging are gated on persisted recovery evidence
(ADR-0043), never on caller-supplied booleans. Evidence binds to the exact
claimed execution (job, worker, start, input SHA256) in the same SQL
statement that inserts it, so it cannot be recorded for another run.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..domain.enums import RenderJobStatus
from ..domain.ids import deterministic_id, render_job_id
from ..domain.models import RenderJob
from .models import media_packages_table, render_jobs_table, render_recovery_evidence_table
from .repositories import get_render_job

__all__ = [
    "RecoveryEvidenceKind",
    "RenderJobSummary",
    "claim_queued_render_job",
    "finish_claimed_render_job",
    "list_render_job_summaries",
    "list_stale_render_job_candidates",
    "quarantine_stale_render_job",
    "queue_reconciled_render_retry",
    "queue_render_job",
    "record_render_recovery_evidence",
    "render_claim_namespace",
]


class RecoveryEvidenceKind(str, Enum):
    """What a persisted recovery record attests about one claimed execution."""

    WORKER_TERMINATION = "worker_termination"
    ORPHAN_ARTIFACTS = "orphan_artifacts"


# Accepted methods per kind. `owner_exit_observed` is written only by the claim
# owner after its own render call returned or raised (subprocess.run has
# already reaped FFmpeg); it cannot describe a killed process. Every other
# method needs an external evidence reference. A listing that was truncated is
# not an inventory.
_EVIDENCE_METHODS: dict[RecoveryEvidenceKind, frozenset[str]] = {
    RecoveryEvidenceKind.WORKER_TERMINATION: frozenset(
        {"owner_exit_observed", "supervisor_attestation"}),
    RecoveryEvidenceKind.ORPHAN_ARTIFACTS: frozenset(
        {"claim_namespace_inventory", "supervisor_attestation"}),
}
_EVIDENCE_JOB_STATUSES: dict[RecoveryEvidenceKind, tuple[str, ...]] = {
    # A running owner can still be proven stopped; its outputs cannot be
    # inventoried as orphans until the claim is fenced by quarantine.
    RecoveryEvidenceKind.WORKER_TERMINATION: (
        RenderJobStatus.RUNNING.value, RenderJobStatus.QUARANTINED.value),
    RecoveryEvidenceKind.ORPHAN_ARTIFACTS: (RenderJobStatus.QUARANTINED.value,),
}


@dataclass(frozen=True, slots=True)
class RenderJobSummary:
    """Read-only persisted state, NOT proof a worker is currently alive."""

    job_id: str
    status: RenderJobStatus
    render_profile: str
    attempt_count: int
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None


def list_render_job_summaries(
    session: Session, *, media_package_id: str, limit: int = 20,
) -> tuple[RenderJobSummary, ...]:
    """Bounded read-only job history for one exact package; no health inference."""
    _required(media_package_id, "media_package_id")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("render history limit must be within 1..100")
    jobs = render_jobs_table.c
    rows = session.execute(
        sa.select(
            jobs.id, jobs.job_status, jobs.render_profile,
            jobs.attempt_count, jobs.started_at, jobs.finished_at,
            jobs.error_code,
        ).where(
            jobs.media_package_id == media_package_id,
        ).order_by(jobs.created_at.desc(), jobs.id.desc()).limit(limit)
    ).mappings()
    return tuple(
        RenderJobSummary(
            job_id=row["id"], status=RenderJobStatus(row["job_status"]),
            render_profile=row["render_profile"], attempt_count=row["attempt_count"],
            started_at=row["started_at"], finished_at=row["finished_at"],
            error_code=row["error_code"],
        )
        for row in rows
    )


def _required(value: str, label: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{label} must not be empty")
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("render job timestamp must include a timezone")
    return value


def render_claim_namespace(*, job_id: str, worker_id: str,
                           input_manifest_hash: str, started_at: datetime) -> str:
    """Derive an isolated blob prefix from the durable original claim identity.

    A recovery reviewer can recompute it from the committed RUNNING row; no
    secret or process-local state is required. Timestamps are normalized to
    UTC, and JSON boundaries prevent ambiguous concatenation collisions.
    It does not grant permission to reclaim or delete any output.
    """
    _required(job_id, "job_id")
    _required(worker_id, "worker_id")
    if re.fullmatch(r"[0-9a-fA-F]{64}", input_manifest_hash) is None:
        raise ValueError("render claim namespace requires input SHA256")
    instant = _aware(started_at).astimezone(UTC).isoformat()
    identity = json.dumps(
        [job_id, worker_id, input_manifest_hash.lower(), instant],
        separators=(",", ":"), ensure_ascii=True,
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def queue_render_job(session: Session, job: RenderJob) -> bool:
    """Record one pre-render intent. Caller commits before any FFmpeg work."""
    if job.job_status is not RenderJobStatus.QUEUED:
        raise ValueError("only queued jobs may be registered")
    if job.worker_id or job.started_at or job.finished_at or job.output_checksum:
        raise ValueError("queued job has prior execution fields")
    if job.error_code or job.error_detail:
        raise ValueError("queued job has failure fields")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", job.input_manifest_hash):
        raise ValueError("queued job needs input SHA256")
    if job.attempt_count < 1 or job.id != render_job_id(
        job.media_package_id, job.render_profile, job.attempt_count,
    ):
        raise ValueError("render ID and identity disagree")
    if job.attempt_count != 1:
        raise ValueError("retry attempts require reconciled retry staging")
    _required(job.media_package_id, "media_package_id")
    _required(job.render_profile, "render_profile")
    _required(job.render_version, "render_version")
    _aware(job.created_at)
    # Do not use a nested SAVEPOINT to absorb a duplicate. SQLite's legacy
    # transaction mode can commit that savepoint even when the outer session
    # is later rolled back, creating a phantom durable render intent.
    values = {column.name: getattr(job, column.name) for column in render_jobs_table.columns}
    values["job_status"] = job.job_status.value
    bind = session.get_bind()
    if bind.dialect.name not in {"postgresql", "sqlite"}:
        raise RuntimeError("render intent idempotency requires PostgreSQL or SQLite")
    statement = (
        pg_insert(render_jobs_table).values(**values)
        if bind.dialect.name == "postgresql"
        else sqlite_insert(render_jobs_table).values(**values)
    )
    inserted = session.execute(
        statement.on_conflict_do_nothing(index_elements=[render_jobs_table.c.id])
        .returning(render_jobs_table.c.id)
    ).scalar_one_or_none()
    if inserted is not None:
        return True
    prior = get_render_job(session, job.id)
    if prior is None:
        raise RuntimeError("render intent conflict without an existing job")
    fields = ("media_package_id", "render_profile", "render_version",
              "priority", "attempt_count", "input_manifest_hash")
    if any(getattr(prior, name) != getattr(job, name) for name in fields):
        raise ValueError("render intent conflicts with prior job")
    return False


def claim_queued_render_job(
    session: Session, *, job_id: str, worker_id: str,
    input_manifest_hash: str, started_at: datetime,
) -> bool:
    """Atomically claim an already committed QUEUED job once.

    No implicit insert or commit. The identity binds job ID, exact input
    manifest, and a non-empty worker ID. A second worker gets False. A
    currently running or completed job cannot be claimed again here.
    """
    _required(job_id, "job_id")
    _required(worker_id, "worker_id")
    _required(input_manifest_hash, "input_manifest_hash")
    _aware(started_at)
    jobs = render_jobs_table.c
    prior = render_jobs_table.alias("prior_render_attempt").c
    later = render_jobs_table.alias("later_render_attempt").c
    packages = media_packages_table.c
    claim = (
        sa.update(render_jobs_table)
        .where(
            jobs.id == job_id,
            jobs.input_manifest_hash == input_manifest_hash,
            jobs.job_status == RenderJobStatus.QUEUED.value,
            jobs.worker_id.is_(None),
            # Recheck at claim time; a queued job is not perpetual authority.
            ~sa.exists(sa.select(sa.literal(1)).where(
                packages.media_package_id == jobs.media_package_id,
            )),
            ~sa.exists(sa.select(sa.literal(1)).where(
                later.media_package_id == jobs.media_package_id,
                later.render_profile == jobs.render_profile,
                later.attempt_count > jobs.attempt_count,
            )),
            sa.or_(
                jobs.attempt_count == 1,
                sa.exists(sa.select(sa.literal(1)).where(
                    prior.media_package_id == jobs.media_package_id,
                    prior.render_profile == jobs.render_profile,
                    prior.attempt_count == jobs.attempt_count - 1,
                    prior.job_status == RenderJobStatus.QUARANTINED.value,
                    prior.error_code == "render_run_stale",
                    prior.finished_at.is_not(None),
                    prior.input_manifest_hash == jobs.input_manifest_hash,
                    prior.render_version == jobs.render_version,
                )),
            ),
        )
        .values(
            job_status=RenderJobStatus.RUNNING.value,
            worker_id=worker_id,
            started_at=started_at,
        )
        .returning(jobs.id)
    )
    return session.execute(claim).scalar_one_or_none() is not None


def finish_claimed_render_job(
    session: Session, *, job_id: str, worker_id: str,
    input_manifest_hash: str, started_at: datetime, finished_at: datetime,
    status: RenderJobStatus, output_checksum: str | None = None,
    error_code: str | None = None, error_detail: str | None = None,
) -> bool:
    """Complete/fail only the same RUNNING job claimed by this worker.

    Worker plus the original started_at form a fencing identity. Even a
    restarted process reusing its worker name cannot finalize another claim.
    The caller must verify outputs and commit before returning success.
    """
    _required(job_id, "job_id")
    _required(worker_id, "worker_id")
    _required(input_manifest_hash, "input_manifest_hash")
    _aware(started_at)
    _aware(finished_at)
    if finished_at < started_at:
        raise ValueError("finished_at cannot predate started_at")
    if status not in {RenderJobStatus.COMPLETED, RenderJobStatus.FAILED,
                      RenderJobStatus.QUARANTINED}:
        raise ValueError("only terminal render job statuses may be finalized")
    if status is RenderJobStatus.COMPLETED:
        if output_checksum is None or re.fullmatch(r"[0-9a-fA-F]{64}", output_checksum) is None:
            raise ValueError("completed render requires a SHA256 output checksum")
    elif not error_code or not error_code.strip():
        raise ValueError("non-completed render requires an error_code")

    jobs = render_jobs_table.c
    later = render_jobs_table.alias("later_render_attempt").c
    packages = media_packages_table.c
    finish = (
        sa.update(render_jobs_table)
        .where(
            jobs.id == job_id,
            jobs.input_manifest_hash == input_manifest_hash,
            jobs.job_status == RenderJobStatus.RUNNING.value,
            jobs.worker_id == worker_id,
            jobs.started_at == started_at,
            # Prevent stale completion when another attempt or package
            # has appeared since the original owner claim was committed.
            ~sa.exists(sa.select(sa.literal(1)).where(
                later.media_package_id == jobs.media_package_id,
                later.render_profile == jobs.render_profile,
                later.attempt_count > jobs.attempt_count,
            )),
            ~sa.exists(sa.select(sa.literal(1)).where(
                packages.media_package_id == jobs.media_package_id,
            )),
        )
        .values(
            job_status=status.value, finished_at=finished_at,
            output_checksum=output_checksum if status is RenderJobStatus.COMPLETED else None,
            error_code=error_code if status is not RenderJobStatus.COMPLETED else None,
            error_detail=error_detail if status is not RenderJobStatus.COMPLETED else None,
        )
        .returning(jobs.id)
    )
    return session.execute(finish).scalar_one_or_none() is not None


def _evidence_id(job_id: str, kind: RecoveryEvidenceKind) -> str:
    return deterministic_id("rrev", job_id, kind.value)


def record_render_recovery_evidence(
    session: Session, *, job_id: str, kind: RecoveryEvidenceKind, method: str,
    worker_id: str, started_at: datetime, input_manifest_hash: str,
    recorded_by: str, recorded_at: datetime, detail: Mapping[str, Any],
    evidence_ref: str | None = None,
) -> bool:
    """Append one recovery fact for an exact claimed execution; never update.

    The INSERT selects from ``render_jobs`` with the full claim identity, so a
    record cannot be written for an unclaimed, completed or different run. A
    byte-identical replay returns False; any other second record of the same
    kind is a conflict. The record is a fact for the caller to commit with the
    transition it supports; it does not itself change the job.
    """
    _required(job_id, "job_id")
    _required(worker_id, "worker_id")
    _required(recorded_by, "recorded_by")
    _aware(started_at)
    _aware(recorded_at)
    if method not in _EVIDENCE_METHODS[RecoveryEvidenceKind(kind)]:
        raise ValueError(f"method {method!r} cannot attest {kind.value}")
    if re.fullmatch(r"[0-9a-fA-F]{64}", input_manifest_hash) is None:
        raise ValueError("recovery evidence needs input SHA256")
    reference = evidence_ref.strip() if evidence_ref is not None else None
    if method != "owner_exit_observed" and not reference:
        raise ValueError(f"{method} requires an evidence reference")
    if method == "claim_namespace_inventory" and (
        detail.get("truncated") is not False
        or isinstance(detail.get("object_count"), bool)
        or not isinstance(detail.get("object_count"), int)
        or detail["object_count"] < 0
    ):
        raise ValueError("an inventory needs a complete, counted listing")
    try:
        detail_json = json.loads(json.dumps(dict(detail), allow_nan=False, sort_keys=True))
    except (TypeError, ValueError) as exc:
        raise ValueError("recovery evidence detail must be strict JSON") from exc

    evidence_id = _evidence_id(job_id, kind)
    jobs = render_jobs_table.c
    values: dict[str, sa.ColumnElement[Any]] = {
        "id": sa.literal(evidence_id),
        "render_job_id": jobs.id,
        "evidence_kind": sa.literal(kind.value),
        "method": sa.literal(method),
        "worker_id": jobs.worker_id,
        "started_at": jobs.started_at,
        "input_manifest_hash": jobs.input_manifest_hash,
        "evidence_ref": sa.literal(reference, type_=sa.Text),
        "detail_json": sa.literal(detail_json, type_=sa.JSON),
        "recorded_by": sa.literal(recorded_by),
        "recorded_at": sa.literal(recorded_at, type_=sa.DateTime(timezone=True)),
    }
    claimed = sa.select(*values.values()).where(
        jobs.id == job_id,
        jobs.worker_id == worker_id,
        jobs.started_at == started_at,
        jobs.input_manifest_hash == input_manifest_hash,
        jobs.job_status.in_(_EVIDENCE_JOB_STATUSES[kind]),
    )
    inserted = session.execute(
        _dialect_insert(session, render_recovery_evidence_table)
        .from_select(list(values), claimed)
        .on_conflict_do_nothing()
        .returning(render_recovery_evidence_table.c.id)
    ).scalar_one_or_none()
    if inserted is not None:
        return True
    evidence = render_recovery_evidence_table.c
    prior = session.execute(
        sa.select(evidence.method, evidence.worker_id, evidence.input_manifest_hash,
                  evidence.evidence_ref, evidence.detail_json)
        .where(evidence.render_job_id == job_id, evidence.evidence_kind == kind.value)
    ).one_or_none()
    if prior is None:
        raise ValueError("recovery evidence does not match a claimed execution")
    if (prior.method, prior.worker_id, prior.input_manifest_hash, prior.evidence_ref,
            prior.detail_json) != (method, worker_id, input_manifest_hash, reference,
                                   detail_json):
        raise ValueError(f"{kind.value} evidence already recorded differently")
    return False


def _dialect_insert(session: Session, table: sa.Table) -> Any:
    name = session.get_bind().dialect.name
    if name == "postgresql":
        return pg_insert(table)
    if name == "sqlite":
        return sqlite_insert(table)
    raise RuntimeError("render claims require PostgreSQL or SQLite")


def _has_evidence(kind: RecoveryEvidenceKind, job: Any) -> sa.Exists:
    """SQL fact: a record of ``kind`` exists for this row's exact claim."""
    evidence = render_recovery_evidence_table.c
    return sa.exists(sa.select(sa.literal(1)).where(
        evidence.render_job_id == job.id,
        evidence.evidence_kind == kind.value,
        evidence.worker_id == job.worker_id,
        evidence.started_at == job.started_at,
        evidence.input_manifest_hash == job.input_manifest_hash,
    ))


def quarantine_stale_render_job(
    session: Session, *, job_id: str, input_manifest_hash: str,
    started_before: datetime, quarantined_at: datetime,
    expected_worker_id: str, expected_started_at: datetime,
) -> bool:
    """Fence a RUNNING owner whose termination is on record.

    Old age alone is never sufficient evidence that FFmpeg has stopped. The
    UPDATE requires a persisted ``worker_termination`` record for this exact
    worker/start/input identity (see ``record_render_recovery_evidence``); it
    cannot itself check liveness or that blobs are safe to reclaim. A
    mismatch, missing evidence or racing completion leaves state unchanged.
    """
    _required(job_id, "job_id")
    _required(expected_worker_id, "expected_worker_id")
    if re.fullmatch(r"[0-9a-fA-F]{64}", input_manifest_hash) is None:
        raise ValueError("stale render needs input SHA256")
    _aware(expected_started_at)
    _aware(started_before)
    _aware(quarantined_at)
    if started_before > quarantined_at:
        raise ValueError("staleness cutoff cannot be in the future")

    jobs = render_jobs_table.c
    quarantine = (
        sa.update(render_jobs_table)
        .where(
            jobs.id == job_id,
            jobs.input_manifest_hash == input_manifest_hash,
            jobs.job_status == RenderJobStatus.RUNNING.value,
            jobs.worker_id == expected_worker_id,
            jobs.started_at == expected_started_at,
            jobs.started_at <= started_before,
            _has_evidence(RecoveryEvidenceKind.WORKER_TERMINATION, jobs),
        )
        .values(
            job_status=RenderJobStatus.QUARANTINED.value,
            finished_at=quarantined_at,
            output_checksum=None,
            error_code="render_run_stale",
            error_detail="Render may have produced blobs; reconcile before retry.",
        )
        .returning(jobs.id)
    )
    return session.execute(quarantine).scalar_one_or_none() is not None


def queue_reconciled_render_retry(
    session: Session, *, previous_job_id: str, expected_worker_id: str,
    expected_started_at: datetime, input_manifest_hash: str,
    now: datetime, max_attempts: int = 3,
) -> RenderJob | None:
    """Atomically stage *only a QUEUED intent* after a fenced stale quarantine.

    This is a supervisor-only primitive, not an HTTP retry or worker dispatcher.
    It requires persisted ``worker_termination`` and ``orphan_artifacts``
    records for the predecessor's exact claim (ADR-0043). Source inputs are
    re-verified where it matters: the executing assembly recomputes the input
    SHA256 and refuses a staged attempt that no longer matches it.
    No FFmpeg, blob deletion, external writes or package publication occur.

    The conditional INSERT SELECT rechecks all facts in SQL, including absence
    of any newer attempt or published package, to fence racing supervisors.
    The deterministic next job ID is unique across concurrent transactions.
    """
    _required(previous_job_id, "previous_job_id")
    _required(expected_worker_id, "expected_worker_id")
    _aware(expected_started_at)
    _aware(now)
    if re.fullmatch(r"[0-9a-fA-F]{64}", input_manifest_hash) is None:
        raise ValueError("retry requires original input SHA256")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 2 <= max_attempts <= 10:
        raise ValueError("max_attempts must be within 2..10")

    previous = get_render_job(session, previous_job_id)
    if previous is None or previous.attempt_count >= max_attempts:
        return None
    next_job = RenderJob.create(
        previous.media_package_id, previous.render_profile,
        render_version=previous.render_version, priority=previous.priority,
        input_manifest_hash=previous.input_manifest_hash,
        attempt=previous.attempt_count + 1, now=now,
    )
    jobs = render_jobs_table.c
    predecessor = render_jobs_table.alias("predecessor").c
    later = render_jobs_table.alias("later_attempt").c
    package = media_packages_table.c
    # Keyed by column name so the INSERT column list and SELECT list cannot
    # drift apart if the table gains or reorders columns.
    staged: dict[str, sa.ColumnElement[Any]] = {
        "id": sa.literal(next_job.id),
        "media_package_id": predecessor.media_package_id,
        "render_profile": predecessor.render_profile,
        "render_version": predecessor.render_version,
        "job_status": sa.literal(RenderJobStatus.QUEUED.value),
        "priority": predecessor.priority,
        "attempt_count": sa.literal(next_job.attempt_count),
        "input_manifest_hash": predecessor.input_manifest_hash,
        "created_at": sa.literal(now, type_=sa.DateTime(timezone=True)),
    }
    eligible = sa.select(*staged.values()).where(
        predecessor.id == previous_job_id,
        predecessor.media_package_id == previous.media_package_id,
        predecessor.render_profile == previous.render_profile,
        predecessor.attempt_count == previous.attempt_count,
        predecessor.job_status == RenderJobStatus.QUARANTINED.value,
        predecessor.error_code == "render_run_stale",
        predecessor.worker_id == expected_worker_id,
        predecessor.started_at == expected_started_at,
        predecessor.finished_at.is_not(None),
        predecessor.finished_at <= now,
        predecessor.input_manifest_hash == input_manifest_hash,
        ~sa.exists(sa.select(sa.literal(1)).where(
            package.media_package_id == predecessor.media_package_id,
        )),
        ~sa.exists(sa.select(sa.literal(1)).where(
            later.media_package_id == predecessor.media_package_id,
            later.render_profile == predecessor.render_profile,
            later.attempt_count > predecessor.attempt_count,
        )),
        _has_evidence(RecoveryEvidenceKind.WORKER_TERMINATION, predecessor),
        _has_evidence(RecoveryEvidenceKind.ORPHAN_ARTIFACTS, predecessor),
    )
    inserted = session.execute(
        _dialect_insert(session, render_jobs_table)
        .from_select(list(staged), eligible)
        .on_conflict_do_nothing(index_elements=[jobs.id])
        .returning(jobs.id)
    ).scalar_one_or_none()
    return next_job if inserted is not None else None


def list_stale_render_job_candidates(
    session: Session, *, started_before: datetime, limit: int = 100,
) -> list[str]:
    """List old RUNNING claims for review; age alone is not proof of failure.

    This is a bounded read-only candidate scan, not a lease-expiry decision.
    A supervisor must check worker liveness and blob state before calling
    quarantine_stale_render_job. Always recheck with its atomic UPDATE.
    """
    _aware(started_before)
    if isinstance(limit, bool) or not 1 <= limit <= 500:
        raise ValueError("stale render scan limit must be within 1..500")
    jobs = render_jobs_table.c
    query = (
        sa.select(jobs.id)
        .where(
            jobs.job_status == RenderJobStatus.RUNNING.value,
            jobs.worker_id.is_not(None),
            jobs.started_at.is_not(None),
            jobs.started_at <= started_before,
        )
        .order_by(jobs.started_at, jobs.id)
        .limit(limit)
    )
    return list(session.execute(query).scalars())
