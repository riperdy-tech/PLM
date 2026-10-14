"""Seam C7 leg-1 consumer: link each released ISRC to its release and lineage (R9, ADR-0030).

P8's distribution record says which recordings (ISRCs) a release contains and which
experiment lineage it was submitted under. P9 stores one link per (release, ISRC). Revenue
on a statement row can then be attributed to the arm through its ISRC, never through an
identifier P9 made up. Ingesting the same record twice is a no-op; a record that
contradicts a stored link (same release and ISRC, different lineage) is refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from plm_contracts import ErrorShape
from plm_contracts.seams import ExperimentLineage, SeamRejection, parse_seam
from plm_contracts.seams.c7 import DistributionRecordPayload
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..storage.models import ReleaseLinkRow

__all__ = ["ReleaseRecordRejected", "ingest_distribution_record", "lineage_for_isrc"]


class ReleaseRecordRejected(ValueError):
    """A distribution record was refused; ``error`` carries the reason code and fields."""

    def __init__(self, error: ErrorShape) -> None:
        self.error = error
        super().__init__(f"{error.code}: {error.message}")


def ingest_distribution_record(
    session: Session, wire: Mapping[str, Any], *, now: datetime
) -> DistributionRecordPayload:
    """Parse a C7 leg-1 record and store its ISRC links (idempotent)."""
    try:
        record = parse_seam(DistributionRecordPayload, wire)
    except SeamRejection as exc:
        raise ReleaseRecordRejected(exc.error) from exc

    lineage_json = record.lineage.model_dump(mode="json")
    for isrc in record.isrcs:
        existing = session.get(ReleaseLinkRow, (record.release_id, isrc))
        if existing is not None:
            if existing.lineage_json != lineage_json:
                raise ReleaseRecordRejected(
                    ErrorShape(
                        code="lineage_conflict",
                        field_paths=["lineage"],
                        message=f"release {record.release_id!r} ISRC {isrc} is already linked "
                        "to a different lineage",
                    )
                )
            continue
        session.add(
            ReleaseLinkRow(
                release_id=record.release_id,
                isrc=isrc,
                media_package_id=record.media_package_id,
                lineage_json=lineage_json,
                linked_at=now,
            )
        )
    session.flush()
    return record


def lineage_for_isrc(session: Session, isrc: str) -> tuple[str, ExperimentLineage] | None:
    """The (release_id, lineage) an ISRC was released under, or ``None`` if unlinked.

    One recording appears on exactly one release today (P8's ISRC uniqueness); if that
    changes, attribution across releases needs an explicit rule.
    """
    rows = session.execute(select(ReleaseLinkRow).where(ReleaseLinkRow.isrc == isrc)).scalars().all()
    if len(rows) != 1:
        return None
    row = rows[0]
    return row.release_id, ExperimentLineage.model_validate(row.lineage_json)
