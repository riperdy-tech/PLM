"""P6 sequencing: beam search over key, tempo, energy and vocal density (R4).

Proves, on hand-built feature records (no audio): the beam search finds a better order
than a deliberately key/BPM-hostile input order; the result and its ties do not depend
on input order; the required duration window is enforced, by selection when the pool
is larger than the window; at most one take per track spec is used; and an unknown
feature costs the worst case of its term (never 0) without excluding the track.
"""

from __future__ import annotations

import pytest
from media_factory.audio.features import TrackFeatures
from media_factory.errors import MediaAssemblyError
from media_factory.playlist.sequencer import (
    REQUIRED_DURATION_UNMET,
    REQUIRED_DURATION_UNREACHABLE,
    SequenceItem,
    SequencingPolicy,
    camelot_cost,
    evaluate_order,
    sequence_tracks,
)


def _features(candidate_id: str, *, camelot: str | None = "8B", bpm: float | None = 120.0,
              energy: float | None = 0.6, vocal: float | None = 0.3) -> TrackFeatures:
    unknown = {name: "test" for name, value in
               (("key", camelot), ("bpm", bpm), ("energy", energy), ("vocal_density", vocal))
               if value is None}
    return TrackFeatures(
        candidate_id=candidate_id, bpm=bpm, bpm_confidence=None if bpm is None else 0.9,
        camelot=camelot, key_confidence=None if camelot is None else 0.9,
        energy_in=energy, energy_out=energy, energy_mean=energy, vocal_density=vocal,
        extractor_version="test", unknown_reasons=unknown,
    )


def _item(candidate_id: str, duration: float = 60.0, spec: str | None = None,
          **features: object) -> SequenceItem:
    return SequenceItem(candidate_id=candidate_id, track_spec_id=spec or f"trk_{candidate_id}",
                        duration_seconds=duration,
                        features=_features(candidate_id, **features))  # type: ignore[arg-type]


def test_beam_search_beats_a_key_and_bpm_hostile_input_order() -> None:
    # Two harmonic families far apart on the Camelot wheel and in tempo; the input order
    # alternates between them, so every input transition clashes.
    pool = [
        _item("a1", camelot="8B", bpm=90.0), _item("b1", camelot="2B", bpm=140.0),
        _item("a2", camelot="9B", bpm=92.0), _item("b2", camelot="3B", bpm=142.0),
        _item("a3", camelot="8A", bpm=91.0), _item("b3", camelot="2A", bpm=139.0),
    ]
    required = 6 * 60.0 - 5 * 2.0
    result = sequence_tracks(pool, required_duration_seconds=required,
                             transition_offset_seconds=-2.0)
    hostile = evaluate_order(pool, [i.candidate_id for i in pool],
                             required_duration_seconds=required, transition_offset_seconds=-2.0)

    assert set(result.order) == {i.candidate_id for i in pool}
    assert result.score < hostile
    assert result.report["input_order_score"] == pytest.approx(hostile, abs=1e-6)
    # The families stay together: exactly one clashing family change.
    family_changes = sum(1 for x, y in zip(result.order, result.order[1:], strict=False)
                         if x[0] != y[0])
    assert family_changes == 1


def test_the_result_and_its_ties_do_not_depend_on_input_order() -> None:
    identical = [_item(cid) for cid in ("c3", "c1", "c2")]
    first = sequence_tracks(identical, required_duration_seconds=176.0,
                            transition_offset_seconds=-2.0)
    second = sequence_tracks(list(reversed(identical)), required_duration_seconds=176.0,
                             transition_offset_seconds=-2.0)
    # Equal costs everywhere: the tie-break is the sequence of candidate ids.
    assert first.order == second.order == ("c1", "c2", "c3")
    assert first.report == second.report


def test_the_required_duration_is_enforced() -> None:
    pool = [_item(f"t{n}", duration=60.0) for n in range(5)]
    # Too long for everything together: unreachable.
    with pytest.raises(MediaAssemblyError) as raised:
        sequence_tracks(pool, required_duration_seconds=600.0, transition_offset_seconds=0.0)
    assert raised.value.code == REQUIRED_DURATION_UNREACHABLE
    # Reachable in total but no subset lands in the window (multiples of 60 s only).
    with pytest.raises(MediaAssemblyError) as raised:
        sequence_tracks(pool, required_duration_seconds=150.0, transition_offset_seconds=0.0,
                        policy=SequencingPolicy(duration_tolerance_ratio=0.05))
    assert raised.value.code == REQUIRED_DURATION_UNMET
    # A larger pool than the window: three of five tracks are chosen, inside the window.
    result = sequence_tracks(pool, required_duration_seconds=180.0,
                             transition_offset_seconds=0.0)
    assert len(result.order) == 3
    low, high = result.window
    assert low <= result.timeline_seconds <= high
    assert len(result.not_selected) == 2


def test_at_most_one_take_of_a_track_spec_is_used() -> None:
    pool = [_item("x1", spec="trk_x"), _item("x2", spec="trk_x"), _item("y1", spec="trk_y")]
    with pytest.raises(MediaAssemblyError) as raised:
        sequence_tracks(pool, required_duration_seconds=180.0, transition_offset_seconds=0.0)
    assert raised.value.code == REQUIRED_DURATION_UNREACHABLE
    result = sequence_tracks(pool, required_duration_seconds=120.0,
                             transition_offset_seconds=0.0)
    assert len({o[0] for o in result.order}) == 2


def test_an_unknown_feature_is_scored_as_worst_case_and_never_as_zero() -> None:
    assert camelot_cost(None, "8B") == camelot_cost("8B", "2B") == 1.0
    assert camelot_cost("8B", "9B") < 1.0

    known = [_item("k1", camelot="8B"), _item("k2", camelot="8B")]
    unknown_key = [_item("k1", camelot="8B"), _item("k2", camelot=None)]
    clash = [_item("k1", camelot="8B"), _item("k2", camelot="2B")]
    zero_vocal = [_item("k1", vocal=0.0), _item("k2", vocal=0.0)]
    unknown_vocal = [_item("k1", vocal=0.0), _item("k2", vocal=None)]
    kwargs = {"required_duration_seconds": 120.0, "transition_offset_seconds": 0.0}

    def score(pool: list[SequenceItem]) -> float:
        return evaluate_order(pool, ["k1", "k2"], **kwargs)  # type: ignore[arg-type]

    assert score(unknown_key) == pytest.approx(score(clash))
    assert score(unknown_key) > score(known)
    # Unknown vocal density is not "no vocals" (0): it costs the full term.
    assert score(unknown_vocal) > score(zero_vocal)


def test_a_track_with_unknown_features_is_not_excluded() -> None:
    pool = [_item("u1", camelot=None, bpm=None, energy=None, vocal=None), _item("k1"),
            _item("k2")]
    result = sequence_tracks(pool, required_duration_seconds=180.0,
                             transition_offset_seconds=0.0)
    assert "u1" in result.order
    assert result.report["unknown_features"]["u1"] == {
        "bpm": "test", "energy": "test", "key": "test", "vocal_density": "test"}
