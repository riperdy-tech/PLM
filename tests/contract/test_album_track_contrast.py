"""P3 -> P4 album contrast is carried to the provider prompt, not just metadata."""
from __future__ import annotations

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
    assert all(d["policy_version"] == "p3-album-contrast-v2" for d in directions)
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
