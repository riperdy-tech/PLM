# services/market_intelligence/taxonomy/arms.py
"""§16: arms are production hypotheses mined from co-occurring classified attributes.

Identity is P1's (R15): the fingerprint covers all 13 dimensions; `arm_id` is the
readable slug the spec's example shows, with a deterministic collision suffix. P1
never allocates, never scores arms against each other as a bandit would, and never
writes arm *state* beyond its own lifecycle column (P2 owns that).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..domain.value_objects import TaxonomyProfile, fingerprint, slug_token
from .validators import ClassificationDraft

__all__ = ["ArmDraft", "arm_id_for", "generate_candidate_arms"]

_ID_DIMENSIONS = (
    "genre", "use_case", "language", "vocal_mode", "duration_bucket",
)


@dataclass(frozen=True, slots=True)
class ArmDraft:
    arm_id: str
    fingerprint: str
    profile: TaxonomyProfile
    source_observation_ids: tuple[str, ...] = ()
    support_count: int = 0


def arm_id_for(profile: TaxonomyProfile, *, taken: frozenset[str] = frozenset()) -> str:
    """Readable slug; on collision, `_2`, `_3`, … in call order (deterministic per batch)."""
    parts = [str(getattr(profile, name)) for name in _ID_DIMENSIONS
             if getattr(profile, name) is not None]
    base = slug_token("_".join(parts), max_length=44) or "arm"
    if base not in taken:
        return base
    suffix = 2
    while f"{base}_{suffix}" in taken:
        suffix += 1
    return f"{base}_{suffix}"


def generate_candidate_arms(
    classifications: Sequence[ClassificationDraft],
    *,
    min_support: int,
    observation_ids_by_content: Mapping[str, tuple[str, ...]] | None = None,
) -> list[ArmDraft]:
    """Group classifications by their *identity-bearing* dimensions; keep supported groups.

    The grouping key is the fingerprint, so two observations that agree on every
    classified dimension form one arm, and an unclassified dimension never silently
    merges two different arms (§14.3).
    """
    if min_support < 1:
        raise ValueError("min_support must be at least 1")
    groups: dict[str, list[ClassificationDraft]] = {}
    for draft in classifications:
        profile = _profile_from_draft(draft)
        groups.setdefault(fingerprint(profile), []).append(draft)
    drafts: list[ArmDraft] = []
    taken: set[str] = set()
    for arm_fingerprint in sorted(groups):
        members = groups[arm_fingerprint]
        if len(members) < min_support:
            continue
        profile = _profile_from_draft(members[0])
        arm_id = arm_id_for(profile, taken=frozenset(taken))
        taken.add(arm_id)
        observation_ids: tuple[str, ...] = ()
        if observation_ids_by_content is not None:
            collected: list[str] = []
            for member in members:
                collected.extend(observation_ids_by_content.get(member.content_fingerprint, ()))
            observation_ids = tuple(dict.fromkeys(collected))
        drafts.append(ArmDraft(arm_id=arm_id, fingerprint=arm_fingerprint, profile=profile,
                               source_observation_ids=observation_ids,
                               support_count=len(members)))
    return drafts


def _profile_from_draft(draft: ClassificationDraft) -> TaxonomyProfile:
    from .ontology import profile_from_dimensions

    return profile_from_dimensions(draft.dimensions)
