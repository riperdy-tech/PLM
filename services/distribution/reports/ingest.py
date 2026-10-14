"""Raw provider report ingestion and versioning for Phase P8 (P8 §51, Ruling R10).

CRITICAL RULING R10 INVARIANT:
P8 stores and versions raw provider statements/reports (raw_report, provider_period,
received_at, report_checksum) and exposes references.
P8 MUST NOT compute revenue. Financial calculations and revenue aggregation belong
strictly to Phase P9 (Analytics & Economics Feedback Engine).
"""

from __future__ import annotations

import logging
from datetime import datetime

from plm_platform.clock import require_aware
from sqlalchemy.orm import Session

from ..domain.ids import compute_sha256
from ..domain.models import DistributionReportReference
from ..storage.repositories import get_provider_report_by_id, insert_provider_report

logger = logging.getLogger(__name__)


def ingest_provider_report(
    session: Session,
    *,
    provider: str,
    provider_period: str,
    raw_content: bytes | str,
    source_reference: str,
    raw_payload_uri: str | None = None,
    report_type: str = "streaming_statement",
    now: datetime,
) -> DistributionReportReference:
    """Ingest and version a raw distributor statement or report.

    Enforces Ruling R10: stores raw statement reference and checksum without
    attempting to parse or compute revenue.
    """
    checksum = compute_sha256(raw_content)
    clean_provider = provider.strip().lower()
    clean_period = provider_period.strip()

    # Deterministic report identifier
    report_id = f"{clean_provider}_{clean_period}_{checksum[:12]}"

    existing = get_provider_report_by_id(session, report_id)
    if existing:
        logger.info("Found existing raw report %s for period %s", report_id, provider_period)
        return existing

    now = require_aware(now)
    resolved_uri = raw_payload_uri or f"reports/raw/{clean_provider}/{clean_period}_{checksum[:8]}.csv"

    ref = DistributionReportReference(
        report_id=report_id,
        provider=clean_provider,
        provider_period=clean_period,
        received_at=now,
        report_checksum=checksum,
        source_reference=source_reference,
        raw_payload_uri=resolved_uri,
        report_type=report_type,
    )

    insert_provider_report(session, ref)
    return ref


def get_report_reference(session: Session, report_id: str) -> DistributionReportReference | None:
    """Retrieve raw provider report reference by ID."""
    return get_provider_report_by_id(session, report_id)
