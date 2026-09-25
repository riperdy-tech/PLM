"""Seam C3 — PHASE-P3 → PHASE-P4 (track spec). FROZEN by Ruling R5.

Producer: P3's `materialize_package()` producing `TrackSpec`.
Consumer schema: the registry's frozen C3 field list (the wire P4 reads).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from services.experiments.domain.enums import TrackRole
from services.experiments.domain.experiment import (
    Experiment,
    Hypothesis,
    Treatment,
)
from services.experiments.domain.genome import BpmRange, ContentGenome, DurationRange
from services.experiments.materialization.builder import materialize_package
from tests.support.contract import SeamRegistry, assert_seam_payload

REGISTRY_ENTRY = "C3 · PHASE-P3 → PHASE-P4"

REGISTRY_FIELDS: tuple[str, ...] = (
    "track_spec_id",
    "playlist_spec_id",
    "experiment_id",
    "content_arm_id",
    "brand_profile_id",
    "hard_constraints",
    "bounded_preferences",
    "creative_latitude",
    "lyric_spec",
    "vocal_spec",
    "instrumentation_spec",
    "production_style",
    "track_role",
    "requested_duration",
    "candidate_target",
    "priority",
    "budget_limit",
    "provider_policy",
)


class ConsumerTrackSpecSchema(BaseModel):
    """P4's expected track spec input schema, fields copied from registry entry C3."""

    model_config = ConfigDict(extra="forbid")

    track_spec_id: str
    playlist_spec_id: str
    experiment_id: str
    content_arm_id: str
    brand_profile_id: str
    hard_constraints: dict[str, Any]
    bounded_preferences: dict[str, Any]
    creative_latitude: dict[str, Any]
    lyric_spec: dict[str, Any]
    vocal_spec: dict[str, Any]
    instrumentation_spec: dict[str, Any]
    production_style: str
    track_role: TrackRole
    requested_duration: int
    candidate_target: int
    priority: int
    budget_limit: float
    provider_policy: dict[str, Any]


def _sample_experiment() -> tuple[Experiment, Treatment, ContentGenome]:
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    genome = ContentGenome(
        genre="rnb",
        subgenre="contemporary_rnb",
        mood=("melancholic",),
        use_case=("late_night_drive",),
        language="english",
        bpm=BpmRange(min_bpm=75, max_bpm=85, target_bpm=80),
        track_duration=DurationRange(min_seconds=160, max_seconds=220, target_seconds=190),
        playlist_duration=DurationRange(min_seconds=2700, max_seconds=3600, target_seconds=3000),
        visual_world="rainy_asian_city",
    )
    trt = Treatment(
        treatment_id="trt_01",
        treatment_index=0,
        name="control",
        variable_assignments={"bpm": 80},
    )
    exp = Experiment(
        experiment_id="exp_c3_01",
        content_arm_id="arm_rnb_01",
        production_directive_id="dir_01",
        experiment_type="single_factor",  # type: ignore[arg-type]
        status="validated",  # type: ignore[arg-type]
        hypothesis=Hypothesis(statement="Test", target_metric="completion"),
        treatments=(trt,),
        base_genome_fingerprint="fp_dummy",
        created_at=now,
    )
    return exp, trt, genome


def test_declared_fields_match_registry() -> None:
    assert set(ConsumerTrackSpecSchema.model_fields) == set(REGISTRY_FIELDS)


def test_producer_track_spec_validates_against_consumer_schema() -> None:
    exp, trt, genome = _sample_experiment()
    package = materialize_package(exp, trt, genome, budget_envelope=100.0, track_count=3)
    track_spec = package.track_specs[0]

    payload = track_spec.model_dump(mode="json")
    assert_seam_payload(
        payload,
        ConsumerTrackSpecSchema,
        seam=REGISTRY_ENTRY,
        producer="P3 materialize_package -> TrackSpec",
        consumer="P4 track input schema (registry C3)",
    )


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    assert contract_registry.exists
    contract_registry.require_entry(REGISTRY_ENTRY)


def test_p4_adapter_ingests_real_p3_track_spec() -> None:
    from services.music_factory.ingestion.p3_adapter import adapt_track_spec

    exp, trt, genome = _sample_experiment()
    package = materialize_package(exp, trt, genome, budget_envelope=100.0, track_count=3)
    track_spec = package.track_specs[0]
    payload = track_spec.model_dump(mode="json")

    adapted = adapt_track_spec(payload)
    assert adapted.track_spec_id == track_spec.track_spec_id
    assert adapted.experiment_id == exp.experiment_id
    assert adapted.content_arm_id == exp.content_arm_id
    assert adapted.genre == "rnb"
    assert adapted.vocal_mode == "vocal"
    assert adapted.requested_duration == 190
    assert adapted.budget_limit > 0.0
