"""Tests for P3 Genome Engine: data-driven templates, inheritance, creative latitude (P3 §25, §67–§69)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from plm_contracts.seams.c2 import ProductionDirectivePayload

from services.experiments.domain.enums import VocalGender, VocalMode
from services.experiments.domain.errors import GenomeBuildError
from services.experiments.experiments.engine import design_experiment
from services.experiments.genome.engine import (
    TEMPLATES_PATH,
    CreativeLatitudeEnvelope,
    GenomeEngine,
    TemplateLibrary,
    TemplateLibraryError,
    TemplateNotFoundError,
    brief_genre,
    build_genome_from_directive,
    default_template_library,
    normalize_dimension_value,
)
from services.experiments.ingestion.p2_adapter import AdaptedDirective, adapt_directive
from tests.support.seams import directive_fields

FIXED_NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _directive(brief: dict[str, Any]) -> AdaptedDirective:
    return adapt_directive(ProductionDirectivePayload.build(**directive_fields(brief=brief)).to_wire())


def _raw_library() -> dict[str, Any]:
    with TEMPLATES_PATH.open(encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def test_dimension_normalization() -> None:
    assert normalize_dimension_value("genre", " R&B  ") == "rnb"
    assert normalize_dimension_value("mood", "Late-Night Driving") == "late_night_driving"
    assert normalize_dimension_value("language", "English ") == "english"


def test_build_genome_from_directive() -> None:
    genome = build_genome_from_directive(
        _directive(
            {
                "genre": "R&B ",
                "mood": ["Melancholic", "DREAMY"],
                "use_case": ["Late-Night Driving "],
                "language": "English",
                "vocal_mode": "vocal",
                "duration_bucket": "45_75m",
                "visual_world": "rainy_asian_city",
            }
        )
    )
    assert genome.genre == "rnb"
    assert genome.mood == ("melancholic", "dreamy")
    assert genome.use_case == ("late_night_driving",)
    assert genome.language == "english"
    assert genome.vocal_mode == VocalMode.VOCAL
    assert genome.visual_world == "rainy_asian_city"
    assert 60 <= genome.bpm.min_bpm <= genome.bpm.max_bpm <= 140


@pytest.mark.parametrize("genre", ["lofi", "city_pop", "jazz", "ambient", "house"])
def test_non_rnb_genre_gets_its_own_template(genre: str) -> None:
    genome = build_genome_from_directive(_directive({"genre": genre}))
    template = default_template_library().for_genre(genre)
    assert genome.genre == genre
    assert template.genome.genre == genre
    assert genome == template.genome  # open brief dimensions take this genre's defaults
    lofi = default_template_library().for_genre("lofi").genome
    if genre != "lofi":
        assert genome.instrumentation_profile != lofi.instrumentation_profile


def test_unknown_genre_is_refused_not_given_another_template() -> None:
    with pytest.raises(GenomeBuildError) as exc:
        build_genome_from_directive(_directive({"genre": "polka"}))
    assert exc.value.code == "unknown_genre"
    assert exc.value.error.field_paths == ["brief.genre"]


def _arm_directive(brief: dict[str, Any], directive_id: str) -> AdaptedDirective:
    return adapt_directive(ProductionDirectivePayload.build(**directive_fields(
        brief=brief, directive_id=directive_id)).to_wire())


def test_missing_genre_is_inferred_from_the_use_case_and_spread() -> None:
    picks = {build_genome_from_directive(_arm_directive(
        {"use_case": ["study_focus"]}, f"dir_study_{n}")).genre for n in range(40)}
    assert picks == {"focus_electronic"}  # ADR-0044: focus arms are the brand's genre
    focus = {build_genome_from_directive(_arm_directive(
        {"use_case": ["focus"]}, f"dir_focus_{n}")).genre for n in range(40)}
    assert focus == {"focus_electronic"}
    sleeps = {build_genome_from_directive(_arm_directive(
        {"use_case": ["sleep"]}, f"dir_sleep_{n}")).genre for n in range(40)}
    assert sleeps == {"ambient", "lofi", "jazz"}  # never one fixed default
    drives = {build_genome_from_directive(_arm_directive(
        {"use_case": ["late_night_drive"]}, f"dir_drive_{n}")).genre for n in range(40)}
    assert drives == {"synthwave", "city_pop", "rnb"}
    same = _arm_directive({"use_case": ["sleep"]}, "dir_same")
    assert build_genome_from_directive(same).genre == build_genome_from_directive(same).genre


def test_inferred_genre_is_recorded_on_the_experiment() -> None:
    directive = _arm_directive({"mood": ["calm"]}, "dir_open")
    genre, note = brief_genre(directive)
    assert note == f"genre_inferred:{genre}:use_case=none"
    named = _arm_directive({"genre": "lofi"}, "dir_named")
    assert brief_genre(named) == ("lofi", None)
    exp = design_experiment(directive, build_genome_from_directive(directive),
                            created_at=FIXED_NOW)
    assert note in exp.design_notes


def test_a_library_without_inference_still_refuses_a_missing_genre() -> None:
    raw = _raw_library()
    del raw["brief_genre_inference"]
    with pytest.raises(GenomeBuildError) as exc:
        build_genome_from_directive(_directive({"mood": ["calm"]}),
                                    library=TemplateLibrary(raw))
    assert exc.value.code == "genre_unspecified"


def test_inference_may_only_name_genres_with_a_template() -> None:
    raw = _raw_library()
    raw["brief_genre_inference"]["open_brief"] = ["lofi", "polka"]
    with pytest.raises(TemplateLibraryError, match="polka"):
        TemplateLibrary(raw)


def test_synthwave_and_deep_house_arms_have_templates() -> None:
    assert build_genome_from_directive(_directive({"genre": "synthwave"})).genre == "synthwave"
    assert build_genome_from_directive(_directive({"genre": "deep_house"})).genre == "house"


def test_unset_brief_dimensions_use_the_template_not_the_string_none() -> None:
    genome = build_genome_from_directive(_directive({"genre": "rnb"}))
    assert genome.language == "english"
    assert genome.visual_world == "rainy_asian_city"
    assert genome.mood == ("melancholic", "dreamy")


def test_unknown_vocal_mode_is_refused() -> None:
    with pytest.raises(GenomeBuildError) as exc:
        build_genome_from_directive(_directive({"genre": "rnb", "vocal_mode": "yodel"}))
    assert exc.value.code == "unknown_vocal_mode"


def test_instrumental_brief_clears_vocal_gender() -> None:
    genome = build_genome_from_directive(_directive({"genre": "rnb", "vocal_mode": "instrumental"}))
    assert genome.vocal_mode is VocalMode.INSTRUMENTAL
    assert genome.vocal_gender is VocalGender.NONE


def test_template_inheritance() -> None:
    engine = GenomeEngine()
    template = engine.get_template("rnb_late_night")
    assert template["genre"] == "rnb"
    assert "rhodes" in template["instrumentation_profile"]
    assert template["mood"] == ["melancholic", "dreamy"]  # child override wins
    assert "visual_world" in template["alternatives"]  # parent's ontology inherited

    with pytest.raises(TemplateNotFoundError):
        engine.get_template("non_existent_template")


def test_shipped_library_loads_and_is_versioned() -> None:
    library = default_template_library()
    assert library.version
    assert set(library.genres) >= {"rnb", "lofi", "city_pop", "jazz", "ambient", "house"}


def test_incomplete_template_is_rejected_at_load(tmp_path: Path) -> None:
    raw = _raw_library()
    del raw["templates"]["jazz_cafe"]["bpm"]
    with pytest.raises(TemplateLibraryError, match="does not declare"):
        TemplateLibrary(raw)


def test_genre_mapped_to_another_genres_template_is_rejected_at_load() -> None:
    raw = _raw_library()
    raw["genre_templates"]["jazz"] = "lofi_chill"
    with pytest.raises(TemplateLibraryError, match="of genre"):
        TemplateLibrary(raw)


def test_unknown_alternative_dimension_is_rejected_at_load() -> None:
    raw = _raw_library()
    raw["templates"]["lofi_chill"]["alternatives"]["genre"] = ["rnb"]
    with pytest.raises(TemplateLibraryError, match="alternative-bearing"):
        TemplateLibrary(raw)


def test_creative_latitude_envelope() -> None:
    envelope = CreativeLatitudeEnvelope.default_for_exploration("exploit")
    assert envelope.bpm_variance_pct == 0.05
    assert not envelope.permits("visual_world")
    assert envelope.permits("title_archetype")

    rad_envelope = CreativeLatitudeEnvelope.default_for_exploration("radical_explore")
    assert rad_envelope.bpm_variance_pct == 0.15
    assert rad_envelope.permits("visual_world")
    assert set(envelope.experimentable_variables) < set(rad_envelope.experimentable_variables)

    with pytest.raises(ValueError):
        CreativeLatitudeEnvelope.default_for_exploration("yolo")
