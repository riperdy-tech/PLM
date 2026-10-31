"""P3 -> P4 album contrast is carried to the provider prompt, not just metadata."""
from __future__ import annotations

from itertools import pairwise
from typing import Any

from services.experiments.domain.enums import VariableClass
from services.experiments.domain.experiment import ExperimentVariable
from services.experiments.materialization.arrangements import (
    ARRANGEMENT_VERSION,
    arrangement_direction,
)
from services.experiments.materialization.builder import extend_package, materialize_package
from services.music_factory.generation.prompts import build_generation_prompt
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from tests.unit.experiments.test_materializer import FIXED_NOW, _sample_experiment


def test_album_tracks_have_distinct_arrangement_briefs_and_provider_prompts() -> None:
    exp, treatment, genome = _sample_experiment()
    package = materialize_package(exp, treatment, genome, created_at=FIXED_NOW)
    replay = materialize_package(exp, treatment, genome, created_at=FIXED_NOW)
    directions = [spec.creative_latitude["track_arrangement"] for spec in package.track_specs]
    assert directions == [spec.creative_latitude["track_arrangement"] for spec in replay.track_specs]
    assert len({(d["rhythmic_feel"], d["melodic_motion"]) for d in directions[:8]}) == 8
    assert all(d["policy_version"] == "p3-album-contrast-v5" for d in directions)
    # Every planned track, not just the first eight, must receive a different
    # rhythmic+melodic pairing. The former eight-pattern cycle reused briefs.
    assert len({(d["rhythmic_feel"], d["melodic_motion"]) for d in directions}) == len(directions)

    prompts = [
        build_generation_prompt(adapt_track_spec(spec.to_wire()))
        for spec in package.track_specs[:8]
    ]
    assert len({p.prompt_hash for p in prompts}) == 8
    for direction, prompt in zip(directions, prompts, strict=False):
        assert direction["melodic_motion"] in prompt.prompt
        assert direction["rhythmic_feel"] in prompt.prompt
    # The experiment's controlled factors are unchanged across all tracks.
    assert {spec.bounded_preferences.bpm for spec in package.track_specs} == {80}
    assert {spec.hard_constraints.genre for spec in package.track_specs} == {"rnb"}


def test_long_album_and_top_up_have_no_eight_track_arrangement_loop() -> None:
    all_directions = [arrangement_direction(
        "album_64", position, ("piano", "bass", "guitar"),
    ) for position in range(64)]
    assert len({
        (direction["rhythmic_feel"], direction["melodic_motion"])
        for direction in all_directions
    }) == 64
    assert all(direction["policy_version"] == ARRANGEMENT_VERSION for direction in all_directions)
    assert all_directions == [arrangement_direction(
        "album_64", position, ("piano", "bass", "guitar"),
    ) for position in range(64)]


def test_extension_gets_fresh_arrangement_not_a_clone() -> None:
    exp, treatment, genome = _sample_experiment()
    package = materialize_package(exp, treatment, genome, created_at=FIXED_NOW)
    extension, tracks = extend_package(
        package, count=2, idempotency_key="more-supply", created_at=FIXED_NOW
    )
    assert extension.track_count == 2
    assert tracks[0].creative_latitude["track_arrangement"] != tracks[1].creative_latitude["track_arrangement"]
    assert tracks[0].creative_latitude["track_arrangement"] != package.track_specs[0].creative_latitude["track_arrangement"]
    assert (
        extend_package(package, count=2, idempotency_key="more-supply", created_at=FIXED_NOW)[1]
        == tracks
    )


def test_a_texture_or_drum_is_never_the_lead() -> None:
    from services.experiments.materialization.arrangements import is_melodic

    palette = ("dusty_rhodes", "vinyl_crackle", "sidechain_kick")
    leads = {arrangement_direction("album", position, palette)["lead_instrument"]
             for position in range(24)}
    assert leads == {"dusty_rhodes"}
    assert not is_melodic("vinyl_crackle") and not is_melodic("brushed_drums")
    assert arrangement_direction("album", 0, ("vinyl_crackle",))["lead_instrument"] == ""


def _lofi_package(held_variable: str | None = None) -> Any:
    exp, treatment, genome = _sample_experiment()
    lofi = genome.model_copy(update={
        "genre": "lofi", "key_profile": "F_major",
        "instrumentation_profile": ("dusty_rhodes", "vinyl_crackle", "sidechain_kick"),
    })
    if held_variable is not None:
        exp = exp.model_copy(update={"variables": (*exp.variables, ExperimentVariable(
            name=held_variable, variable_class=VariableClass.EXPERIMENTAL,
            baseline_value=getattr(lofi, held_variable)))})
    return materialize_package(exp, treatment, lofi, created_at=FIXED_NOW)


def test_lofi_tracks_vary_every_style_dimension_with_one_album_texture() -> None:
    package = _lofi_package()
    specs = package.track_specs
    directions = [spec.creative_latitude["track_arrangement"] for spec in specs]

    assert len({spec.bounded_preferences.key_profile for spec in specs}) >= 6
    assert all(a.bounded_preferences.key_profile != b.bounded_preferences.key_profile
               for a, b in pairwise(specs))
    assert len({d["lead_instrument"] for d in directions}) >= 5
    assert all(d["lead_instrument"] not in ("vinyl_crackle", "sidechain_kick")
               for d in directions)
    assert {spec.production_style for spec in specs} == {
        "tape_saturated", "lofi_textured", "analog_warm"}
    assert {spec.instrumentation_spec["hook_profile"] for spec in specs} == {
        "instrumental_riff", "atmospheric", "slow_burn"}
    structures = [spec.lyric_spec["song_structure"] for spec in specs]
    assert 0.2 <= structures.count("ambient_evolving") / len(specs) <= 0.45
    energies = [spec.creative_latitude["energy"] for spec in specs]
    assert {"low", "medium"} <= set(energies)
    for spec, energy in zip(specs, energies, strict=True):  # lifts only on looped, drummed tracks
        drummed = not spec.creative_latitude["track_arrangement"]["rhythmic_feel"].startswith(
            "no drums")
        if energy == "medium":
            assert drummed and spec.lyric_spec["song_structure"] == "loop_progression"
    for spec in specs:
        profile = spec.instrumentation_spec["instrumentation_profile"]
        if spec.lyric_spec["song_structure"] == "ambient_evolving":  # a drift stays calm
            groove = spec.creative_latitude["track_arrangement"]["rhythmic_feel"]
            assert groove not in ("percussive interlocking rhythm", "syncopated offbeat accents")
            assert "sidechain_kick" not in profile and "dusty_boom_bap_drums" not in profile
        assert profile[-1] == "vinyl_crackle"  # the album texture
        assert "rain_ambience" not in profile
        assert spec.creative_latitude["track_arrangement"]["lead_instrument"] == profile[0]
    assert sum("dusty_rhodes" in s.instrumentation_spec["instrumentation_profile"]
               for s in specs) < len(specs) / 3

    prompt = build_generation_prompt(adapt_track_spec(specs[1].to_wire())).prompt
    assert f"harmonic key: {specs[1].bounded_preferences.key_profile}" in prompt
    assert f"production approach: {specs[1].production_style}" in prompt
    assert f"energy: {energies[1]}" in prompt

    _, top_up = extend_package(package, count=2, idempotency_key="lofi-more",
                               created_at=FIXED_NOW)
    for spec in top_up:
        profile = spec.instrumentation_spec["instrumentation_profile"]
        assert profile[-1] == "vinyl_crackle" and len(profile) == len(set(profile))
    assert top_up[0].production_style != top_up[1].production_style or (
        top_up[0].instrumentation_spec != top_up[1].instrumentation_spec)


def test_a_dimension_the_experiment_tests_is_never_varied_by_the_palette() -> None:
    _, _, genome = _sample_experiment()
    specs = _lofi_package(held_variable="hook_profile").track_specs
    assert {spec.instrumentation_spec["hook_profile"] for spec in specs} == {genome.hook_profile}
    assert len({spec.production_style for spec in specs}) == 3


def test_every_template_genre_has_a_palette_that_varies_without_lockstep() -> None:
    from itertools import combinations

    from services.experiments.genome.engine import default_template_library
    from services.experiments.materialization.arrangements import _GENRE_PALETTES, is_melodic

    library = default_template_library()
    assert set(library.genres) <= set(_GENRE_PALETTES)
    dims = ("lead_instrument", "production_style", "hook_profile", "song_structure",
            "energy", "key_profile")
    for genre in library.genres:
        genome = library.for_genre(genre).genome
        for package in ("pkg_a", "pkg_b", "pkg_c"):
            tracks = [arrangement_direction(package, i, genome.instrumentation_profile,
                                            genre=genre) for i in range(23)]
            assert len({d["key_profile"] for d in tracks}) >= 6, genre
            assert all(is_melodic(d["lead_instrument"]) for d in tracks), genre
            for x, y in combinations(dims, 2):
                xs, ys = {d[x] for d in tracks}, {d[y] for d in tracks}
                if len(xs) > 1 and len(ys) > 1:
                    assert len({(d[x], d[y]) for d in tracks}) > max(len(xs), len(ys)), (
                        genre, x, y)
            for d in tracks:
                calm = (d.get("song_structure") == "ambient_evolving"
                        or d["rhythmic_feel"].startswith("no drums"))
                assert not (calm and d.get("energy") in ("medium", "high")), genre
                if genre == "house":
                    assert any("kick" in item for item in d["instrumentation"])
                    assert not d["rhythmic_feel"].startswith(("half-time", "no drums"))
    ambient = [arrangement_direction("pkg_a", i, (), genre="ambient") for i in range(23)]
    assert "very_low" in {d["energy"] for d in ambient}  # calm rule never lifts it to low
