# tests/unit/ar/test_explanation.py
"""P2.14 §109: the explanation is a deterministic template, reconstructable from rows."""

from services.autonomous_ar.observability.explanation import explain_decision


def test_explanation_is_deterministic_and_reconstructable_without_an_llm() -> None:
    decision = {"arm_id": "rnb-1", "allocation_type": "exploit", "sampled_reward": 0.71,
                "posterior_expected_reward": 0.66, "reason_codes": ["STRONG_POSTERIOR"],
                "policy_version": "p2-v1", "model_version": "p2-nn-v1",
                "feature_version": "p2-features-v1"}
    directive = {"market_context_json": {"p1_score": 82.0, "p1_confidence": 0.7},
                 "expected_value": 0.66, "uncertainty": 0.18,
                 "value_model": "early-stage-proxy-v1"}
    first = explain_decision(decision, directive=directive)
    second = explain_decision(decision, directive=directive)
    assert first == second
    for fragment in ("rnb-1", "exploit", "0.66", "p2-v1", "STRONG_POSTERIOR", "82.0"):
        assert fragment in first


def test_without_a_directive_the_text_still_names_the_versions() -> None:
    decision = {"arm_id": "rnb-2", "allocation_type": "radical_explore", "sampled_reward": 0.4,
                "posterior_expected_reward": 0.42, "reason_codes": [],
                "policy_version": "p2-v1", "model_version": "p2-nn-v1",
                "feature_version": "p2-features-v1"}
    text = explain_decision(decision)
    assert "rnb-2" in text and "none recorded" in text and "p2-v1" in text
    assert "market score" not in text, "absent data is omitted, never fabricated"
