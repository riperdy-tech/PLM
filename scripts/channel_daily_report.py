"""Read-only daily channel report: impressions, CTR, watch time per impression, verdicts.

Joins the Reporting API reach job (thumbnail impressions and CTR, one CSV per day) with
the Analytics API (average view duration) for the channel's recent uploads, applies the
growth spec's decision rules (``analytics.evaluation.channel_rules``) and writes
``<out>/<date>.md``. It never edits a video, creates a job or feeds P2.

    python -X utf8 scripts/channel_daily_report.py --channel-id UC... \
        --reach-job-id <job> --reach-ctr-unit ratio
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analytics.evaluation.channel_rules import (
    ChannelVerdict,
    VideoFacts,
    evaluate_channel,
    first_week_impressions,
)
from analytics.ingestion.youtube_client import RealYouTubeAnalyticsClient, YouTubeAnalyticsError
from analytics.ingestion.youtube_reach import ReachReportError, ReachRow, summarize_reach
from analytics.ingestion.youtube_reporting import (
    AuthenticatedReachDownloader,
    ReportingDownloadError,
)
from youtube_publishing.client.base import YouTubeClientError
from youtube_publishing.client.real import RealYouTubeClient

logger = logging.getLogger("channel_daily_report")


def download_reach_rows(downloader: AuthenticatedReachDownloader, *, job_id: str,
                        ctr_unit: str, channel_id: str) -> tuple[ReachRow, ...]:
    """Every current (not superseded) daily report of the job, one row per day and video."""
    rows: dict[tuple[date, str], ReachRow] = {}
    for version in downloader.list_reach_reports(job_id=job_id):
        if version.revision_status != "current":
            continue
        report = downloader.fetch(job_id=job_id, report_id=version.report_id,
                                  ctr_unit=ctr_unit)  # type: ignore[arg-type]
        for row in report.rows:
            if row.channel_id != channel_id:
                raise ReportingDownloadError("reach rows belong to another channel")
            key = (row.date, row.video_id)
            if key in rows and rows[key] != row:
                raise ReachReportError(f"two current reports disagree on {key}")
            rows[key] = row
    return tuple(rows.values())


def collect_facts(data_client: RealYouTubeClient, analytics: RealYouTubeAnalyticsClient,
                  rows: tuple[ReachRow, ...] | None, *, channel_id: str, today: date,
                  limit: int) -> list[VideoFacts]:
    end = today - timedelta(days=1)  # the latest complete Analytics day
    facts = []
    for item in data_client.list_recent_uploads(max_results=limit, channel_id=channel_id):
        # Private and unlisted uploads (old account videos, replaced renders) get no
        # impressions; comparing with them would drag every median down.
        resource = data_client.get_video(item["video_id"]) or {}
        if (resource.get("status") or {}).get("privacyStatus") != "public":
            continue
        published = datetime.fromisoformat(item["published_at"]).astimezone(UTC).date()
        impressions = ctr = week = None
        if rows is not None and published <= end:
            reach = summarize_reach(rows, video_id=item["video_id"], channel_id=channel_id,
                                    start_date=published, end_date=end)
            if reach.observed_days:
                impressions = reach.impressions
                pct = reach.impression_weighted_ctr_pct
                ctr = None if pct is None else float(pct)
            week = first_week_impressions(rows, video_id=item["video_id"], published=published)
        avd = None
        if published <= end:
            try:
                metrics = analytics.fetch_video_metrics(
                    video_id=item["video_id"], start_date=published.isoformat(),
                    end_date=end.isoformat())
                seconds = metrics.average_view_duration_seconds
                avd = None if seconds is None else round(seconds / 60, 1)
            except YouTubeAnalyticsError as exc:
                logger.warning("Analytics unavailable for %s: %s", item["video_id"], exc)
        facts.append(VideoFacts(item["video_id"], item["title"], (today - published).days,
                                impressions, ctr, avd, week))
    return facts


def render(facts: list[VideoFacts], verdicts: list[ChannelVerdict], *, today: date,
           reach_days: int) -> str:
    def show(value: object, suffix: str = "") -> str:
        return "unknown" if value is None else f"{value}{suffix}"

    by_id = {f.video_id: f for f in facts}
    lines = [f"# Channel report {today.isoformat()}", "",
             (f"Reach data: {reach_days} daily rows. Thresholds are the growth spec's "
             "heuristics (recalibrate after ~30 videos)."), "",
             ("| Video | Age (days) | Impressions | First week | CTR | Avg view (min) "
             "| Watch min / 1k impressions | Verdict | What to do |"),
             "|---|---|---|---|---|---|---|---|---|"]
    for v in verdicts:
        f = by_id[v.video_id]
        ctr = None if f.ctr_pct is None else f"{f.ctr_pct:.1f}%"
        title = v.title[:60].replace("|", "\\|")
        lines.append(f"| {title} ({v.video_id}) | {f.age_days} | {show(f.impressions)} "
                     f"| {show(f.first_week_impressions)} | {show(ctr)} "
                     f"| {show(f.avg_view_minutes)} | {show(v.wti)} | **{v.verdict}** "
                     f"| {v.advice} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--channel-id", required=True)
    parser.add_argument("--reach-job-id", required=True, help="existing channel_reach_basic_a1 job")
    parser.add_argument("--reach-ctr-unit", required=True, choices=("ratio", "percent"))
    parser.add_argument("--limit", type=int, default=20, help="recent uploads to include (1-50)")
    parser.add_argument("--out", type=Path, default=Path("data/analytics-reports"))
    options = parser.parse_args(argv)
    if not 1 <= options.limit <= 50:
        parser.error("--limit must be 1..50")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    data_client, analytics = RealYouTubeClient(), RealYouTubeAnalyticsClient()
    if not data_client.is_configured or not analytics.is_configured:
        logger.error("YouTube OAuth credentials are missing; nothing was fetched")
        return 2
    rows: tuple[ReachRow, ...] | None
    try:
        with httpx.Client(timeout=60.0, follow_redirects=False) as http_client:
            downloader = AuthenticatedReachDownloader(
                client=http_client, access_token=analytics._get_access_token)
            rows = download_reach_rows(downloader, job_id=options.reach_job_id,
                                       ctr_unit=options.reach_ctr_unit,
                                       channel_id=options.channel_id)
    except (ReportingDownloadError, ReachReportError, YouTubeAnalyticsError) as exc:
        logger.error("Reach reports unavailable: %s", exc)
        return 2
    if not rows:
        logger.warning("The reach job has no reports yet; impressions and CTR stay unknown")
    today = datetime.now(UTC).date()
    try:
        facts = collect_facts(data_client, analytics, rows or None,
                              channel_id=options.channel_id, today=today, limit=options.limit)
    except (YouTubeClientError, YouTubeAnalyticsError, ReachReportError, ValueError) as exc:
        logger.error("Could not read the channel's uploads: %s", exc)
        return 2
    text = render(facts, evaluate_channel(facts), today=today, reach_days=len(rows))
    options.out.mkdir(parents=True, exist_ok=True)
    path = options.out / f"{today.isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    logger.info("Written to %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
