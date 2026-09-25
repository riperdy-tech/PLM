"""Deterministic UUID5 ID generators for music factory entities (Ruling R14)."""

from __future__ import annotations

import uuid

__all__ = [
    "deterministic_id",
    "plan_id",
    "attempt_id",
    "candidate_id",
    "artifact_id",
    "variant_id",
    "rights_snapshot_id",
]

_PLM_MUSIC_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "music.factory.plm.internal")


def deterministic_id(prefix: str, *keys: str) -> str:
    """Generate a reproducible, collision-resistant identifier from domain keys."""
    combined = ":".join(keys)
    digest = uuid.uuid5(_PLM_MUSIC_NAMESPACE, combined).hex[:16]
    return f"{prefix}_{digest}"


def plan_id(track_spec_id: str, experiment_id: str, policy_version: str) -> str:
    return deterministic_id("gen_plan", track_spec_id, experiment_id, policy_version)


def variant_id(generation_plan_id: str, variant_code: str) -> str:
    return deterministic_id("var", generation_plan_id, variant_code)


def attempt_id(generation_plan_id: str, variant_code: str, attempt_index: int) -> str:
    return deterministic_id("gen_att", generation_plan_id, variant_code, str(attempt_index))


def candidate_id(generation_plan_id: str, attempt_id: str, candidate_index: int) -> str:
    return deterministic_id("cand", generation_plan_id, attempt_id, str(candidate_index))


def artifact_id(candidate_id: str, artifact_type: str, sha256: str) -> str:
    return deterministic_id("art", candidate_id, artifact_type, sha256[:16])


def rights_snapshot_id(attempt_id: str, provider_id: str) -> str:
    return deterministic_id("rgt", attempt_id, provider_id)
