"""Controlled production dry-run smoke test for PHASE-P4 (§111, R6).

Runs the full P4 generation pipeline hermetically:
C3 track spec → generation plan → mock provider synthesis → artifact probe & intake
→ candidate bank → R6 rights snapshot → Seam C4 event validation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from plm_platform.config import load_settings
from plm_platform.db import build_engine, session_factory
from sqlalchemy import text

from ..api.events import build_candidate_ready_event
from ..candidates.bank import get_candidate_detail, mark_candidate_exported_to_p5
from ..domain.enums import CandidateStatus, CommercialUseStatus, PlanStatus
from ..generation.engine import GenerationEngine
from ..generation.planner import plan_generation
from ..ingestion.p3_adapter import adapt_track_spec
from ..providers.adapters import MockAdapter
from ..storage.artifacts import InMemoryArtifactStorage

__all__ = ["run_smoke_test"]

_TOKEN_REASON = "blocked: pending music factory service token (PLM_SERVICE_TOKEN)"


def run_smoke_test() -> str | None:
    """Run the P4 dry-run smoke test. Returns None on success, or blocked reason string."""
    platform = load_settings()
    if platform.service_token is None:
        return _TOKEN_REASON

    # Test database connectivity
    engine = build_engine(platform)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        return f"blocked: database unreachable ({exc})"

    factory = session_factory(engine)
    with factory() as session:
        now = datetime.now(UTC)
        wire_spec = {
            "track_spec_id": f"trk_smoke_{int(now.timestamp())}",
            "playlist_spec_id": f"ply_smoke_{int(now.timestamp())}",
            "experiment_id": f"exp_smoke_{int(now.timestamp())}",
            "content_arm_id": "arm_smoke_01",
            "brand_profile_id": "neon_seoul",
            "hard_constraints": {"genre": "rnb", "language": "english", "vocal_mode": "vocal"},
            "bounded_preferences": {
                "bpm": 80,
                "mood": ["melancholic"],
                "use_case": ["late_night_drive"],
            },
            "creative_latitude": {},
            "lyric_spec": {},
            "vocal_spec": {"vocal_mode": "vocal"},
            "instrumentation_spec": {},
            "production_style": "lofi_contemporary",
            "track_role": "standout_single",
            "requested_duration": 60,
            "candidate_target": 1,
            "priority": 1,
            "budget_limit": 50.0,
            "provider_policy": {},
        }

        # 1. Adapt P3 spec
        spec = adapt_track_spec(wire_spec)

        # 2. Plan generation
        plan, variants = plan_generation(spec)
        assert len(variants) >= 1

        # 3. Execute generation with Mock provider
        storage = InMemoryArtifactStorage()
        gen_engine = GenerationEngine(storage=storage)
        result = gen_engine.execute_plan(
            session,
            plan=plan,
            variants=variants,
            spec=spec,
            provider_adapter=MockAdapter(),
        )

        if result.status != PlanStatus.COMPLETED or not result.candidates:
            return f"failed: generation engine failed with errors {result.errors}"

        candidate = result.candidates[0]

        # 4. Verify candidate detail and artifacts
        detail = get_candidate_detail(session, candidate.candidate_id)
        if not detail.artifacts:
            return "failed: candidate has no audio artifacts"

        if detail.rights_snapshot is None:
            return "failed: candidate missing Ruling R6 rights snapshot"

        if detail.rights_snapshot.commercial_use_status != CommercialUseStatus.PERMITTED:
            return "failed: candidate rights not permitted for commercial use"

        # 5. Export to P5 and emit Seam C4 event
        exported = mark_candidate_exported_to_p5(session, candidate.candidate_id)
        if exported.status != CandidateStatus.EXPORTED_TO_P5:
            return "failed: candidate could not be transitioned to EXPORTED_TO_P5"

        event = build_candidate_ready_event(
            candidate_id=candidate.candidate_id,
            artifact_id=detail.artifacts[0].artifact_id,
            track_spec_id=spec.track_spec_id,
            experiment_id=spec.experiment_id,
            content_arm_id=spec.content_arm_id,
            provider_id="mock",
            model_id="mock-v1",
            duration_seconds=detail.artifacts[0].duration_seconds,
            sample_rate=detail.artifacts[0].sample_rate,
            channels=detail.artifacts[0].channels,
            rights_status=detail.rights_snapshot.commercial_use_status.value,
            provenance_complete=True,
        )
        assert event.event_type == "music_candidate_ready_for_p5"

        session.commit()

    return None
