"""Money: explicit currency, decimal-only, reproducible point-in-time conversion."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from plm_contracts.money import (
    CurrencyMismatchError,
    FxRate,
    FxRateMissingError,
    FxTable,
    Money,
    money_sum,
)

T0 = datetime(2026, 10, 1, tzinfo=UTC)
T1 = datetime(2026, 10, 2, tzinfo=UTC)


def test_float_amounts_are_refused() -> None:
    with pytest.raises((TypeError, ValueError)):
        Money(amount=0.1, currency="USD")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Money.of(0.1, "USD")  # type: ignore[arg-type]


def test_decimal_arithmetic_is_exact() -> None:
    total = money_sum([Money.of("0.1", "USD")] * 3, currency="USD")
    assert total == Money.of("0.3", "USD")


def test_currencies_never_mix_implicitly() -> None:
    with pytest.raises(CurrencyMismatchError):
        _ = Money.of(1, "USD") + Money.of(1, "KRW")
    with pytest.raises(CurrencyMismatchError):
        money_sum([Money.of(1, "USD")], currency="KRW")


def test_presentation_rounds_to_minor_unit_half_even() -> None:
    assert Money.of("1234.5", "KRW").to_minor_units() == Decimal(1234)
    assert Money.of("1235.5", "KRW").to_minor_units() == Decimal(1236)
    assert Money.of("0.125", "USD").to_minor_units() == Decimal("0.12")


def test_conversion_uses_the_latest_rate_at_or_before_as_of() -> None:
    table = FxTable([
        FxRate(base="USD", quote="KRW", rate=Decimal(1300), source="ecb", as_of=T0),
        FxRate(base="USD", quote="KRW", rate=Decimal(1400), source="ecb", as_of=T1),
    ])
    early = table.convert(Money.of(2, "USD"), "KRW", as_of=T0)
    late = table.convert(Money.of(2, "USD"), "KRW", as_of=T1)
    assert early.converted == Money.of(2600, "KRW")
    assert late.converted == Money.of(2800, "KRW")
    assert early.rate is not None and early.rate.as_of == T0


def test_conversion_is_reproducible_from_the_same_table() -> None:
    rates = [FxRate(base="USD", quote="KRW", rate=Decimal("1333.33"), source="ecb", as_of=T0)]
    first = FxTable(rates).convert(Money.of("19.99", "USD"), "KRW", as_of=T1)
    second = FxTable(list(reversed(rates))).convert(Money.of("19.99", "USD"), "KRW", as_of=T1)
    assert first == second


def test_inverse_rate_is_applied_and_flagged() -> None:
    table = FxTable([FxRate(base="USD", quote="KRW", rate=Decimal(1250), source="ecb", as_of=T0)])
    converted = table.convert(Money.of(2500, "KRW"), "USD", as_of=T0)
    assert converted.converted == Money.of(2, "USD")
    assert converted.inverted is True


def test_missing_rate_raises_and_is_never_one() -> None:
    table = FxTable([FxRate(base="USD", quote="KRW", rate=Decimal(1300), source="ecb", as_of=T1)])
    with pytest.raises(FxRateMissingError):
        table.convert(Money.of(1, "USD"), "KRW", as_of=T0)
    with pytest.raises(FxRateMissingError):
        table.convert(Money.of(1, "EUR"), "KRW", as_of=T1)


def test_same_currency_needs_no_rate() -> None:
    converted = FxTable().convert(Money.of(5, "KRW"), "KRW", as_of=T0)
    assert converted.rate is None and converted.converted == Money.of(5, "KRW")


def test_conflicting_rates_at_one_instant_are_refused() -> None:
    table = FxTable([
        FxRate(base="USD", quote="KRW", rate=Decimal(1300), source="a", as_of=T0),
        FxRate(base="USD", quote="KRW", rate=Decimal(1301), source="b", as_of=T0),
    ])
    with pytest.raises(FxRateMissingError):
        table.convert(Money.of(1, "USD"), "KRW", as_of=T0)
