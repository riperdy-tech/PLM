# tests/unit/ar/test_domain_policy.py
import pytest
from plm_contracts import ChannelActivationState

from services.autonomous_ar.config import ARSettings, load_ar_settings
from services.autonomous_ar.domain.policy import DEFAULT_POLICY, PolicyConfig


def test_default_policy_fractions_sum_to_exactly_one() -> None:
    total = (DEFAULT_POLICY.exploit_fraction + DEFAULT_POLICY.adjacent_explore_fraction
             + DEFAULT_POLICY.radical_explore_fraction)
    assert total == pytest.approx(1.0), "§13 governs §90; baseline is inside exploit (ADR-0023 #3)"
    assert (DEFAULT_POLICY.exploit_fraction, DEFAULT_POLICY.adjacent_explore_fraction,
            DEFAULT_POLICY.radical_explore_fraction) == (0.70, 0.20, 0.10)
    assert DEFAULT_POLICY.baseline_fraction <= DEFAULT_POLICY.exploit_fraction


def test_policy_whose_fractions_exceed_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="1.1"):
        PolicyConfig(name="bad", exploit_fraction=0.70, adjacent_explore_fraction=0.20,
                     radical_explore_fraction=0.20)


def test_baseline_fraction_cannot_exceed_the_exploit_slice() -> None:
    with pytest.raises(ValueError, match="baseline_fraction"):
        PolicyConfig(name="bad", exploit_fraction=0.5, adjacent_explore_fraction=0.2,
                     radical_explore_fraction=0.3, baseline_fraction=0.6)


def test_ar_settings_defaults_match_the_frozen_version_strings() -> None:
    settings = load_ar_settings()
    assert settings.policy_version == "p2-v1" and settings.model_version == "p2-nn-v1"
    assert settings.default_activation_state is ChannelActivationState.SHADOW
    assert settings.market_base_url == "http://127.0.0.1:8081"
    assert settings.directive_version == "p2-directive-v1"
    assert settings.value_model == "early-stage-proxy-v1"
    with pytest.raises(TypeError, match="unknown ar settings"):
        load_ar_settings(not_a_knob=1)


def test_settings_are_overridable_without_touching_the_environment() -> None:
    settings: ARSettings = load_ar_settings(similarity_threshold=0.75)
    assert settings.similarity_threshold == 0.75
