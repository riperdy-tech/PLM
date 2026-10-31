"""P6 render orchestration, stored outputs and post-render QA, without FFmpeg (R4).

With the stand-in toolchain (placeholder bytes, conforming or overridden probe) these
prove P6's own logic: every URI and checksum in the package and the C6 payload is the
sha256 of bytes stored in the artifact store; a tampered or missing source fails closed
before anything is rendered; a failed post-render check blocks the package with its
reason code; the MinIO store refuses a checksum mismatch. They prove nothing about
encoding: ``test_media_render_ffmpeg.py`` (``media``) does that.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from media_factory.artifacts import MinioArtifactStore
from media_factory.domain.enums import PackageStatus, RenderJobStatus, TransitionType
from media_factory.errors import MediaAssemblyError
from media_factory.rendering import ffmpeg as ffmpeg_module
from media_factory.rendering.assembler import AssemblyPipelineResult, assemble_media_package
from media_factory.rendering.ffmpeg import FFmpegToolchain
from media_factory.rendering.profiles import RENDER_PROFILES
from media_factory.rendering.qa import post_render_qa
from media_factory.rendering.toolchain import (
    AudioMasterSpec,
    AudioSegment,
    LoudnessMeasurement,
    ProbeReport,
    StreamInfo,
)
from media_factory.visuals.planner import BrandProfile
from plm_contracts.seams import parse_seam
from plm_contracts.seams.c3 import DurationRangeSeconds
from plm_contracts.seams.c6 import MediaPackagePayload
from plm_platform.clock import FixedClock
from plm_platform.config import load_settings
from plm_platform.storage import build_key, to_uri

from tests.support.media_backend import (
    InMemoryArtifactStore,
    StandInToolchain,
    stored_manifest,
    synth_specs,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
BRAND = BrandProfile(brand_id="brand_neon", brand_name="Neon Records")
PROFILE = RENDER_PROFILES["preview_360p_v1"]


def _assemble(store: InMemoryArtifactStore, toolchain: StandInToolchain,
              **overrides: object) -> AssemblyPipelineResult:
    manifests = [stored_manifest(store, f"cand_{n}", measured_duration_seconds=60.0, synth=s)
                 for n, s in enumerate(synth_specs())]
    kwargs: dict[str, object] = {
        "track_manifests": manifests, "brand_profile": BRAND,
        "duration_range": DurationRangeSeconds(min_seconds=170, max_seconds=180), "visual_profile": "neon_waveform",
        "playlist_concept": "Late Night Focus", "store": store, "toolchain": toolchain,
        "render_profile_name": "preview_360p_v1", "clock": FixedClock(NOW),
    }
    kwargs.update(overrides)
    return assemble_media_package(**kwargs)  # type: ignore[arg-type]


def test_every_checksum_is_the_sha256_of_the_stored_bytes() -> None:
    store, toolchain = InMemoryArtifactStore(), StandInToolchain()
    result = _assemble(store, toolchain)
    pkg = result.media_package
    assert pkg.status is PackageStatus.READY_FOR_PUBLISH

    def stored_hash(uri: str) -> str:
        return hashlib.sha256(store.get(uri)).hexdigest()

    assert stored_hash(pkg.video_master_uri) == pkg.video_checksum
    assert stored_hash(pkg.audio_master_uri) == pkg.audio_checksum
    for thumbnail in pkg.thumbnails:
        assert stored_hash(thumbnail["uri"]) == thumbnail["checksum"]
    for asset in result.visual_assets:
        assert stored_hash(asset.uri) == asset.checksum
    assert result.render_job.output_checksum == pkg.video_checksum

    wire = parse_seam(MediaPackagePayload, pkg.to_c6_payload())
    assert wire.video.checksum == pkg.video_checksum
    assert wire.audio.checksum == pkg.audio_checksum
    assert [t.checksum for t in wire.thumbnails] == [t["checksum"] for t in pkg.thumbnails]
    assert len(wire.chapters) == 3
    assert pkg.render_report["sequencing"]["policy"]["version"] == "seq-beam-v2"


def test_claimed_render_scopes_all_blobs_and_preserves_content_checksums() -> None:
    """One committed claim isolates masters, background and thumbnails, not C6 IDs."""
    store, toolchain = InMemoryArtifactStore(), StandInToolchain()
    token = "a" * 64
    result = _assemble(store, toolchain, before_render=lambda *_: token)
    package = result.media_package
    assert package.render_report["artifact_claim_namespace"] == token
    assert f"/claims/{token}/" in package.audio_master_uri
    assert f"/claims/{token}/" in package.video_master_uri
    assert all(f"/claims/{token}/" in thumb["uri"] for thumb in package.thumbnails)
    assert all(f"/claims/{token}/" in asset.uri for asset in result.visual_assets)
    assert package.video_checksum == hashlib.sha256(store.get(package.video_master_uri)).hexdigest()
    assert package.audio_checksum == hashlib.sha256(store.get(package.audio_master_uri)).hexdigest()
    assert package.to_c6_payload()["video"]["master_uri"] == package.video_master_uri


def test_claimed_render_rejects_unsafe_storage_prefix_before_writing() -> None:
    """An invalid execution token must not create any render or thumbnail outputs."""
    store, toolchain = InMemoryArtifactStore(), StandInToolchain()
    with pytest.raises(ValueError, match="claim namespace"):
        _assemble(store, toolchain, before_render=lambda *_: "../other-package")
    assert toolchain.calls == []
    assert all(not key.startswith("media/renders/") for key in store.objects)
    assert all(not key.startswith("media/thumbnails/") for key in store.objects)


def test_a_tampered_source_track_fails_closed_before_rendering() -> None:
    store, toolchain = InMemoryArtifactStore(), StandInToolchain()
    manifests = [stored_manifest(store, f"cand_{n}", measured_duration_seconds=60.0)
                 for n in range(3)]
    store.tamper(manifests[1].artifact_uri, b"RIFF tampered bytes")
    with pytest.raises(MediaAssemblyError) as raised:
        _assemble(store, toolchain, track_manifests=manifests)
    assert raised.value.code == "source_checksum_mismatch"
    assert toolchain.calls == []


def test_a_manifest_checksum_that_differs_from_the_stored_object_fails_closed() -> None:
    store, toolchain = InMemoryArtifactStore(), StandInToolchain()
    good = [stored_manifest(store, f"cand_{n}", measured_duration_seconds=60.0)
            for n in range(3)]
    lying = good[:2] + [good[2].model_copy(update={"artifact_sha256": "f" * 64})]
    with pytest.raises(MediaAssemblyError) as raised:
        _assemble(store, toolchain, track_manifests=lying)
    assert raised.value.code == "source_checksum_mismatch"
    missing = good[:2] + [good[2].model_copy(update={
        "artifact_uri": "memory://music/gone/" + "a" * 64, "artifact_sha256": "a" * 64})]
    with pytest.raises(MediaAssemblyError) as raised:
        _assemble(store, toolchain, track_manifests=missing)
    assert raised.value.code == "source_artifact_missing"
    assert toolchain.calls == []


@pytest.mark.parametrize(("toolchain", "code"), [
    (StandInToolchain(loudness_lufs=-20.0), "loudness_out_of_tolerance"),
    (StandInToolchain(loudness_lufs=None, probe_overrides={"width": 1280}),
     "resolution_mismatch"),
    (StandInToolchain(probe_overrides={"duration_seconds": 150.0}), "render_duration_mismatch"),
    (StandInToolchain(probe_overrides={"audio_codec": "mp3"}), "audio_codec_mismatch"),
])
def test_a_failed_post_render_check_blocks_the_package(toolchain: StandInToolchain,
                                                       code: str) -> None:
    result = _assemble(InMemoryArtifactStore(), toolchain)
    pkg = result.media_package
    assert pkg.status is PackageStatus.FAILED
    assert result.render_job.job_status is RenderJobStatus.FAILED
    assert result.render_job.error_code == "post_render_qa_failed"
    assert code in pkg.render_report["qa"]["reason_codes"]
    assert pkg.to_c6_payload()["status"] == "failed"


def _probe(**overrides: object) -> ProbeReport:
    fields: dict[str, object] = {"width": PROFILE.width, "height": PROFILE.height,
                                 "fps": float(PROFILE.fps), "streams": None}
    fields.update(overrides)
    streams = fields["streams"] if fields["streams"] is not None else (
        StreamInfo(codec_type="video", codec_name="h264", width=fields["width"],  # type: ignore[arg-type]
                   height=fields["height"], fps=fields["fps"], pixel_format="yuv420p",
                   duration_seconds=100.0),  # type: ignore[arg-type]
        StreamInfo(codec_type="audio", codec_name="aac", sample_rate=48000, channels=2,
                   duration_seconds=100.0),
    )
    return ProbeReport(format_name="mov,mp4,m4a", duration_seconds=100.0, size_bytes=10,
                       streams=streams)  # type: ignore[arg-type]


def test_post_render_qa_codes() -> None:
    loud = LoudnessMeasurement(integrated_lufs=-14.2, true_peak_dbtp=-1.1, meter="t")
    ok = post_render_qa(probe=_probe(), loudness=loud, profile=PROFILE,
                        expected_duration_seconds=100.0, required_window_seconds=(95.0, 105.0),
                        size_bytes=10)
    assert ok.passed and ok.reason_codes == []

    def codes(**kwargs: object) -> list[str]:
        args: dict[str, object] = {
            "probe": _probe(), "loudness": loud, "profile": PROFILE,
            "expected_duration_seconds": 100.0, "required_window_seconds": (95.0, 105.0),
            "size_bytes": 10}
        args.update(kwargs)
        return post_render_qa(**args).reason_codes  # type: ignore[arg-type]

    assert codes(probe=None) == ["container_unreadable"]
    assert "audio_stream_missing" in codes(probe=_probe(streams=_probe().streams[:1]))
    assert "fps_mismatch" in codes(probe=_probe(fps=29.97))
    assert "duration_outside_required_window" in codes(required_window_seconds=(200.0, 210.0))
    assert "loudness_unmeasurable" in codes(
        loudness=LoudnessMeasurement(integrated_lufs=None, true_peak_dbtp=None, meter="t"))
    assert "true_peak_exceeded" in codes(
        loudness=LoudnessMeasurement(integrated_lufs=-14.0, true_peak_dbtp=0.8, meter="t"))
    assert "empty_output" in codes(size_bytes=0)


def test_stream_timing_cannot_be_hidden_by_matching_container_duration() -> None:
    loud = LoudnessMeasurement(integrated_lufs=-14.0, true_peak_dbtp=-1.0, meter="fixture")
    normal = _probe()
    video, audio = normal.streams

    def check(*, video_seconds: float | None = 100.0,
              audio_seconds: float | None = 100.0) -> list[str]:
        probe = replace(
            normal, streams=(
                replace(video, duration_seconds=video_seconds),
                replace(audio, duration_seconds=audio_seconds),
            ),
        )
        return post_render_qa(
            probe=probe, loudness=loud, profile=PROFILE,
            expected_duration_seconds=100.0,
            required_window_seconds=(95.0, 105.0), size_bytes=10,
        ).reason_codes

    assert check() == []
    assert "stream_duration_unmeasured" in check(audio_seconds=None)
    assert "stream_duration_unmeasured" in check(video_seconds=float("nan"))
    assert "stream_duration_mismatch" in check(audio_seconds=80.0)
    assert "video_audio_duration_mismatch" in check(audio_seconds=80.0)
    assert "stream_duration_mismatch" in check(video_seconds=120.0)
    assert "video_audio_duration_mismatch" in check(video_seconds=120.0)


@pytest.mark.parametrize("overrides,expected", [
    ({"audio_stream_duration_seconds": None}, "stream_duration_unmeasured"),
    ({"video_stream_duration_seconds": 60.0}, "stream_duration_mismatch"),
    ({"audio_stream_duration_seconds": 60.0}, "video_audio_duration_mismatch"),
])
def test_p6_package_blocks_unverified_stream_durations(
    overrides: dict[str, float | None], expected: str,
) -> None:
    result = _assemble(InMemoryArtifactStore(), StandInToolchain(probe_overrides=overrides))
    assert result.media_package.status is PackageStatus.FAILED
    assert result.render_job.job_status is RenderJobStatus.FAILED
    assert expected in result.media_package.render_report["qa"]["reason_codes"]



@pytest.mark.parametrize(("value", "code"), [
    (None, "true_peak_unmeasurable"),
    (float("nan"), "true_peak_unmeasurable"),
    (float("inf"), "true_peak_unmeasurable"),
    (-float("inf"), "true_peak_unmeasurable"),
    (0.8, "true_peak_exceeded"),
])
def test_true_peak_requires_finite_measured_fact(value: float | None, code: str) -> None:
    result = post_render_qa(
        probe=_probe(),
        loudness=LoudnessMeasurement(integrated_lufs=-14.0, true_peak_dbtp=value, meter="test"),
        profile=PROFILE,
        expected_duration_seconds=100.0, required_window_seconds=(95.0, 105.0),
        size_bytes=10,
    )
    assert not result.passed
    assert code in result.reason_codes


@pytest.mark.parametrize(("value", "code"), [
    (float("nan"), "loudness_unmeasurable"),
    (float("inf"), "loudness_unmeasurable"),
    (-float("inf"), "loudness_unmeasurable"),
])
def test_integrated_loudness_requires_finite_fact(value: float, code: str) -> None:
    result = post_render_qa(
        probe=_probe(),
        loudness=LoudnessMeasurement(integrated_lufs=value, true_peak_dbtp=-1.0, meter="test"),
        profile=PROFILE,
        expected_duration_seconds=100.0, required_window_seconds=(95.0, 105.0),
        size_bytes=10,
    )
    assert not result.passed
    assert code in result.reason_codes


@pytest.mark.parametrize(("probe", "expected", "window", "code"), [
    (_probe(fps=float("nan")), 100.0, (95.0, 105.0), "fps_mismatch"),
    (_probe(fps=float("inf")), 100.0, (95.0, 105.0), "fps_mismatch"),
    (replace(_probe(), duration_seconds=float("nan")), 100.0,
     (95.0, 105.0), "duration_unmeasured"),
    (replace(_probe(), duration_seconds=float("inf")), 100.0,
     (95.0, 105.0), "duration_unmeasured"),
    (_probe(), float("nan"), (95.0, 105.0), "expected_duration_unmeasured"),
    (_probe(), 100.0, (float("nan"), 105.0), "duration_window_invalid"),
    (_probe(), 100.0, (105.0, 95.0), "duration_window_invalid"),
])
def test_nonfinite_or_invalid_timeline_fails_closed(
    probe: ProbeReport, expected: float, window: tuple[float, float], code: str,
) -> None:
    result = post_render_qa(
        probe=probe,
        loudness=LoudnessMeasurement(integrated_lufs=-14.0, true_peak_dbtp=-1.0, meter="test"),
        profile=PROFILE, expected_duration_seconds=expected,
        required_window_seconds=window, size_bytes=10,
    )
    assert not result.passed
    assert code in result.reason_codes


def test_missing_true_peak_blocks_p6_package_and_render_job() -> None:
    class _NoPeakToolchain(StandInToolchain):
        def measure_loudness(self, path: Path) -> LoudnessMeasurement:
            measured = super().measure_loudness(path)
            return replace(measured, true_peak_dbtp=None)

    result = _assemble(InMemoryArtifactStore(), _NoPeakToolchain())
    assert result.media_package.status is PackageStatus.FAILED
    assert result.render_job.job_status is RenderJobStatus.FAILED
    assert result.render_job.error_code == "post_render_qa_failed"
    assert "true_peak_unmeasurable" in result.media_package.render_report["qa"]["reason_codes"]
    assert result.media_package.to_c6_payload()["status"] == "failed"



@pytest.mark.parametrize("field", [
    "true_peak_dbtp", "integrated_lufs", "probe_video_fps", "probe_container_duration",
])
def test_nonfinite_qa_readings_stay_strict_json_serializable(field: str) -> None:
    """Stored P6 JSON must never contain NaN, including on FAILED reports."""
    class _InvalidMeasurements(StandInToolchain):
        def measure_loudness(self, path: Path) -> LoudnessMeasurement:
            reading = super().measure_loudness(path)
            if field in ("true_peak_dbtp", "integrated_lufs"):
                return replace(reading, **{field: float("nan")})
            return reading

        def probe(self, path: Path) -> ProbeReport:
            reading = super().probe(path)
            if field == "probe_video_fps":
                return replace(reading, streams=(
                    replace(reading.streams[0], fps=float("nan")), reading.streams[1],
                ))
            if field == "probe_container_duration":
                return replace(reading, duration_seconds=float("nan"))
            return reading

    result = _assemble(InMemoryArtifactStore(), _InvalidMeasurements())
    assert result.media_package.status is PackageStatus.FAILED
    report = result.media_package.render_report
    json.dumps(report, allow_nan=False)
    metrics = report["qa"]["metrics"]
    if field in ("true_peak_dbtp", "integrated_lufs"):
        assert metrics["loudness"][field] is None
    elif field == "probe_video_fps":
        assert metrics["probe"]["streams"][0]["fps"] is None
    else:
        assert metrics["probe"]["duration_seconds"] is None
        assert report["duration_seconds"] > 0


ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
}


def _minio_store(data: bytes) -> MinioArtifactStore:
    response = MagicMock()
    response.read.return_value = data
    client = MagicMock()
    client.get_object.return_value = response
    return MinioArtifactStore(load_settings(env=ENV), client=client)


def test_the_minio_store_verifies_what_it_reads() -> None:
    settings = load_settings(env=ENV)
    data = b"cleared audio bytes"
    digest = hashlib.sha256(data).hexdigest()
    uri = to_uri(build_key("music/", "cand/full_mix.wav", digest), settings=settings)

    assert _minio_store(data).fetch_verified(uri, digest) == data
    # Bytes altered behind the content-addressed key.
    with pytest.raises(MediaAssemblyError) as raised:
        _minio_store(b"tampered").fetch_verified(uri, digest)
    assert raised.value.code == "source_checksum_mismatch"
    # A manifest whose checksum differs from the object's content address.
    with pytest.raises(MediaAssemblyError) as raised:
        _minio_store(data).fetch_verified(uri, "0" * 64)
    assert raised.value.code == "source_checksum_mismatch"
    # A URI the store did not build.
    with pytest.raises(MediaAssemblyError) as raised:
        _minio_store(data).fetch_verified("music/test/x.wav", digest)
    assert raised.value.code == "source_uri_invalid"


def test_ffmpeg_step_timeouts_scale_with_the_programme_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 120-minute master gets 2.5x its length per step, not a flat hour (ADR-0044)."""
    timeouts: list[float] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeouts.append(float(kwargs["timeout"]))  # type: ignore[arg-type]
        stderr = ('{"input_i": "-20", "input_tp": "-2", "input_lra": "5", '
                  '"input_thresh": "-30", "target_offset": "0"}')
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr=stderr)

    monkeypatch.setattr(ffmpeg_module.subprocess, "run", fake_run)
    monkeypatch.setattr(ffmpeg_module.shutil, "which", lambda name: name)
    tool = FFmpegToolchain()
    segments = tuple(AudioSegment(Path(f"t{n}.wav"), 180.0) for n in range(40))  # 120 min
    spec = AudioMasterSpec(segments, TransitionType.HARD_CUT, 0.0)
    tool.render_audio_master(spec, RENDER_PROFILES["yt_longform_1080p_v1"], Path("out.wav"))
    assert timeouts == [2.5 * 7200.0, 2.5 * 7200.0]
    # A short programme keeps the base timeout as its floor.
    timeouts.clear()
    tool.render_audio_master(AudioMasterSpec(segments[:1], TransitionType.HARD_CUT, 0.0),
                             RENDER_PROFILES["yt_longform_1080p_v1"], Path("out.wav"))
    assert timeouts == [3600.0, 3600.0]


def test_the_ai_disclosure_is_on_even_when_p5_declares_none() -> None:
    store = InMemoryArtifactStore()
    manifests = [stored_manifest(store, f"cand_{n}", measured_duration_seconds=60.0, synth=s,
                                 ai_disclosure_required=False)
                 for n, s in enumerate(synth_specs())]
    pkg = _assemble(store, StandInToolchain(), track_manifests=manifests).media_package
    assert pkg.to_c6_payload()["ai_disclosure_required"] is True
    for meta in pkg.metadata_candidates:
        assert meta["contains_synthetic_media"] is True
        assert "Notice: Sound recording" in meta["description"]
