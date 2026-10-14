"""Tests for P3 Genome Engine, template inheritance, and creative latitude (Task P3.5)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.experiments.domain.enums import VocalMode
from services.experiments.genome.engine import (
    CreativeLatitudeEnvelope,
    GenomeEngine,
    TemplateNotFoundError,
    build_genome_from_directive,
    normalize_dimension_value,
)
from services.experiments.ingestion.p2_adapter import AdaptedDirective

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def _sample_directive() -> AdaptedDirective:
    return AdaptedDirective(
        directive_id="dir_01",
        decision_id="dec_01",
        arm_id="rnb_late_night",
        exploration_level="exploit",
        priority=1,
        requested_experiment_count=1,
        production_deadline=NOW,
        budget_limit=100.0,
        brief={
            "genre": "R&B ",
            "mood": ["Melancholic", "DREAMY"],
            "use_case": ["Late-Night Driving "],
            "language": "English",
            "vocal_mode": "vocal",
            "duration_bucket": "45_75m",
            "visual_world": "rainy_asian_city",
        },
        required_constraints={"genre": "rnb", "language": "english", "vocal_mode": "vocal"},
        recommended_attributes={"mood": ["melancholic", "dreamy"]},
        market_context={"brand_id": "neon_seoul"},
        selection_reason=["test"],
        expected_value=0.8,
        uncertainty=0.1,
        value_model="p2-proxy-v1",
        directive_version="p2-v1",
        policy_version="p2-v1",
    )


def test_dimension_normalization() -> None:
    assert normalize_dimension_value("genre", " R&B  ") == "rnb"
    assert normalize_dimension_value("mood", "Late-Night Driving") == "late_night_driving"
    assert normalize_dimension_value("language", "English ") == "english"


def test_build_genome_from_directive() -> None:
    dir_obj = _sample_directive()
    genome = build_genome_from_directive(dir_obj)

    assert genome.genre == "rnb"
    assert "melancholic" in genome.mood
    assert "dreamy" in genome.mood
    assert genome.language == "english"
    assert genome.vocal_mode == VocalMode.VOCAL
    assert genome.visual_world == "rainy_asian_city"
    assert 60 <= genome.bpm.min_bpm <= genome.bpm.max_bpm <= 140


def test_template_inheritance() -> None:
    engine = GenomeEngine()
    template = engine.get_template("rnb_late_night")
    assert template["genre"] == "rnb"
    assert "rhodes" in template["instrumentation_profile"]

    with pytest.raises(TemplateNotFoundError):
        engine.get_template("non_existent_template")


def test_creative_latitude_envelope() -> None:
    envelope = CreativeLatitudeEnvelope.default_for_exploration("exploit")
    assert envelope.bpm_variance_pct == 0.05

    rad_envelope = CreativeLatitudeEnvelope.default_for_exploration("radical_explore")
    assert rad_envelope.bpm_variance_pct == 0.15
    assert len(rad_envelope.allowed_visual_worlds) > 1
