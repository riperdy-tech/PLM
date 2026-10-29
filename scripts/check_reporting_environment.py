"""Secrets-safe, opt-in read-only readiness diagnostics for P7 ↔ P9 ↔ Google.

Default: local config presence only (no network). --probe-services checks only
loopback HTTP healthz/readyz. --probe-google-catalog uses OAuth to GET existing
Reporting jobs, but never requests downloads or creates a job.
"""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from analytics.ingestion.youtube_client import RealYouTubeAnalyticsClient, YouTubeAnalyticsError
from analytics.ingestion.youtube_reporting import (
    AuthenticatedReachDownloader,
    ReportingAccessDenied,
    ReportingDownloadError,
)
from plm_platform.dotenv import EnvFileError, load_env_file

_OAUTH_KEYS = (
    "YOUTUBE_OAUTH_CLIENT_ID", "YOUTUBE_OAUTH_CLIENT_SECRET",
    "YOUTUBE_OAUTH_REFRESH_TOKEN",
)
_SERVICE_PORTS = {"youtube": 8007, "analytics": 8009}
_SERVICE_NAMES = {"youtube": "YOUTUBE", "analytics": "ANALYTICS"}


def configuration_summary(config: Mapping[str, str]) -> dict[str, object]:
    """Never include any value from env or .env, even a nonsecret URL."""
    oauth_fields = {name: bool(config.get(name, "").strip()) for name in _OAUTH_KEYS}
    oauth_ready = all(oauth_fields.values())
    token_ready = bool(config.get("PLM_SERVICE_TOKEN", "").strip())
    return {
        "mode": "offline_configuration_only",
        "oauth_fields_present": oauth_fields,
        "oauth_credentials": "present_unverified" if oauth_ready else "incomplete",
        "service_token": "present_unverified" if token_ready else "missing",
        "service_url_overrides": {
            phase: bool(config.get(f"PLM_SERVICE_URL__{name}", "").strip())
            for phase, name in _SERVICE_NAMES.items()
        },
        "can_attempt_authenticated_p9_read": token_ready,
        "can_attempt_google_catalog_read": oauth_ready,
        "google_oauth_scopes": "not_checked",
        "google_catalog": "not_checked",
        "p7_service": "not_checked",
        "p9_service": "not_checked",
        "google_report_download": "not_checked",
        "actual_candidate_exposure": "not_verified",
    }


def _loopback_target(config: Mapping[str, str], phase: str) -> str | None:
    """Refuse off-host config overrides instead of sending private probes there."""
    key = f"PLM_SERVICE_URL__{_SERVICE_NAMES[phase]}"
    raw = config.get(key, "").strip()
    if not raw:
        return f"http://127.0.0.1:{_SERVICE_PORTS[phase]}"
    try:
        parsed = urlsplit(raw)
        if (
            parsed.scheme not in ("http", "https")
            or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or parsed.path not in ("", "/")
            or not parsed.port
        ):
            return None
    except ValueError:
        return None
    return raw.rstrip("/")


def probe_service(client: httpx.Client, base_url: str, name: str) -> str:
    """Only GET the two platform health surfaces; never include response text."""
    try:
        alive = client.get(base_url + "/healthz", follow_redirects=False)
        if alive.status_code != 200 or alive.json() != {
            "service": name, "status": "alive",
        }:
            return "unverified_or_unavailable"
        ready = client.get(base_url + "/readyz", follow_redirects=False)
        if ready.status_code == 200 and ready.json() == {
            "service": name, "status": "ready",
        }:
            return "health_and_database_ready"
        if ready.status_code == 503:
            return "alive_database_not_ready"
        return "alive_readiness_unverified"
    except (httpx.HTTPError, ValueError):
        return "unverified_or_unavailable"


def probe_internal_auth(
    client: httpx.Client, config: Mapping[str, str],
) -> dict[str, str]:
    """Read-only P7/P9 token acceptance on reserved nonexistent identifiers.

    No ids are enumerated and no response body or token is included in output.
    """
    token = config.get("PLM_SERVICE_TOKEN", "").strip()
    result: dict[str, str] = {}
    if not token:
        return {"p7_auth": "service_token_missing", "p9_auth": "service_token_missing"}
    for phase, key, suffix in (
        ("youtube", "p7_auth", "/youtube/attempts/plm_readiness_probe_reserved"),
        ("analytics", "p9_auth", "/analytics/metrics/videos/plm_readiness_probe_reserved"),
    ):
        target = _loopback_target(config, phase)
        if target is None:
            result[key] = "non_loopback_or_invalid_url_not_probed"
            continue
        try:
            response = client.get(
                target + suffix,
                headers={"X-PLM-Service-Token": token},
                follow_redirects=False,
            )
            if phase == "youtube":
                detail = response.json().get("detail", {})
                valid = response.status_code == 404 and isinstance(detail, dict) and (
                    detail.get("code") == "attempt_not_found"
                )
            else:
                valid = response.status_code == 200 and response.json() == []
            result[key] = "token_accepted_read_only" if valid else "not_verified"
        except (httpx.HTTPError, ValueError, AttributeError):
            result[key] = "not_verified"
    return result


def probe_google_catalog(analytics: RealYouTubeAnalyticsClient,
                         http_client: httpx.Client) -> str:
    """One OAuth-authenticated catalog GET; does not download or store reports."""
    if not analytics.is_configured:
        return "oauth_credentials_missing"
    downloader = AuthenticatedReachDownloader(
        client=http_client, access_token=analytics._get_access_token,
    )
    try:
        jobs = downloader.list_reach_jobs()
    except ReportingAccessDenied:
        return "permission_denied"
    except YouTubeAnalyticsError:
        return "oauth_refresh_failed"
    except (ReportingDownloadError, httpx.HTTPError):
        return "catalog_unavailable"
    return "read_access_confirmed_jobs_found" if jobs else "read_access_confirmed_no_reach_job"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Secrets-safe readiness; offline by default, GET-only probes opt-in.",
    )
    parser.add_argument("--probe-services", action="store_true",
                        help="GET P7/P9 loopback healthz and readyz only")
    parser.add_argument("--probe-internal-auth", action="store_true",
                        help="GET reserved nonexistent P7/P9 records on loopback with service token")
    parser.add_argument("--probe-google-catalog", action="store_true",
                        help="OAuth GET existing Reporting job list; no report download")
    options = parser.parse_args(argv)
    try:
        values = {**load_env_file(Path(".env")), **os.environ}
    except EnvFileError:
        print(json.dumps({"status": "invalid_local_env_file", "secrets_printed": False}))
        return 2
    result = configuration_summary(values)
    exit_code = 0
    if options.probe_services:
        with httpx.Client(timeout=3.0, follow_redirects=False, trust_env=False) as client:
            for phase, key in (("youtube", "p7_service"), ("analytics", "p9_service")):
                target = _loopback_target(values, phase)
                result[key] = (
                    probe_service(client, target, phase) if target
                    else "non_loopback_or_invalid_url_not_probed"
                )
                if result[key] != "health_and_database_ready":
                    exit_code = 2
    if options.probe_internal_auth:
        with httpx.Client(timeout=3.0, follow_redirects=False, trust_env=False) as client:
            result.update(probe_internal_auth(client, values))
        if any(result[key] != "token_accepted_read_only" for key in ("p7_auth", "p9_auth")):
            exit_code = 2
    if options.probe_google_catalog:
        analytics = RealYouTubeAnalyticsClient()
        with httpx.Client(timeout=15.0, follow_redirects=False) as client:
            result["google_catalog"] = probe_google_catalog(analytics, client)
        if not str(result["google_catalog"]).startswith("read_access_confirmed"):
            result["google_oauth_scopes"] = analytics.reporting_scope_status()
            exit_code = 2
        else:
            result["google_oauth_scopes"] = "reporting_catalog_read_accepted"
    if options.probe_services or options.probe_internal_auth or options.probe_google_catalog:
        result["mode"] = "explicit_read_only_probes"
    result["network_probes"] = {
        "services": options.probe_services,
        "internal_auth": options.probe_internal_auth,
        "google_catalog": options.probe_google_catalog,
    }
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
