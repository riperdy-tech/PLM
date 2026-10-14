"""Unit tests for Deterministic Renderer, Preflight, and Post-Render QA."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from media_factory.domain.enums import RenderJobStatus
from media_factory.domain.models import PlaylistSession, PlaylistTrack, VisualSession
from media_factory.rendering.renderer import DeterministicRenderer, RenderExecutionResult
from plm_platform.clock import FixedClock

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

def test_renderer_preflight_checks() -> None:
    renderer = DeterministicRenderer()

    # Empty playlist fails preflight
    ps_empty = PlaylistSession.create("rm_01", "exp_01", "arm_01", "brand_01", 100.0, now=NOW)
    vs_empty = VisualSession.create("ps_empty", "brand_01", "v1", "profile_01", now=NOW)
    pf_fail = renderer.run_preflight(ps_empty, vs_empty)
    assert pf_fail.passed is False
    assert any("zero tracks" in r for r in pf_fail.reasons)

    # Valid inputs pass preflight
    ps_valid = PlaylistSession.create("rm_01", "exp_01", "arm_01", "brand_01", 120.0, now=NOW)
    t1 = PlaylistTrack.create(
        session_id=ps_valid.id,
        song_id="s1",
        source_artifact_id="a1",
        position=0,
        start_offset_seconds=0.0,
        end_offset_seconds=120.0,
        audio_uri="media/audio/s1.wav",
        audio_checksum="chk_s1",
        now=NOW,
    )
    ps_valid.tracks = [t1]
    vs_valid = VisualSession.create(
        ps_valid.id,
        "brand_01",
        "v1",
        "profile_01",
        visual_plan_json={"scenes": [{"scene_index": 0, "duration_seconds": 120.0}]},
        now=NOW,
    )

    pf_pass = renderer.run_preflight(ps_valid, vs_valid)
    assert pf_pass.passed is True
    assert pf_pass.input_manifest_hash != ""


def test_renderer_full_execution() -> None:
    renderer = DeterministicRenderer()
    ps = PlaylistSession.create("rm_01", "exp_01", "arm_01", "brand_01", 120.0, now=NOW)
    ps.actual_duration_seconds = 120.0
    ps.sequence_checksum = "seq_chk_123"
    t1 = PlaylistTrack.create(
        session_id=ps.id,
        song_id="s1",
        source_artifact_id="a1",
        position=0,
        start_offset_seconds=0.0,
        end_offset_seconds=120.0,
        audio_uri="media/audio/s1.wav",
        audio_checksum="chk_s1",
        now=NOW,
    )
    ps.tracks = [t1]
    vs = VisualSession.create(
        ps.id,
        "brand_01",
        "v1",
        "profile_01",
        visual_plan_json={"scenes": [{"scene_index": 0, "duration_seconds": 120.0}]},
        now=NOW,
    )

    result = renderer.render(
        media_package_id_str="mp_test_01",
        playlist=ps,
        visual_session=vs,
        clock=FixedClock(NOW),
    )

    assert result.render_job.job_status == RenderJobStatus.COMPLETED
    assert result.post_render.passed is True
    assert result.video_master_uri.endswith(".mp4")
    assert result.audio_master_uri.endswith(".wav")
    assert result.render_report["status"] == "passed"
    assert result.render_report["resource_usage"]["cpu_seconds"] > 0


class _SteppingClock:
    """Moves one second forward on every read, so start and finish differ."""

    def __init__(self, at: datetime) -> None:
        self._clock = FixedClock(at)

    def now(self) -> datetime:
        current = self._clock.now()
        self._clock.advance(timedelta(seconds=1))
        return current


def test_render_job_is_stamped_by_the_injected_clock_and_replays() -> None:
    ps = PlaylistSession.create("rm_01", "exp_01", "arm_01", "brand_01", 120.0, now=NOW)
    ps.actual_duration_seconds = 120.0
    ps.tracks = [
        PlaylistTrack.create(
            session_id=ps.id,
            song_id="s1",
            source_artifact_id="a1",
            position=0,
            start_offset_seconds=0.0,
            end_offset_seconds=120.0,
            audio_uri="media/audio/s1.wav",
            audio_checksum="chk_s1",
            now=NOW,
        )
    ]
    vs = VisualSession.create(
        ps.id,
        "brand_01",
        "v1",
        "profile_01",
        visual_plan_json={"scenes": [{"scene_index": 0, "duration_seconds": 120.0}]},
        now=NOW,
    )

    def run() -> RenderExecutionResult:
        return DeterministicRenderer().render(
            media_package_id_str="mp_test_01",
            playlist=ps,
            visual_session=vs,
            clock=_SteppingClock(NOW),
        )

    first = run()
    job = first.render_job
    assert job.created_at == NOW
    assert job.started_at == NOW
    assert job.finished_at == NOW + timedelta(seconds=1)
    assert run() == first


def test_failed_preflight_job_is_stamped_by_the_injected_clock() -> None:
    ps = PlaylistSession.create("rm_01", "exp_01", "arm_01", "brand_01", 100.0, now=NOW)
    vs = VisualSession.create("ps_empty", "brand_01", "v1", "profile_01", now=NOW)

    result = DeterministicRenderer().render(
        media_package_id_str="mp_test_01",
        playlist=ps,
        visual_session=vs,
        clock=_SteppingClock(NOW),
    )

    assert result.render_job.job_status == RenderJobStatus.FAILED
    assert result.render_job.created_at == NOW
    assert result.render_job.finished_at == NOW + timedelta(seconds=1)
