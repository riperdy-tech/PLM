"""Read-only P7 candidate assignment × P9 video snapshot attribution.

This is an observational *one-video* report, not an A/B winner analysis. A
P7 read authenticated from its own API supplies the frozen choice and C6
publication identity; P9 supplies only its already-stored video snapshots.
No cross-phase imports, invented CTR or overlapping-window sums.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c6 import YouTubePublicationPayload
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..domain.enums import MetricStatus, ObservationWindow
from ..domain.models import VideoMetricObservation
from ..ingestion.youtube_reach import CtrUnit, parse_reach_csv, summarize_reach
from ..ingestion.youtube_reporting import AuthenticatedReachReport, ReachReportVersion

REPORTING_PACIFIC = ZoneInfo("America/Los_Angeles")
"""Reporting `date` uses Pacific local days, including DST (23/25-hour days)."""


class CandidateAttributionError(ValueError):
    """A P7 receipt or P9 snapshot cannot safely be joined."""


class _Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version: Literal["p7-candidate-assignment-v1"]
    method: Literal["explicit", "first_declared"]
    metadata_variant_id: str = Field(min_length=1)
    thumbnail_id: str = Field(min_length=1)
    eligible_metadata_ids: tuple[str, ...] = Field(min_length=1)
    eligible_thumbnail_ids: tuple[str, ...] = Field(min_length=1)


class _Attempt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    attempt_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    youtube_video_id: str | None
    state: str
    thumbnail_uploaded: bool
    candidate_selection: _Assignment | None
    publication_event: dict[str, Any] | None


def build_candidate_performance(
    attempt_data: Mapping[str, Any],
    video_metrics: Sequence[VideoMetricObservation],
    *,
    attempt_id: str,
    window: ObservationWindow,
) -> dict[str, Any]:
    """Join verified identities, keeping missing/invalid measurement as unknown.

    The report describes the selected *video*, never per-thumbnail randomized
    impressions. Its upload flag is P7's receipt, not on-screen exposure.
    """
    try:
        attempt = _Attempt.model_validate(dict(attempt_data))
    except ValidationError as exc:
        raise CandidateAttributionError("P7 attempt receipt incomplete") from exc
    if attempt.attempt_id != attempt_id:
        raise CandidateAttributionError("P7 attempt identity mismatch")
    if attempt.state != "verified" or attempt.publication_event is None:
        raise CandidateAttributionError("publication not verified by P7")
    try:
        publication = parse_seam(YouTubePublicationPayload, attempt.publication_event)
    except SeamRejection as exc:
        raise CandidateAttributionError("P7 publication C6 event invalid") from exc
    if (
        not attempt.youtube_video_id
        or attempt.youtube_video_id != publication.youtube_video_id
        or attempt.channel_id != publication.channel_id
        or attempt.attempt_id != publication.publish_attempt_id
    ):
        raise CandidateAttributionError("P7 publication/attempt identity mismatch")

    assignment = attempt.candidate_selection
    if assignment is not None and (
        assignment.metadata_variant_id not in assignment.eligible_metadata_ids
        or assignment.thumbnail_id not in assignment.eligible_thumbnail_ids
        or len(set(assignment.eligible_metadata_ids)) != len(assignment.eligible_metadata_ids)
        or len(set(assignment.eligible_thumbnail_ids)) != len(assignment.eligible_thumbnail_ids)
        or assignment.metadata_variant_id != publication.metadata_version
    ):
        raise CandidateAttributionError("P7 frozen candidate assignment inconsistent")

    # Refuse misattributed video/channel rows, even when they aren't in this window.
    if any(
        metric.video_id != publication.youtube_video_id
        or metric.channel_id != publication.channel_id
        for metric in video_metrics
    ):
        raise CandidateAttributionError("P9 metric video/channel identity mismatch")
    matching = [
        metric for metric in video_metrics
        if metric.window == window and metric.observed_at >= publication.published_at
    ]
    latest = max(matching, key=lambda m: (m.observed_at, m.id), default=None)
    usable = latest is not None and latest.status in (MetricStatus.VALID, MetricStatus.REVISED)
    observed = latest if usable else None
    if assignment is None:
        assignment_status = "unknown_legacy"
    elif attempt.thumbnail_uploaded:
        assignment_status = "p7_thumbnail_upload_recorded"
    else:
        assignment_status = "p7_thumbnail_upload_unconfirmed"
    return {
        "attempt_id": attempt.attempt_id,
        "video_id": publication.youtube_video_id,
        "channel_id": publication.channel_id,
        "media_package_id": publication.media_package_id,
        "experiment_id": publication.lineage.experiment_id,
        "content_arm_id": publication.lineage.content_arm_id,
        "published_at": publication.published_at.isoformat(),
        "window": window.value,
        "candidate_selection": assignment.model_dump(mode="json") if assignment else None,
        "assignment_status": assignment_status,
        "metric_status": (
            "unavailable" if latest is None else
            "held_nonvalid" if not usable else "observed"
        ),
        "metric_source": observed.source.value if observed else None,
        "metric_observed_at": observed.observed_at.isoformat() if observed else None,
        "views": observed.views if observed else None,
        "watch_time_minutes": observed.watch_time_minutes if observed else None,
        "average_percentage_viewed": (
            observed.average_percentage_viewed if observed else None
        ),
        "thumbnail_impressions": None,
        "thumbnail_ctr_percent": None,
        "inference": "not_an_ab_test",
    }


def preview_operator_reach(
    performance: Mapping[str, Any],
    *,
    csv_content: str,
    ctr_unit: CtrUnit,
    source_reference: str,
    start_date: date,
    end_date: date,
    as_of_date: date,
) -> dict[str, Any]:
    """Append video-level reach *alongside*, never as the candidate's own CTR.

    CSV formatting and the claimed source_reference are operator supplied; a
    correct header/hash cannot prove a Reporting API export is authentic.
    Full UTC days strictly after publication avoid partial publication-day
    exposure, but do not assert YouTube's reporting timezone equals UTC.
    """
    published_at = datetime.fromisoformat(str(performance["published_at"]))
    if published_at.tzinfo is None:
        raise CandidateAttributionError("publication timestamp lacks a timezone")
    published_day_utc = published_at.astimezone(UTC).date()
    if start_date <= published_day_utc or start_date > end_date:
        raise CandidateAttributionError("reach window must begin after the publication UTC date")
    if end_date >= as_of_date:
        raise CandidateAttributionError("reach report may include only completed past days")

    rows = parse_reach_csv(csv_content, ctr_unit=ctr_unit)
    channel_id = str(performance["channel_id"])
    if any(row.channel_id != channel_id for row in rows):
        raise CandidateAttributionError("reach CSV contains a different channel")
    if any(row.date >= as_of_date for row in rows):
        raise CandidateAttributionError("reach CSV contains a current/future day")

    summary = summarize_reach(
        rows, video_id=str(performance["video_id"]), channel_id=channel_id,
        start_date=start_date, end_date=end_date,
    )
    coverage_status = (
        "unavailable" if summary.observed_days == 0 else
        "complete" if summary.observed_days == summary.window_days else "partial"
    )
    return {
        **performance,
        "video_reach": {
            "report_type": "channel_reach_basic_a1",
            "source_kind": "operator_supplied_csv_unverified",
            "source_reference": source_reference,
            "source_sha256": hashlib.sha256(csv_content.encode("utf-8")).hexdigest(),
            "ctr_unit_in_csv": ctr_unit,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "observed_days": summary.observed_days,
            "window_days": summary.window_days,
            "coverage_status": coverage_status,
            "video_thumbnail_impressions": (
                summary.impressions if summary.observed_days else None
            ),
            "video_thumbnail_ctr_percent": (
                str(summary.impression_weighted_ctr_pct)
                if summary.impression_weighted_ctr_pct is not None else None
            ),
            "attribution_scope": "video_level_only_not_selected_candidate_effect",
            "reporting_timezone_verified": False,
        },
    }


def validate_reporting_day(
    performance: Mapping[str, Any], *, revision: ReachReportVersion,
    start_date: date, end_date: date, as_of_date: date,
) -> None:
    """Shared Pacific-day eligibility gate for preflight and downloaded CSV."""
    if any(t.tzinfo is None for t in (
        revision.start_time, revision.end_time, revision.create_time,
    )):
        raise CandidateAttributionError("report timestamp lacks timezone")
    expected_start = datetime.combine(
        start_date, datetime.min.time(), tzinfo=REPORTING_PACIFIC,
    ).astimezone(UTC)
    expected_end = datetime.combine(
        start_date + timedelta(days=1), datetime.min.time(),
        tzinfo=REPORTING_PACIFIC,
    ).astimezone(UTC)
    if (
        end_date != start_date or start_date >= as_of_date
        or revision.start_time.astimezone(UTC) != expected_start
        or revision.end_time.astimezone(UTC) != expected_end
        or revision.create_time.astimezone(REPORTING_PACIFIC).date() > as_of_date
    ):
        raise CandidateAttributionError("report is not one completed Pacific reporting day")
    try:
        published = datetime.fromisoformat(str(performance["published_at"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise CandidateAttributionError("publication timestamp invalid") from exc
    if published.tzinfo is None or start_date <= published.astimezone(REPORTING_PACIFIC).date():
        raise CandidateAttributionError("reach starts before completed publication Pacific day")


def build_reporting_readiness(
    performance: Mapping[str, Any], *, revision: ReachReportVersion,
    start_date: date, end_date: date, as_of_date: date,
) -> dict[str, Any]:
    """Catalog metadata only: do not fetch the CSV or infer impressions."""
    validate_reporting_day(
        performance, revision=revision, start_date=start_date, end_date=end_date,
        as_of_date=as_of_date,
    )
    return {
        "status": "catalog_metadata_eligible_only",
        "attempt_id": performance["attempt_id"],
        "video_id": performance["video_id"],
        "channel_id": performance["channel_id"],
        "candidate_selection": performance.get("candidate_selection"),
        "report": {
            "job_id": revision.job_id,
            "report_id": revision.report_id,
            "start_time": revision.start_time.isoformat(),
            "end_time_exclusive": revision.end_time.isoformat(),
            "created_at": revision.create_time.isoformat(),
            "revision_status": revision.revision_status,
            "superseded_by": revision.superseded_by,
            "reporting_day_basis": "America/Los_Angeles_documented_DST_aware",
        },
        "checks": {
            "p7_publication": "verified_by_p7_receipt",
            "google_job_and_report": "read_from_authenticated_catalog",
            "pacific_reporting_day": "eligible",
            "google_download_access": "not_checked",
            "csv_integrity_and_channel": "not_checked",
            "channel_ownership": "not_independently_verified",
            "thumbnail_exposure": "not_verified",
            "ab_effect": "not_established",
        },
        "download_performed": False,
        "report_metrics": None,
    }


def preview_authenticated_reach(
    performance: Mapping[str, Any], *,
    report: AuthenticatedReachReport, revision: ReachReportVersion,
    ctr_unit: CtrUnit, start_date: date, end_date: date, as_of_date: date,
) -> dict[str, Any]:
    """Attach verified Google transfer metadata without asserting creative lift."""
    if (
        report.source_kind != "google_reporting_api_authenticated_download"
        or report.report_type != "channel_reach_basic_a1"
        or report.job_id != revision.job_id
        or report.report_id != revision.report_id
        or report.start_time != revision.start_time
        or report.end_time != revision.end_time
        or report.create_time != revision.create_time
        or revision.revision_status not in ("current", "superseded")
        or (revision.revision_status == "superseded") != (revision.superseded_by is not None)
    ):
        raise CandidateAttributionError("Google report differs from catalog revision")
    validate_reporting_day(
        performance, revision=revision, start_date=start_date, end_date=end_date,
        as_of_date=as_of_date,
    )
    start_utc = report.start_time.astimezone(UTC)
    end_utc = report.end_time.astimezone(UTC)
    if any(r.date != start_date for r in report.rows):
        raise CandidateAttributionError("CSV day differs from authenticated Pacific day")
    channel = str(performance["channel_id"])
    if any(r.channel_id != channel or r.date >= as_of_date for r in report.rows):
        raise CandidateAttributionError("Google CSV channel/date mismatch")
    summary = summarize_reach(
        report.rows, video_id=str(performance["video_id"]), channel_id=channel,
        start_date=start_date, end_date=end_date,
    )
    result = {
        **performance,
        "video_reach": {
            "report_type": report.report_type,
            "source_kind": report.source_kind,
            "source_reference": f"jobs/{report.job_id}/reports/{report.report_id}",
            "source_sha256": report.csv_sha256,
            "ctr_unit_in_csv": ctr_unit,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "observed_days": summary.observed_days,
            "window_days": summary.window_days,
            "coverage_status": (
                "unavailable" if summary.observed_days == 0 else
                "complete" if summary.observed_days == summary.window_days else "partial"
            ),
            "video_thumbnail_impressions": (
                summary.impressions if summary.observed_days else None
            ),
            "video_thumbnail_ctr_percent": (
                str(summary.impression_weighted_ctr_pct)
                if summary.impression_weighted_ctr_pct is not None else None
            ),
            "attribution_scope": "video_level_only_not_selected_candidate_effect",
            "reporting_timezone_verified": False,
            "reporting_day_basis": "America/Los_Angeles_documented_DST_aware",
            "report_provenance": {
                "job_id": report.job_id,
                "report_id": report.report_id,
                "report_start_time": start_utc.isoformat(),
                "report_end_time_exclusive": end_utc.isoformat(),
                "report_created_at": report.create_time.isoformat(),
                "revision_status": revision.revision_status,
                "superseded_by": revision.superseded_by,
                "authenticated_google_download": True,
                "candidate_exposure_verified": False,
            },
        },
    }
    return result
