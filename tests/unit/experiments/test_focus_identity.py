"""P3 inside the brand's identity: focus-electronic templates, sub-styles and tempo bounds.

ADR-0044 (amendment 2026-11-01): the channel is instrumental deep-focus electronic music,
70–110 BPM on every track, in nine sub-styles, as 90–120 min videos. These tests run P3's
own genome engine, experiment designer and materializer.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from plm_contracts.seams.c2 import ProductionDirectivePayload

from services.experiments.domain.enums import VocalGender, VocalMode
from services.experiments.experiments.engine import design_experiment
from services.experiments.genome.engine import (
    TEMPLATES_PATH,
    TemplateLibrary,
    TemplateLibraryError,
    brief_template,
    build_genome_from_directive,
    default_template_library,
)
from services.experiments.ingestion.p2_adapter import AdaptedDirective, adapt_directive
from services.experiments.materialization.builder import extend_package, materialize_package
from services.experiments.validation.engine import promote_to_validated, validate_experiment
from services.media_factory.visuals.worlds import WORLDS
from tests.support.seams import directive_fields

FIXED_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

SUB_STYLES = (
    "future_garage_focus", "soft_cyber_electronica", "minimal_pulse", "ambient_breakbeat",
    "downtempo_synth", "deep_work_chillstep", "restrained_retro_future", "organic_electronic",
    "piano_synth_concentration",
)
FOCUS_WORLDS = {
    "architect_studio_night", "drafting_table_dawn", "minimal_desk_monolith",
    "observatory_workspace", "server_hall_glow", "circuit_grid_abstract", "blueprint_hall",
}
"""P6's focus-identity workspace worlds (ADR-0044, commit 8c76c6c)."""
OUT_OF_IDENTITY = ("vinyl", "crackle", "trap", "808", "neon", "vocal", "choir", "drop")


def _focus(brief: dict[str, Any], *, directive_id: str = "dir_focus_001",
           allocation_type: str = "exploit", quantity: int = 2) -> AdaptedDirective:
    return adapt_directive(ProductionDirectivePayload.build(**directive_fields(
        directive_id=directive_id, allocation_type=allocation_type, quantity=quantity,
        brief={"use_case": ["study_focus"], "vocal_mode": "instrumental", **brief},
    )).to_wire())


def _raw_library() -> dict[str, Any]:
    with TEMPLATES_PATH.open(encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def test_every_focus_template_is_instrumental_and_inside_the_identity() -> None:
    library = default_template_library()
    assert library.subgenres("focus_electronic") == tuple(sorted(SUB_STYLES))
    templates = [library.for_genre("focus_electronic"),
                 *(library.for_subgenre("focus_electronic", s) for s in SUB_STYLES)]
    assert len({t.template_name for t in templates}) == 10
    for template in templates:
        genome = template.genome
        assert genome.genre == "focus_electronic"
        assert genome.vocal_mode is VocalMode.INSTRUMENTAL
        assert genome.vocal_gender is VocalGender.NONE
        assert genome.language == "instrumental"
        assert genome.lyric_density == "instrumental"
        assert genome.energy.value in ("low", "medium")
        assert 70 <= genome.bpm.min_bpm <= genome.bpm.max_bpm <= 110
        assert template.bpm_limits is not None
        assert (template.bpm_limits.min_bpm, template.bpm_limits.max_bpm) == (70, 110)
        # inside manual supply's 90-300 s per-song limits
        assert 90 <= genome.track_duration.min_seconds <= genome.track_duration.max_seconds <= 300
        assert (genome.playlist_duration.min_seconds,
                genome.playlist_duration.max_seconds) == (5400, 7200)
        assert genome.hook_profile != "vocal_refrain"
        assert genome.thumbnail_family != "illustrated_lofi"
        assert genome.song_structure != "build_drop_breakdown"
        assert genome.visual_world in FOCUS_WORLDS and genome.visual_world in WORLDS
        assert set(template.alternatives["visual_world"]) == FOCUS_WORLDS
        sound = " ".join((*genome.instrumentation_profile, genome.production_style))
        assert not any(word in sound for word in OUT_OF_IDENTITY), template.template_name
    children = templates[1:]
    assert len({t.genome.instrumentation_profile for t in children}) == 9
    assert len({t.genome.bpm.target_bpm for t in children}) == 9
    assert {t.genome.subgenre for t in children} == set(SUB_STYLES)
    assert len({t.genome.visual_world for t in children}) >= 6


@pytest.mark.parametrize("sub_style", SUB_STYLES)
def test_a_briefs_subgenre_selects_its_sub_style_template(sub_style: str) -> None:
    directive = _focus({"genre": "focus_electronic", "subgenre": sub_style})
    template, note = brief_template(directive)
    assert note is None
    genome = build_genome_from_directive(directive)
    assert genome.subgenre == sub_style
    assert genome == template.genome.model_copy(update={"use_case": ("study_focus",)})


def test_an_inferred_focus_genre_still_takes_the_sub_style() -> None:
    """A P1 focus arm often names no genre: P3 infers focus_electronic, then the sub-style."""
    genome = build_genome_from_directive(_focus({"subgenre": "Minimal Pulse"}))
    assert (genome.genre, genome.subgenre) == ("focus_electronic", "minimal_pulse")
    assert genome.bpm.target_bpm == 102


def test_no_subgenre_gets_the_base_and_an_unlisted_one_is_recorded() -> None:
    base = default_template_library().for_genre("focus_electronic")
    assert build_genome_from_directive(_focus({})).instrumentation_profile == (
        base.genome.instrumentation_profile)
    directive = _focus({"subgenre": "lofi_hip_hop"})
    template, note = brief_template(directive)
    assert template.template_name == base.template_name
    assert note == "subgenre_unmatched:lofi_hip_hop->focus_electronic_base"
    exp = design_experiment(directive, build_genome_from_directive(directive),
                            created_at=FIXED_NOW)
    assert note in exp.design_notes


def test_another_genre_ignores_the_subgenre_as_before() -> None:
    directive = adapt_directive(ProductionDirectivePayload.build(**directive_fields(
        brief={"genre": "rnb", "subgenre": "neo_soul"})).to_wire())
    assert brief_template(directive) == (default_template_library().for_genre("rnb"), None)


@pytest.mark.parametrize("sub_style", SUB_STYLES)
@pytest.mark.parametrize("level", ["exploit", "adjacent_explore", "radical_explore"])
def test_bpm_treatments_never_leave_70_to_110(sub_style: str, level: str) -> None:
    directive = _focus({"subgenre": sub_style}, allocation_type=level, quantity=12)
    genome = build_genome_from_directive(directive)
    exp = design_experiment(directive, genome, target_variable="bpm", created_at=FIXED_NOW)
    bpms = [t.variable_assignments["bpm"] for t in exp.treatments]
    assert len(bpms) >= 2
    for bpm in bpms:
        assert 70 <= bpm["min_bpm"] <= bpm["target_bpm"] <= bpm["max_bpm"] <= 110, (
            sub_style, level, bpm)


def test_bpm_limits_cut_a_shift_instead_of_dropping_the_variable() -> None:
    """The 15 % radical shift of the chillstep target (74) is 11: +11 is offered, -11 (63,
    sleep tempo) is not, and the smaller shifts still give treatments."""
    directive = _focus({"subgenre": "deep_work_chillstep"},
                       allocation_type="radical_explore", quantity=12)
    exp = design_experiment(directive, build_genome_from_directive(directive),
                            target_variable="bpm", created_at=FIXED_NOW)
    targets = {t.variable_assignments["bpm"]["target_bpm"] for t in exp.treatments}
    assert 74 + 11 in targets and 74 - 11 not in targets


def test_template_bpm_outside_its_limits_is_refused_at_load() -> None:
    raw = _raw_library()
    raw["templates"]["focus_minimal_pulse"]["bpm"] = {
        "min_bpm": 100, "max_bpm": 124, "target_bpm": 120}
    with pytest.raises(TemplateLibraryError, match="bpm_limits"):
        TemplateLibrary(raw)


def test_unknown_sub_style_template_is_refused_at_load() -> None:
    raw = _raw_library()
    raw["subgenre_templates"]["by_genre"]["focus_electronic"]["minimal_pulse"] = "nope"
    with pytest.raises(TemplateLibraryError, match="missing template"):
        TemplateLibrary(raw)
    raw = _raw_library()
    raw["subgenre_templates"]["by_genre"]["focus_electronic"]["minimal_pulse"] = (
        "focus_organic_electronic")
    with pytest.raises(TemplateLibraryError, match="of focus_electronic/organic_electronic"):
        TemplateLibrary(raw)
    raw = _raw_library()
    raw["subgenre_templates"]["by_genre"]["polka"] = {"oompah": "focus_minimal_pulse"}
    with pytest.raises(TemplateLibraryError, match="no template"):
        TemplateLibrary(raw)


def test_a_sub_style_package_keeps_its_sound_through_a_top_up() -> None:
    directive = _focus({"subgenre": "organic_electronic"})
    genome = build_genome_from_directive(directive)
    exp = design_experiment(directive, genome, created_at=FIXED_NOW)
    exp = promote_to_validated(exp, validate_experiment(exp, genome))
    package = materialize_package(exp, exp.treatments[0], genome, created_at=FIXED_NOW)
    # 90-120 min at 210 s tracks: ceil(5400/210) = 26 base + ceil(26 * 0.25) = 7 spares
    assert len(package.track_specs) == 33
    _, extra = extend_package(package, count=2, idempotency_key="k", created_at=FIXED_NOW)
    organic_leads = {"kalimba", "marimba", "nylon_guitar", "soft_flute", "handpan",
                     "felt_piano"}
    for spec in (*package.track_specs, *extra):
        arrangement = spec.creative_latitude["track_arrangement"]
        assert arrangement["subgenre"] == "organic_electronic"
        assert arrangement["lead_instrument"] in organic_leads
        assert spec.creative_latitude["energy"] in ("low", "medium")
        assert spec.bounded_preferences.bpm == 96
        assert spec.vocal_spec == {"vocal_mode": "instrumental", "vocal_gender": "none"}
