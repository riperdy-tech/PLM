"""P2 settings: deployment values come from `plm_platform.config`; these are P2's knobs.

Constructed from code defaults plus explicit overrides — P2 never reads `os.environ`.
Version strings live here once and are copied onto every decision row (§76).
"""

from __future__ import annotations

from dataclasses import dataclass

from plm_contracts import ChannelActivationState
from plm_platform.config import Settings

__all__ = ["ARSettings", "load_ar_settings"]


@dataclass(frozen=True, slots=True)
class ARSettings:
    platform: Settings | None = None
    policy_version: str = "p2-v1"
    model_version: str = "p2-nn-v1"
    prior_version: str = "p2-prior-v1"
    feature_version: str = "p2-features-v1"
    proxy_normalization_version: str = "p2-proxy-norm-v1"
    directive_version: str = "p2-directive-v1"
    value_model: str = "early-stage-proxy-v1"
    default_activation_state: ChannelActivationState = ChannelActivationState.SHADOW
    market_base_url: str = "http://127.0.0.1:8081"
    market_timeout_seconds: float = 10.0
    similarity_threshold: float = 0.6
    min_transfer_shared_dimensions: int = 2
    transfer_max_weight: float = 0.25
    observation_variance: float = 0.09
    posterior_variance_floor: float = 1e-6
    default_slot_count: int = 10
    default_budget: float = 10.0
    cycle_cadence_hours: float = 168.0
    simulation_reward_version: str = "p2-sim-v1"


def load_ar_settings(*, platform: Settings | None = None, **overrides: object) -> ARSettings:
    """Build P2 settings; unknown keyword overrides raise (a typo must not pass silently).

    The `type: ignore` is deliberate: `overrides` is name-checked against the dataclass
    fields above, so mypy cannot see that each remaining value matches its field type.
    """
    unknown = set(overrides) - set(ARSettings.__dataclass_fields__)
    if unknown:
        raise TypeError(f"unknown ar settings: {sorted(unknown)}")
    return ARSettings(platform=platform, **overrides)  # type: ignore[arg-type]
