"""Complete, valid seam payload fixtures (ADR-0025).

Seam models have no defaults for required facts, so a test that needs "a track spec"
must supply a whole one. These builders do that in one place; tests override only the
fields they care about.
"""

from __future__ import annotations

import hashlib
from typing import Any

from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c4 import CandidateReadyPayload
from plm_contracts.seams.c5 import ReleaseManifestPayload

__all__ = [
    "candidate_event",
    "candidate_event_fields",
    "lineage_fields",
    "release_manifest",
    "release_manifest_fields",
    "rights_fields",
    "sample_lineage",
    "track_spec_fields",
]


def lineage_fields(**overrides: str) -> dict[str, str]:
    fields = {
        "experiment_id": "exp_test_01",
        "experiment_family_id": "fam_test_01",
        "content_arm_id": "arm_test_01",
        "content_genome_id": "gen_test_01",
        "production_directive_id": "dir_test_01",
    }
    fields.update(overrides)
    return fields


def sample_lineage(**overrides: str) -> ExperimentLineage:
    return ExperimentLineage(**lineage_fields(**overrides))


def track_spec_fields(**overrides: Any) -> dict[str, Any]:
    """Keyword arguments for a complete C3 track spec (no ``schema_version``)."""
    fields: dict[str, Any] = {
        "track_spec_id": "trk_test_01",
        "playlist_spec_id": "ply_test_01",
        "lineage": lineage_fields(),
        "brand_profile_id": "neon_seoul_profile",
        "hard_constraints": {"genre": "rnb", "language": "english", "vocal_mode": "vocal"},
        "bounded_preferences": {
            "bpm": 88,
            "key_profile": "A_minor",
            "mood": ["melancholic"],
            "use_case": ["late_night_drive"],
        },
        "creative_latitude": {"energy": "medium"},
        "lyric_spec": {"lyric_density": "moderate"},
        "vocal_spec": {"vocal_mode": "vocal", "vocal_gender": "female"},
        "instrumentation_spec": {"instrumentation_profile": ["rhodes"]},
        "production_style": "clean_modern",
        "track_role": "focus",
        "requested_duration": 180,
        "candidate_target": 2,
        "priority": 1,
        "budget_limit": 20.0,
        "provider_policy": {"allowed_providers": ["mock"]},
    }
    fields.update(overrides)
    return fields


def rights_fields(**overrides: Any) -> dict[str, Any]:
    """A complete declared rights snapshot (C4 ``rights``)."""
    fields: dict[str, Any] = {
        "commercial_use_status": "eligible",
        "basis": "commercial_subscription",
        "license_reference": "lic_test_01",
        "plan_or_tier": "pro",
        "account_identity_ref": "acct_test_01",
        "terms_version": "terms_2026_01",
        "attempt_id": "att_test_01",
        "provider_id": "mock",
        "policy_version": "p4-v1",
        "checked_at": "2026-09-25T12:00:00+00:00",
        "downloaded_at": "2026-09-25T12:00:00+00:00",
    }
    fields.update(overrides)
    return fields


def candidate_event_fields(**overrides: Any) -> dict[str, Any]:
    """Keyword arguments for a complete C4 event (no ``schema_version``)."""
    fields: dict[str, Any] = {
        "event_type": "music_candidate_ready_for_p5",
        "candidate_id": "cand_test_01",
        "artifact_id": "art_test_01",
        "artifact_uri": "music/test/art_test_01.wav",
        "artifact_sha256": "0" * 64,
        "track_spec_id": "trk_test_01",
        "lineage": lineage_fields(),
        "provider_id": "mock",
        "model_id": "mock-v1",
        "measured_audio": {"duration_seconds": 1.0, "sample_rate": 44100, "channels": 2},
        "requested_duration_seconds": 1,
        "vocal_mode": "instrumental",
        "genre_tags": ["ambient"],
        "prompt_text": "ambient synth pad electronic chill",
        "lyrics_text": None,
        "rights": rights_fields(),
    }
    fields.update(overrides)
    return fields


def candidate_event(**overrides: Any) -> CandidateReadyPayload:
    return CandidateReadyPayload.build(**candidate_event_fields(**overrides))


def release_manifest_fields(**overrides: Any) -> dict[str, Any]:
    """Keyword arguments for a complete C5 track manifest (no ``schema_version``)."""
    candidate = overrides.get("candidate_id", "cand_test_01")
    fields: dict[str, Any] = {
        "p5_manifest_id": f"rel_man_{candidate}",
        "release_manifest_version": "1.0",
        "candidate_id": candidate,
        "canonical_artifact_id": f"art_{candidate}",
        "artifact_uri": f"music/test/{candidate}.wav",
        "artifact_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
        "measured_duration_seconds": 150.0,
        "track_spec_id": "trk_test_01",
        "lineage": lineage_fields(),
        "disposition": "PASSED",
        "override": None,
        "technical_status": "PASSED",
        "commercial_use_status": "eligible",
        "provenance_status": "COMPLETE",
        "ai_disclosure_required": True,
        "rights_risk": "LOW",
        "policy_status": "COMPLIANT",
        "evaluation_id": f"eval_{candidate}",
        "evaluation_policy_version": "p5-v1",
        "warning_codes": [],
        "evidence_bundle_id": f"evi_{candidate}",
    }
    fields.update(overrides)
    return fields


def release_manifest(**overrides: Any) -> ReleaseManifestPayload:
    return ReleaseManifestPayload.build(**release_manifest_fields(**overrides))
