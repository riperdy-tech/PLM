"""Money with an explicit currency, decimal arithmetic and reproducible conversion.

Rules (AGENTS.md "Money and measurement"):

* Every amount carries its ISO 4217 currency. Adding or comparing two currencies is an
  error, never an implicit conversion.
* Amounts are `Decimal`. A `float` is refused at construction, because binary floats
  cannot represent most cent values and rounding drift compounds in a ledger.
* Ledger precision is `LEDGER_QUANTUM` (six decimal places, half-even). Rounding to a
  currency's minor unit happens only for presentation (`to_minor_units`).
* Conversion goes through an `FxTable` of recorded rates (rate, source, as_of). The rate
  used is the latest one at or before the requested instant, so a report recomputed
  later from the same table gives the same number. A missing rate raises; it is never
  assumed to be 1.
* The default reporting currency is KRW (P9 §23).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Annotated, Final, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from .envelopes import CurrencyCode

__all__ = [
    "DEFAULT_REPORTING_CURRENCY",
    "LEDGER_QUANTUM",
    "ConvertedMoney",
    "CurrencyCode",
    "CurrencyMismatchError",
    "FxRate",
    "FxRateMissingError",
    "FxTable",
    "Money",
    "money_sum",
]

DEFAULT_REPORTING_CURRENCY: Final[str] = "KRW"
LEDGER_QUANTUM: Final[Decimal] = Decimal("0.000001")

_MINOR_UNIT_EXPONENT: Final[dict[str, int]] = {"KRW": 0, "JPY": 0, "USD": 2, "EUR": 2, "GBP": 2}


class CurrencyMismatchError(ValueError):
    """Two amounts in different currencies were combined without a conversion."""


class FxRateMissingError(LookupError):
    """No recorded rate covers the requested pair at the requested instant."""


def _as_decimal(value: object) -> Decimal:
    if isinstance(value, float):
        raise TypeError("money amounts must be Decimal, int or a decimal string, not float")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int | str):
        try:
            number = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal amount: {value!r}") from exc
    else:
        raise TypeError(f"unsupported amount type {type(value).__name__}")
    if not number.is_finite():
        raise ValueError(f"money amount must be finite, got {number!r}")
    return number


class Money(BaseModel):
    """An amount in one currency. Immutable; arithmetic returns new values."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: Decimal
    currency: CurrencyCode

    @field_validator("amount", mode="before")
    @classmethod
    def _decimal_only(cls, value: object) -> Decimal:
        return _as_decimal(value).quantize(LEDGER_QUANTUM, rounding=ROUND_HALF_EVEN)

    @classmethod
    def of(cls, amount: Decimal | int | str, currency: str) -> Self:
        return cls(amount=_as_decimal(amount), currency=currency)

    @classmethod
    def zero(cls, currency: str) -> Self:
        return cls(amount=Decimal(0), currency=currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"cannot combine {self.currency} with {other.currency}; convert first"
            )

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __neg__(self) -> Money:
        return Money(amount=-self.amount, currency=self.currency)

    def scale(self, factor: Decimal | int) -> Money:
        return Money(amount=self.amount * _as_decimal(factor), currency=self.currency)

    def ratio_to(self, other: Money) -> Decimal | None:
        """``self / other`` in the same currency; ``None`` when ``other`` is zero."""
        self._same_currency(other)
        if other.amount == 0:
            return None
        return self.amount / other.amount

    def compare(self, other: Money) -> int:
        self._same_currency(other)
        return (self.amount > other.amount) - (self.amount < other.amount)

    def to_minor_units(self) -> Decimal:
        """The amount rounded half-even to the currency's minor unit, for presentation."""
        exponent = _MINOR_UNIT_EXPONENT.get(self.currency, 2)
        return self.amount.quantize(Decimal(1).scaleb(-exponent), rounding=ROUND_HALF_EVEN)


def money_sum(items: Iterable[Money], *, currency: str) -> Money:
    """Sum of amounts that must all be in ``currency``; the empty sum is zero."""
    total = Money.zero(currency)
    for item in items:
        total = total + item
    return total


class FxRate(BaseModel):
    """One recorded conversion rate: 1 ``base`` = ``rate`` ``quote`` as of ``as_of``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base: CurrencyCode
    quote: CurrencyCode
    rate: Decimal = Field(gt=0)
    source: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    as_of: AwareDatetime

    @field_validator("rate", mode="before")
    @classmethod
    def _decimal_rate(cls, value: object) -> Decimal:
        return _as_decimal(value)

    @property
    def rate_id(self) -> str:
        return f"{self.base}{self.quote}@{self.as_of.isoformat()}#{self.source}"


class ConvertedMoney(BaseModel):
    """A converted amount and the provenance of the rate that produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    original: Money
    converted: Money
    rate: FxRate | None
    """``None`` only when no conversion was needed (same currency)."""
    inverted: bool = False
    """``True`` when the recorded rate was quote→base and its inverse was applied."""


class FxTable:
    """Recorded rates; lookups are point-in-time and never invent a rate."""

    def __init__(self, rates: Sequence[FxRate] = ()) -> None:
        self._rates: tuple[FxRate, ...] = tuple(
            sorted(rates, key=lambda r: (r.base, r.quote, r.as_of, r.source))
        )

    @property
    def rates(self) -> tuple[FxRate, ...]:
        return self._rates

    def _latest(self, base: str, quote: str, as_of: datetime) -> FxRate | None:
        candidates = [
            r for r in self._rates if r.base == base and r.quote == quote and r.as_of <= as_of
        ]
        if not candidates:
            return None
        latest_instant = max(r.as_of for r in candidates)
        same_instant = [r for r in candidates if r.as_of == latest_instant]
        if len({r.rate for r in same_instant}) > 1:
            raise FxRateMissingError(
                f"conflicting {base}->{quote} rates recorded at {latest_instant.isoformat()}"
            )
        return same_instant[0]

    def convert(self, money: Money, to: str, *, as_of: datetime) -> ConvertedMoney:
        if money.currency == to:
            return ConvertedMoney(original=money, converted=money, rate=None)
        direct = self._latest(money.currency, to, as_of)
        if direct is not None:
            return ConvertedMoney(
                original=money,
                converted=Money(amount=money.amount * direct.rate, currency=to),
                rate=direct,
            )
        inverse = self._latest(to, money.currency, as_of)
        if inverse is not None:
            return ConvertedMoney(
                original=money,
                converted=Money(amount=money.amount / inverse.rate, currency=to),
                rate=inverse,
                inverted=True,
            )
        raise FxRateMissingError(
            f"no {money.currency}->{to} rate recorded at or before {as_of.isoformat()}"
        )
