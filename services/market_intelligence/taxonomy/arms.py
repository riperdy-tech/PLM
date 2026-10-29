# services/market_intelligence/taxonomy/arms.py
"""§16: arms are production hypotheses mined from co-occurring classified attributes.

Identity is P1's (R15): the fingerprint covers all 13 dimensions; `arm_id` is the
readable slug the spec's example shows, with a deterministic collision suffix. P1
never allocates, never scores arms against each other as a bandit would, and never
writes arm *state* beyond its own lifecycle column (P2 owns that).
"""

from __future__ import annotations

import hashlib
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
    taxonomy_version: str | None = None,
) -> list[ArmDraft]:
    """Group classifications by dimensions in a stable taxonomy-version namespace.

    Previously stored v1 arms must never be updated with v2 observation
    evidence even when a profile happens to be identical. The optional
    namespace keeps direct historical callers unchanged. P1 recomputation
    passes its configured taxonomy version explicitly (ADR-0042).

    The opt-in v3 namespace uses a 160-bit SHA-256 digest prefix of the
    canonical, versioned 13-dimensional fingerprint, plus a short readable
    slug, capped at 64 characters for downstream SQL schemas. Unlike v1/v2
    batch-ordinal suffixes, the ID does not depend on other profiles in a
    batch. It changes no existing v2 rows or default taxonomy setting.
    """
    if min_support < 1:
        raise ValueError("min_support must be at least 1")
    if taxonomy_version is not None and not taxonomy_version.strip():
        raise ValueError("taxonomy_version namespace cannot be blank")
    prefix = (
        f"{slug_token(taxonomy_version, max_length=20)}_"
        if taxonomy_version is not None else ""
    )
    groups: dict[str, list[ClassificationDraft]] = {}
    for draft in classifications:
        profile = _profile_from_draft(draft)
        profile_fingerprint = fingerprint(profile)
        identity = (
            f"{taxonomy_version}|{profile_fingerprint}"
            if taxonomy_version is not None else profile_fingerprint
        )
        groups.setdefault(identity, []).append(draft)
    drafts: list[ArmDraft] = []
    taken: set[str] = set()
    for arm_fingerprint in sorted(groups):
        members = groups[arm_fingerprint]
        if len(members) < min_support:
            continue
        profile = _profile_from_draft(members[0])
        if taxonomy_version == "p1-taxonomy-v3":
            # Consumer schemas (P5/P6/P7/P8/P9) store arm IDs in VARCHAR(64).
            # Preserve 160 bits of collision-resistant, versioned identity
            # and spend only the remaining space on a human-readable hint.
            digest = hashlib.sha256(arm_fingerprint.encode("utf-8")).hexdigest()[:40]
            slug_size = 64 - len(prefix) - len(digest) - 1
            slug = slug_token(arm_id_for(profile), max_length=slug_size) or "arm"
            arm_id = f"{prefix}{slug}_{digest}"
        else:
            # Keep v1 and already-materialized v2 IDs byte-for-byte stable.
            slug = arm_id_for(
                profile,
                taken=frozenset(
                    token[len(prefix):] for token in taken if token.startswith(prefix)
                ),
            )
            arm_id = f"{prefix}{slug}"
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
