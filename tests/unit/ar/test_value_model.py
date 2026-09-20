# tests/unit/ar/test_value_model.py
import pytest
from plm_contracts import Tri

from services.autonomous_ar.domain.reward import RESERVED_VALUE_TERMS
from services.autonomous_ar.modeling.value_model import (
    EARLY_STAGE_PROXY_WEIGHTS,
    VALUE_MODEL_ID,
    compose_proxy,
    term_from_raw,
)


def test_proxy_weights_are_the_spec_decomposition_and_carry_no_revenue_term() -> None:
    assert VALUE_MODEL_ID == "early-stage-proxy-v1"
    assert set(EARLY_STAGE_PROXY_WEIGHTS) == {
        "view_velocity", "watch_time", "retention", "subscriber_conversion", "engagement",
        "qualitative_catalog",
    }
    assert sum(EARLY_STAGE_PROXY_WEIGHTS.values()) == pytest.approx(1.0)
    assert not (set(EARLY_STAGE_PROXY_WEIGHTS) & set(RESERVED_VALUE_TERMS)), (
        "ADR-0023 #4: the proxy never fabricates a revenue term"
    )


def test_composite_is_unknown_when_any_term_is_unmeasured() -> None:
    terms = {name: Tri.known(0.5) for name in EARLY_STAGE_PROXY_WEIGHTS}
    assert compose_proxy(terms).unwrap() == pytest.approx(0.5)
    terms["engagement"] = Tri.unknown()
    assert compose_proxy(terms).state.value == "unknown", (
        "never renormalise around a hole: the composite is unknown (§20 full-basis definition)"
    )
    del terms["retention"]
    assert compose_proxy(terms).state.value == "unknown"


def test_unknown_term_names_are_refused() -> None:
    with pytest.raises(ValueError, match="unknown proxy term"):
        compose_proxy({"revenue": Tri.known(0.9)})


def test_term_from_raw_maps_only_what_is_directly_measurable() -> None:
    raw = {"watch_time_minutes": Tri.known(185000.0),
           "average_percentage_viewed": Tri.known(41.2), "subscriber_gain": Tri.known(613.0)}
    assert term_from_raw("watch_time", raw).unwrap() == 185000.0
    assert term_from_raw("retention", raw).unwrap() == 41.2
    assert term_from_raw("subscriber_conversion", raw).unwrap() == 613.0
    assert term_from_raw("engagement", raw).state.value == "unknown"
    assert term_from_raw("view_velocity", {}).state.value == "unknown"


def test_reserved_economic_terms_stay_unknown_never_zero() -> None:
    raw = {"watch_time_minutes": Tri.known(185000.0)}
    assert term_from_raw("revenue", raw).state.value == "unknown", (
        "the revenue term is reserved for P9; with no measurement it is unknown, never 0.0"
    )
    assert term_from_raw("production_cost", raw).state.value == "unknown"
    terms = {name: term_from_raw(name, raw) for name in EARLY_STAGE_PROXY_WEIGHTS}
    composite = compose_proxy(terms)
    assert composite.state.value == "unknown" and not composite.is_zero, (
        "an unmeasured term keeps the composite unknown — never renormalised, never 0.0"
    )
    zero = term_from_raw("watch_time", {"watch_time_minutes": Tri.zero()})
    assert zero.is_known and zero.unwrap() == 0.0, "an explicit zero is a measurement"
