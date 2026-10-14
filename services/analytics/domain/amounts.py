"""Parsing declared amounts at P9's ingestion boundary (ADR-0031).

An amount arrives as a decimal string (CSV cell, JSON string), an ``int`` or a
``Decimal``. It is parsed straight to ``Decimal``. A ``float`` is refused: by the time a
value is a binary float, the cents it claimed may already be gone. An empty value is
``None`` (not reported), never ``0``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

__all__ = ["AmountParseError", "parse_amount"]


class AmountParseError(ValueError):
    """A value that should be an amount is not a finite decimal (or is a float)."""


def parse_amount(value: object) -> Decimal | None:
    """``Decimal`` for a declared amount, ``None`` when the value is absent or blank."""
    if value is None:
        return None
    if isinstance(value, bool | float):
        raise AmountParseError(
            f"amounts must be decimal strings or integers, not {type(value).__name__} {value!r}"
        )
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise AmountParseError(f"not a decimal amount: {value!r}") from exc
    else:
        raise AmountParseError(f"unsupported amount type {type(value).__name__}")
    if not number.is_finite():
        raise AmountParseError(f"amount must be finite, got {value!r}")
    return number
