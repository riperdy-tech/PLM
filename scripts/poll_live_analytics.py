"""Read-only health check of recent uploads from the authenticated YouTube channel.

This operator diagnostic never publishes, trains P2 or fills unavailable data
with fabricated zeroes. It uses recent channel uploads rather than fixed IDs.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.ingestion.youtube_client import (
    RealYouTubeAnalyticsClient,
    YouTubeAnalyticsError,
    YouTubeRetentionPoint,
)
from analytics.ingestion.youtube_reach import (
    ReachReportError,
    ReachRow,
    parse_reach_csv,
    summarize_reach,
)
from analytics.ingestion.youtube_reporting import (
    AuthenticatedReachDownloader,
    ReportingDownloadError,
)
from youtube_publishing.client.base import YouTubeClientError
from youtube_publishing.client.real import RealYouTubeClient
from youtube_publishing.diagnostics import inspect_uploaded_video

logger = logging.getLogger("analytics_watcher")


def _date_of_upload(published_at: str) -> datetime:
    """Refuse invalid/missing video dates instead of inventing an age."""
    try:
        date = datetime.fromisoformat(published_at)
    except ValueError as exc:
        raise ValueError("upload has invalid published_at timestamp") from exc
    if date.tzinfo is None:
        raise ValueError("upload published_at has no timezone")
    return date.astimezone(UTC)


def _point_near(points: tuple[YouTubeRetentionPoint, ...], fraction: float) -> str:
    nearest = min(points, key=lambda p: abs(p.elapsed_video_fraction - fraction), default=None)
    if nearest is None or abs(nearest.elapsed_video_fraction - fraction) > 0.015:
        return "unavailable"
    # Ratio can exceed 100% due to repeat/rewind viewing; never clamp.
    return f"{nearest.audience_watch_ratio * 100:.1f}% observed"


def poll_recent_uploads(
    data_client: RealYouTubeClient,
    analytics_client: RealYouTubeAnalyticsClient,
    *,
    now: datetime,
    limit: int = 10,
    window_days: int = 28,
    channel_id: str | None = None,
    with_retention: bool = True,
    with_sources: bool = True,
    reach_rows: tuple[ReachRow, ...] | None = None,
) -> tuple[int, int]:
    """Inspect recent channel videos and return (inspected, API/errors).

    Per-video windows begin no earlier than that video's publication date.
    Missing Analytics rows and unpublished-young videos are marked unknown.
    """
    if not 1 <= limit <= 50 or not 1 <= window_days <= 365:
        raise ValueError("limit must be 1..50; window_days must be 1..365")
    if now.tzinfo is None:
        raise ValueError("now requires a timezone")
    if reach_rows is not None and not channel_id:
        raise ValueError("reach report requires an expected authenticated channel_id")
    uploads = data_client.list_recent_uploads(max_results=limit, channel_id=channel_id)
    if not uploads:
        logger.warning("Authenticated channel has no returned recent uploads; nothing measured")
        return 0, 0
    # The latest analytics day may lag the current UTC calendar day.
    end = now.astimezone(UTC).date() - timedelta(days=1)
    earliest = end - timedelta(days=window_days - 1)
    errors = 0
    inspected = 0
    for item in uploads:
        video_id = item["video_id"]
        title = item["title"]
        try:
            published = _date_of_upload(item["published_at"]).date()
        except ValueError as exc:
            logger.error("Video %s: %s", video_id, exc)
            errors += 1
            continue
        try:
            resource = data_client.get_video(video_id)
        except YouTubeClientError as exc:
            logger.error("Video %s: cannot read YouTube metadata: %s", video_id, exc)
            errors += 1
            continue
        if resource is None:
            logger.error("Video %s: YouTube metadata not returned (deleted/inaccessible?)", video_id)
            errors += 1
            continue
        chapters = inspect_uploaded_video(resource, now=now)
        duration = chapters["duration_seconds"]
        if not isinstance(duration, (int, float)) or duration <= 0:
            logger.error("Video %s: published duration could not be verified", video_id)
            errors += 1
            continue
        logger.info("  Video length=%.0fs; processing=%s; clickable chapters=%s",
                    duration, chapters["processing_status"], chapters["player_chapters_visibility"])
        if duration >= 600:
            if not chapters["description_chapters_valid"]:
                logger.error("  Album chapters metadata invalid: %s", chapters["findings"])
                errors += 1
            else:
                logger.info("  Chapter timestamps structurally valid (%s); watch-page still UNVERIFIED",
                            chapters["chapter_count"])
        if published > end:
            logger.info("Video %s (%s): newly published; completed Analytics days unavailable", video_id, title)
            continue
        start = max(published, earliest)
        logger.info("Video %s (%s): observed window %s..%s; published %s",
                    video_id, title, start, end, published)
        if reach_rows is not None and channel_id is not None:
            reach = summarize_reach(
                reach_rows, video_id=video_id, channel_id=channel_id,
                start_date=start, end_date=end,
            )
            if reach.impression_weighted_ctr_pct is None:
                logger.warning(
                    "  Thumbnail reach unknown (observed_days=%d/%d); cannot infer CTR",
                    reach.observed_days, reach.window_days,
                )
            else:
                logger.info(
                    "  Thumbnail impressions=%d; weighted CTR=%s%%; observed_days=%d/%d",
                    reach.impressions, reach.impression_weighted_ctr_pct,
                    reach.observed_days, reach.window_days,
                )
                if reach.observed_days < reach.window_days:
                    logger.warning("  Reach data cover only part of the requested analysis window")
        try:
            metrics = analytics_client.fetch_video_metrics(
                video_id=video_id, start_date=start.isoformat(), end_date=end.isoformat(),
            )
            inspected += 1
            logger.info("  Views=%s, watch_minutes=%s, average_view_seconds=%s, avg_viewed_pct=%s",
                        metrics.views, metrics.watch_time_minutes,
                        metrics.average_view_duration_seconds, metrics.average_percentage_viewed)
            if metrics.views is None:
                logger.warning("  No Analytics metrics rows; not a measured zero")
            if with_retention:
                retention = analytics_client.fetch_video_retention(
                    video_id=video_id, start_date=start.isoformat(), end_date=end.isoformat(),
                )
                logger.info("  Retention points=%d; at 10%%=%s; at 50%%=%s",
                            len(retention.points),
                            _point_near(retention.points, 0.10),
                            _point_near(retention.points, 0.50))
                if duration >= 300:
                    logger.info(
                        "  Early retention (0:30 / 2:00 / 5:00): %s / %s / %s",
                        _point_near(retention.points, 30 / duration),
                        _point_near(retention.points, 120 / duration),
                        _point_near(retention.points, 300 / duration),
                    )
                if not retention.points:
                    logger.warning("  Retention curve unavailable; no drop-off diagnosis possible")
        except YouTubeAnalyticsError as exc:
            logger.error("  Analytics unavailable for video %s: %s", video_id, exc)
            errors += 1
        if with_sources:
            try:
                sources = analytics_client.fetch_video_traffic_sources(
                    video_id=video_id, start_date=start.isoformat(), end_date=end.isoformat(),
                )
                if not sources.rows:
                    logger.warning("  Traffic-source breakdown unavailable; no source-specific views inferred")
                else:
                    for source in sources.rows:
                        logger.info("  Source %s: views=%d, watch_minutes=%.2f",
                                    source.source_type, source.views, source.watch_time_minutes)
            except YouTubeAnalyticsError as exc:
                logger.error("  Traffic-source report unavailable for %s: %s", video_id, exc)
                errors += 1
    return inspected, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only diagnostics for recent channel uploads")
    parser.add_argument("--limit", type=int, default=10, help="Most recent uploads to inspect (1-50)")
    parser.add_argument("--days", type=int, default=28, help="Maximum per-video Analytics window (1-365 days)")
    parser.add_argument("--channel-id", help="Expected authenticated channel ID (mismatch refuses)")
    parser.add_argument("--no-retention", action="store_true", help="Skip video retention API queries")
    parser.add_argument("--no-sources", action="store_true", help="Skip traffic-source breakdown queries")
    parser.add_argument("--reach-report", type=Path, help="Operator-provided reach CSV (source unverified)")
    parser.add_argument("--reach-job-id", help="Existing Google Reporting API reach job ID (read only)")
    parser.add_argument("--reach-report-id", help="Exact Google report ID, including backfill revision")
    parser.add_argument("--list-reach-jobs", action="store_true",
                        help="List existing accessible reach job IDs (no download)")
    parser.add_argument("--list-reach-reports", metavar="JOB_ID",
                        help="List all report IDs/revisions for an existing reach job (no download)")
    parser.add_argument("--reach-ctr-unit", choices=("ratio", "percent"),
                        help="Explicit CTR numeric unit used in the reach CSV")
    options = parser.parse_args(argv)
    if not 1 <= options.limit <= 50 or not 1 <= options.days <= 365:
        parser.error("--limit must be 1..50 and --days must be 1..365")
    listing = options.list_reach_jobs or options.list_reach_reports is not None
    if listing and (
        options.list_reach_jobs and options.list_reach_reports is not None
        or options.reach_job_id or options.reach_report_id
        or options.reach_report is not None or options.reach_ctr_unit
        or options.channel_id
    ):
        parser.error("catalog listing cannot be combined with download or channel options")
    if bool(options.reach_job_id) != bool(options.reach_report_id):
        parser.error("--reach-job-id and --reach-report-id must be specified together")
    if options.reach_report is not None and options.reach_job_id:
        parser.error("choose either --reach-report or --reach-job-id/--reach-report-id")
    if (options.reach_report is not None or options.reach_job_id) and (
        not options.reach_ctr_unit or not options.channel_id
    ):
        parser.error("reach data requires --channel-id and --reach-ctr-unit")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    reach_rows: tuple[ReachRow, ...] | None = None
    if options.reach_report is not None:
        try:
            reach_rows = parse_reach_csv(
                options.reach_report.read_text(encoding="utf-8-sig"),
                ctr_unit=options.reach_ctr_unit,
            )
        except (ReachReportError, OSError, UnicodeError) as exc:
            logger.error("Official reach CSV could not be verified: %s", exc)
            return 2
    if listing:
        analytics_client = RealYouTubeAnalyticsClient()
        if not analytics_client.is_configured:
            logger.error("YouTube OAuth credentials missing; cannot list Reporting catalog")
            return 2
        try:
            with httpx.Client(timeout=30.0, follow_redirects=False) as http_client:
                downloader = AuthenticatedReachDownloader(
                    client=http_client, access_token=analytics_client._get_access_token,
                )
                if options.list_reach_jobs:
                    jobs = downloader.list_reach_jobs()
                    for job in jobs:
                        logger.info("Reach job id=%s type=%s", job.job_id, job.report_type)
                    if not jobs:
                        logger.warning("No accessible channel reach jobs; none assumed")
                    return 0 if jobs else 2
                versions = downloader.list_reach_reports(job_id=options.list_reach_reports)
                for version in versions:
                    logger.info(
                        "Reach report job=%s id=%s period=%s..%s created=%s "
                        "revision=%s superseded_by=%s",
                        version.job_id, version.report_id,
                        version.start_time.isoformat(), version.end_time.isoformat(),
                        version.create_time.isoformat(), version.revision_status,
                        version.superseded_by,
                    )
                if not versions:
                    logger.warning("Reach job has no report instances; no period assumed")
                return 0 if versions else 2
        except (ReportingDownloadError, YouTubeAnalyticsError) as exc:
            logger.error("Could not enumerate Google Reporting catalog: %s", exc)
            return 2
    data_client = RealYouTubeClient()
    analytics_client = RealYouTubeAnalyticsClient()
    if not data_client.is_configured or not analytics_client.is_configured:
        logger.error("YouTube OAuth credentials are missing; no metrics were fetched")
        return 2
    if options.reach_job_id:
        try:
            with httpx.Client(timeout=30.0, follow_redirects=False) as http_client:
                downloader = AuthenticatedReachDownloader(
                    client=http_client, access_token=analytics_client._get_access_token,
                )
                downloaded = downloader.fetch(
                    job_id=options.reach_job_id,
                    report_id=options.reach_report_id,
                    ctr_unit=options.reach_ctr_unit,
                )
            if any(row.channel_id != options.channel_id for row in downloaded.rows):
                raise ReportingDownloadError("Google reach rows do not match requested channel")
            reach_rows = downloaded.rows
            logger.info(
                "Reporting download job=%s report=%s type=%s created=%s period=%s..%s "
                "bytes_sha256=%s rows=%d (video-wide only, not candidate exposure)",
                downloaded.job_id, downloaded.report_id, downloaded.report_type,
                downloaded.create_time.isoformat(), downloaded.start_time.isoformat(),
                downloaded.end_time.isoformat(), downloaded.csv_sha256, len(reach_rows),
            )
        except (ReportingDownloadError, ReachReportError, YouTubeAnalyticsError) as exc:
            logger.error("Authenticated Reporting reach report unavailable: %s", exc)
            return 2
    try:
        inspected, errors = poll_recent_uploads(
            data_client, analytics_client, now=datetime.now(UTC),
            limit=options.limit, window_days=options.days,
            channel_id=options.channel_id, with_retention=not options.no_retention,
            with_sources=not options.no_sources, reach_rows=reach_rows,
        )
    except (YouTubeClientError, YouTubeAnalyticsError, ValueError) as exc:
        logger.error("Could not discover recent uploads: %s", exc)
        return 2
    logger.info("Recent-upload check: inspected=%d, errors=%d", inspected, errors)
    return 0 if inspected > 0 and errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

