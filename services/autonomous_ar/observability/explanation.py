# services/autonomous_ar/observability/explanation.py
"""§109: structured rows → stable prose. The template is the source of truth; an LLM (later)
may only rephrase it, so this function never imports one.

Every figure states its unit or version: rewards are normalized 0–1 (the §20 proxy scale,
not money), and the policy/model/feature versions bind the claim to the rows that made it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["explain_decision"]


def explain_decision(decision: Mapping[str, Any], *,
                     directive: Mapping[str, Any] | None = None) -> str:
    """Render one stored decision (± its directive); absent pieces are omitted, never faked."""
    context = dict(directive.get("market_context_json", {})) if directive else {}
    reasons = ", ".join(decision.get("reason_codes", [])) or "none recorded"
    uncertainty = directive.get("uncertainty") if directive else None
    lines = [
        f"{decision['arm_id']}: {decision['allocation_type']}",
        (f"  posterior reward {decision['posterior_expected_reward']:.2f} "
         f"(normalized 0-1; sampled {decision['sampled_reward']:.2f})"),
    ]
    if uncertainty is not None:
        lines.append(f"  uncertainty {uncertainty:.2f}")
    if context:
        lines.append(f"  market score {context.get('p1_score')} "
                     f"(confidence {context.get('p1_confidence')})")
    lines.append(f"  reasons: {reasons}")
    lines.append(f"  policy {decision['policy_version']} · model {decision['model_version']} "
                 f"· features {decision['feature_version']}")
    return "\n".join(lines)
