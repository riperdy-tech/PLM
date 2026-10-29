"""Read-only P6 C6 -> deployed P7 refusal probe on the loopback compose stack.

Uses an already-persisted READY P6 package; never creates a render or edits P6.
Only the *request copy* is marked failed, so this proves P7's deployed HTTP
boundary, not that the deployed P6 renderer has produced a timing failure.
Checks P7 publication row counts before/after the refusal. No live upload.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.db import build_engine
from plm_platform.media_timing import classify_saved_media_timing
from plm_platform.service_urls import SERVICE_PORTS
from plm_platform.temporal import Phase

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
MEDIA_URL = f"http://127.0.0.1:{SERVICE_PORTS[Phase.MEDIA]}"
YOUTUBE_URL = f"http://127.0.0.1:{SERVICE_PORTS[Phase.YOUTUBE]}"

# Only the local development channel may be used by this regression.
CHANNEL = "UC_plm_dev_mock_channel"


def _published_rows(conn: sa.Connection, package_id: str) -> tuple[int, int]:
    """Count the two possible P7 write stages for this exact package."""
    manifest_count = conn.execute(
        sa.text("SELECT count(*) FROM youtube.publish_manifests "
                "WHERE media_package_id = :package_id"),
        {"package_id": package_id},
    ).scalar_one()
    attempt_count = conn.execute(
        sa.text("SELECT count(*) FROM youtube.publish_attempts a "
                "JOIN youtube.publish_manifests m ON a.manifest_id = m.manifest_id "
                "WHERE m.media_package_id = :package_id"),
        {"package_id": package_id},
    ).scalar_one()
    return int(manifest_count), int(attempt_count)


@pytest.mark.parametrize("reason", [
    "stream_duration_unmeasured",
    "stream_duration_mismatch",
    "video_audio_duration_mismatch",
])
def test_deployed_p7_refuses_a_failed_copy_of_existing_p6_c6_without_writes(
    reason: str,
) -> None:
    settings = load_settings(dotenv_path=ROOT / ".env")
    if settings.service_token is None:
        pytest.skip("local service token unavailable")

    # Never let this diagnostic silently target a remote DB or HTTP endpoint.
    engine = build_engine(settings)
    try:
        if engine.url.host not in ("127.0.0.1", "localhost"):
            pytest.skip("this probe is allowed only against the loopback compose DB")
        token = settings.service_token.get_secret_value()
        headers = {SERVICE_TOKEN_HEADER: token}
        with engine.connect() as conn, httpx.Client(
            timeout=10, headers=headers,
        ) as client:
            for url in (MEDIA_URL, YOUTUBE_URL):
                try:
                    ready = client.get(f"{url}/readyz")
                except httpx.HTTPError as exc:
                    pytest.skip(f"local service unreachable: {type(exc).__name__}")
                if ready.status_code != 200:
                    pytest.skip(f"local service not ready: {ready.status_code}")

            configured = conn.execute(sa.text(
                "SELECT count(*) FROM youtube.channel_profiles "
                "WHERE channel_id = :channel AND active = true",
            ), {"channel": CHANNEL}).scalar_one()
            if not configured:
                pytest.skip("the local mock channel is not configured")

            package_ids = conn.execute(sa.text(
                "SELECT media_package_id FROM media.media_packages "
                "WHERE status = 'ready_for_publish' ORDER BY created_at DESC LIMIT 20",
            )).scalars().all()
            original: dict[str, Any] | None = None
            for package_id in package_ids:
                response = client.get(f"{MEDIA_URL}/media/packages/{package_id}/c6")
                if response.status_code == 200:
                    original = response.json()
                    break
            if original is None:
                pytest.skip("no persisted, C6-readable local P6 package")

            assert original["status"] == "ready_for_publish"
            assert original["media_package_id"] == package_id
            before = _published_rows(conn, package_id)
            # Keep the P6 record immutable. The changed status and QA reason
            # exist only in this in-memory HTTP request to P7.
            refused_copy = deepcopy(original)
            refused_copy["status"] = "failed"
            refused_copy["render_report"] = {
                **refused_copy.get("render_report", {}),
                "qa": {"passed": False, "reason_codes": [reason]},
            }

            result = client.post(f"{YOUTUBE_URL}/youtube/publish", json={
                "channel_id": CHANNEL,
                "operating_mode": "dry_run",
                "media_package": refused_copy,
            })
            assert result.status_code == 422, result.text
            assert result.json()["detail"]["code"] == "package_not_ready"
            assert _published_rows(conn, package_id) == before
    finally:
        engine.dispose()


def test_deployed_p7_refuses_actual_legacy_ready_p6_c6_without_timing_evidence() -> None:
    """No forged failed flag: send an actual old READY C6 with missing saved QA facts."""
    settings = load_settings(dotenv_path=ROOT / ".env")
    if settings.service_token is None:
        pytest.skip("local service token unavailable")
    engine = build_engine(settings)
    try:
        if engine.url.host not in ("127.0.0.1", "localhost"):
            pytest.skip("loopback development DB only")
        headers = {SERVICE_TOKEN_HEADER: settings.service_token.get_secret_value()}
        with engine.connect() as conn, httpx.Client(
            timeout=10, headers=headers,
        ) as client:
            for url in (MEDIA_URL, YOUTUBE_URL):
                try:
                    ready = client.get(f"{url}/readyz")
                except httpx.HTTPError as exc:
                    pytest.skip(f"local service unavailable: {type(exc).__name__}")
                if ready.status_code != 200:
                    pytest.skip("one local service is not ready")
            channel_active = conn.execute(sa.text(
                "SELECT count(*) FROM youtube.channel_profiles "
                "WHERE channel_id = :channel AND active = true",
            ), {"channel": CHANNEL}).scalar_one()
            if not channel_active:
                pytest.skip("local mock channel is not configured")

            package_ids = conn.execute(sa.text(
                "SELECT media_package_id FROM media.media_packages "
                "WHERE status = 'ready_for_publish' ORDER BY created_at DESC LIMIT 100",
            )).scalars().all()
            legacy: dict[str, Any] | None = None
            missing_reason: str | None = None
            for package_id in package_ids:
                reply = client.get(f"{MEDIA_URL}/media/packages/{package_id}/c6")
                if reply.status_code != 200:
                    continue
                candidate = reply.json()
                reason = classify_saved_media_timing(
                    candidate["status"], candidate["video"]["duration_seconds"],
                    candidate["render_report"],
                )
                if reason != "saved_stream_evidence_consistent":
                    legacy, missing_reason = candidate, reason
                    break
            if legacy is None:
                pytest.skip("no legacy READY C6 with missing timing evidence")
            assert missing_reason is not None
            before = _published_rows(conn, legacy["media_package_id"])
            response = client.post(f"{YOUTUBE_URL}/youtube/publish", json={
                "channel_id": CHANNEL, "operating_mode": "dry_run",
                "media_package": legacy,
            })
            assert response.status_code == 422, response.text
            assert response.json()["detail"]["code"] == "package_not_ready"
            assert missing_reason in response.json()["detail"]["message"]
            assert _published_rows(conn, legacy["media_package_id"]) == before
    finally:
        engine.dispose()

