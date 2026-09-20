"""Tri-state metric semantics — the contract that keeps missing data out of arithmetic.

Review Focus 1 of the master plan: an absent metric, an unmeasured arm or a missing
rights field must never render as ``0`` or as a pass. Everything downstream of this
type depends on three states being distinguishable *after* serialisation, comparison
and validation:

============  =========================================================  ==========================
State         Meaning (P9 §58)                                            Construction path
============  =========================================================  ==========================
``known``     a real observed value, *including* a verified zero         ``Tri.known(0)`` / ``Tri.zero()``
``unknown``   absent data (P9 §58 ``NULL = unknown``)                    ``Tri.unknown()``
``not_ready`` expected later; the window is incomplete (P9 §58)          ``Tri.not_ready()``
============  =========================================================  ==========================

``zero`` deliberately is *not* a sentinel state: it is the known value ``0``. The
guarantees this type provides:

* ``Tri.unknown() != Tri.known(0)`` and ``Tri.not_ready() != Tri.known(0)`` — the
  states never compare equal to each other.
* Raw numbers, booleans and numeric casts are refused: use the explicit, named
  :meth:`Tri.unwrap` (raises :class:`TriStateError`) or :meth:`Tri.unwrap_or`.
* A boolean cannot arrive as a number: ``Tri[int]`` refuses
  ``{"state": "known", "value": true}`` instead of letting pydantic's lax mode mint a
  ``1``; a boolean remains valid where the declared payload type is ``bool``.
* JSON serialises to a tagged object (``{"state": ..., "value": ...}``) that no
  consumer can confuse with a bare number; ``{"state": "unknown", "value": 0}`` is
  rejected outright.
* Pydantic v2 integration is built in: a field annotated ``Tri[float]`` validates the
  tagged payload (and refuses raw numbers) and round-trips through
  ``model_dump_json`` / ``model_validate_json`` with its state intact.

Values carried by a ``known`` Tri should be JSON-native (numbers, strings, booleans,
``None`` is not allowed) so that ``model_dump_json`` can serialise them.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Final, final, get_args

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema
from pydantic_core.core_schema import ValidatorFunctionWrapHandler

__all__ = ["Tri", "TriCoercionError", "TriState", "TriStateError"]

_STATE_KEY: Final[str] = "state"
_VALUE_KEY: Final[str] = "value"
_ALLOWED_KEYS: Final[frozenset[str]] = frozenset({_STATE_KEY, _VALUE_KEY})


class TriState(StrEnum):
    """The three states a ``Tri`` can be in. Tokens match the master plan vocabulary."""

    KNOWN = "known"
    UNKNOWN = "unknown"
    NOT_READY = "not_ready"


class TriStateError(ValueError):
    """An operation required a known value and the ``Tri`` does not carry one.

    Also raised when a payload tries to smuggle a value alongside ``unknown`` /
    ``not_ready`` — the exact collapse the type exists to prevent.
    """


class TriCoercionError(TriStateError, TypeError):
    """A ``Tri`` was used as if it were a plain number, boolean or raw value."""


@final
class Tri[T]:
    """A metric value with three explicitly distinguishable states.

    Prefer the named constructors (:meth:`known`, :meth:`zero`, :meth:`unknown`,
    :meth:`not_ready`); the constructor validates state/value consistency and raises
    :class:`TriCoercionError` on any attempt to mix the two.
    """

    __slots__ = ("_state", "_value")

    def __init__(self, state: TriState, value: T | None = None) -> None:
        if state is TriState.KNOWN:
            if value is None:
                raise TriCoercionError(
                    "a known Tri must carry a non-None value; use Tri.unknown() or "
                    "Tri.not_ready() for absent data"
                )
        elif value is not None:
            raise TriCoercionError(
                f"a {state.value} Tri cannot carry a value (got {value!r}); "
                "mixing the two states is exactly what Tri prevents"
            )
        self._state = state
        self._value = value

    # -- Construction paths --------------------------------------------------------

    @classmethod
    def known(cls, value: T) -> Tri[T]:
        """A real, measured value. ``0`` is data: use this (or :meth:`zero`) for it."""
        return cls(TriState.KNOWN, value)

    @staticmethod
    def zero() -> Tri[float]:
        """An explicit, verified zero — sugar for ``Tri.known(0.0)``. Never a placeholder."""
        return Tri(TriState.KNOWN, 0.0)

    @classmethod
    def unknown(cls) -> Tri[Any]:
        """Absent data: nothing was measured (P9 §58 ``NULL = unknown``)."""
        return cls(TriState.UNKNOWN)

    @classmethod
    def not_ready(cls) -> Tri[Any]:
        """Expected later: the observation window is incomplete (P9 §58 ``NOT_READY``)."""
        return cls(TriState.NOT_READY)

    @classmethod
    def from_json_value(cls, raw: object) -> Tri[Any]:
        """Parse the tagged JSON form, e.g. ``{"state": "known", "value": 1.5}``.

        Refuses raw values and refuses ``{"state": "unknown", "value": 0}`` — wrap real
        numbers explicitly or the collapse is rejected at the boundary.
        """
        if isinstance(raw, Tri):
            return raw
        state, raw_value, has_value = _parse_payload(raw)
        if state is TriState.KNOWN:
            if not has_value or raw_value is None:
                raise TriStateError("a 'known' Tri payload must carry a non-None value")
            return Tri.known(raw_value)
        return Tri.unknown() if state is TriState.UNKNOWN else Tri.not_ready()

    # -- State inspection ----------------------------------------------------------

    @property
    def state(self) -> TriState:
        return self._state

    @property
    def is_known(self) -> bool:
        return self._state is TriState.KNOWN

    @property
    def is_unknown(self) -> bool:
        return self._state is TriState.UNKNOWN

    @property
    def is_not_ready(self) -> bool:
        return self._state is TriState.NOT_READY

    @property
    def is_zero(self) -> bool:
        """True only for a *verified* zero; ``unknown`` and ``not_ready`` are never zero."""
        return self._state is TriState.KNOWN and self._value == 0

    # -- Explicit consumption ------------------------------------------------------

    def unwrap(self) -> T:
        """The explicit, named unwrap. Raises :class:`TriStateError` unless known."""
        if self._state is TriState.NOT_READY:
            raise TriStateError(
                "cannot unwrap a not_ready Tri: the measurement window is incomplete or "
                "the metric is expected later; handle not_ready explicitly, or use "
                "unwrap_or() only where collapsing the state is deliberate"
            )
        if self._state is TriState.UNKNOWN:
            raise TriStateError(
                "cannot unwrap an unknown Tri: nothing was measured; an unknown value is "
                "not zero and not a pass; use unwrap_or() only where collapsing the "
                "state is deliberate"
            )
        value = self._value
        if value is None:  # unreachable by construction; keeps the type checker honest
            raise TriStateError("a known Tri without a value is corrupt")
        return value

    def unwrap_or(self, default: T) -> T:
        """Deliberate fallback for display paths; the Tri itself becomes indistinguishable."""
        if self._state is TriState.KNOWN and self._value is not None:
            return self._value
        return default

    # -- Serialisation -------------------------------------------------------------

    def to_json_value(self) -> dict[str, Any]:
        """The tagged JSON form: a JSON consumer can always tell the states apart."""
        return {_STATE_KEY: self._state.value, _VALUE_KEY: self._value}

    # -- Refusals ------------------------------------------------------------------

    def _comparable(self, other: object) -> tuple[Any, Any]:
        if not isinstance(other, Tri):
            raise TriCoercionError(
                f"cannot compare {self!r} with the raw value {other!r}: unwrap() "
                "explicitly, or compare two Tri values"
            )
        if self._state is not TriState.KNOWN or other._state is not TriState.KNOWN:
            raise TriStateError(
                f"cannot compare {self!r} with {other!r}: comparison needs a known value "
                "on both sides"
            )
        return (self._value, other._value)

    def __lt__(self, other: object) -> bool:
        left, right = self._comparable(other)
        return bool(left < right)

    def __le__(self, other: object) -> bool:
        left, right = self._comparable(other)
        return bool(left <= right)

    def __gt__(self, other: object) -> bool:
        left, right = self._comparable(other)
        return bool(left > right)

    def __ge__(self, other: object) -> bool:
        left, right = self._comparable(other)
        return bool(left >= right)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Tri):
            return NotImplemented
        if self._state is not other._state:
            return False
        if self._state is not TriState.KNOWN:
            return True
        return bool(self._value == other._value)

    def __hash__(self) -> int:
        return hash((self._state, self._value))

    def __bool__(self) -> bool:
        raise TriCoercionError(
            f"a Tri does not coerce to bool ({self!r}): use is_known / is_unknown / "
            "is_not_ready, or unwrap() explicitly"
        )

    def __float__(self) -> float:
        raise TriCoercionError(
            f"a Tri does not coerce to float ({self!r}): call unwrap() explicitly"
        )

    def __int__(self) -> int:
        raise TriCoercionError(
            f"a Tri does not coerce to int ({self!r}): call unwrap() explicitly"
        )

    def __repr__(self) -> str:
        if self._state is TriState.KNOWN:
            return f"Tri.known({self._value!r})"
        if self._state is TriState.UNKNOWN:
            return "Tri.unknown()"
        return "Tri.not_ready()"

    # -- Pydantic v2 integration ---------------------------------------------------

    @classmethod
    def __get_pydantic_core_schema__(
        cls,
        source_type: Any,
        handler: GetCoreSchemaHandler,
    ) -> CoreSchema:
        args = get_args(source_type)
        inner_schema = handler.generate_schema(args[0]) if args else core_schema.any_schema()
        return core_schema.no_info_wrap_validator_function(
            cls._validate_payload,
            inner_schema,
            serialization=core_schema.plain_serializer_function_ser_schema(
                cls._serialize_tri, when_used="always"
            ),
        )

    @staticmethod
    def _validate_payload(value: object, handler: ValidatorFunctionWrapHandler) -> Tri[Any]:
        if isinstance(value, Tri):
            if value.state is TriState.KNOWN:
                inner = handler(value._value)
                _refuse_bool_coercion(value._value, inner)
                if inner is None:
                    raise TriStateError("a known Tri must carry a non-None value")
                return Tri.known(inner)
            return value
        state, raw_value, has_value = _parse_payload(value)
        if state is TriState.KNOWN:
            if not has_value:
                raise TriStateError("a 'known' Tri payload must carry a 'value' key")
            inner = handler(raw_value)
            _refuse_bool_coercion(raw_value, inner)
            if inner is None:
                raise TriStateError("a 'known' Tri must carry a non-None value")
            return Tri.known(inner)
        return Tri.unknown() if state is TriState.UNKNOWN else Tri.not_ready()

    @staticmethod
    def _serialize_tri(value: Tri[Any]) -> dict[str, Any]:
        return value.to_json_value()


def _refuse_bool_coercion(raw: object, validated: object) -> None:
    """Refuse pydantic's lax bool-to-number coercion inside a ``known`` payload.

    ``Tri[int]`` fed ``{"state": "known", "value": true}`` would otherwise validate to
    ``1``: ``bool`` is a subclass of ``int`` in Python and pydantic's lax mode exploits
    that, minting a measured value out of a non-numeric one. A boolean stays valid
    exactly where the declared payload type is ``bool``.
    """
    if isinstance(raw, bool) and not isinstance(validated, bool):
        raise TriCoercionError(
            "a known Tri payload cannot carry a boolean where a number is expected: "
            f"{raw!r} would be silently coerced to {validated!r}"
        )


def _parse_payload(raw: object) -> tuple[TriState, object, bool]:
    """Validate the tagged JSON shape; returns (state, value, value-present)."""
    if not isinstance(raw, Mapping):
        raise TriCoercionError(
            f"raw value {raw!r} is not a Tri payload: wrap it as "
            "{'state': 'known', 'value': ...}, {'state': 'unknown'} or "
            "{'state': 'not_ready'}; this is what stops absent data arriving as a number"
        )
    unexpected = set(raw.keys()) - _ALLOWED_KEYS
    if unexpected:
        raise TriStateError(
            f"unexpected keys {sorted(unexpected)!r} in a Tri payload: allowed keys are "
            f"{sorted(_ALLOWED_KEYS)!r}"
        )
    raw_state = raw.get(_STATE_KEY)
    if raw_state is None:
        raise TriStateError("Tri payload is missing the 'state' key")
    try:
        state = TriState(raw_state)
    except ValueError as exc:
        allowed = [member.value for member in TriState]
        raise TriStateError(
            f"invalid Tri state {raw_state!r}: expected one of {allowed!r}"
        ) from exc
    has_value = _VALUE_KEY in raw
    raw_value = raw.get(_VALUE_KEY)
    if state is not TriState.KNOWN and raw_value is not None:
        raise TriStateError(
            f"state {state.value!r} must not carry a value (got {raw_value!r}): "
            "treating that value as the state is exactly the collapse Tri prevents"
        )
    return (state, raw_value, has_value)
