"""Review Focus 1: missing data must never render as zero or as a passing value.

Owning test for the `plm_contracts.Tri` tri-state (master plan W0.3, §3 Review Focus 1).
P9 §58 names the states this type must keep distinct: NULL = unknown, NOT_READY = expected
later, ZERO = verified zero. A `0` in a JSON payload is data; it must never be what an
absent or immature measurement looks like.
"""

from __future__ import annotations

import pytest
from plm_contracts import Tri, TriCoercionError, TriState, TriStateError
from pydantic import BaseModel, ValidationError


class _Cost(BaseModel):
    """A minimal holder so the tri-state can be exercised through pydantic (de)serialisation."""

    metric: Tri[float]


class TestStatesAreDistinct:
    def test_unknown_is_not_equal_to_verified_zero(self) -> None:
        assert Tri.unknown() != Tri.known(0)
        assert Tri.known(0) != Tri.unknown()

    def test_not_ready_is_not_equal_to_verified_zero(self) -> None:
        assert Tri.not_ready() != Tri.known(0)
        assert Tri.known(0) != Tri.not_ready()

    def test_unknown_is_not_equal_to_not_ready(self) -> None:
        assert Tri.unknown() != Tri.not_ready()
        assert Tri.not_ready() != Tri.unknown()

    def test_predicates_report_the_state_they_are_named_for(self) -> None:
        assert Tri.known(0).is_known and Tri.known(0).is_zero
        assert Tri.unknown().is_unknown and not Tri.unknown().is_known
        assert Tri.not_ready().is_not_ready and not Tri.not_ready().is_known
        assert Tri.zero().is_zero
        assert Tri.zero() == Tri.known(0.0)

    def test_known_non_zero_value_is_still_known(self) -> None:
        assert Tri.known(41.2).is_known
        assert not Tri.known(41.2).is_zero


class TestNoSilentCoercion:
    def test_unknown_does_not_compare_against_a_raw_number(self) -> None:
        with pytest.raises(TriStateError):
            _ = Tri.unknown() >= 0
        with pytest.raises(TriStateError):
            _ = 0 <= Tri.unknown()
        with pytest.raises(TriStateError):
            _ = Tri.not_ready() < 1

    def test_unknown_does_not_compare_against_a_known_value(self) -> None:
        with pytest.raises(TriStateError):
            _ = Tri.unknown() >= Tri.known(0)
        with pytest.raises(TriStateError):
            _ = Tri.not_ready() <= Tri.known(0)

    def test_known_values_compare_normally(self) -> None:
        assert Tri.known(2.0) >= Tri.known(1.0)
        assert Tri.known(1.0) < Tri.known(2.0)
        assert not Tri.known(1.0) > Tri.known(2.0)

    def test_boolean_coercion_is_refused(self) -> None:
        with pytest.raises(TriCoercionError):
            _ = bool(Tri.unknown())
        with pytest.raises(TriCoercionError):
            _ = bool(Tri.known(0.0))

    def test_numeric_coercion_is_refused(self) -> None:
        with pytest.raises(TriCoercionError):
            _ = float(Tri.known(3.0))
        with pytest.raises(TriCoercionError):
            _ = int(Tri.known(3.0))


class TestExplicitUnwrap:
    """A code path that requires a real number must go through a named unwrap."""

    @staticmethod
    def _require_number(metric: Tri[float]) -> float:
        return metric.unwrap()

    def test_unknown_cannot_be_consumed_as_a_number(self) -> None:
        with pytest.raises(TriStateError):
            self._require_number(Tri.unknown())

    def test_not_ready_cannot_be_consumed_as_a_number(self) -> None:
        with pytest.raises(TriStateError):
            self._require_number(Tri.not_ready())

    def test_verified_zero_is_a_real_number(self) -> None:
        assert self._require_number(Tri.known(0.0)) == 0.0
        assert self._require_number(Tri.known(41.2)) == 41.2

    def test_explicit_fallback_keeps_zero_separate_from_unknown(self) -> None:
        # unwrap_or is the deliberate, named escape hatch; the states stay distinguishable
        # everywhere else, including in the values that produced them.
        assert Tri.unknown().unwrap_or(0.0) == 0.0
        assert Tri.known(41.2).unwrap_or(0.0) == 41.2


class TestSerialisationRoundTrip:
    def test_serialising_then_deserialising_preserves_which_state_it_was(self) -> None:
        for original in (Tri.unknown(), Tri.not_ready(), Tri.known(0.0), Tri.known(41.2)):
            dumped = _Cost(metric=original).model_dump_json()
            revived = _Cost.model_validate_json(dumped).metric
            assert revived.state is original.state
            assert revived == original

    def test_the_three_states_serialise_differently(self) -> None:
        dumps = {
            _Cost(metric=Tri.unknown()).model_dump_json(),
            _Cost(metric=Tri.not_ready()).model_dump_json(),
            _Cost(metric=Tri.known(0.0)).model_dump_json(),
        }
        assert len(dumps) == 3

    def test_json_value_shape_is_tagged_and_distinguishable(self) -> None:
        assert Tri.unknown().to_json_value() == {"state": "unknown", "value": None}
        assert Tri.not_ready().to_json_value() == {"state": "not_ready", "value": None}
        assert Tri.known(0).to_json_value() == {"state": "known", "value": 0}

    def test_from_json_value_preserves_state(self) -> None:
        assert Tri.from_json_value({"state": "unknown"}) == Tri.unknown()
        assert Tri.from_json_value({"state": "not_ready", "value": None}) == Tri.not_ready()
        assert Tri.from_json_value({"state": "known", "value": 0}) == Tri.known(0)

    def test_state_tokens_are_the_spec_tokens(self) -> None:
        assert TriState.UNKNOWN.value == "unknown"
        assert TriState.NOT_READY.value == "not_ready"
        assert {s.value for s in TriState} == {"unknown", "not_ready", "known"}


class TestPayloadValidationRefusesCollapse:
    def test_raw_number_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _Cost(metric=0)

    def test_zero_smuggled_alongside_unknown_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _Cost(metric={"state": "unknown", "value": 0})

    def test_known_without_a_value_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _Cost(metric={"state": "known"})

    def test_unknown_payload_without_value_is_accepted(self) -> None:
        assert _Cost(metric={"state": "unknown"}).metric.is_unknown
        assert _Cost(metric={"state": "not_ready"}).metric.is_not_ready
