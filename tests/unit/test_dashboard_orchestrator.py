"""Unit tests for the dashboard in-process pipeline orchestrator."""

from __future__ import annotations

import asyncio

import pytest
from plm_platform.dashboard import DATA_SOURCE
from plm_platform.dashboard.orchestrator import DemoOrchestrator


@pytest.mark.asyncio
async def test_pipeline_orchestrator_execution() -> None:
    orchestrator = DemoOrchestrator()
    events = []

    async def listener() -> None:
        async for event in orchestrator.subscribe():
            events.append(event)
            if event.get("type") in ("pipeline_complete", "pipeline_error"):
                break

    listener_task = asyncio.create_task(listener())
    run_id = await orchestrator.start_run()
    assert run_id.startswith("run_")

    await asyncio.wait_for(listener_task, timeout=10.0)

    event_types = [e.get("type") for e in events]
    assert "stage_start" in event_types
    assert "stage_complete" in event_types
    assert "pipeline_complete" in event_types

    # Ensure all 10 stages were reported
    stages_seen = {e.get("stage") for e in events if e.get("type") == "stage_complete"}
    expected_stages = {
        "p1_market",
        "p2_ar",
        "p3_genome",
        "p4_music",
        "p5_qa",
        "p6_media",
        "p7_youtube",
        "p8_distribution",
        "p9_analytics",
        "p2_loop",
    }
    assert expected_stages.issubset(stages_seen)

    # Playback is labelled demo on every event and never claims a real success.
    assert all(e.get("data_source") == DATA_SOURCE for e in events)
    complete = next(e for e in events if e.get("type") == "pipeline_complete")
    assert complete["status"] == "DEMO_COMPLETE"


@pytest.mark.asyncio
async def test_pipeline_orchestrator_status() -> None:
    orchestrator = DemoOrchestrator()
    status = orchestrator.get_status()
    assert "is_running" in status
    assert "last_run_id" in status
    assert status["data_source"] == DATA_SOURCE
