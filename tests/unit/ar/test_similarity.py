# tests/unit/ar/test_similarity.py
from services.autonomous_ar.domain.arm import ArmDimensions
from services.autonomous_ar.modeling.similarity import (
    arm_similarity,
    cluster_arms,
    structural_overlap,
)
from services.autonomous_ar.storage.ids import deterministic_id

WINNER = ArmDimensions(genre="rnb", moods=("melancholic", "dreamy"),
                       use_cases=("late_night_drive",), language="english",
                       vocal_mode="vocal")
NEIGHBOUR = ArmDimensions(genre="rnb", moods=("melancholic", "dreamy"),
                          use_cases=("rainy_commute",), language="english",
                          vocal_mode="vocal")
DISTANT = ArmDimensions(genre="city_pop", moods=("playful",), use_cases=("cafe_study",),
                        language="japanese", vocal_mode="instrumental")


def test_identical_arms_are_one_and_near_neighbours_beat_distant_arms() -> None:
    assert arm_similarity(WINNER, WINNER) == 1.0
    assert arm_similarity(WINNER, NEIGHBOUR) > arm_similarity(WINNER, DISTANT)
    assert structural_overlap(WINNER, DISTANT) == 5  # genre/mood/use_case/language/vocal_mode


def test_absent_dimensions_are_excluded_never_credited() -> None:
    sparse = ArmDimensions(genre="rnb")
    assert structural_overlap(sparse, WINNER) == 1
    # only genre is comparable; equal ⇒ 1.0 over the present weight, not over nine
    assert arm_similarity(sparse, WINNER) == 1.0
    # present on both sides but with disjoint tokens is a mismatch for that dimension
    assert arm_similarity(ArmDimensions(genre="rnb", language="english"),
                          ArmDimensions(genre="rnb", language="korean")) == 0.6  # 3.0 of 5.0


def test_clustering_is_transitive_and_deterministic() -> None:
    pairs = [("a", "b", 0.8), ("b", "c", 0.7), ("d", "e", 0.9), ("c", "d", 0.1)]
    first = cluster_arms(pairs, threshold=0.6)
    second = cluster_arms(list(reversed(pairs)), threshold=0.6)
    assert first == second, "cluster assignment must not depend on pair order (replayability)"
    assert first["a"] == first["b"] == first["c"] != first["d"] == first["e"]
    assert first["a"] == deterministic_id("cluster", "a", "b", "c"), (
        "cluster ids are derived from the member set, never minted"
    )


def test_below_threshold_pairs_never_cluster() -> None:
    assignments = cluster_arms([("a", "b", 0.59)], threshold=0.6)
    assert assignments["a"] != assignments["b"]


def test_replay_from_the_same_inputs_is_identical() -> None:
    pairs = [("a", "b", 0.8), ("b", "c", 0.7), ("d", "e", 0.9)]
    first = cluster_arms(pairs, threshold=0.6)
    second = cluster_arms(pairs, threshold=0.6)
    assert list(first.items()) == list(second.items()), "same inputs ⇒ same ids, same order"
    assert arm_similarity(WINNER, NEIGHBOUR) == arm_similarity(WINNER, NEIGHBOUR)
    assert structural_overlap(WINNER, DISTANT) == structural_overlap(WINNER, DISTANT)
