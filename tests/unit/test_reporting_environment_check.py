"""Secrets-safe runtime preflight and opt-in read-only probes."""
from __future__ import annotations

import json

import httpx
import pytest

from scripts import check_reporting_environment as readiness

FULL = {
    "YOUTUBE_OAUTH_CLIENT_ID": "private-client-id",
    "YOUTUBE_OAUTH_CLIENT_SECRET": "private-client-secret",
    "YOUTUBE_OAUTH_REFRESH_TOKEN": "private-refresh-token",
    "PLM_SERVICE_TOKEN": "private-service-token",
}


def test_config_presence_does_not_claim_live_oauth_scope() -> None:
    summary = readiness.configuration_summary(FULL)
    assert summary["oauth_credentials"] == "present_unverified"
    assert summary["service_token"] == "present_unverified"
    assert summary["google_oauth_scopes"] == "not_checked"
    assert summary["google_catalog"] == "not_checked"
    assert summary["p7_service"] == "not_checked"
    assert summary["google_report_download"] == "not_checked"
    output = json.dumps(summary)
    assert all(value not in output for value in FULL.values())


def test_partial_credentials_cannot_be_reported_as_ready() -> None:
    summary = readiness.configuration_summary({"YOUTUBE_OAUTH_CLIENT_ID": "yes"})
    assert summary["oauth_credentials"] == "incomplete"
    assert summary["can_attempt_google_catalog_read"] is False
    assert summary["service_token"] == "missing"
    assert summary["can_attempt_authenticated_p9_read"] is False


@pytest.mark.parametrize("url", [
    "https://outside.example:443", "http://127.0.0.1.evil.example:8007",
    "http://user:pass@127.0.0.1:8007", "http://127.0.0.1:8007/other",
    "http://127.0.0.1:8007?token=private", "http://localhost",
    "ftp://127.0.0.1:8007", "http://127.0.0.1:invalid",
])
def test_nonloopback_or_ambiguous_service_targets_are_rejected(url: str) -> None:
    assert readiness._loopback_target({"PLM_SERVICE_URL__YOUTUBE": url}, "youtube") is None


def test_default_service_health_targets_loopback() -> None:
    assert readiness._loopback_target({}, "youtube") == "http://127.0.0.1:8007"
    assert readiness._loopback_target({}, "analytics") == "http://127.0.0.1:8009"


def test_service_health_probes_only_two_get_endpoints_without_tokens() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.method == "GET"
        assert "authorization" not in request.headers
        assert "x-plm-service-token" not in request.headers
        status = "alive" if request.url.path == "/healthz" else "ready"
        return httpx.Response(200, json={"service": "youtube", "status": status})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert readiness.probe_service(client, "http://127.0.0.1:8007", "youtube") == (
            "health_and_database_ready"
        )
    assert seen == ["/healthz", "/readyz"]


def test_live_service_unready_is_not_reported_as_ready() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"service": "youtube", "status": "alive"})
        return httpx.Response(503, json={"service": "youtube", "status": "not_ready"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert readiness.probe_service(client, "http://127.0.0.1:8007", "youtube") == (
            "alive_database_not_ready"
        )


def test_google_catalog_probe_never_downloads_or_creates_jobs() -> None:
    seen: list[str] = []

    class TokenHolder:
        is_configured = True

        def _get_access_token(self) -> str:
            return "private-oauth-access"

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.method == "GET"
        assert request.url.host == "youtubereporting.googleapis.com"
        return httpx.Response(200, json={"jobs": [
            {"id": "reach_1", "reportTypeId": "channel_reach_basic_a1"},
        ]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = readiness.probe_google_catalog(TokenHolder(), client)  # type: ignore[arg-type]
    assert result == "read_access_confirmed_jobs_found"
    assert seen == ["/v1/jobs"]


def test_google_catalog_403_does_not_claim_scope() -> None:
    class TokenHolder:
        is_configured = True

        def _get_access_token(self) -> str:
            return "private-oauth-access"

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(403, text="private-data")),
    ) as client:
        assert readiness.probe_google_catalog(
            TokenHolder(), client,  # type: ignore[arg-type]
        ) == "permission_denied"


def test_internal_auth_probe_only_reads_reserved_records_and_redacts_token() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.method == "GET"
        assert request.headers["x-plm-service-token"] == FULL["PLM_SERVICE_TOKEN"]
        if request.url.path.startswith("/youtube/attempts/"):
            return httpx.Response(404, json={"detail": {"code": "attempt_not_found"}})
        if request.url.path.startswith("/analytics/metrics/videos/"):
            return httpx.Response(200, json=[])
        pytest.fail("probe must not fetch or change real records")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = readiness.probe_internal_auth(client, FULL)
    assert result == {
        "p7_auth": "token_accepted_read_only",
        "p9_auth": "token_accepted_read_only",
    }
    assert len(seen) == 2
    assert all("plm_readiness_probe_reserved" in item for item in seen)
    assert FULL["PLM_SERVICE_TOKEN"] not in json.dumps(result)


def test_missing_or_denied_service_token_never_passes_internal_auth() -> None:
    assert readiness.probe_internal_auth(
        httpx.Client(), {},
    ) == {"p7_auth": "service_token_missing", "p9_auth": "service_token_missing"}

    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(401)),
    ) as client:
        report = readiness.probe_internal_auth(client, FULL)
    assert report == {"p7_auth": "not_verified", "p9_auth": "not_verified"}


def test_default_cli_is_offline_and_never_displays_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(readiness, "load_env_file", lambda _: dict(FULL))
    monkeypatch.setattr(readiness.os, "environ", {})
    assert readiness.main([]) == 0
    printed = capsys.readouterr().out
    assert json.loads(printed)["network_probes"] == {
        "services": False, "internal_auth": False, "google_catalog": False,
    }
    assert all(private not in printed for private in FULL.values())


def test_bad_env_file_reports_only_safe_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from plm_platform.dotenv import EnvFileError

    def invalid(_):
        raise EnvFileError("file contains private-supersecret")

    monkeypatch.setattr(readiness, "load_env_file", invalid)
    assert readiness.main([]) == 2
    printed = capsys.readouterr().out
    assert "private-supersecret" not in printed
    assert json.loads(printed)["status"] == "invalid_local_env_file"
