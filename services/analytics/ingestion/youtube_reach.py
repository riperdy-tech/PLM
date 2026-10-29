"""Strict offline parser for official YouTube Reporting API channel reach CSV.

Unlike Analytics reports.query, Reporting API channel_reach_basic_a1 supplies
video_thumbnail_impressions and video_thumbnail_impressions_ctr. Reports are
daily snapshots, so never sum overlapping report revisions or average CTRs
without weighing by impressions. The official raw unit must be declared by
the caller because CSV formatting and exports may differ.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

CtrUnit = Literal["ratio", "percent"]


class ReachReportError(ValueError):
    """A reach report is not suitable as analytics evidence."""


@dataclass(frozen=True, slots=True)
class ReachRow:
    date: date
    channel_id: str
    video_id: str
    impressions: int
    ctr_ratio: Decimal


@dataclass(frozen=True, slots=True)
class ReachSummary:
    video_id: str
    start_date: date
    end_date: date
    observed_days: int
    window_days: int
    impressions: int
    impression_weighted_ctr_pct: Decimal | None


_REQUIRED = frozenset({
    "date", "channel_id", "video_id",
    "video_thumbnail_impressions", "video_thumbnail_impressions_ctr",
})


def parse_reach_csv(content: str, *, ctr_unit: CtrUnit) -> tuple[ReachRow, ...]:
    """Validate an unmodified reach-basic CSV, rejecting ambiguous/missing facts."""
    if ctr_unit not in ("ratio", "percent"):
        raise ReachReportError("CTR unit must be ratio or percent (explicit declaration)")
    reader = csv.DictReader(io.StringIO(content, newline=""))
    if reader.fieldnames is None or not _REQUIRED.issubset(reader.fieldnames):
        raise ReachReportError("not a channel_reach_basic_a1 CSV: required columns missing")
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ReachReportError("duplicate column name")
    seen: set[tuple[date, str, str]] = set()
    results: list[ReachRow] = []
    for line, raw in enumerate(reader, 2):
        if None in raw or any(raw.get(field) is None for field in _REQUIRED):
            raise ReachReportError(f"row {line}: invalid CSV column count")
        try:
            when = date.fromisoformat(raw["date"].strip())
            channel_id = raw["channel_id"].strip()
            video_id = raw["video_id"].strip()
            impression_str = raw["video_thumbnail_impressions"].strip()
            ctr_str = raw["video_thumbnail_impressions_ctr"].strip()
            if not impression_str.isdecimal() or not channel_id or not video_id:
                raise ValueError("invalid impression count or identity")
            impressions = int(impression_str)
            ctr_raw = Decimal(ctr_str)
            fraction = ctr_raw if ctr_unit == "ratio" else ctr_raw / 100
        except (ValueError, InvalidOperation, AttributeError) as exc:
            raise ReachReportError(f"row {line}: invalid reach measurement") from exc
        if not fraction.is_finite() or not 0 <= fraction <= 1:
            raise ReachReportError(f"row {line}: CTR is outside declared unit range")
        if impressions == 0 and fraction != 0:
            raise ReachReportError(f"row {line}: positive CTR with no impressions")
        key = (when, channel_id, video_id)
        if key in seen:
            raise ReachReportError(f"row {line}: duplicate daily video/channel snapshot")
        seen.add(key)
        results.append(ReachRow(when, channel_id, video_id, impressions, fraction))
    return tuple(results)


def summarize_reach(
    rows: tuple[ReachRow, ...],
    *,
    video_id: str,
    channel_id: str,
    start_date: date,
    end_date: date,
) -> ReachSummary:
    """Impression-weight CTR *only* over observed days in the requested window."""
    if not channel_id or not video_id or start_date > end_date:
        raise ReachReportError("video, channel and chronological dates are required")
    window = tuple(row for row in rows if (
        row.video_id == video_id and row.channel_id == channel_id
        and start_date <= row.date <= end_date
    ))
    impressions = sum(row.impressions for row in window)
    weighted = (
        sum((row.ctr_ratio * row.impressions for row in window), Decimal(0))
        * 100 / Decimal(impressions)
        if impressions else None
    )
    return ReachSummary(
        video_id=video_id, start_date=start_date, end_date=end_date,
        observed_days=len(window), window_days=(end_date - start_date).days + 1,
        impressions=impressions, impression_weighted_ctr_pct=weighted,
    )
