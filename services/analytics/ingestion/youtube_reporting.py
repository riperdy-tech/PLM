"""Authenticated, read-only YouTube Reporting API reach report retrieval.

A job/report ID names one exact immutable *report resource*, including backfills.
OAuth validates access to the resource but is not proof of any thumbnail A/B
exposure. No jobs are created, no files are written, and no rewards are posted.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx

from .youtube_reach import CtrUnit, ReachRow, parse_reach_csv

API_ORIGIN = "https://youtubereporting.googleapis.com"
REPORT_TYPE = "channel_reach_basic_a1"
MAX_CSV_BYTES = 4 * 1024 * 1024
MAX_CATALOG_PAGES = 10
CATALOG_PAGE_SIZE = 100
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")


class ReportingDownloadError(ValueError):
    """An authenticated report cannot be safely used as a reach observation."""


class ReportingAccessDenied(ReportingDownloadError):
    """Google refused access: OAuth scope, token or account may be insufficient."""


@dataclass(frozen=True, slots=True)
class AuthenticatedReachReport:
    job_id: str
    report_id: str
    report_type: str
    source_kind: str
    start_time: datetime
    end_time: datetime
    create_time: datetime
    csv_sha256: str
    rows: tuple[ReachRow, ...]


@dataclass(frozen=True, slots=True)
class ReachJob:
    """Existing YouTube-generated report job, not a request to create one."""

    job_id: str
    report_type: str


@dataclass(frozen=True, slots=True)
class ReachReportVersion:
    """An exact report ID; periods can have multiple backfilled revisions."""

    job_id: str
    report_id: str
    start_time: datetime
    end_time: datetime
    create_time: datetime
    revision_status: Literal["current", "superseded"]
    superseded_by: str | None


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ReportingDownloadError("report timestamp is missing")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReportingDownloadError("invalid report timestamp") from exc
    if result.tzinfo is None:
        raise ReportingDownloadError("report timestamp must be timezone-aware")
    return result


def _download_url(url: object) -> str:
    """Do not leak the OAuth bearer token to a metadata-controlled URL."""
    if not isinstance(url, str):
        raise ReportingDownloadError("report download URL missing")
    try:
        parts = urlsplit(url)
        safe_origin = (
            parts.scheme == "https"
            and parts.hostname == "youtubereporting.googleapis.com"
            and parts.port is None
            and parts.username is None and parts.password is None
            and bool(re.fullmatch(r"/v1/media/[A-Za-z0-9_-]+", parts.path))
            and parts.query in ("", "alt=media")
            and not parts.fragment
        )
    except ValueError:
        safe_origin = False
    if not safe_origin:
        raise ReportingDownloadError("report download URL is outside approved Google origin/path")
    return url


class AuthenticatedReachDownloader:
    """Google API metadata checks + bounded, authenticated GET of one report."""

    def __init__(
        self, *, client: httpx.Client, access_token: Callable[[], str],
    ) -> None:
        self.client = client
        self.access_token = access_token

    def _get(
        self, url: str, headers: dict[str, str], params: dict[str, str | int] | None = None,
    ) -> httpx.Response:
        try:
            response = self.client.get(
                url, headers=headers, params=params, follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise ReportingDownloadError("Google Reporting API request failed") from exc
        if response.status_code in (401, 403):
            raise ReportingAccessDenied(
                f"Google Reporting API access denied (HTTP {response.status_code}); "
                "check yt-analytics.readonly scope and channel/job access"
            )
        if response.status_code != 200:
            raise ReportingDownloadError(
                f"Google Reporting API returned HTTP {response.status_code}"
            )
        return response

    def _metadata(
        self, url: str, headers: dict[str, str], params: dict[str, str | int] | None = None,
    ) -> dict[str, object]:
        response = self._get(url, headers, params=params)
        try:
            value = response.json()
        except ValueError as exc:
            raise ReportingDownloadError("Google Reporting API returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ReportingDownloadError("report metadata must be an object")
        return value

    def _headers(self) -> dict[str, str]:
        token = self.access_token()
        if not isinstance(token, str) or not token.strip():
            raise ReportingDownloadError("Google OAuth token unavailable")
        return {"Authorization": f"Bearer {token}"}

    def _pages(
        self, url: str, headers: dict[str, str], key: str,
        params: dict[str, str | int],
    ) -> tuple[dict[str, object], ...]:
        """Exhaust bounded pagination or reject; never present a partial catalog."""
        collected: list[dict[str, object]] = []
        seen_tokens: set[str] = set()
        for index in range(MAX_CATALOG_PAGES):
            payload = self._metadata(url, headers, params)
            items = payload.get(key, [])
            if not isinstance(items, list) or len(items) > CATALOG_PAGE_SIZE:
                raise ReportingDownloadError("invalid or excessive Reporting catalog page")
            if any(not isinstance(item, dict) for item in items):
                raise ReportingDownloadError("malformed Reporting catalog item")
            collected.extend(items)
            next_token = payload.get("nextPageToken")
            if next_token is None or next_token == "":
                return tuple(collected)
            if (
                not isinstance(next_token, str) or len(next_token) > 1024
                or next_token in seen_tokens or index + 1 == MAX_CATALOG_PAGES
            ):
                raise ReportingDownloadError("Reporting catalog pagination incomplete or cyclic")
            seen_tokens.add(next_token)
            params = {**params, "pageToken": next_token}
        raise ReportingDownloadError("Reporting catalog pagination incomplete")

    def list_reach_jobs(self) -> tuple[ReachJob, ...]:
        """List all reachable reach jobs, including system-managed jobs."""
        headers = self._headers()
        rows = self._pages(
            f"{API_ORIGIN}/v1/jobs", headers, "jobs",
            {"pageSize": CATALOG_PAGE_SIZE, "includeSystemManaged": "true"},
        )
        jobs: dict[str, ReachJob] = {}
        for row in rows:
            job_id = row.get("id")
            report_type = row.get("reportTypeId")
            if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
                raise ReportingDownloadError("Reporting job catalog contains an invalid ID")
            if not isinstance(report_type, str) or not report_type:
                raise ReportingDownloadError("Reporting job catalog missing report type")
            if job_id in jobs:
                raise ReportingDownloadError("Reporting job catalog contains duplicate IDs")
            if report_type == REPORT_TYPE:
                jobs[job_id] = ReachJob(job_id=job_id, report_type=report_type)
        return tuple(jobs[job_id] for job_id in sorted(jobs))

    def list_reach_reports(self, *, job_id: str) -> tuple[ReachReportVersion, ...]:
        """Classify every known revision per exact (start, end) interval.

        No implicit downloading or selection. Same-interval/timestamp collisions
        cannot be resolved safely and are rejected rather than guessed.
        """
        if not _ID.fullmatch(job_id):
            raise ReportingDownloadError("invalid Google job/report identifier")
        headers = self._headers()
        job = self._metadata(f"{API_ORIGIN}/v1/jobs/{job_id}", headers)
        if job.get("id") != job_id or job.get("reportTypeId") != REPORT_TYPE:
            raise ReportingDownloadError("job is not the exact approved channel reach type")
        rows = self._pages(
            f"{API_ORIGIN}/v1/jobs/{job_id}/reports", headers, "reports",
            {"pageSize": CATALOG_PAGE_SIZE},
        )
        versions: dict[str, tuple[datetime, datetime, datetime]] = {}
        by_period: dict[tuple[datetime, datetime], list[tuple[datetime, str]]] = {}
        for row in rows:
            report_id = row.get("id")
            if (not isinstance(report_id, str) or not _ID.fullmatch(report_id)
                or row.get("jobId") != job_id):
                raise ReportingDownloadError("report catalog identity mismatch")
            if report_id in versions:
                raise ReportingDownloadError("report catalog contains duplicate report IDs")
            start = _time(row.get("startTime")).astimezone(UTC)
            end = _time(row.get("endTime")).astimezone(UTC)
            created = _time(row.get("createTime")).astimezone(UTC)
            if start >= end:
                raise ReportingDownloadError("report data interval is invalid")
            _download_url(row.get("downloadUrl"))
            versions[report_id] = (start, end, created)
            by_period.setdefault((start, end), []).append((created, report_id))

        results: list[ReachReportVersion] = []
        for (start, end), revisions in sorted(by_period.items()):
            sorted_revisions = sorted(revisions, reverse=True)
            if len({created for created, _ in sorted_revisions}) != len(sorted_revisions):
                raise ReportingDownloadError("ambiguous report revision creation timestamp")
            winner = sorted_revisions[0][1]
            for created, report_id in sorted_revisions:
                results.append(ReachReportVersion(
                    job_id=job_id, report_id=report_id,
                    start_time=start, end_time=end, create_time=created,
                    revision_status="current" if report_id == winner else "superseded",
                    superseded_by=None if report_id == winner else winner,
                ))
        return tuple(results)

    def fetch(
        self, *, job_id: str, report_id: str, ctr_unit: CtrUnit,
    ) -> AuthenticatedReachReport:
        if not _ID.fullmatch(job_id) or not _ID.fullmatch(report_id):
            raise ReportingDownloadError("invalid Google job/report identifier")
        headers = self._headers()
        job = self._metadata(f"{API_ORIGIN}/v1/jobs/{job_id}", headers)
        if job.get("id") != job_id or job.get("reportTypeId") != REPORT_TYPE:
            raise ReportingDownloadError("job is not the exact approved channel reach type")

        report = self._metadata(
            f"{API_ORIGIN}/v1/jobs/{job_id}/reports/{report_id}", headers,
        )
        if report.get("id") != report_id or report.get("jobId") != job_id:
            raise ReportingDownloadError("report metadata does not match its job/report IDs")
        start = _time(report.get("startTime"))
        end = _time(report.get("endTime"))
        created = _time(report.get("createTime"))
        if not start < end:
            raise ReportingDownloadError("report data interval is invalid")
        url = _download_url(report.get("downloadUrl"))

        # Explicit stream bounds decompressed payloads, including gzip responses.
        try:
            with self.client.stream(
                "GET", url, headers=headers, follow_redirects=False,
            ) as response:
                if response.status_code in (401, 403):
                    raise ReportingAccessDenied(
                        f"Google Reporting download denied (HTTP {response.status_code}); "
                        "check OAuth scope and report access"
                    )
                if response.status_code != 200:
                    raise ReportingDownloadError(
                        f"Google report download returned HTTP {response.status_code}"
                    )
                contents = bytearray()
                for chunk in response.iter_bytes():
                    contents.extend(chunk)
                    if len(contents) > MAX_CSV_BYTES:
                        raise ReportingDownloadError("reach CSV exceeds 4 MiB limit")
        except httpx.HTTPError as exc:
            raise ReportingDownloadError("Google report download failed") from exc
        try:
            raw = bytes(contents)
            csv = raw.decode("utf-8-sig")
        except UnicodeError as exc:
            raise ReportingDownloadError("Google report is not UTF-8 CSV") from exc
        rows = parse_reach_csv(csv, ctr_unit=ctr_unit)
        return AuthenticatedReachReport(
            job_id=job_id, report_id=report_id, report_type=REPORT_TYPE,
            source_kind="google_reporting_api_authenticated_download",
            start_time=start, end_time=end, create_time=created,
            csv_sha256=hashlib.sha256(raw).hexdigest(), rows=rows,
        )
