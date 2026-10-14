"""Complete, valid seam payload fixtures (ADR-0025).

Seam models have no defaults for required facts, so a test that needs "a track spec"
must supply a whole one. These builders do that in one place; tests override only the
fields they care about.
"""

from __future__ import annotations

from typing import Any

from plm_contracts.seams import ExperimentLineage

__all__ = ["lineage_fields", "sample_lineage", "track_spec_fields"]


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
