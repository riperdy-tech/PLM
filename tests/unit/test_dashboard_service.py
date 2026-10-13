"""Unit tests for the PLM dashboard data and audio service."""

from __future__ import annotations

import io
import wave
from packages.plm_platform.dashboard.service import DashboardService


def test_dashboard_service_stats() -> None:
    service = DashboardService()
    stats = service.get_stats()
    assert isinstance(stats, dict)
    assert "total_cycles" in stats
    assert "active_arms" in stats
    assert "candidates_generated" in stats
    assert "cleared_releases" in stats
    assert "total_revenue_usd" in stats
    assert "avg_normalized_reward" in stats
    assert stats["total_cycles"] >= 0


def test_dashboard_service_arms() -> None:
    service = DashboardService()
    arms = service.get_arms()
    assert isinstance(arms, list)
    assert len(arms) > 0
    arm = arms[0]
    assert "arm_id" in arm
    assert "name" in arm
    assert "posterior_mean" in arm
    assert "observation_count" in arm


def test_dashboard_service_candidates() -> None:
    service = DashboardService()
    candidates = service.get_candidates()
    assert isinstance(candidates, list)
    assert len(candidates) > 0
    c = candidates[0]
    assert "candidate_id" in c
    assert "title" in c
    assert "genre" in c
    assert "rights_status" in c


def test_dashboard_service_releases() -> None:
    service = DashboardService()
    releases = service.get_releases()
    assert isinstance(releases, list)
    assert len(releases) > 0
    rel = releases[0]
    assert "release_id" in rel
    assert "title" in rel
    assert "status" in rel


def test_dashboard_service_synthesizes_wav() -> None:
    service = DashboardService()
    wav_bytes = service.generate_audio_wav("cand_test_01")
    assert isinstance(wav_bytes, bytes)
    assert wav_bytes.startswith(b"RIFF")
    assert b"WAVE" in wav_bytes[:16]

    # Verify standard readable WAV headers
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() in (1, 2)
        assert wf.getsampwidth() == 2  # 16-bit
        assert wf.getframerate() == 44100
        assert wf.getnframes() > 0


def test_dashboard_service_qa_resolve() -> None:
    service = DashboardService()
    result = service.resolve_qa_case("case_123", "CLEARED_BY_OVERRIDE", "Audited by operator")
    assert result["status"] == "resolved"
    assert result["case_id"] == "case_123"
    assert result["resolution"] == "CLEARED_BY_OVERRIDE"
