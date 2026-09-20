# services/autonomous_ar/modeling/value_model.py
"""The versioned value model (ADR-0023 #4): what a 0–1 `expected_value` means.

First cycles use §20's early-stage proxy. The full Expected Economic Value terms are
reserved in C7's reward schema; this module never emits one — an unmeasured term keeps
the composite `unknown`, and renormalising around a hole is forbidden.
"""

from __future__ import annotations

from collections.abc import Mapping

from plm_contracts import Tri

__all__ = [
    "EARLY_STAGE_PROXY_WEIGHTS", "VALUE_MODEL_ID", "compose_proxy", "term_from_raw",
]

VALUE_MODEL_ID = "early-stage-proxy-v1"

EARLY_STAGE_PROXY_WEIGHTS: Mapping[str, float] = {
    "watch_time": 0.30, "view_velocity": 0.25, "retention": 0.15,
    "subscriber_conversion": 0.10, "engagement": 0.10, "qualitative_catalog": 0.10,
}
"""§20's decomposition. Weights sum to 1.0; no key is a reserved economic term."""

TERM_SOURCES: Mapping[str, str] = {
    "watch_time": "watch_time_minutes",
    "retention": "average_percentage_viewed",
    "subscriber_conversion": "subscriber_gain",
}
"""Terms directly measurable from a P9-neutral raw metric. Everything else is unknown
until a producer measures it — including `view_velocity`, which needs a denominator."""


def term_from_raw(term: str, raw_metrics: Mapping[str, Tri[float]]) -> Tri[float]:
    """A proxy term from raw metrics; unknown for unmapped or absent sources."""
    source = TERM_SOURCES.get(term)
    if source is None:
        return Tri.unknown()
    return raw_metrics.get(source, Tri.unknown())


def compose_proxy(terms: Mapping[str, Tri[float]]) -> Tri[float]:
    """§20's weighted composite. Every weighted term must be measured, else `unknown`."""
    unknown = set(terms) - set(EARLY_STAGE_PROXY_WEIGHTS)
    if unknown:
        raise ValueError(f"unknown proxy term(s): {sorted(unknown)}")
    total = 0.0
    for term, weight in EARLY_STAGE_PROXY_WEIGHTS.items():
        value = terms.get(term, Tri.unknown())
        if value.state.value != "known":
            return Tri.unknown()
        total += weight * float(value.unwrap())
    return Tri.known(total)
