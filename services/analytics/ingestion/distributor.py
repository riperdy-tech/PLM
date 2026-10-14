"""Distributor report parsing and revenue statement ingestion (Ruling R10, P9 §53, §54)."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Any

from plm_platform.clock import require_aware
from sqlalchemy.orm import Session

from ..domain.enums import MetricSource, MetricStatus, ObservationWindow, RevenueStatus
from ..domain.ids import build_observation_id, build_statement_id, compute_sha256
from ..domain.models import RevenueStatement, TrackMetricObservation
from ..storage.repositories import (
    get_revenue_statement_by_hash,
    insert_revenue_statement,
    insert_track_metric,
)
from .releases import lineage_for_isrc

logger = logging.getLogger(__name__)


def ingest_distributor_statement(
    session: Session,
    *,
    provider: str,
    period: str,
    raw_content: bytes | str,
    source_reference: str,
    rows: list[dict[str, Any]] | None = None,
    now: datetime,
) -> tuple[RevenueStatement, list[TrackMetricObservation]]:
    """Parse raw distributor statement into canonical facts and archive (Ruling R10, P9 §53, §54).

    Deduplicates by file hash so identical statements are never double-counted.
    """
    now = require_aware(now)
    content_bytes = raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
    file_hash = compute_sha256(content_bytes)

    # 1. Deduplication check
    existing = get_revenue_statement_by_hash(session, file_hash)
    if existing:
        logger.info("Found existing revenue statement for hash %s", file_hash)
        return existing, []

    # 2. Parse statement records
    parsed_rows: list[dict[str, Any]] = []
    if rows is not None:
        parsed_rows = rows
    else:
        # Simple CSV parser
        text_content = raw_content if isinstance(raw_content, str) else raw_content.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text_content))
        for r in reader:
            parsed_rows.append(r)

    total_revenue = 0.0
    total_streams = 0
    unattributed_revenue = 0.0
    unattributed_rows = 0
    track_observations: list[TrackMetricObservation] = []

    for r in parsed_rows:
        streams = int(r.get("streams") or 0)
        revenue = float(r.get("revenue_usd", r.get("revenue")) or 0.0)
        total_streams += streams
        total_revenue += revenue

        isrc = (r.get("isrc") or "").strip().upper()
        if not isrc:
            # No recording named: the revenue is real but cannot be attributed (ADR-0030).
            unattributed_revenue += revenue
            unattributed_rows += 1
            continue

        link = lineage_for_isrc(session, isrc)
        release_id = r.get("release_id") or (link[0] if link else None)
        track_id = r.get("track_id") or None

        obs_id = build_observation_id(isrc, "streams", ObservationWindow.LIFETIME.value, period)
        obs = TrackMetricObservation(
            id=obs_id,
            isrc=isrc,
            track_id=track_id,
            release_id=release_id,
            window=ObservationWindow.LIFETIME,
            date_str=period,
            streams=streams,
            reported_revenue_usd=revenue,
            provider=provider,
            revenue_status=RevenueStatus.REPORTED,
            source=MetricSource.DISTRIBUTOR_STATEMENT,
            status=MetricStatus.VALID,
            observed_at=now,
        )
        insert_track_metric(session, obs)
        track_observations.append(obs)

    stmt_id = build_statement_id(provider, period, file_hash[:12])
    statement = RevenueStatement(
        id=stmt_id,
        provider=provider,
        period=period,
        total_revenue_usd=round(total_revenue, 4),
        total_streams=total_streams,
        file_hash=file_hash,
        source_reference=source_reference,
        received_at=now,
        unattributed_revenue_usd=round(unattributed_revenue, 4),
        unattributed_row_count=unattributed_rows,
    )
    insert_revenue_statement(session, statement)

    return statement, track_observations
