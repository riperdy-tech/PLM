# tests/contract/test_c3_experiments_to_music.py
"""Seam C3 — PHASE-P3 → PHASE-P4 (track spec). FROZEN by Ruling R5, ADR-0025, ADR-0026.

Producer: P3's real ``materialize_package`` (``TrackSpec.to_wire()``).
Consumer: P4's real ``adapt_track_spec`` and planner. Both use the shared
``plm_contracts.seams.c3.TrackSpecPayload``. The test proves the full experiment lineage
crosses intact into the P4 plan, that no value is invented on either side, and that P4
refuses incomplete, placeholder, unknown-field or future-version specs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from plm_contracts.seams.c3 import TrackSpecPayload

from services.experiments.domain.experiment import Experiment, Hypothesis, Treatment
from services.experiments.domain.genome import BpmRange, ContentGenome, DurationRange
from services.experiments.materialization.builder import (
    content_genome_id,
    materialize_package,
)
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import (
    TrackSpecValidationError,
    adapt_track_spec,
)
from tests.support.contract import SeamRegistry, assert_refused, with_value, without

REGISTRY_ENTRY = "C3 · PHASE-P3 → PHASE-P4"

LINEAGE_FIELDS = (
    "experiment_id",
    "experiment_family_id",
    "content_arm_id",
    "content_genome_id",
    "production_directive_id",
)


def _experiment(*, target_bpm: int | None = 80) -> tuple[Experiment, Treatment, ContentGenome]:
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    genome = ContentGenome(
        genre="rnb",
        subgenre="contemporary_rnb",
        mood=("melancholic",),
        use_case=("late_night_drive",),
        language="english",
        bpm=BpmRange(min_bpm=75, max_bpm=86, target_bpm=target_bpm),
        track_duration=DurationRange(min_seconds=160, max_seconds=221, target_seconds=None),
        playlist_duration=DurationRange(min_seconds=2700, max_seconds=3600, target_seconds=3000),
        visual_world="rainy_asian_city",
    )
    trt = Treatment(
        treatment_id="trt_01", treatment_index=0, name="control", variable_assignments={}
    )
    exp = Experiment(
        experiment_id="exp_c3_01",
        content_arm_id="arm_rnb_01",
        production_directive_id="dir_c3_01",
        experiment_type="single_factor",  # type: ignore[arg-type]
        status="validated",  # type: ignore[arg-type]
        hypothesis=Hypothesis(statement="Test", target_metric="completion"),
        treatments=(trt,),
        base_genome_fingerprint="fp_dummy",
        created_at=now,
    )
    return exp, trt, genome


def _wire(**genome_kw: Any) -> dict[str, Any]:
    exp, trt, genome = _experiment(**genome_kw)
    package = materialize_package(
        exp, trt, genome, track_count=2, created_at=datetime(2026, 9, 25, tzinfo=UTC)
    )
    return json.loads(json.dumps(package.track_specs[0].to_wire()))


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)


def test_producer_emits_exactly_the_shared_wire() -> None:
    wire = _wire()
    assert tuple(wire) == tuple(TrackSpecPayload.model_fields)
    assert wire["schema_version"] == TrackSpecPayload.SCHEMA_VERSION


def test_lineage_is_complete_and_derived_from_the_experiment() -> None:
    exp, _, genome = _experiment()
    lineage = _wire()["lineage"]
    assert lineage == {
        "experiment_id": exp.experiment_id,
        "experiment_family_id": exp.experiment_family_id,
        "content_arm_id": exp.content_arm_id,
        "content_genome_id": content_genome_id(genome),
        "production_directive_id": exp.production_directive_id,
    }


def test_documented_derivation_rules_replace_invented_defaults() -> None:
    # Genome with no BPM target and no duration target: P3 used to send 80 BPM / 180 s.
    wire = _wire(target_bpm=None)
    assert wire["bounded_preferences"]["bpm"] == round((75 + 86) / 2)
    assert wire["requested_duration"] == round((160 + 221) / 2)
    assert wire["bounded_preferences"]["key_profile"] is None  # open, not "C_minor"


def test_real_producer_spec_reaches_the_p4_plan_with_its_lineage() -> None:
    wire = _wire()
    adapted = adapt_track_spec(wire)
    assert adapted.lineage.model_dump() == wire["lineage"]
    assert adapted.genre == wire["hard_constraints"]["genre"]
    assert adapted.language == wire["hard_constraints"]["language"]
    assert adapted.vocal_mode == wire["hard_constraints"]["vocal_mode"]
    assert adapted.bpm == wire["bounded_preferences"]["bpm"]
    assert adapted.brand_profile_id == wire["brand_profile_id"]
    assert adapted.playlist_spec_id == wire["playlist_spec_id"]
    assert list(adapted.allowed_providers) == wire["provider_policy"]["allowed_providers"]

    plan, _ = plan_generation(adapted)
    assert plan.lineage == adapted.lineage


REQUIRED_PATHS = (
    *(f for f in TrackSpecPayload.model_fields),
    *(f"lineage.{f}" for f in LINEAGE_FIELDS),
    "hard_constraints.genre",
    "hard_constraints.language",
    "hard_constraints.vocal_mode",
    "bounded_preferences.bpm",
    "bounded_preferences.key_profile",
    "provider_policy.allowed_providers",
)


@pytest.mark.parametrize("field", REQUIRED_PATHS)
def test_p4_refuses_a_spec_missing_any_required_fact(field: str) -> None:
    exc = assert_refused(
        adapt_track_spec, without(_wire(), field), TrackSpecValidationError,
        because=f"{field} missing",
    )
    assert isinstance(exc, TrackSpecValidationError)
    assert exc.error.code == "missing_required_field"
    assert field in exc.error.field_paths


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_policy.allowed_providers", []),
        ("lineage.content_arm_id", "arm_placeholder"),
        ("lineage.experiment_id", ""),
        ("hard_constraints.genre", " "),
        ("track_role", "standout_single"),
        ("requested_duration", 0),
        ("budget_limit", -1.0),
    ],
)
def test_p4_refuses_invalid_or_placeholder_values(field: str, value: Any) -> None:
    assert_refused(
        adapt_track_spec, with_value(_wire(), field, value), TrackSpecValidationError,
        because=f"{field}={value!r}",
    )


def test_p4_refuses_unknown_fields_and_future_majors() -> None:
    unknown = assert_refused(
        adapt_track_spec, with_value(_wire(), "surprise", 1), TrackSpecValidationError,
        because="unknown field",
    )
    assert isinstance(unknown, TrackSpecValidationError)
    assert unknown.error.code == "unknown_field"
    future = assert_refused(
        adapt_track_spec, with_value(_wire(), "schema_version", "2.0"), TrackSpecValidationError,
        because="future major",
    )
    assert isinstance(future, TrackSpecValidationError)
    assert future.error.code == "unsupported_schema_version"
