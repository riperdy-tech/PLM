"""Deterministic UUID5 ID generators for P3 experiments and packages (PHASE-P3 §4.6, §73)."""

from __future__ import annotations

import uuid
from typing import Final

PLM_EXPERIMENTS_NAMESPACE: Final[uuid.UUID] = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://plm.music/experiments"
)


def deterministic_id(prefix: str, *parts: str) -> str:
    """Generate a deterministic UUID5 hex ID from business key components."""
    joined = ":".join(parts)
    raw_uuid = uuid.uuid5(PLM_EXPERIMENTS_NAMESPACE, joined)
    return f"{prefix}_{raw_uuid.hex[:16]}"


def experiment_id(arm_id: str, directive_id: str, experiment_type: str) -> str:
    return deterministic_id("exp", arm_id, directive_id, experiment_type)


def treatment_id(exp_id: str, treatment_index: int) -> str:
    return deterministic_id("trt", exp_id, str(treatment_index))


def package_id(exp_id: str, trt_id: str) -> str:
    return deterministic_id("pkg", exp_id, trt_id)


def spec_id(spec_type: str, pkg_id: str, qualifier: str = "") -> str:
    return deterministic_id(spec_type, pkg_id, qualifier)
