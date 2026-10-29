"""P6 assembles over HTTP on the running stack (R4): P5 manifests, MinIO, FFmpeg.

The producer side is staged directly, standing in for P4 and P5: synthetic track audio
is stored in MinIO under ``music/`` as P4 would store it, and P5 release manifests that
point at it are inserted into P5's table. Everything after that is the deployed system:
``POST /media/packages/assemble`` on the ``api-media`` container takes candidate ids,
asks ``api-qa`` for the manifests and the exception queue for holds, fetches and
verifies the audio from MinIO, renders with FFmpeg (preview profile), runs post-render
QA, and stores every output in MinIO.

Proves: a ready package whose masters and thumbnails are in MinIO under ``media/`` with
the checksums P6 recorded (read back with verification); a recording P5 did not clear
is refused; audio altered in MinIO after clearance fails closed.

Needs ``docker compose up -d --wait`` with the api-media image rebuilt for the R4
dependencies; skips cleanly when the stack is unreachable.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from plm_contracts import GateDisposition
from plm_platform.config import SERVICE_TOKEN_HEADER, Settings, load_settings
from plm_platform.db import build_engine, session_factory
from plm_platform.service_urls import LOOPBACK_HOST, service_url
from plm_platform.storage import (
    MUSIC_PREFIX,
    build_client,
    from_uri,
    get_verified,
    put_content,
    replace_object,
    to_uri,
)
from plm_platform.temporal import Phase
from sqlalchemy import text
from sqlalchemy.orm import Session

from services.quality_gate.gate.manifest import build_release_manifest
from services.quality_gate.storage.repositories import insert_release_manifest
from tests.support.media_audio import synth_track
from tests.support.seams import sample_lineage

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
# 3 BPM above the R5 step test's tracks: P6 holds a near-copy of an earlier same-brand
# package (album-texture-v1, 0.998); against those tracks these measure at most 0.93.
TRACKS = ((12.0, {"bpm": 123.0, "tonic": 0}), (13.0, {"bpm": 125.0, "tonic": 7}),
          (12.0, {"bpm": 121.0, "tonic": 9, "minor": True}))


@pytest.fixture(scope="module")
def settings() -> Settings:
    return load_settings(dotenv_path=REPO_ROOT / ".env")


@pytest.fixture(scope="module")
def media(settings: Settings) -> Iterator[httpx.Client]:
    if settings.service_token is None:
        pytest.skip("PLM_SERVICE_TOKEN is not set")
    base = service_url(Phase.MEDIA, default_host=LOOPBACK_HOST)
    client = httpx.Client(base_url=base, timeout=300.0, headers={
        SERVICE_TOKEN_HEADER: settings.service_token.get_secret_value()})
    try:
        ready = client.get("/readyz")
    except httpx.HTTPError as exc:
        pytest.skip(f"api-media not reachable at {base}: {exc}")
    if ready.status_code != 200:
        pytest.skip(f"api-media not ready: {ready.status_code}")
    yield client
    client.close()


@pytest.fixture(scope="module")
def p5(settings: Settings) -> Iterator[Session]:
    engine = build_engine(settings)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        pytest.skip(f"postgres not reachable: {exc}")
    with session_factory(engine)() as session:
        yield session
    engine.dispose()


def _stage(settings: Settings, p5: Session, run: str, *,
           disposition: GateDisposition = GateDisposition.PASSED) -> list[dict[str, Any]]:
    """Store the track audio as P4 would and insert P5's manifests; return the facts."""
    lineage = sample_lineage(experiment_id=f"exp_it_{run}", content_arm_id=f"arm_it_{run}")
    staged = []
    for n, (seconds, synth) in enumerate(TRACKS):
        candidate_id = f"cand_it_{run}_{n}"
        stored = put_content(MUSIC_PREFIX, f"it_p6/{candidate_id}/full_mix.wav",
                             synth_track(seconds=seconds, **synth),  # type: ignore[arg-type]
                             content_type="audio/wav", settings=settings,
                             client=build_client(settings))
        uri = to_uri(stored.key, settings=settings)
        insert_release_manifest(p5, build_release_manifest(
            candidate_id=candidate_id, canonical_artifact_id=f"art_it_{run}_{n}",
            artifact_uri=uri, artifact_sha256=stored.digest,
            measured_duration_seconds=seconds, track_spec_id=f"trk_it_{run}_{n}",
            lineage=lineage, disposition=disposition, override=None,
            technical_status="PASSED", commercial_use_status="eligible",
            provenance_status="COMPLETE", ai_disclosure_required=True, rights_risk="LOW",
            policy_status="COMPLIANT", evaluation_id_str=f"eval_it_{run}_{n}",
            evaluation_policy_version="p5-v1", warning_codes=[],
            evidence_bundle_id=f"evi_it_{run}_{n}", now=NOW,
        ))
        staged.append({"candidate_id": candidate_id, "uri": uri, "key": stored.key})
    p5.commit()
    return staged


def _request(staged: list[dict[str, Any]]) -> dict[str, Any]:
    return {"candidate_ids": [s["candidate_id"] for s in staged], "brand_id": "neon_seoul",
            # Only all tracks together land in this declared range.
            "duration_range_seconds": {"min_seconds": int(sum(t[0] for t in TRACKS) - 4.0),
                                       "max_seconds": int(sum(t[0] for t in TRACKS) - 4.0) + 1},
            "visual_profile": "it_waveform", "playlist_concept": "Integration Focus",
            "render_profile": "preview_360p_v1"}


def test_p6_assembles_renders_and_stores_from_p5_and_minio(
        settings: Settings, media: httpx.Client, p5: Session) -> None:
    staged = _stage(settings, p5, uuid.uuid4().hex[:10])
    resp = media.post("/media/packages/assemble", json=_request(staged))
    assert resp.status_code == 200, resp.text
    pkg = resp.json()
    assert pkg["status"] == "ready_for_publish", pkg["blocking_reason_codes"]
    assert pkg["render_report"]["toolchain"].startswith("ffmpeg version 7.")
    assert len(pkg["chapters"]) == 3

    minio = build_client(settings)
    outputs = [(pkg["video_master_uri"], pkg["video_checksum"]),
               (pkg["audio_master_uri"], pkg["audio_checksum"])]
    outputs += [(t["uri"], t["checksum"]) for t in pkg["thumbnails"]]
    for uri, checksum in outputs:
        key = from_uri(uri, settings=settings)
        assert key.startswith("media/") and key.endswith(checksum)
        get_verified(key, settings=settings, client=minio)  # re-hashes the stored bytes
    video = get_verified(from_uri(pkg["video_master_uri"], settings=settings),
                         settings=settings, client=minio)
    assert video[4:8] == b"ftyp"

    c6 = media.get(f"/media/packages/{pkg['media_package_id']}/c6").json()
    assert c6["video"]["checksum"] == pkg["video_checksum"]
    assert c6["lineage"]["content_arm_id"].startswith("arm_it_")
    assert [t["song_id"] for t in c6["tracklist"]] != [] and \
        {t["song_id"] for t in c6["tracklist"]} == {s["candidate_id"] for s in staged}


def test_p6_refuses_a_recording_p5_did_not_clear(
        settings: Settings, media: httpx.Client, p5: Session) -> None:
    staged = _stage(settings, p5, uuid.uuid4().hex[:10], disposition=GateDisposition.REJECTED)
    resp = media.post("/media/packages/assemble", json=_request(staged))
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "track_not_cleared"


def test_audio_altered_in_minio_after_clearance_fails_closed(
        settings: Settings, media: httpx.Client, p5: Session) -> None:
    staged = _stage(settings, p5, uuid.uuid4().hex[:10])
    replace_object(staged[1]["key"], synth_track(seconds=12.0, bpm=90.0), settings=settings,
                   client=build_client(settings))
    resp = media.post("/media/packages/assemble", json=_request(staged))
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "source_checksum_mismatch"
