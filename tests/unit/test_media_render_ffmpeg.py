"""P6 renders a real master with FFmpeg 7 and validates it with ffprobe (R4, ``media``).

Runs in the dev image (``PLM_REQUIRE_MEDIA_TOOLS=1`` there, so it cannot pass by
skipping). Three synthetic tracks of 12-13 s (known keys and tempi) go through the full
P6 path with a tiny render profile: verified fetch, measured features, beam-search
sequencing, chapters, the FFmpeg render (crossfades, two-pass loudnorm, showwaves over
the brand background, H.264/AAC MP4), ffprobe + ebur128 post-render QA, Pillow
thumbnails.

Proves: the produced MP4 has the profile's codecs, resolution, frame rate and an audio
stream, a duration matching the timeline, and integrated loudness at the target, as
measured independently here (ffprobe, and pyloudnorm on the decoded audio master); every
checksum is the sha256 of the stored bytes; and real ffprobe facts that do not match the
profile block the package.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pyloudnorm  # type: ignore[import-untyped]
import pytest
import soundfile as sf  # type: ignore[import-untyped]
from media_factory.domain.enums import PackageStatus, RenderJobStatus
from media_factory.rendering.assembler import AssemblyPipelineResult, assemble_media_package
from media_factory.rendering.ffmpeg import FFmpegToolchain
from media_factory.rendering.profiles import RENDER_PROFILES, RenderProfile
from media_factory.rendering.qa import post_render_qa
from media_factory.rendering.toolchain import VisualizerSpec
from media_factory.visuals.planner import BrandProfile
from plm_contracts.seams.c3 import DurationRangeSeconds
from plm_platform.clock import FixedClock

from tests.support.media_backend import InMemoryArtifactStore, stored_manifest, synth_specs
from tests.support.media_tools import require_media_tool

pytestmark = pytest.mark.media

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
TINY = RenderProfile(name="test_tiny_v1", width=160, height=90, fps=10,
                     x264_preset="ultrafast", x264_crf=35, audio_bitrate="96k")
SECONDS = (12.0, 13.0, 12.0)


@pytest.fixture(scope="module")
def rendered() -> tuple[InMemoryArtifactStore, AssemblyPipelineResult]:
    ffprobe = require_media_tool("ffprobe")
    require_media_tool("ffmpeg")
    assert ffprobe
    store = InMemoryArtifactStore()
    manifests = [
        stored_manifest(store, f"cand_{n}", measured_duration_seconds=seconds,
                        audio_seconds=seconds, synth=synth)
        for n, (seconds, synth) in enumerate(zip(SECONDS, synth_specs(), strict=True))
    ]
    result = assemble_media_package(
        track_manifests=manifests,
        brand_profile=BrandProfile(brand_id="brand_neon", brand_name="Neon Records"),
        duration_range=DurationRangeSeconds(min_seconds=int(sum(SECONDS) - 2 * 2.0),
                                            max_seconds=int(sum(SECONDS) - 2 * 2.0) + 1),
        visual_profile="neon_waveform", playlist_concept="Late Night Focus",
        store=store, toolchain=FFmpegToolchain(), render_profile_name=TINY,
        clock=FixedClock(NOW),
    )
    return store, result


def _ffprobe(data: bytes) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "master.mp4"
        path.write_bytes(data)
        done = subprocess.run(
            [require_media_tool("ffprobe"), "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, check=True)
    parsed: dict[str, object] = json.loads(done.stdout)
    return parsed


def test_a_real_master_is_rendered_and_passes_ffprobe_validation(
        rendered: tuple[InMemoryArtifactStore, AssemblyPipelineResult]) -> None:
    store, result = rendered
    pkg = result.media_package
    assert pkg.status is PackageStatus.READY_FOR_PUBLISH, pkg.render_report["qa"]
    assert pkg.render_report["qa"]["reason_codes"] == []
    assert pkg.render_report["toolchain"].startswith(("ffmpeg version 7.", "ffmpeg version 8."))

    probe = _ffprobe(store.get(pkg.video_master_uri))
    streams = probe["streams"]
    assert isinstance(streams, list)
    video = next(s for s in streams if s["codec_type"] == "video")
    audio = next(s for s in streams if s["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", 160, 90)
    assert video["avg_frame_rate"] == "10/1"
    assert video["pix_fmt"] == "yuv420p"
    assert (audio["codec_name"], int(audio["sample_rate"]), audio["channels"]) == \
        ("aac", 48000, 2)
    timeline = result.playlist_session.actual_duration_seconds
    assert timeline == pytest.approx(sum(SECONDS) - 4.0)
    duration = float(probe["format"]["duration"])  # type: ignore[index]
    assert abs(duration - timeline) <= TINY.duration_tolerance_seconds
    # Guard against a matching container duration hiding a shorter AAC stream.
    for stream in (video, audio):
        assert abs(float(stream["duration"]) - timeline) <= TINY.duration_tolerance_seconds
    assert abs(float(video["duration"]) - float(audio["duration"])) <= (
        TINY.duration_tolerance_seconds
    )
    reported_streams = pkg.render_report["qa"]["metrics"]["probe"]["streams"]
    assert sorted(float(s["duration_seconds"]) for s in reported_streams) == sorted(
        (float(video["duration"]), float(audio["duration"])),
    )

    # Loudness measured here, independently of the renderer's normaliser and QA meter.
    samples, rate = sf.read(io.BytesIO(store.get(pkg.audio_master_uri)))
    lufs = pyloudnorm.Meter(rate).integrated_loudness(samples)
    assert abs(lufs - TINY.loudness_target_lufs) <= TINY.loudness_tolerance_lu
    measured = pkg.render_report["qa"]["metrics"]["loudness"]["integrated_lufs"]
    assert abs(measured - TINY.loudness_target_lufs) <= TINY.loudness_tolerance_lu


def test_every_checksum_is_the_sha256_of_the_stored_rendered_bytes(
        rendered: tuple[InMemoryArtifactStore, AssemblyPipelineResult]) -> None:
    store, result = rendered
    pkg = result.media_package
    pairs = [(pkg.video_master_uri, pkg.video_checksum), (pkg.audio_master_uri, pkg.audio_checksum)]
    pairs += [(t["uri"], t["checksum"]) for t in pkg.thumbnails]
    pairs += [(a.uri, a.checksum) for a in result.visual_assets]
    for uri, checksum in pairs:
        assert hashlib.sha256(store.get(uri)).hexdigest() == checksum
    assert store.get(pkg.video_master_uri)[4:8] == b"ftyp"
    assert len(pkg.chapters) == 3
    assert pkg.chapters[0]["start_seconds"] == 0.0


def test_real_probe_facts_that_miss_the_profile_block_the_package(
        rendered: tuple[InMemoryArtifactStore, AssemblyPipelineResult]) -> None:
    store, result = rendered
    pkg = result.media_package
    toolchain = FFmpegToolchain()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "master.mp4"
        path.write_bytes(store.get(pkg.video_master_uri))
        probe = toolchain.probe(path)
        loudness = toolchain.measure_loudness(path)
    timeline = result.playlist_session.actual_duration_seconds
    full_hd = RENDER_PROFILES["yt_longform_1080p_v1"]
    blocked = post_render_qa(probe=probe, loudness=loudness, profile=full_hd,
                             expected_duration_seconds=timeline,
                             required_window_seconds=(timeline - 1, timeline + 1),
                             size_bytes=len(store.get(pkg.video_master_uri)))
    assert not blocked.passed
    assert {"resolution_mismatch", "fps_mismatch"} <= set(blocked.reason_codes)
    stricter = RenderProfile(name="strict", width=160, height=90, fps=10,
                             loudness_target_lufs=-23.0)
    assert "loudness_out_of_tolerance" in post_render_qa(
        probe=probe, loudness=loudness, profile=stricter, expected_duration_seconds=timeline,
        required_window_seconds=(timeline - 1, timeline + 1), size_bytes=1).reason_codes


@pytest.mark.parametrize(("video_seconds", "audio_seconds", "expect_block"), [
    (4, 4, False), (4, 2, True), (2, 4, True),
])
def test_real_ffmpeg_stream_tail_mismatch_blocks_even_with_matching_container(
        video_seconds: int, audio_seconds: int, expect_block: bool) -> None:
    """Prove the active P6 probe/QA detects a tail hidden by MP4 container duration.

    All fixtures are generated locally from FFmpeg lavfi sources in a temporary
    directory. They have no dependency on P5, MinIO, an operator DB or YouTube.
    The matched case must pass, preventing an always-rejecting implementation.
    """
    ffmpeg = require_media_tool("ffmpeg")
    require_media_tool("ffprobe")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "synthetic.mp4"
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i",
             f"color=c=navy:s=160x90:r=10:d={video_seconds}",
             "-f", "lavfi", "-i",
             f"sine=frequency=440:sample_rate=48000:duration={audio_seconds}",
             "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
             "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(path)],
            check=True, capture_output=True, text=True, timeout=40,
        )
        toolchain = FFmpegToolchain()
        probe = toolchain.probe(path)
        measured = toolchain.measure_loudness(path)
        assert measured.integrated_lufs is not None
        assert probe.duration_seconds == pytest.approx(4.0, abs=0.15)
        streams = {s.codec_type: s for s in probe.streams}
        assert streams["video"].duration_seconds == pytest.approx(video_seconds, abs=0.15)
        assert streams["audio"].duration_seconds == pytest.approx(audio_seconds, abs=0.15)

        # Isolate timing checks: make the synthetic profile match its measured
        # loudness while keeping the exact stream/container timing tolerance.
        profile = RenderProfile(
            name="synthetic_tail_probe", width=160, height=90, fps=10,
            x264_preset="ultrafast", audio_bitrate="96k",
            loudness_target_lufs=measured.integrated_lufs,
        )
        checked = post_render_qa(
            probe=probe, loudness=measured, profile=profile,
            expected_duration_seconds=4.0, required_window_seconds=(3.9, 4.1),
            size_bytes=path.stat().st_size,
        )
    assert checked.passed is not expect_block, checked.reason_codes
    if expect_block:
        assert {"stream_duration_mismatch", "video_audio_duration_mismatch"} <= set(
            checked.reason_codes
        )
        assert "render_duration_mismatch" not in checked.reason_codes
    else:
        assert checked.reason_codes == []


class _ShortAudioFFmpeg(FFmpegToolchain):
    """Simulate an encoder regression *after* the genuine P6 video render.

    Produces a structurally valid MP4 whose container/video are full-length but
    whose AAC stream ends two seconds early. Used only with in-memory storage.
    """

    def render_video_master(self, audio_master: Path, visual: VisualizerSpec,
                            profile: RenderProfile, out: Path) -> None:
        super().render_video_master(audio_master, visual, profile, out)
        length = self.probe(out).duration_seconds
        assert length is not None and length > 3
        damaged = out.with_name("short_audio.mp4")
        subprocess.run(
            [require_media_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(out), "-map", "0:v:0", "-map", "0:a:0",
             "-c:v", "copy", "-c:a", "aac", "-b:a", profile.audio_bitrate,
             "-af", f"atrim=duration={length - 2:.3f},asetpts=PTS-STARTPTS",
             "-movflags", "+faststart", str(damaged)],
            check=True, capture_output=True, text=True, timeout=60,
        )
        out.write_bytes(damaged.read_bytes())


def test_real_ffmpeg_short_aac_stream_marks_p6_package_and_render_job_failed() -> None:
    """Exercise the *assembler*, not just QA, without mutating the running stack."""
    require_media_tool("ffmpeg")
    require_media_tool("ffprobe")
    store = InMemoryArtifactStore()
    manifests = [
        stored_manifest(store, f"short_audio_cand_{n}",
                        measured_duration_seconds=seconds,
                        audio_seconds=seconds, synth=synth)
        for n, (seconds, synth) in enumerate(zip(SECONDS, synth_specs(), strict=True))
    ]
    result = assemble_media_package(
        track_manifests=manifests,
        brand_profile=BrandProfile(brand_id="brand_neon", brand_name="Neon Records"),
        duration_range=DurationRangeSeconds(min_seconds=int(sum(SECONDS) - 4),
                                            max_seconds=int(sum(SECONDS) - 3)),
        visual_profile="neon_waveform", playlist_concept="Late Night Focus",
        store=store, toolchain=_ShortAudioFFmpeg(), render_profile_name=TINY,
        clock=FixedClock(NOW),
    )
    pkg = result.media_package
    qa = pkg.render_report["qa"]
    assert pkg.status is PackageStatus.FAILED
    assert result.render_job.job_status is RenderJobStatus.FAILED
    assert result.render_job.error_code == "post_render_qa_failed"
    assert pkg.render_report["status"] == "failed" and not qa["passed"]
    assert {"stream_duration_mismatch", "video_audio_duration_mismatch"} <= set(
        qa["reason_codes"]
    )
    assert "render_duration_mismatch" not in qa["reason_codes"]
    probe = qa["metrics"]["probe"]
    expected_seconds = result.playlist_session.actual_duration_seconds
    assert probe["duration_seconds"] == pytest.approx(expected_seconds, abs=0.5)
    streams = {stream["codec_type"]: stream for stream in probe["streams"]}
    assert streams["video"]["duration_seconds"] == pytest.approx(expected_seconds, abs=0.5)
    assert streams["audio"]["duration_seconds"] < expected_seconds - 1.5
    assert hashlib.sha256(store.get(pkg.video_master_uri)).hexdigest() == pkg.video_checksum


def test_real_ffmpeg_failed_render_is_persisted_by_p6_http_and_refused_by_p7() -> None:
    """Actual encoder -> P6 HTTP/SQLite -> stored C6 -> P7 no-call rejection.

    P6 and P7 run in separate TestClients with isolated in-memory SQLite DBs.
    P5 and artifacts are test-only; no running compose service is modified.
    """
    require_media_tool("ffmpeg")
    require_media_tool("ffprobe")
    from youtube_publishing.client.mock import MockYouTubeClient

    from tests.support.media_backend import backend_for
    from tests.unit.test_media_api import HEADERS as P6_HEADERS
    from tests.unit.test_media_api import _client as p6_client
    from tests.unit.test_media_api import _request as p6_request
    from tests.unit.test_youtube_api_r5 import HEADERS as P7_HEADERS
    from tests.unit.test_youtube_api_r5 import _app as p7_client

    class CountingShortAudioFFmpeg(_ShortAudioFFmpeg):
        def __init__(self) -> None:
            super().__init__()
            self.video_calls = 0

        def render_video_master(self, audio_master: Path, visual: VisualizerSpec,
                                profile: RenderProfile, out: Path) -> None:
            self.video_calls += 1
            super().render_video_master(audio_master, visual, profile, out)

    store = InMemoryArtifactStore()
    manifests = [
        stored_manifest(store, f"api_bad_aac_{n}", measured_duration_seconds=seconds,
                        audio_seconds=seconds, synth=synth)
        for n, (seconds, synth) in enumerate(zip(SECONDS, synth_specs(), strict=True))
    ]
    toolchain = CountingShortAudioFFmpeg()
    p6 = p6_client(backend_for(store, manifests, toolchain=toolchain))
    request = p6_request(
        *[manifest.candidate_id for manifest in manifests],
        duration_range_seconds={"min_seconds": 33, "max_seconds": 34},
        visual_profile="neon_waveform",
        playlist_concept="Late Night Focus",
        render_profile=TINY.name,
    )
    # The API normally accepts only registered deployment profiles. Override
    # resolution for this test so real FFmpeg can render the tiny synthetic
    # profile; do not alter registered production profiles.
    with patch("media_factory.service.render_profile", return_value=TINY):
        first = p6.post("/media/packages/assemble", json=request, headers=P6_HEADERS)
        assert first.status_code == 200, first.text
        package = first.json()
        assert package["status"] == "failed"
        assert {"stream_duration_mismatch", "video_audio_duration_mismatch"} <= set(
            package["blocking_reason_codes"]
        )
        assert toolchain.video_calls == 1
        package_id = package["media_package_id"]
        read_back = p6.get(f"/media/packages/{package_id}", headers=P6_HEADERS)
        assert read_back.status_code == 200
        assert read_back.json()["blocking_reason_codes"] == package["blocking_reason_codes"]

        render_id = package["render_report"]["render_job_id"]
        saved_render = p6.get(f"/media/renders/{render_id}", headers=P6_HEADERS)
        assert saved_render.status_code == 200
        assert saved_render.json()["job_status"] == "failed"
        assert saved_render.json()["error_code"] == "post_render_qa_failed"

        # A replay is persisted read-only, without another (damaged) MP4 encode.
        replay = p6.post("/media/packages/assemble", json=request, headers=P6_HEADERS)
        assert replay.status_code == 200
        assert replay.json()["media_package_id"] == package_id
        assert toolchain.video_calls == 1
        c6_response = p6.get(f"/media/packages/{package_id}/c6", headers=P6_HEADERS)
        assert c6_response.status_code == 200, c6_response.text
        failed_c6 = c6_response.json()
        assert failed_c6["status"] == "failed"
        assert failed_c6["video"]["checksum"] == package["video_checksum"]

    youtube = MockYouTubeClient()
    p7 = p7_client(client=youtube)
    blocked = p7.post("/youtube/publish", headers=P7_HEADERS, json={
        "channel_id": "UC_r5",
        "operating_mode": "dry_run",
        "media_package": failed_c6,
    })
    assert blocked.status_code == 422, blocked.text
    assert blocked.json()["detail"]["code"] == "package_not_ready"
    assert youtube.recorded_calls == []
