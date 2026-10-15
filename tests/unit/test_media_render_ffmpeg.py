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

import pyloudnorm  # type: ignore[import-untyped]
import pytest
import soundfile as sf  # type: ignore[import-untyped]
from media_factory.domain.enums import PackageStatus
from media_factory.rendering.assembler import AssemblyPipelineResult, assemble_media_package
from media_factory.rendering.ffmpeg import FFmpegToolchain
from media_factory.rendering.profiles import RENDER_PROFILES, RenderProfile
from media_factory.rendering.qa import post_render_qa
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
    assert pkg.render_report["toolchain"].startswith("ffmpeg version 7.")

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
