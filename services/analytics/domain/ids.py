"""Deterministic identifier generation for Phase P9 (P9 §4, §54)."""

from __future__ import annotations

import hashlib


def compute_sha256(content: bytes | str) -> str:
    """Compute hex SHA-256 digest of string or bytes."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    return hashlib.sha256(content).hexdigest()


def build_observation_id(entity_id: str, metric_name: str, window: str, date_str: str) -> str:
    """Build deterministic observation record ID."""
    canonical = f"{entity_id}|{metric_name}|{window}|{date_str}"
    return f"obs_{compute_sha256(canonical)[:16]}"


def build_statement_id(provider: str, period: str, content_hash: str) -> str:
    """Build deterministic revenue statement ID."""
    canonical = f"{provider}|{period}|{content_hash}"
    return f"stmt_{compute_sha256(canonical)[:16]}"


def build_cost_id(entity_id: str, category: str, period: str) -> str:
    """Build deterministic cost record ID."""
    canonical = f"{entity_id}|{category}|{period}"
    return f"cost_{compute_sha256(canonical)[:16]}"


def build_outcome_id(experiment_id: str, unit_id: str, window: str, maturity: str) -> str:
    """One measured fact: an experiment's unit (its content: a video, a release) over one
    window at one maturity. An experiment has several treatments and so several units;
    keying on the arm made the second unit overwrite the first (ADR-0031)."""
    canonical = f"{experiment_id}|{unit_id}|{window}|{maturity}"
    return f"outc_{compute_sha256(canonical)[:16]}"


def build_reward_id(experiment_id: str, content_id: str, window: str, maturity: str) -> str:
    """Deterministic C7 leg-2 reward record ID: one per fact P2 stores (ADR-0031).

    P2 keys a result on (experiment, content, window, maturity). Keying the reward on
    (experiment, arm, window) let a mature reward overwrite the provisional one before it
    and two contents of one arm overwrite each other.
    """
    canonical = f"{experiment_id}|{content_id}|{window}|{maturity}"
    return f"rwd_{compute_sha256(canonical)[:16]}"


def build_fx_rate_id(base: str, quote: str, as_of_iso: str, source: str) -> str:
    """Build deterministic FX rate record ID (one per pair, instant and source)."""
    canonical = f"{base}|{quote}|{as_of_iso}|{source}"
    return f"fx_{compute_sha256(canonical)[:16]}"
