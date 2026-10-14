"""Distributor report parsing and revenue statement ingestion (Ruling R10, P9 §22, §53, §54).

A statement is ingested in the currency it is denominated in, declared by the caller
(ADR-0031); no currency is assumed. Amounts are parsed straight from the CSV text to
``Decimal``. Each statement line is an incremental amount for the statement period.

A statement with a line that has no revenue amount, an unparseable amount, or a revenue
column in another currency is refused as a whole, before anything is stored.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Any

from plm_contracts import ErrorShape
from plm_contracts.measurement import ObservationKind
from plm_contracts.money import Money, money_sum
from plm_platform.clock import require_aware
from sqlalchemy.orm import Session

from ..domain.amounts import AmountParseError, parse_amount
from ..domain.enums import MetricSource, MetricStatus, ObservationWindow, RevenueStatus
from ..domain.ids import build_observation_id, build_statement_id, compute_sha256
from ..domain.models import RevenueStatement, TrackMetricObservation
from ..storage.repositories import (
    get_revenue_statement_by_hash,
    insert_revenue_statement,
    insert_track_metric,
)
from .releases import lineage_for_isrc

__all__ = ["StatementRejected", "ingest_distributor_statement"]

logger = logging.getLogger(__name__)

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_REVENUE_COLUMN = re.compile(r"^revenue_(?P<currency>[a-z]{3})$")
_REALIZED_STATUSES = frozenset({RevenueStatus.REPORTED, RevenueStatus.FINAL})


class StatementRejected(ValueError):
    """A statement was refused; ``error`` carries the reason code and field paths."""

    def __init__(self, error: ErrorShape) -> None:
        self.error = error
        super().__init__(f"{error.code}: {error.message}")


def _reject(code: str, field: str, message: str) -> StatementRejected:
    return StatementRejected(ErrorShape(code=code, field_paths=[field], message=message))


def _row_revenue(row: dict[str, Any], index: int, currency: str) -> Decimal:
    for key, value in row.items():
        match = _REVENUE_COLUMN.match(str(key))
        if match and match["currency"].upper() != currency and parse_amount(value) is not None:
            raise _reject(
                "currency_mismatch",
                f"rows[{index}].{key}",
                f"row {index} reports revenue in {match['currency'].upper()} but the statement "
                f"is declared in {currency}",
            )
    for key in ("revenue", f"revenue_{currency.lower()}"):
        if key in row:
            try:
                amount = parse_amount(row[key])
            except AmountParseError as exc:
                raise _reject("invalid_amount", f"rows[{index}].{key}", str(exc)) from exc
            if amount is not None:
                return amount
    raise _reject(
        "missing_required_field",
        f"rows[{index}].revenue",
        f"row {index} has no revenue amount; an unreported amount is not zero",
    )


def _row_streams(row: dict[str, Any], index: int) -> int | None:
    raw = row.get("streams")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool) or not isinstance(raw, int | str):
        raise _reject("invalid_count", f"rows[{index}].streams", f"streams must be an integer: {raw!r}")
    try:
        streams = int(raw)
    except ValueError as exc:
        raise _reject("invalid_count", f"rows[{index}].streams", f"streams must be an integer: {raw!r}") from exc
    if streams < 0:
        raise _reject("invalid_count", f"rows[{index}].streams", "streams cannot be negative")
    return streams


def ingest_distributor_statement(
    session: Session,
    *,
    provider: str,
    period: str,
    currency: str,
    raw_content: bytes | str,
    source_reference: str,
    rows: list[dict[str, Any]] | None = None,
    revenue_status: RevenueStatus = RevenueStatus.REPORTED,
    now: datetime,
) -> tuple[RevenueStatement, list[TrackMetricObservation]]:
    """Parse raw distributor statement into canonical facts and archive (Ruling R10, P9 §53, §54).

    ``currency`` is the statement's denomination and is required. Deduplicates by file
    hash so identical statements are never double-counted.
    """
    now = require_aware(now)
    if not _CURRENCY.match(currency or ""):
        raise _reject("invalid_currency", "currency", f"not an ISO 4217 code: {currency!r}")
    if revenue_status not in _REALIZED_STATUSES:
        raise _reject(
            "invalid_revenue_status",
            "revenue_status",
            "a distributor statement is reported or final revenue, never an estimate",
        )
    content_bytes = raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
    file_hash = compute_sha256(content_bytes)

    # 1. Deduplication check
    existing = get_revenue_statement_by_hash(session, file_hash)
    if existing:
        if existing.currency is not None and existing.currency != currency:
            raise _reject(
                "currency_conflict",
                "currency",
                f"this statement was ingested in {existing.currency}, not {currency}",
            )
        logger.info("Found existing revenue statement for hash %s", file_hash)
        return existing, []

    # 2. Parse every line before storing anything
    parsed_rows: list[dict[str, Any]] = []
    if rows is not None:
        parsed_rows = rows
    else:
        text_content = raw_content if isinstance(raw_content, str) else raw_content.decode("utf-8")
        parsed_rows = list(csv.DictReader(io.StringIO(text_content)))

    lines = [
        (row, Money.of(_row_revenue(row, i, currency), currency), _row_streams(row, i))
        for i, row in enumerate(parsed_rows)
    ]

    total_streams = 0
    unattributed: list[Money] = []
    track_observations: list[TrackMetricObservation] = []

    for row, revenue, streams in lines:
        total_streams += streams or 0
        isrc = str(row.get("isrc") or "").strip().upper()
        if not isrc:
            # No recording named: the revenue is real but cannot be attributed (ADR-0030).
            unattributed.append(revenue)
            continue

        link = lineage_for_isrc(session, isrc)
        release_id = row.get("release_id") or (link[0] if link else None)
        track_id = row.get("track_id") or None

        # The id keeps its pre-ADR-0031 shape so replays stay idempotent.
        obs_id = build_observation_id(isrc, "streams", ObservationWindow.LIFETIME.value, period)
        obs = TrackMetricObservation(
            id=obs_id,
            isrc=isrc,
            track_id=track_id,
            release_id=release_id,
            window=None,
            date_str=period,
            observation_kind=ObservationKind.INCREMENTAL,
            streams=streams,
            reported_revenue=revenue,
            provider=provider,
            revenue_status=revenue_status,
            source=MetricSource.DISTRIBUTOR_STATEMENT,
            status=MetricStatus.VALID,
            observed_at=now,
        )
        insert_track_metric(session, obs)
        track_observations.append(obs)

    total = money_sum((revenue for _, revenue, _ in lines), currency=currency)
    stmt_id = build_statement_id(provider, period, file_hash[:12])
    statement = RevenueStatement(
        id=stmt_id,
        provider=provider,
        period=period,
        currency=currency,
        total_revenue_amount=total.amount,
        total_streams=total_streams,
        file_hash=file_hash,
        source_reference=source_reference,
        received_at=now,
        unattributed_revenue_amount=money_sum(unattributed, currency=currency).amount,
        unattributed_row_count=len(unattributed),
    )
    insert_revenue_statement(session, statement)

    return statement, track_observations
