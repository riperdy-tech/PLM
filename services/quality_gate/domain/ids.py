"""Deterministic UUID5 ID generators for quality gate entities (Ruling R14)."""

from __future__ import annotations

import uuid

__all__ = [
    "check_id",
    "deterministic_id",
    "evaluation_id",
    "evidence_id",
    "gate_decision_id",
    "p5_manifest_id",
    "quarantine_case_id",
    "risk_finding_id",
    "snapshot_id",
]

_PLM_QA_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "qa.gate.plm.internal")


def deterministic_id(prefix: str, *keys: str) -> str:
    """Generate a reproducible, collision-resistant identifier from domain keys."""
    combined = ":".join(keys)
    digest = uuid.uuid5(_PLM_QA_NAMESPACE, combined).hex[:16]
    return f"{prefix}_{digest}"


def evaluation_id(candidate_id: str, policy_version: str, trigger: str) -> str:
    return deterministic_id("eval", candidate_id, policy_version, trigger)


def snapshot_id(evaluation_id_str: str) -> str:
    return deterministic_id("snap", evaluation_id_str)


def check_id(evaluation_id_str: str, check_category: str, check_type: str) -> str:
    return deterministic_id("chk", evaluation_id_str, check_category, check_type)


def risk_finding_id(evaluation_id_str: str, finding_code: str) -> str:
    return deterministic_id("fnd", evaluation_id_str, finding_code)


def evidence_id(evaluation_id_str: str, evidence_type: str, content_hash: str) -> str:
    return deterministic_id("evi", evaluation_id_str, evidence_type, content_hash[:16])


def gate_decision_id(evaluation_id_str: str, decision_version: str) -> str:
    return deterministic_id("gdec", evaluation_id_str, decision_version)


def quarantine_case_id(candidate_id: str, evaluation_id_str: str) -> str:
    return deterministic_id("quar", candidate_id, evaluation_id_str)


def p5_manifest_id(candidate_id: str, evaluation_id_str: str, version: str) -> str:
    """Stable downstream manifest identifier (Ruling R7)."""
    return deterministic_id("rel_man", candidate_id, evaluation_id_str, version)
