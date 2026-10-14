"""Scripted demo playback of the pipeline stages, broadcast over SSE.

:class:`DemoOrchestrator` calls no PLM service. Each stage's log line and output are
fixed literals replayed on a timer, so a "run" always ends the same way. It exists to
exercise the dashboard UI. The real pipeline workflow is stage R3 of the remediation
plan; until then nothing here is evidence that the pipeline works.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from .service import DATA_SOURCE


class DemoOrchestrator:
    """Replays scripted stage events for the dashboard UI. Calls no PLM service."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._is_running: bool = False
        self._last_run_id: str | None = None
        self._history: list[dict[str, Any]] = []

    def get_status(self) -> dict[str, Any]:
        """Return the orchestrator's current state."""
        return {
            "is_running": self._is_running,
            "last_run_id": self._last_run_id,
            "subscriber_count": len(self._subscribers),
            "data_source": DATA_SOURCE,
        }

    async def subscribe(self) -> AsyncGenerator[dict[str, Any], None]:
        """Subscribe to live pipeline execution events."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(q)
        try:
            while True:
                event = await q.get()
                yield event
        finally:
            self._subscribers.discard(q)

    async def broadcast(self, event: dict[str, Any]) -> None:
        """Push an event to all active subscriber queues."""
        event["timestamp"] = time.time()
        event["data_source"] = DATA_SOURCE
        for q in list(self._subscribers):
            await q.put(event)

    async def start_run(self, mode: str = "full") -> str:
        """Start a scripted demo playback in the background."""
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        self._is_running = True
        self._last_run_id = run_id
        asyncio.create_task(self._execute_pipeline(run_id, mode=mode))
        return run_id

    async def _execute_pipeline(self, run_id: str, mode: str = "full") -> None:
        """Replay the ten scripted stage events. No service is called."""
        await self.broadcast({
            "type": "pipeline_start",
            "run_id": run_id,
            "mode": mode,
            "message": "Demo playback started: scripted stage events, no PLM service is called.",
        })

        stages = [
            (
                "p1_market",
                "P1 Market Intelligence",
                "Scanned YouTube & Spotify streaming trends. Identified high-velocity synthwave niche.",
                {"opportunity_id": "opp_91823", "target_arm": "arm_synthwave_night", "score": 0.88},
            ),
            (
                "p2_ar",
                "P2 Autonomous A&R",
                "Bandit policy allocated budget envelope. Selected arm_synthwave_night (EXPLOIT). Emitted ProductionDirective.",
                {"directive_id": "dir_71829", "arm_id": "arm_synthwave_night", "budget": 12.5},
            ),
            (
                "p3_genome",
                "P3 Experiment Genome",
                "Materialized experiment treatment into TrackSpec (118 BPM, A minor, analog lead).",
                {"experiment_id": "exp_81723", "track_spec_id": "spec_10928", "genome_id": "gen_88192"},
            ),
            (
                "p4_music",
                "P4 Music Factory",
                "Synthesized 184s audio candidate. Captured commercial rights snapshot (tier: pro, license_id: lic_8819).",
                {"candidate_id": "cand_live_99", "duration": 184.0, "status": "READY_FOR_P5"},
            ),
            (
                "p5_qa",
                "P5 Quality & Rights Gate",
                "Evaluated audio quality (-14.1 LUFS), safety (CLEAN), and rights. Emitted signed ReleaseManifest.",
                {"manifest_id": "rel_man_882910", "verdict": "PASSED"},
            ),
            (
                "p6_media",
                "P6 Media Factory",
                "Assembled visual audio playlist and rendered video package med_pkg_91823.",
                {"media_package_id": "med_pkg_91823", "render_profile": "yt_longform_1080p_v1"},
            ),
            (
                "p7_youtube",
                "P7 YouTube Publishing",
                "Preflight verified synthetic media disclosures. Published video to channel UC_neon_records.",
                {"video_id": "yt_vid_live88", "status": "PUBLISHED"},
            ),
            (
                "p8_distribution",
                "P8 DSP Distribution",
                "Compiled DSP release. Assigned ISRC US-PLM-26-00109. Submitted to distributor client.",
                {"release_id": "rel_live_99", "isrc": "US-PLM-26-00109", "distributor_status": "SUBMITTED"},
            ),
            (
                "p9_analytics",
                "P9 Analytics & Economics",
                "Ingested streaming statement. Reconciled $14.20 revenue. Computed normalized reward 0.86 (Ruling R11).",
                {"normalized_reward": 0.86, "attribution_confidence": 0.95},
            ),
            (
                "p2_loop",
                "P2 Posterior Update",
                "Dispatched reward to POST /ar/results. Bayesian posterior updated: mean shifted 0.82 -> 0.86.",
                {"arm_id": "arm_synthwave_night", "old_mean": 0.82, "new_mean": 0.86, "observation_count": 9},
            ),
        ]

        try:
            for stage_id, title, log_msg, output in stages:
                t0 = time.time()
                await self.broadcast({
                    "type": "stage_start",
                    "run_id": run_id,
                    "stage": stage_id,
                    "title": title,
                })

                # Fixed pacing so the UI can animate; no work happens here.
                await asyncio.sleep(0.15)

                await self.broadcast({
                    "type": "stage_log",
                    "run_id": run_id,
                    "stage": stage_id,
                    "message": log_msg,
                    "level": "info",
                })

                duration_ms = int((time.time() - t0) * 1000)
                await self.broadcast({
                    "type": "stage_complete",
                    "run_id": run_id,
                    "stage": stage_id,
                    "title": title,
                    "duration_ms": duration_ms,
                    "output": output,
                })

            await self.broadcast({
                "type": "pipeline_complete",
                "run_id": run_id,
                "status": "DEMO_COMPLETE",
                "final_reward": 0.86,
                "arm_updated": "arm_synthwave_night",
                "message": "Demo playback finished. Nothing was generated, published or learned.",
            })
            self._history.append({
                "run_id": run_id,
                "status": "DEMO_COMPLETE",
                "timestamp": time.time(),
                "reward": 0.86,
            })
        except Exception as exc:  # noqa: BLE001
            await self.broadcast({
                "type": "pipeline_error",
                "run_id": run_id,
                "error": str(exc),
                "message": f"Pipeline halted: {exc}",
            })
        finally:
            self._is_running = False
