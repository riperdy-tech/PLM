"""Media Factory API: service-token auth and assembly by candidate id (ADR-0028, R4).

The backend is in-process: a fake P5 manifest source and hold source, an in-memory
artifact store and the stand-in toolchain (no rendering). Proves the API takes ids, not
manifest bodies; refuses a missing, uncleared or held recording with its reason code;
answers 503 when an authority or the backend is unavailable; persists and serves the
package and its C6 payload; and replays an assembled request without rendering again.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from media_factory.api.app import create_app
from media_factory.backend import MediaBackend
from media_factory.config import MediaFactorySettings
from plm_contracts.seams import parse_seam
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings

from tests.support.media_backend import (
    FakeHolds,
    FakeP5,
    InMemoryArtifactStore,
    StandInToolchain,
    backend_for,
    stored_manifest,
    synth_specs,
)
from tests.support.media_db import create_media_engine, media_session
from tests.support.render_evidence import attest_termination

MINIMAL_ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
}
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
TOKEN = "test-token"
HEADERS = {SERVICE_TOKEN_HEADER: TOKEN}
OVERRIDE = {
    "status": "CLEARED_BY_OVERRIDE", "quarantine_case_id": "qc_01", "auditor_id": "aud_01",
    "resolved_at": "2026-09-25T12:00:00+00:00", "reason_codes": ["UNKNOWN_COMMERCIAL_LICENSE"],
}


def _client(backend: MediaBackend | None) -> TestClient:
    platform = load_settings(env={**MINIMAL_ENV, "PLM_SERVICE_TOKEN": TOKEN})
    engine = create_media_engine(static_pool=True)
    app = create_app(session_factory=lambda: media_session(engine),
                     settings=MediaFactorySettings(platform=platform), clock=FixedClock(NOW),
                     backend=backend)
    return TestClient(app)


def _request(*candidate_ids: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "candidate_ids": list(candidate_ids), "brand_id": "brand_neon",
        "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
        "visual_profile": "brand_neon_rain_v1",
        "playlist_concept": "Deep Focus", "render_profile": "preview_360p_v1",
    }
    body.update(overrides)
    return body


def _rebuilt(manifest: ReleaseManifestPayload, **changes: Any) -> ReleaseManifestPayload:
    """The manifest P5 would serve with ``changes``, validated by the shared model."""
    return parse_seam(ReleaseManifestPayload, {**manifest.to_wire(), **changes})


@pytest.fixture
def cleared() -> tuple[InMemoryArtifactStore, list[Any]]:
    store = InMemoryArtifactStore()
    manifests = [stored_manifest(store, f"song_api_{n}", measured_duration_seconds=60.0, synth=s)
                 for n, s in enumerate(synth_specs())]
    return store, manifests


def test_media_api_health_endpoint() -> None:
    app = create_app(session_factory=None, settings=MediaFactorySettings(),
                     clock=FixedClock(NOW))
    response = TestClient(app).get("/media/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "media_factory"}


def test_media_api_auth_enforcement() -> None:
    platform_no_token = load_settings(env=MINIMAL_ENV)
    app_no_token = create_app(session_factory=None,
                              settings=MediaFactorySettings(platform=platform_no_token),
                              clock=FixedClock(NOW))
    resp = TestClient(app_no_token).get("/media/packages/mp_123")
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "blocked_pending_credentials"

    client = _client(None)
    assert client.get("/media/packages/mp_123").json()["detail"]["code"] == \
        "invalid_service_token"
    wrong = client.get("/media/packages/mp_123", headers={SERVICE_TOKEN_HEADER: "wrong"})
    assert wrong.status_code == 401


def test_assemble_by_candidate_id_then_query_and_replay(cleared: Any) -> None:
    store, manifests = cleared
    toolchain = StandInToolchain()
    client = _client(backend_for(store, manifests, toolchain=toolchain))
    ids = [m.candidate_id for m in manifests]

    resp = client.post("/media/packages/assemble", json=_request(*ids), headers=HEADERS)
    assert resp.status_code == 200, resp.text
    pkg = resp.json()
    assert pkg["status"] == "ready_for_publish"
    assert pkg["blocking_reason_codes"] == []
    assert len(pkg["chapters"]) == 3
    assert pkg["video_master_uri"].startswith("memory://media/")
    # The HTTP entry point commits a real SQL claim before this stand-in render.
    # Provisional blobs remain segregated by a hashed execution identity.
    namespace = pkg["render_report"]["artifact_claim_namespace"]
    assert isinstance(namespace, str) and len(namespace) == 64
    assert f"/claims/{namespace}/" in pkg["video_master_uri"]
    assert f"/claims/{namespace}/" in pkg["audio_master_uri"]
    assert all(f"/claims/{namespace}/" in item["uri"] for item in pkg["thumbnails"])
    assert all(f"/claims/{namespace}/" in key for key in store.objects
               if key.startswith(("media/renders/", "media/thumbnails/")))

    assert client.get(f"/media/packages/{pkg['media_package_id']}",
                      headers=HEADERS).json()["video_checksum"] == pkg["video_checksum"]
    c6 = client.get(f"/media/packages/{pkg['media_package_id']}/c6", headers=HEADERS).json()
    assert c6["video"]["checksum"] == pkg["video_checksum"]
    playlist = client.get(f"/media/playlists/{pkg['playlist_session_id']}",
                          headers=HEADERS).json()
    assert sorted(t["song_id"] for t in playlist["tracks"]) == sorted(ids)

    # A replay must not depend on the historical catalog being readable.
    rendered = list(toolchain.calls)
    with patch("media_factory.service.list_catalog_albums",
               side_effect=RuntimeError("unavailable historical catalog")):
        again = client.post("/media/packages/assemble",
                            json=_request(*reversed(ids)), headers=HEADERS)
    assert again.status_code == 200
    assert again.json()["media_package_id"] == pkg["media_package_id"]
    assert toolchain.calls == rendered


def test_new_album_with_reused_music_is_rejected_by_stored_catalog(cleared: Any) -> None:
    store, first_album = cleared
    repeat_album = [
        stored_manifest(
            store, f"song_reused_{i}", measured_duration_seconds=60.0, synth=spec
        )
        for i, spec in enumerate(synth_specs())
    ]
    toolchain = StandInToolchain()
    client = _client(backend_for(
        store, [*first_album, *repeat_album], toolchain=toolchain,
    ))
    first_ids = [track.candidate_id for track in first_album]
    second_ids = [track.candidate_id for track in repeat_album]

    initial = client.post("/media/packages/assemble",
                          json=_request(*first_ids), headers=HEADERS)
    assert initial.status_code == 200, initial.text
    report = initial.json()["render_report"]["album_texture"]
    assert report["catalog"]["coverage"] == "complete"
    assert report["catalog"]["compared_packages"] == 0
    assert len(report["evidence"]["track_signatures"]) == 3
    rendered = list(toolchain.calls)

    repeated = client.post("/media/packages/assemble",
                           json=_request(*second_ids), headers=HEADERS)
    assert repeated.status_code == 422, repeated.text
    assert repeated.json()["detail"]["code"] == "album_catalog_near_duplicate"
    assert toolchain.calls == rendered


def test_a_manifest_body_is_not_accepted(cleared: Any) -> None:
    store, manifests = cleared
    body = _request("x") | {"track_manifests": [manifests[0].to_wire()]}
    resp = _client(backend_for(store, manifests)).post(
        "/media/packages/assemble", json=body, headers=HEADERS)
    assert resp.status_code == 422


@pytest.mark.parametrize(("change", "code"), [
    ({"disposition": "QUARANTINED"}, "track_not_cleared"),
    ({"disposition": "REJECTED"}, "track_not_cleared"),
    (None, "manifest_missing"),
])
def test_a_recording_p5_did_not_clear_is_refused(cleared: Any, change: dict[str, Any] | None,
                                                 code: str) -> None:
    store, manifests = cleared
    served = list(manifests)
    if change is None:
        served = served[:2]
    else:
        served[2] = _rebuilt(manifests[2], **change)
    toolchain = StandInToolchain()
    client = _client(backend_for(store, served, toolchain=toolchain))
    resp = client.post("/media/packages/assemble",
                       json=_request(*[m.candidate_id for m in manifests]), headers=HEADERS)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == code
    assert resp.json()["detail"]["field_paths"] == ["candidate_ids[2]"]
    assert toolchain.calls == []


def test_an_overridden_quarantine_is_accepted(cleared: Any) -> None:
    store, manifests = cleared
    served = list(manifests)
    served[2] = _rebuilt(manifests[2], disposition="QUARANTINED", override=OVERRIDE)
    resp = _client(backend_for(store, served)).post(
        "/media/packages/assemble", json=_request(*[m.candidate_id for m in manifests]),
        headers=HEADERS)
    assert resp.status_code == 200, resp.text


def test_an_open_hold_on_a_cleared_recording_blocks(cleared: Any) -> None:
    store, manifests = cleared
    holds = FakeHolds({f"recording/{manifests[0].candidate_id}": ("CONTENT_ID_DISPUTE",)})
    resp = _client(backend_for(store, manifests, holds=holds)).post(
        "/media/packages/assemble", json=_request(*[m.candidate_id for m in manifests]),
        headers=HEADERS)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "open_hold"


def test_unavailable_authorities_and_backend_are_503(cleared: Any) -> None:
    store, manifests = cleared
    ids = [m.candidate_id for m in manifests]
    down = backend_for(store, manifests)
    down = MediaBackend(manifests=FakeP5(manifests, unavailable=True), holds=down.holds,
                        store=down.store, toolchain=down.toolchain, brands=down.brands)
    resp = _client(down).post("/media/packages/assemble", json=_request(*ids), headers=HEADERS)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "clearance_unverifiable"

    resp = _client(None).post("/media/packages/assemble", json=_request(*ids), headers=HEADERS)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "media_backend_unavailable"


@pytest.mark.parametrize(("override", "code"), [
    ({"brand_id": "brand_unknown"}, "brand_profile_unknown"),
    ({"render_profile": "yt_8k_v9"}, "unknown_render_profile"),
    ({"duration_range_seconds": {"min_seconds": 900, "max_seconds": 1000}},
     "playlist_supply_short"),
])
def test_unknown_brand_profile_or_short_supply_is_refused(
        cleared: Any, override: dict[str, Any], code: str) -> None:
    store, manifests = cleared
    resp = _client(backend_for(store, manifests)).post(
        "/media/packages/assemble",
        json=_request(*[m.candidate_id for m in manifests], **override), headers=HEADERS)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == code


def test_a_package_blocked_by_post_render_qa_is_reported(cleared: Any) -> None:
    store, manifests = cleared
    resp = _client(backend_for(store, manifests,
                               toolchain=StandInToolchain(loudness_lufs=-25.0))).post(
        "/media/packages/assemble", json=_request(*[m.candidate_id for m in manifests]),
        headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed"
    assert resp.json()["blocking_reason_codes"] == ["loudness_out_of_tolerance"]


@pytest.mark.parametrize(("probe_overrides", "expected_codes"), [
    ({"audio_stream_duration_seconds": None}, {"stream_duration_unmeasured"}),
    ({"audio_stream_duration_seconds": 100.0},
     {"stream_duration_mismatch", "video_audio_duration_mismatch"}),
    ({"video_stream_duration_seconds": None}, {"stream_duration_unmeasured"}),
])
def test_stream_duration_failure_survives_authenticated_p6_http_and_replay(
    cleared: Any, probe_overrides: dict[str, float | None],
    expected_codes: set[str],
) -> None:
    """P6 emits its real package failure/codes on POST and persisted GET."""
    store, manifests = cleared
    toolchain = StandInToolchain(probe_overrides=probe_overrides)
    client = _client(backend_for(store, manifests, toolchain=toolchain))
    ids = [m.candidate_id for m in manifests]
    first = client.post("/media/packages/assemble", json=_request(*ids), headers=HEADERS)
    assert first.status_code == 200, first.text
    pkg = first.json()
    assert pkg["status"] == "failed"
    assert expected_codes <= set(pkg["blocking_reason_codes"])
    assert expected_codes <= set(pkg["render_report"]["qa"]["reason_codes"])
    retrieved = client.get(f"/media/packages/{pkg['media_package_id']}", headers=HEADERS)
    assert retrieved.status_code == 200
    assert retrieved.json()["status"] == "failed"
    assert retrieved.json()["blocking_reason_codes"] == pkg["blocking_reason_codes"]
    retry = client.post("/media/packages/assemble", json=_request(*ids), headers=HEADERS)
    assert retry.status_code == 200
    assert retry.json()["blocking_reason_codes"] == pkg["blocking_reason_codes"]
    assert toolchain.calls.count("video") == 1

    # Feed P7 the real persisted C6 hop-1 payload, not a hand-built failed
    # package. A blocked P6 render must never reach the publishing adapter.
    from youtube_publishing.client.mock import MockYouTubeClient

    from tests.unit.test_youtube_api_r5 import HEADERS as P7_HEADERS
    from tests.unit.test_youtube_api_r5 import _app as p7_app

    c6_response = client.get(f"/media/packages/{pkg['media_package_id']}/c6",
                             headers=HEADERS)
    assert c6_response.status_code == 200, c6_response.text
    assert c6_response.json()["status"] == "failed"
    youtube = MockYouTubeClient()
    refused = p7_app(client=youtube).post(
        "/youtube/publish", headers=P7_HEADERS, json={
            "channel_id": "UC_r5", "operating_mode": "dry_run",
            "media_package": c6_response.json(),
        },
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "package_not_ready"
    assert youtube.recorded_calls == []


@pytest.mark.parametrize(("which", "value", "reason"), [
    ("true_peak", None, "true_peak_unmeasurable"),
    ("true_peak", float("nan"), "true_peak_unmeasurable"),
    ("integrated", float("nan"), "loudness_unmeasurable"),
    ("probe_duration", float("nan"), "duration_unmeasured"),
    ("probe_fps", float("nan"), "fps_mismatch"),
])
def test_invalid_measurements_persist_as_p6_http_failure_and_block_p7(
    cleared: Any, which: str, value: float | None, reason: str,
) -> None:
    """Nonfinite measured numbers must never crash HTTP JSON or reach publishing."""
    from media_factory.rendering.toolchain import LoudnessMeasurement, ProbeReport
    from youtube_publishing.client.mock import MockYouTubeClient

    from tests.unit.test_youtube_api_r5 import HEADERS as P7_HEADERS
    from tests.unit.test_youtube_api_r5 import _app as p7_app

    class InvalidLoudnessToolchain(StandInToolchain):
        def measure_loudness(self, path: Path) -> LoudnessMeasurement:
            measured = super().measure_loudness(path)
            if which == "true_peak":
                return replace(measured, true_peak_dbtp=value)
            if which == "integrated":
                return replace(measured, integrated_lufs=value)
            return measured

        def probe(self, path: Path) -> ProbeReport:
            measured = super().probe(path)
            if which == "probe_duration":
                return replace(measured, duration_seconds=value)
            if which == "probe_fps":
                return replace(measured, streams=(
                    replace(measured.streams[0], fps=value), measured.streams[1],
                ))
            return measured

    def strict_json(raw: str) -> Any:
        def reject_constant(value: str) -> None:
            raise ValueError(f"non-JSON numeric constant: {value}")
        return json.loads(raw, parse_constant=reject_constant)

    store, manifests = cleared
    toolchain = InvalidLoudnessToolchain()
    client = _client(backend_for(store, manifests, toolchain=toolchain))
    ids = [m.candidate_id for m in manifests]
    request = _request(*ids)
    response = client.post("/media/packages/assemble", json=request, headers=HEADERS)
    assert response.status_code == 200, response.text
    package = strict_json(response.text)
    assert package["status"] == "failed"
    assert reason in package["blocking_reason_codes"]
    render_id = package["render_report"]["render_job_id"]
    saved = client.get(f"/media/renders/{render_id}", headers=HEADERS)
    assert saved.status_code == 200
    assert strict_json(saved.text)["job_status"] == "failed"
    assert strict_json(saved.text)["error_code"] == "post_render_qa_failed"
    persisted = client.get(f"/media/packages/{package['media_package_id']}", headers=HEADERS)
    assert persisted.status_code == 200
    assert strict_json(persisted.text)["blocking_reason_codes"] == package["blocking_reason_codes"]
    replay = client.post("/media/packages/assemble", json=request, headers=HEADERS)
    assert replay.status_code == 200
    assert strict_json(replay.text)["media_package_id"] == package["media_package_id"]
    assert toolchain.calls.count("video") == 1

    c6 = client.get(f"/media/packages/{package['media_package_id']}/c6", headers=HEADERS)
    assert c6.status_code == 200
    assert strict_json(c6.text)["status"] == "failed"
    mock = MockYouTubeClient()
    refused = p7_app(client=mock).post("/youtube/publish", headers=P7_HEADERS, json={
        "channel_id": "UC_r5", "operating_mode": "dry_run", "media_package": c6.json(),
    })
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "package_not_ready"
    assert mock.recorded_calls == []


def test_actual_p6_http_failure_payload_is_refused_by_pipeline_transport(
    cleared: Any,
) -> None:
    """Join real P6 TestClient JSON to real pipeline HTTP logic, no external services.

    This does not start Temporal or live P7; the pipeline's mock transport
    delivers the exact failed P6 response instead of a hand-written fixture.
    """
    import asyncio

    from plm_platform.temporal import ValidationFailure

    from tests.unit.test_pipeline_http_assemble import _Phases, _steps
    from tests.unit.test_pipeline_http_assemble import _request as pipeline_request

    store, manifests = cleared
    client = _client(backend_for(
        store, manifests, toolchain=StandInToolchain(
            probe_overrides={"audio_stream_duration_seconds": 80.0},
        ),
    ))
    ids = [m.candidate_id for m in manifests]
    response = client.post(
        "/media/packages/assemble", json=_request(*ids), headers=HEADERS,
    )
    assert response.status_code == 200
    actual_p6_answer = response.json()
    assert actual_p6_answer["status"] == "failed"
    assert "video_audio_duration_mismatch" in actual_p6_answer["blocking_reason_codes"]

    phases = _Phases({"c0": (response.status_code, actual_p6_answer)})
    with pytest.raises(ValidationFailure, match="video_audio_duration_mismatch"):
        asyncio.run(_steps(phases).assemble(pipeline_request(["c0", "c1", "c2"])))
    assert len(phases.p6_bodies) == 1

    # Exercise the actual Temporal activity wrapper too: a stream-timing
    # refusal must be non-retryable before any upload activity can run.
    from unittest.mock import Mock

    from plm_platform.pipeline.activities import build_activities
    from plm_platform.pipeline.states import Step, step_activity_name
    from temporalio.exceptions import ApplicationError
    from temporalio.testing import ActivityEnvironment

    activity_name = step_activity_name(Step.ASSEMBLE)
    activities = build_activities(_steps(phases), Mock())
    assemble_activity = next(
        fn for fn in activities if fn.__temporal_activity_definition.name == activity_name
    )
    with pytest.raises(ApplicationError, match="video_audio_duration_mismatch") as raised:
        asyncio.run(ActivityEnvironment().run(
            assemble_activity, pipeline_request(["c0", "c1", "c2"]),
        ))
    assert raised.value.type == "ValidationFailure"
    assert raised.value.non_retryable is True
    assert raised.value.details[0]["attempt"] == 1
    assert len(phases.p6_bodies) == 2  # direct call plus activity, no upload


def test_short_supply_reports_the_missing_seconds_for_a_top_up(cleared: Any) -> None:
    """Proves the API refusal carries ``detail.facts.missing_seconds``: three cleared
    60 s tracks with 2 s crossfades give 176 s; a 45-75 min range is 2524 s short."""
    store, manifests = cleared
    resp = _client(backend_for(store, manifests)).post(
        "/media/packages/assemble",
        json=_request(*[m.candidate_id for m in manifests],
                      duration_range_seconds={"min_seconds": 2700, "max_seconds": 4500}),
        headers=HEADERS)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "playlist_supply_short"
    assert detail["facts"]["missing_seconds"] == 2700 - 176
    assert detail["facts"]["available_seconds"] == 176.0


@pytest.mark.parametrize("body_change", [
    {"duration_range_seconds": None},
    {"duration_range_seconds": {"min_seconds": 200, "max_seconds": 100}},
    {"required_duration_seconds": 176.0},
])
def test_a_missing_or_invalid_range_is_refused(cleared: Any,
                                               body_change: dict[str, Any]) -> None:
    """Proves P6 has no duration of its own: no range (or the old exact duration, or an
    inverted range) is refused before anything is fetched or rendered."""
    store, manifests = cleared
    body = _request(*[m.candidate_id for m in manifests])
    del body["duration_range_seconds"]
    body.update({k: v for k, v in body_change.items() if v is not None})
    resp = _client(backend_for(store, manifests)).post(
        "/media/packages/assemble", json=body, headers=HEADERS)
    assert resp.status_code == 422


def test_a_request_naming_no_render_profile_uses_the_deployments(cleared: Any) -> None:
    """R5: the request schema hard-coded ``yt_longform_1080p_v1`` and P6's own
    ``default_render_profile`` setting was never read, so a deployment could not choose
    the profile for callers that name none (the pipeline names none). Proves: no profile
    in the request renders with the configured one; a named profile still wins, and its
    package reuses the stored sequence and visual plan (they used to collide on the
    playlist session's key, a 500)."""
    store, manifests = cleared
    ids = [m.candidate_id for m in manifests]
    platform = load_settings(env={**MINIMAL_ENV, "PLM_SERVICE_TOKEN": TOKEN})
    engine = create_media_engine(static_pool=True)
    app = create_app(session_factory=lambda: media_session(engine),
                     settings=MediaFactorySettings(platform=platform,
                                                   default_render_profile="preview_360p_v1"),
                     clock=FixedClock(NOW),
                     backend=backend_for(store, manifests, toolchain=StandInToolchain()))
    client = TestClient(app)
    body = _request(*ids)
    del body["render_profile"]
    resp = client.post("/media/packages/assemble", json=body, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["render_profile"] == "preview_360p_v1"
    named = client.post("/media/packages/assemble", headers=HEADERS,
                        json=_request(*ids, render_profile="yt_longform_1080p_v1"))
    assert named.status_code == 200, named.text
    assert named.json()["render_profile"] == "yt_longform_1080p_v1"


def test_the_render_profile_setting_is_read_and_checked_at_boot() -> None:
    from media_factory.config import load_media_factory_settings
    from plm_platform.config import SettingsError

    assert load_media_factory_settings(env={}).default_render_profile == \
        "yt_longform_1080p_v1"
    assert load_media_factory_settings(
        env={"PLM_MEDIA_RENDER_PROFILE": "preview_360p_v1"}).default_render_profile == \
        "preview_360p_v1"
    with pytest.raises(SettingsError, match="not declared"):
        load_media_factory_settings(env={"PLM_MEDIA_RENDER_PROFILE": "yt_8k_v9"})


def test_a_retry_while_the_render_runs_waits_and_replays_instead_of_rendering_again(
        cleared: Any) -> None:
    """R5: P6 renders inside the request, so a caller whose read timed out retried while
    the first render still ran, and the same package was rendered twice side by side (in
    the full chain, concurrent hour-long renders were OOM-killed). Proves: a second
    request for the same package during the render waits for it and replays the stored
    package; the video is rendered once."""
    import threading
    import time

    class SlowToolchain(StandInToolchain):
        def render_video_master(self, *args: Any, **kwargs: Any) -> None:
            time.sleep(0.5)
            super().render_video_master(*args, **kwargs)

    store, manifests = cleared
    toolchain = SlowToolchain()
    client = _client(backend_for(store, manifests, toolchain=toolchain))
    ids = [m.candidate_id for m in manifests]
    answers: list[Any] = []

    def post() -> None:
        answers.append(client.post("/media/packages/assemble", json=_request(*ids),
                                   headers=HEADERS))

    threads = [threading.Thread(target=post) for _ in range(2)]
    for thread in threads:
        thread.start()
        time.sleep(0.1)
    for thread in threads:
        thread.join(timeout=30)
    assert [a.status_code for a in answers] == [200, 200], [a.text for a in answers]
    assert answers[0].json()["media_package_id"] == answers[1].json()["media_package_id"]
    assert toolchain.calls.count("video") == 1


def test_recordings_are_measured_one_at_a_time_and_only_the_selected_are_rendered(
        cleared: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """R5: P6 held every offered recording in memory while it measured them, so memory
    grew with the supply (about 60 cleared tracks for one long playlist) and the render
    was OOM-killed. Proves: each recording is fetched and measured before the next is
    fetched, and the render fetches again (on each read, never keeping them) only the
    recordings the sequence selected."""
    from media_factory.rendering import assembler

    store, manifests = cleared
    events: list[str] = []

    class RecordingStore(type(store)):  # type: ignore[misc]
        def fetch_verified(self, uri: str, sha256: str) -> bytes:
            events.append("fetch")
            return super().fetch_verified(uri, sha256)

    recording = RecordingStore()
    recording.objects = store.objects
    real_measure = assembler.measure_features

    def measure(candidate_id: str, audio: bytes, policy: Any = None) -> Any:
        events.append("measure")
        return real_measure(candidate_id, audio) if policy is None else real_measure(
            candidate_id, audio, policy)

    monkeypatch.setattr(assembler, "measure_features", measure)
    client = _client(backend_for(recording, manifests, toolchain=StandInToolchain()))
    resp = client.post("/media/packages/assemble", headers=HEADERS,
                       json=_request(*[m.candidate_id for m in manifests]))
    assert resp.status_code == 200, resp.text
    offered = len(manifests)
    assert events[:2 * offered] == ["fetch", "measure"] * offered
    selected = len(resp.json()["tracklist"])
    # The render reads each selected recording when it needs it (verify, then write to
    # its work directory) instead of keeping them all.
    assert events[2 * offered:] == ["fetch"] * (2 * selected)


def test_http_render_claim_survives_worker_exception_and_refuses_duplicate(
    cleared: Any,
) -> None:
    """A failed HTTP process cannot erase its already-committed render claim."""
    import sqlalchemy as sa
    from media_factory.domain.enums import RenderJobStatus
    from media_factory.storage.models import render_jobs_table, render_recovery_evidence_table
    from media_factory.storage.repositories import get_media_package_by_id

    store, manifests = cleared
    toolchain = StandInToolchain()
    engine = create_media_engine(static_pool=True)
    platform = load_settings(env={**MINIMAL_ENV, "PLM_SERVICE_TOKEN": TOKEN})
    app = create_app(
        session_factory=lambda: media_session(engine),
        settings=MediaFactorySettings(platform=platform), clock=FixedClock(NOW),
        backend=backend_for(store, manifests, toolchain=toolchain),
    )
    client = TestClient(app)
    payload = _request(*[m.candidate_id for m in manifests])
    with (
        patch("media_factory.rendering.assembler.MediaRenderer.render",
              side_effect=RuntimeError("simulated worker killed before FFmpeg")),
        pytest.raises(RuntimeError, match="simulated worker killed"),
    ):
        client.post("/media/packages/assemble", headers=HEADERS, json=payload)
    with media_session(engine) as session:
        rows = session.execute(sa.select(render_jobs_table)).mappings().all()
        assert len(rows) == 1
        # The owner observed its own render raise (ADR-0043): it fenced the
        # claim with persisted evidence. A killed process would leave RUNNING.
        assert rows[0]["job_status"] == RenderJobStatus.QUARANTINED.value
        assert rows[0]["error_code"] == "render_run_stale"
        evidence = session.execute(sa.select(render_recovery_evidence_table)).mappings().all()
        assert [(e["evidence_kind"], e["method"], e["worker_id"]) for e in evidence] == [
            ("worker_termination", "owner_exit_observed", rows[0]["worker_id"])]
        assert evidence[0]["detail_json"] == {
            "error_code": None, "exception_type": "RuntimeError"}
        assert rows[0]["worker_id"]
        assert len(rows[0]["input_manifest_hash"]) == 64
        assert get_media_package_by_id(session, rows[0]["media_package_id"]) is None
    assert toolchain.calls == []
    retry = client.post("/media/packages/assemble", headers=HEADERS, json=payload)
    assert retry.status_code == 409
    assert retry.json()["detail"]["code"] == "render_job_reconciliation_required"
    assert toolchain.calls == []
    with media_session(engine) as session:
        assert session.execute(sa.select(sa.func.count()).select_from(render_jobs_table)).scalar_one() == 1
    engine.dispose()


def test_http_render_completion_is_fenced_if_claim_was_quarantined(
    cleared: Any,
) -> None:
    """A stale owner may render blobs but cannot publish a C6 media package."""
    import sqlalchemy as sa
    from media_factory.domain.enums import RenderJobStatus
    from media_factory.rendering.renderer import MediaRenderer
    from media_factory.storage.models import media_packages_table, render_jobs_table
    from media_factory.storage.render_claims import quarantine_stale_render_job

    store, manifests = cleared
    engine = create_media_engine(static_pool=True)
    platform = load_settings(env={**MINIMAL_ENV, "PLM_SERVICE_TOKEN": TOKEN})
    toolchain = StandInToolchain()
    app = create_app(
        session_factory=lambda: media_session(engine),
        settings=MediaFactorySettings(platform=platform), clock=FixedClock(NOW),
        backend=backend_for(store, manifests, toolchain=toolchain),
    )
    actual_render = MediaRenderer.render

    def render_then_quarantine(renderer: MediaRenderer, **kwargs: Any) -> Any:
        output = actual_render(renderer, **kwargs)
        with media_session(engine) as claim_session:
            from media_factory.storage.repositories import get_render_job

            prior = get_render_job(claim_session, output.render_job.id)
            assert prior is not None and prior.worker_id is not None
            # SQLite strips timezone metadata; the injected clock has the
            # original aware start instant used by the committed claim.
            assert prior.started_at == NOW.replace(tzinfo=None)
            # Simulated supervisor evidence of forced termination only.
            assert attest_termination(
                claim_session, output.render_job.id, worker_id=prior.worker_id,
                started_at=NOW, sha=output.preflight.input_manifest_hash, at=NOW,
            )
            assert quarantine_stale_render_job(
                claim_session, job_id=output.render_job.id,
                input_manifest_hash=output.preflight.input_manifest_hash,
                started_before=NOW, quarantined_at=NOW,
                expected_worker_id=prior.worker_id,
                expected_started_at=NOW,
            )
            claim_session.commit()
        return output

    with patch.object(MediaRenderer, "render", render_then_quarantine):
        response = TestClient(app).post(
            "/media/packages/assemble", headers=HEADERS,
            json=_request(*[m.candidate_id for m in manifests]),
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "render_ownership_lost"
    with media_session(engine) as session:
        assert session.execute(sa.select(render_jobs_table.c.job_status)).scalar_one() == (
            RenderJobStatus.QUARANTINED.value
        )
        assert session.execute(
            sa.select(sa.func.count()).select_from(media_packages_table),
        ).scalar_one() == 0
    engine.dispose()
