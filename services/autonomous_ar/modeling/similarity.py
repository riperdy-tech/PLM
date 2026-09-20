# services/autonomous_ar/modeling/similarity.py
"""Deterministic structured similarity (§7/§8/§25/§53).

Structured comparison comes first and only (§8: an embedding layer, if ever added, may
not replace this). Weights are a fixed, named set tied to `feature_version`; a dimension
the wire left empty is excluded from the denominator and never credited as a match.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..domain.arm import ArmDimensions
from ..storage.ids import deterministic_id

__all__ = ["DIMENSION_WEIGHTS", "arm_similarity", "cluster_arms", "structural_overlap"]

DIMENSION_WEIGHTS: Mapping[str, float] = {
    "genre": 3.0, "subgenre": 1.0, "mood": 1.5, "use_case": 2.0, "language": 2.0,
    "vocal_mode": 1.5, "duration_bucket": 1.0, "visual_world": 2.0, "title_archetype": 0.5,
}
"""Invented weights (the spec fixes the dimension set, not the weights); changing them is a
new `feature_version`, never a silent edit."""


def _jaccard(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    set_a, set_b = set(a), set(b)
    return len(set_a & set_b) / len(set_a | set_b)


def arm_similarity(a: ArmDimensions, b: ArmDimensions) -> float:
    """Weighted Jaccard over dimensions present on both sides; 1.0 for identical arms.

    A dimension the wire left empty on *either* side is excluded from the denominator and
    never credited as a match — absence is not a measured zero. Sparse arms therefore
    score high on what little they share, and sparsity is bounded by `structural_overlap`
    (the P2.5 transfer gate) instead.
    """
    scored = 0.0
    total = 0.0
    for name, weight in DIMENSION_WEIGHTS.items():
        tokens_a, tokens_b = a.tokens(name), b.tokens(name)
        if tokens_a is None or tokens_b is None:
            continue  # absent on one or both sides: excluded, never credited as a match
        total += weight
        scored += weight * _jaccard(tokens_a, tokens_b)
    return 0.0 if total == 0.0 else scored / total


def structural_overlap(a: ArmDimensions, b: ArmDimensions) -> int:
    """How many dimensions both arms actually carry (§64/§65's transfer gate)."""
    return sum(
        1 for name in DIMENSION_WEIGHTS
        if a.tokens(name) is not None and b.tokens(name) is not None
    )


def cluster_arms(pairs: Sequence[tuple[str, str, float]], *, threshold: float) -> dict[str, str]:
    """Union-find over pairs at/above the threshold; cluster ids derive from member sets."""
    parent: dict[str, str] = {}

    def find(item: str) -> str:
        parent.setdefault(item, item)
        root = item
        while parent[root] != root:
            root = parent[root]
        while parent[item] != root:  # path compression, order-independent
            parent[item], item = root, parent[item]
        return root

    for arm_a, arm_b, score in pairs:
        if score < threshold:
            parent.setdefault(arm_a, arm_a)
            parent.setdefault(arm_b, arm_b)
            continue
        root_a, root_b = find(arm_a), find(arm_b)
        if root_a != root_b:
            parent[max(root_a, root_b)] = min(root_a, root_b)

    members: dict[str, list[str]] = {}
    for arm_id in parent:
        members.setdefault(find(arm_id), []).append(arm_id)
    assignments: dict[str, str] = {}
    for members_list in members.values():
        cluster_id = deterministic_id("cluster", *sorted(members_list))
        for arm_id in members_list:
            assignments[arm_id] = cluster_id
    return assignments
