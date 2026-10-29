"""The persisted cost ledger and budget ceiling (R3; ADR-0033 ``ops`` schema).

Every live external write is funded before it happens. The
:class:`~plm_platform.execution.ExternalWriteGuard` asks its budget gate to *reserve* the
write's cost; :class:`PersistentBudgetGate` answers from this ledger. After the provider
call the engine settles the reservation: ``COMMITTED`` with the actual cost when the
write happened, ``RELEASED`` when it did not.

State lives in three platform-owned tables (migration ``0027_ops_cost_ledger``):

``ops.budget_ceilings``
    A spending limit for one scope over one half-open period ``[period_start,
    period_end)``, in an explicit currency. The scope is :data:`GLOBAL_SCOPE` (the
    program ceiling, which every reservation needs) or ``capability:<name>`` (an
    optional, tighter limit for one capability). Periods of one scope never overlap.
``ops.budget_rates``
    The operator's rate card: what one write of a capability costs, effective from an
    instant. A caller that cannot price its own write (P7, P8 today) is priced from
    here. A capability with no rate has an *unknown* cost, never a zero one.
``ops.budget_entries``
    One row per (capability, idempotency key): ``RESERVED`` -> ``COMMITTED`` |
    ``RELEASED``. Each entry is bound to the ceiling(s) it was charged against, so the
    spend of a ceiling is the sum of its own entries; no overlapping windows are summed.

Rules:

* **Fail closed.** A reservation is refused when there is no ceiling covering ``now``,
  when the ceiling is exhausted, when the cost is unknown, negative or currency-less,
  when its currency differs from the ledger's and the injected FX table has no rate,
  and when a ceiling is stored in a currency other than the ledger's.
* **Idempotent.** A reservation replayed with the same (capability, key) returns the
  original verdict and never reserves twice. Only a ``RELEASED`` entry (the write did
  not happen) is re-reserved, against current headroom. A refusal is not stored as a
  verdict: a replay after the operator raises the ceiling is evaluated afresh.
* **Serialised.** Reservation, commit and release lock the ceiling rows they charge
  (``SELECT ... FOR UPDATE`` on PostgreSQL, ``BEGIN IMMEDIATE`` on SQLite), so two
  reservations racing for the last headroom cannot both pass.
* **Money.** Amounts are :class:`~plm_contracts.money.Money` (``Decimal`` + currency).
  They are stored as ``Numeric(20, 6)`` on PostgreSQL and as exact decimal strings on
  SQLite, whose numeric type is a binary float. Arithmetic happens in Python.
* **Time.** Nothing here reads the clock: callers pass ``now`` (the gate takes a
  :class:`~plm_platform.clock.Clock`).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import Any, Final

import sqlalchemy as sa
from plm_contracts.money import (
    LEDGER_QUANTUM,
    CurrencyMismatchError,
    FxRateMissingError,
    FxTable,
    Money,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.types import TypeDecorator

from .clock import Clock, require_aware
from .db import PLATFORM_SCHEMA
from .execution import BudgetGate, BudgetVerdict, CostEstimate, ExecutionSettings, NoBudgetLedger

__all__ = [
    "GLOBAL_SCOPE",
    "LEDGER_METADATA",
    "BudgetLedger",
    "Ceiling",
    "CeilingConflictError",
    "CeilingStatus",
    "CostRate",
    "EntryStatus",
    "LedgerEntry",
    "LedgerEntryNotFoundError",
    "LedgerError",
    "LedgerStateError",
    "PersistentBudgetGate",
    "Reason",
    "ReservationResult",
    "budget_ceilings",
    "budget_entries",
    "budget_gate_for",
    "budget_rates",
    "capability_scope",
]

logger = logging.getLogger(__name__)

GLOBAL_SCOPE: Final[str] = "global"
"""The program-wide ceiling. Every reservation is charged against it."""

_CAPABILITY_SCOPE_PREFIX: Final[str] = "capability:"


def capability_scope(capability: str) -> str:
    """The scope of an optional per-capability ceiling: ``capability:youtube.upload``."""
    if not capability.strip():
        raise ValueError("capability must be non-empty")
    return f"{_CAPABILITY_SCOPE_PREFIX}{capability}"


class EntryStatus(StrEnum):
    RESERVED = "reserved"
    """Headroom is held; the write may be in flight or its outcome is not yet known."""
    COMMITTED = "committed"
    """The write happened; ``actual`` is the recorded spend."""
    RELEASED = "released"
    """The write did not happen; the entry holds no headroom."""


class Reason(StrEnum):
    """Reason codes a reservation answers with (``BudgetVerdict.reason``)."""

    RESERVED = "reserved"
    NO_CEILING = "no_budget_ceiling"
    AMBIGUOUS_CEILING = "ambiguous_budget_ceiling"
    EXHAUSTED = "budget_exhausted"
    CAPABILITY_EXHAUSTED = "capability_budget_exhausted"
    CEILING_CURRENCY_MISMATCH = "ceiling_currency_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    COST_UNKNOWN = "cost_estimate_unknown"
    COST_CURRENCY_UNSPECIFIED = "cost_currency_unspecified"
    COST_NEGATIVE = "cost_estimate_negative"
    LEDGER_ERROR = "budget_ledger_error"


class LedgerError(RuntimeError):
    """A ledger operation was refused."""


class LedgerEntryNotFoundError(LedgerError, LookupError):
    """No reservation exists for the (capability, idempotency key)."""


class LedgerStateError(LedgerError):
    """An illegal transition, e.g. committing a released write or a conflicting commit."""


class CeilingConflictError(LedgerError):
    """A ceiling period would overlap another ceiling of the same scope."""


# --- storage ------------------------------------------------------------------------------


class _LedgerAmount(TypeDecorator[Decimal]):
    """An exact ledger amount: ``Numeric(20, 6)``, or a decimal string on SQLite.

    SQLite's NUMERIC affinity stores a binary float, which cannot hold most cent values;
    a string round-trips exactly. No SQL arithmetic is done on amounts.
    """

    impl = sa.Numeric(20, 6, asdecimal=True)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> sa.types.TypeEngine[Any]:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(sa.String(40))
        return dialect.type_descriptor(sa.Numeric(20, 6, asdecimal=True))

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        quantized = Decimal(value).quantize(LEDGER_QUANTUM, rounding=ROUND_HALF_EVEN)
        return str(quantized) if dialect.name == "sqlite" else quantized

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value)).quantize(LEDGER_QUANTUM, rounding=ROUND_HALF_EVEN)


LEDGER_METADATA: Final = sa.MetaData(schema=PLATFORM_SCHEMA)

budget_ceilings = sa.Table(
    "budget_ceilings", LEDGER_METADATA,
    sa.Column("ceiling_id", sa.String(200), primary_key=True),
    sa.Column("scope", sa.String(160), nullable=False),
    sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
    sa.Column("amount", _LedgerAmount(), nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("set_by", sa.String(128), nullable=False),
    sa.Column("set_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("reference", sa.String(256), nullable=True),
    sa.UniqueConstraint("scope", "period_start", name="uq_budget_ceilings_scope_period"),
)

budget_rates = sa.Table(
    "budget_rates", LEDGER_METADATA,
    sa.Column("capability", sa.String(128), primary_key=True),
    sa.Column("effective_from", sa.DateTime(timezone=True), primary_key=True),
    sa.Column("amount", _LedgerAmount(), nullable=False),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("set_by", sa.String(128), nullable=False),
    sa.Column("set_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("reference", sa.String(256), nullable=True),
)

budget_entries = sa.Table(
    "budget_entries", LEDGER_METADATA,
    sa.Column("entry_id", sa.String(64), primary_key=True),
    sa.Column("capability", sa.String(128), nullable=False),
    sa.Column("idempotency_key", sa.String(256), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column(
        "ceiling_id", sa.String(200), sa.ForeignKey(budget_ceilings.c.ceiling_id), nullable=False
    ),
    sa.Column(
        "capability_ceiling_id", sa.String(200),
        sa.ForeignKey(budget_ceilings.c.ceiling_id), nullable=True,
    ),
    sa.Column("currency", sa.String(3), nullable=False),
    sa.Column("reserved_amount", _LedgerAmount(), nullable=False),
    sa.Column("estimate_source", sa.String(16), nullable=False),
    sa.Column("original_amount", _LedgerAmount(), nullable=False),
    sa.Column("original_currency", sa.String(3), nullable=False),
    sa.Column("fx_rate_id", sa.String(200), nullable=True),
    sa.Column("actual_amount", _LedgerAmount(), nullable=True),
    sa.Column("actual_basis", sa.String(32), nullable=True),
    sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("settle_reason", sa.String(128), nullable=True),
    sa.UniqueConstraint("capability", "idempotency_key", name="uq_budget_entries_capability_key"),
)
sa.Index("ix_budget_entries_ceiling_id", budget_entries.c.ceiling_id)
sa.Index("ix_budget_entries_capability_ceiling_id", budget_entries.c.capability_ceiling_id)
sa.Index("ix_budget_entries_status", budget_entries.c.status)

ESTIMATE_FROM_CALLER: Final[str] = "caller"
ESTIMATE_FROM_RATE_CARD: Final[str] = "rate_card"
ACTUAL_PROVIDER_REPORTED: Final[str] = "provider_reported"
ACTUAL_RESERVED_ESTIMATE: Final[str] = "reserved_estimate"
"""``actual_basis`` when the provider reported no cost: the reserved estimate is recorded
as the spend, and labelled as such."""


# --- records -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Ceiling:
    ceiling_id: str
    scope: str
    period_start: datetime
    period_end: datetime
    limit: Money
    set_by: str
    set_at: datetime
    reference: str | None = None

    def covers(self, now: datetime) -> bool:
        return self.period_start <= now < self.period_end


@dataclass(frozen=True, slots=True)
class CostRate:
    capability: str
    effective_from: datetime
    cost: Money
    set_by: str
    set_at: datetime
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    entry_id: str
    capability: str
    idempotency_key: str
    status: EntryStatus
    ceiling_id: str
    capability_ceiling_id: str | None
    reserved: Money
    estimate_source: str
    original: Money
    """The estimate as offered, before any FX conversion into the ledger currency."""
    fx_rate_id: str | None
    actual: Money | None
    actual_basis: str | None
    reserved_at: datetime
    settled_at: datetime | None
    settle_reason: str | None

    @property
    def charged(self) -> Money:
        """What this entry holds against its ceilings."""
        if self.status is EntryStatus.RESERVED:
            return self.reserved
        if self.status is EntryStatus.COMMITTED and self.actual is not None:
            return self.actual
        return Money.zero(self.reserved.currency)


@dataclass(frozen=True, slots=True)
class ReservationResult:
    allowed: bool
    reason: Reason
    entry: LedgerEntry | None = None
    replayed: bool = False
    remaining: Money | None = None
    """Global headroom after this reservation (``None`` when no ceiling was reached)."""

    def verdict(self) -> BudgetVerdict:
        return BudgetVerdict(allowed=self.allowed, reason=self.reason.value,
                             replayed=self.replayed)


@dataclass(frozen=True, slots=True)
class CeilingStatus:
    ceiling: Ceiling
    reserved: Money
    """Held by ``RESERVED`` entries (outcome not yet recorded)."""
    committed: Money
    """Recorded spend of ``COMMITTED`` entries."""

    @property
    def remaining(self) -> Money:
        return self.ceiling.limit - self.reserved - self.committed


def _entry_id(capability: str, idempotency_key: str) -> str:
    material = f"{capability}\x1f{idempotency_key}".encode()
    return hashlib.sha256(material).hexdigest()[:32]


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; every stored instant is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _ceiling(row: Any) -> Ceiling:
    return Ceiling(
        ceiling_id=row.ceiling_id, scope=row.scope,
        period_start=_aware(row.period_start), period_end=_aware(row.period_end),
        limit=Money(amount=row.amount, currency=row.currency),
        set_by=row.set_by, set_at=_aware(row.set_at), reference=row.reference,
    )


def _rate(row: Any) -> CostRate:
    return CostRate(
        capability=row.capability, effective_from=_aware(row.effective_from),
        cost=Money(amount=row.amount, currency=row.currency),
        set_by=row.set_by, set_at=_aware(row.set_at), reference=row.reference,
    )


def _entry(row: Any) -> LedgerEntry:
    return LedgerEntry(
        entry_id=row.entry_id, capability=row.capability, idempotency_key=row.idempotency_key,
        status=EntryStatus(row.status), ceiling_id=row.ceiling_id,
        capability_ceiling_id=row.capability_ceiling_id,
        reserved=Money(amount=row.reserved_amount, currency=row.currency),
        estimate_source=row.estimate_source,
        original=Money(amount=row.original_amount, currency=row.original_currency),
        fx_rate_id=row.fx_rate_id,
        actual=(None if row.actual_amount is None
                else Money(amount=row.actual_amount, currency=row.currency)),
        actual_basis=row.actual_basis,
        reserved_at=_aware(row.reserved_at),
        settled_at=None if row.settled_at is None else _aware(row.settled_at),
        settle_reason=row.settle_reason,
    )


# --- the ledger --------------------------------------------------------------------------


class BudgetLedger:
    """Ceilings, rate card and reservations in ``ops``; one transaction per operation.

    ``currency`` is the ledger currency: every ceiling is denominated in it and every
    reservation is converted into it, only through ``fx`` (recorded rates; a missing
    rate refuses the reservation). Phases reach the ledger only through this API
    (ADR-0033); they never write its tables.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        currency: str,
        fx: FxTable | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._currency = Money.zero(currency).currency
        self._fx = fx

    @property
    def currency(self) -> str:
        return self._currency

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        """A session whose transaction is serialised against other ledger writers.

        SQLite has no row locks: ``BEGIN IMMEDIATE`` takes the database write lock up
        front, so a second writer waits (busy timeout) instead of reading stale headroom.
        PostgreSQL locks the ceiling rows with ``SELECT ... FOR UPDATE`` (:meth:`_lock`).
        """
        with self._session_factory() as session:
            if session.get_bind().dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield session
            except BaseException:
                session.rollback()
                raise

    @staticmethod
    def _lock(session: Session, ceiling_ids: Sequence[str]) -> list[Ceiling]:
        """Lock and re-read ceilings in the given (always global-first) order."""
        locked: list[Ceiling] = []
        for ceiling_id in ceiling_ids:
            row = session.execute(
                sa.select(budget_ceilings)
                .where(budget_ceilings.c.ceiling_id == ceiling_id)
                .with_for_update()
            ).one()
            locked.append(_ceiling(row))
        return locked

    # -- operator surface ----------------------------------------------------------------

    def set_ceiling(
        self,
        *,
        limit: Money,
        period_start: datetime,
        period_end: datetime,
        set_by: str,
        now: datetime,
        scope: str = GLOBAL_SCOPE,
        reference: str | None = None,
    ) -> Ceiling:
        """Create, or change the amount of, the ceiling for ``scope`` starting at
        ``period_start``. A period overlapping another ceiling of the scope is refused."""
        start, end, now = require_aware(period_start), require_aware(period_end), require_aware(now)
        if end <= start:
            raise ValueError("period_end must be after period_start")
        if limit.amount < 0:
            raise ValueError("a ceiling cannot be negative")
        if limit.currency != self._currency:
            raise CurrencyMismatchError(
                f"ceiling in {limit.currency}; the ledger currency is {self._currency}"
            )
        if not set_by.strip():
            raise ValueError("set_by must name the operator")
        if scope != GLOBAL_SCOPE and not scope.startswith(_CAPABILITY_SCOPE_PREFIX):
            raise ValueError(f"unknown ceiling scope {scope!r}")
        ceiling_id = f"{scope}@{start.isoformat()}"
        with self._transaction() as session:
            for other in self._ceilings_of(session, scope):
                if other.ceiling_id != ceiling_id and other.period_start < end and start < other.period_end:
                    raise CeilingConflictError(
                        f"{scope} period [{start.isoformat()}, {end.isoformat()}) overlaps "
                        f"{other.ceiling_id} ending {other.period_end.isoformat()}"
                    )
            values = {
                "scope": scope, "period_start": start, "period_end": end,
                "amount": limit.amount, "currency": limit.currency, "set_by": set_by,
                "set_at": now, "reference": reference,
            }
            exists = session.execute(
                sa.select(budget_ceilings.c.ceiling_id)
                .where(budget_ceilings.c.ceiling_id == ceiling_id)
                .with_for_update()
            ).first()
            if exists is None:
                session.execute(budget_ceilings.insert().values(ceiling_id=ceiling_id, **values))
            else:
                session.execute(
                    budget_ceilings.update()
                    .where(budget_ceilings.c.ceiling_id == ceiling_id)
                    .values(**values)
                )
            session.commit()
        return Ceiling(ceiling_id, scope, start, end, limit, set_by, now, reference)

    def set_rate(
        self,
        *,
        capability: str,
        cost: Money,
        effective_from: datetime,
        set_by: str,
        now: datetime,
        reference: str | None = None,
    ) -> CostRate:
        """Record what one write of ``capability`` costs from ``effective_from`` on.

        A rate of zero is a stated fact ("this write costs nothing"), distinct from no
        rate at all, which leaves the cost unknown and the write unfunded.
        """
        start, now = require_aware(effective_from), require_aware(now)
        if not capability.strip() or not set_by.strip():
            raise ValueError("capability and set_by must be non-empty")
        if cost.amount < 0:
            raise ValueError("a rate cannot be negative")
        values = {"amount": cost.amount, "currency": cost.currency, "set_by": set_by,
                  "set_at": now, "reference": reference}
        key = (budget_rates.c.capability == capability) & (budget_rates.c.effective_from == start)
        with self._transaction() as session:
            if session.execute(sa.select(budget_rates.c.capability).where(key)).first() is None:
                session.execute(
                    budget_rates.insert().values(capability=capability, effective_from=start, **values)
                )
            else:
                session.execute(budget_rates.update().where(key).values(**values))
            session.commit()
        return CostRate(capability, start, cost, set_by, now, reference)

    # -- reservation -----------------------------------------------------------------------

    def reserve(
        self,
        capability: str,
        idempotency_key: str,
        *,
        cost: Money | None,
        now: datetime,
    ) -> ReservationResult:
        """Reserve the cost of one write against the ceilings covering ``now``.

        ``cost=None`` means the caller cannot price the write: the rate card prices it,
        or the reservation is refused as ``cost_estimate_unknown``.
        """
        now = require_aware(now)
        if not capability.strip() or not idempotency_key.strip():
            raise ValueError("capability and idempotency_key must be non-empty")
        try:
            with self._transaction() as session:
                result = self._reserve(session, capability, idempotency_key, cost, now)
                if result.allowed and not result.replayed:
                    session.commit()
                else:
                    session.rollback()
                return result
        except IntegrityError:
            # A concurrent writer inserted the same key between our check and insert
            # (possible only when the two did not share a ceiling lock): replay it.
            existing = self.entry(capability, idempotency_key)
            if existing is not None and existing.status is not EntryStatus.RELEASED:
                return ReservationResult(True, Reason.RESERVED, existing, replayed=True)
            raise

    def _reserve(
        self,
        session: Session,
        capability: str,
        key: str,
        cost: Money | None,
        now: datetime,
    ) -> ReservationResult:
        existing = self._entry_row(session, capability, key)
        if existing is not None and existing.status is not EntryStatus.RELEASED:
            return ReservationResult(True, Reason.RESERVED, existing, replayed=True)

        source = ESTIMATE_FROM_CALLER
        if cost is None:
            rate = self._rate_at(session, capability, now)
            if rate is None:
                return ReservationResult(False, Reason.COST_UNKNOWN)
            cost, source = rate.cost, ESTIMATE_FROM_RATE_CARD
        if cost.amount < 0:
            return ReservationResult(False, Reason.COST_NEGATIVE)
        fx_rate_id: str | None = None
        amount = cost
        if cost.currency != self._currency:
            if self._fx is None:
                return ReservationResult(False, Reason.CURRENCY_MISMATCH)
            try:
                converted = self._fx.convert(cost, self._currency, as_of=now)
            except FxRateMissingError:
                return ReservationResult(False, Reason.CURRENCY_MISMATCH)
            amount = converted.converted
            fx_rate_id = None if converted.rate is None else converted.rate.rate_id

        global_ceilings = [c for c in self._ceilings_of(session, GLOBAL_SCOPE) if c.covers(now)]
        if not global_ceilings:
            return ReservationResult(False, Reason.NO_CEILING)
        cap_ceilings = [
            c for c in self._ceilings_of(session, capability_scope(capability)) if c.covers(now)
        ]
        if len(global_ceilings) > 1 or len(cap_ceilings) > 1:
            return ReservationResult(False, Reason.AMBIGUOUS_CEILING)

        lock_ids = [global_ceilings[0].ceiling_id, *(c.ceiling_id for c in cap_ceilings)]
        locked = self._lock(session, lock_ids)
        if any(c.limit.currency != self._currency for c in locked):
            return ReservationResult(False, Reason.CEILING_CURRENCY_MISMATCH)
        global_ceiling, cap_ceiling = locked[0], (locked[1] if len(locked) > 1 else None)

        # Re-read under the lock: a racing writer of the same key has committed by now.
        existing = self._entry_row(session, capability, key)
        if existing is not None and existing.status is not EntryStatus.RELEASED:
            return ReservationResult(True, Reason.RESERVED, existing, replayed=True)

        remaining = self._status(session, global_ceiling).remaining
        if amount.compare(remaining) > 0:
            return ReservationResult(False, Reason.EXHAUSTED, remaining=remaining)
        if cap_ceiling is not None and amount.compare(self._status(session, cap_ceiling).remaining) > 0:
            return ReservationResult(False, Reason.CAPABILITY_EXHAUSTED, remaining=remaining)

        values = {
            "status": EntryStatus.RESERVED.value,
            "ceiling_id": global_ceiling.ceiling_id,
            "capability_ceiling_id": None if cap_ceiling is None else cap_ceiling.ceiling_id,
            "currency": amount.currency,
            "reserved_amount": amount.amount,
            "estimate_source": source,
            "original_amount": cost.amount,
            "original_currency": cost.currency,
            "fx_rate_id": fx_rate_id,
            "actual_amount": None,
            "actual_basis": None,
            "reserved_at": now,
            "settled_at": None,
            "settle_reason": None,
        }
        entry_id = _entry_id(capability, key)
        if existing is None:
            session.execute(
                budget_entries.insert().values(
                    entry_id=entry_id, capability=capability, idempotency_key=key, **values
                )
            )
        else:  # RELEASED: the write never happened; fund it again from current headroom
            session.execute(
                budget_entries.update().where(budget_entries.c.entry_id == existing.entry_id).values(**values)
            )
        entry = self._entry_row(session, capability, key)
        return ReservationResult(True, Reason.RESERVED, entry, remaining=remaining - amount)

    # -- settlement --------------------------------------------------------------------------

    def commit(
        self,
        capability: str,
        idempotency_key: str,
        *,
        actual: Money | None,
        now: datetime,
    ) -> LedgerEntry:
        """Record that the write happened, with its actual cost.

        ``actual=None`` means the provider reported no cost: the reserved estimate is
        recorded as the spend with ``actual_basis = "reserved_estimate"``. An actual above
        the estimate is recorded as it is, even past the ceiling; the next reservation
        then sees the overrun. Committing again with the same actual is a no-op.
        """
        now = require_aware(now)
        with self._transaction() as session:
            entry = self._settleable(session, capability, idempotency_key)
            if actual is None:
                spend, basis = entry.reserved, ACTUAL_RESERVED_ESTIMATE
            else:
                spend, basis = self._to_ledger(actual, now), ACTUAL_PROVIDER_REPORTED
            if spend.amount < 0:
                raise ValueError("an actual cost cannot be negative")
            if entry.status is EntryStatus.COMMITTED:
                if entry.actual == spend:
                    return entry
                raise LedgerStateError(
                    f"{capability}/{idempotency_key} already committed at {entry.actual}; "
                    f"refusing a different actual {spend}"
                )
            if entry.status is EntryStatus.RELEASED:
                raise LedgerStateError(
                    f"{capability}/{idempotency_key} was released; a released write cannot be committed"
                )
            session.execute(
                budget_entries.update()
                .where(budget_entries.c.entry_id == entry.entry_id)
                .values(status=EntryStatus.COMMITTED.value, actual_amount=spend.amount,
                        actual_basis=basis, settled_at=now, settle_reason="write_performed")
            )
            updated = self._entry_row(session, capability, idempotency_key)
            session.commit()
        assert updated is not None
        return updated

    def release(
        self,
        capability: str,
        idempotency_key: str,
        *,
        reason: str,
        now: datetime,
    ) -> LedgerEntry:
        """Record that the write did not happen; its headroom is freed.

        Only a reservation is released. A committed write has spent its money: releasing
        it is refused. Releasing twice is a no-op.
        """
        now = require_aware(now)
        if not reason.strip():
            raise ValueError("a release needs a reason")
        with self._transaction() as session:
            entry = self._settleable(session, capability, idempotency_key)
            if entry.status is EntryStatus.RELEASED:
                return entry
            if entry.status is EntryStatus.COMMITTED:
                raise LedgerStateError(
                    f"{capability}/{idempotency_key} is committed; a performed write cannot be released"
                )
            session.execute(
                budget_entries.update()
                .where(budget_entries.c.entry_id == entry.entry_id)
                .values(status=EntryStatus.RELEASED.value, settled_at=now, settle_reason=reason)
            )
            updated = self._entry_row(session, capability, idempotency_key)
            session.commit()
        assert updated is not None
        return updated

    def _settleable(self, session: Session, capability: str, key: str) -> LedgerEntry:
        entry = self._entry_row(session, capability, key)
        if entry is None:
            raise LedgerEntryNotFoundError(f"no reservation for {capability}/{key}")
        ids = [entry.ceiling_id]
        if entry.capability_ceiling_id is not None:
            ids.append(entry.capability_ceiling_id)
        self._lock(session, ids)
        locked = self._entry_row(session, capability, key)
        assert locked is not None
        return locked

    def _to_ledger(self, money: Money, now: datetime) -> Money:
        if money.currency == self._currency:
            return money
        if self._fx is None:
            raise CurrencyMismatchError(
                f"{money.currency} amount and no FX table for the {self._currency} ledger"
            )
        return self._fx.convert(money, self._currency, as_of=now).converted

    # -- reads -------------------------------------------------------------------------------

    def ceilings(self, *, scope: str | None = None) -> list[Ceiling]:
        with self._session_factory() as session:
            stmt = sa.select(budget_ceilings).order_by(
                budget_ceilings.c.scope, budget_ceilings.c.period_start
            )
            if scope is not None:
                stmt = stmt.where(budget_ceilings.c.scope == scope)
            return [_ceiling(r) for r in session.execute(stmt)]

    def ceiling_status(self, *, now: datetime, scope: str = GLOBAL_SCOPE) -> CeilingStatus | None:
        """The ceiling of ``scope`` covering ``now`` with its reserved and committed spend;
        ``None`` when no ceiling covers ``now``."""
        now = require_aware(now)
        with self._session_factory() as session:
            covering = [c for c in self._ceilings_of(session, scope) if c.covers(now)]
            if len(covering) != 1:
                return None
            return self._status(session, covering[0])

    def rates(self, *, capability: str | None = None) -> list[CostRate]:
        with self._session_factory() as session:
            stmt = sa.select(budget_rates).order_by(
                budget_rates.c.capability, budget_rates.c.effective_from
            )
            if capability is not None:
                stmt = stmt.where(budget_rates.c.capability == capability)
            return [_rate(r) for r in session.execute(stmt)]

    def entry(self, capability: str, idempotency_key: str) -> LedgerEntry | None:
        with self._session_factory() as session:
            return self._entry_row(session, capability, idempotency_key)

    def entries(
        self,
        *,
        status: EntryStatus | None = None,
        capability: str | None = None,
        limit: int = 100,
    ) -> list[LedgerEntry]:
        stmt = sa.select(budget_entries).order_by(
            budget_entries.c.reserved_at, budget_entries.c.entry_id
        ).limit(limit)
        if status is not None:
            stmt = stmt.where(budget_entries.c.status == status.value)
        if capability is not None:
            stmt = stmt.where(budget_entries.c.capability == capability)
        with self._session_factory() as session:
            return [_entry(r) for r in session.execute(stmt)]

    @staticmethod
    def _ceilings_of(session: Session, scope: str) -> list[Ceiling]:
        rows = session.execute(
            sa.select(budget_ceilings)
            .where(budget_ceilings.c.scope == scope)
            .order_by(budget_ceilings.c.period_start)
        )
        return [_ceiling(r) for r in rows]

    @staticmethod
    def _rate_at(session: Session, capability: str, now: datetime) -> CostRate | None:
        rows = session.execute(
            sa.select(budget_rates).where(budget_rates.c.capability == capability)
        )
        effective = [r for r in (_rate(row) for row in rows) if r.effective_from <= now]
        return max(effective, key=lambda r: r.effective_from) if effective else None

    @staticmethod
    def _entry_row(session: Session, capability: str, key: str) -> LedgerEntry | None:
        row = session.execute(
            sa.select(budget_entries).where(
                budget_entries.c.capability == capability,
                budget_entries.c.idempotency_key == key,
            )
        ).first()
        return None if row is None else _entry(row)

    @staticmethod
    def _status(session: Session, ceiling: Ceiling) -> CeilingStatus:
        column = (
            budget_entries.c.ceiling_id if ceiling.scope == GLOBAL_SCOPE
            else budget_entries.c.capability_ceiling_id
        )
        rows = session.execute(sa.select(budget_entries).where(column == ceiling.ceiling_id))
        currency = ceiling.limit.currency
        reserved, committed = Money.zero(currency), Money.zero(currency)
        for entry in (_entry(row) for row in rows):
            if entry.status is EntryStatus.RESERVED:
                reserved = reserved + entry.charged
            elif entry.status is EntryStatus.COMMITTED:
                committed = committed + entry.charged
        return CeilingStatus(ceiling=ceiling, reserved=reserved, committed=committed)


# --- the guard's budget gate --------------------------------------------------------------


class PersistentBudgetGate:
    """:class:`~plm_platform.execution.BudgetGate` backed by :class:`BudgetLedger`.

    Currency: the guard's ``cost_estimate`` is a :data:`~plm_platform.execution.CostEstimate`.

    * ``Money`` is reserved as given; a currency other than the ledger's is converted
      through the ledger's FX table or refused (``currency_mismatch``).
    * ``None`` means the caller cannot price the write; the ledger's rate card prices
      it, or it is refused (``cost_estimate_unknown``).
    * A bare ``Decimal`` carries no currency and is refused
      (``cost_currency_unspecified``); it is never assumed to be in the ledger currency.

    Any database failure refuses the write (``budget_ledger_error``): fail closed.
    """

    def __init__(self, ledger: BudgetLedger, *, clock: Clock) -> None:
        self._ledger = ledger
        self._clock = clock

    @property
    def ledger(self) -> BudgetLedger:
        return self._ledger

    def reserve(
        self, capability: str, idempotency_key: str, cost_estimate: CostEstimate
    ) -> BudgetVerdict:
        if cost_estimate is not None and not isinstance(cost_estimate, Money):
            return BudgetVerdict(allowed=False, reason=Reason.COST_CURRENCY_UNSPECIFIED.value)
        try:
            result = self._ledger.reserve(
                capability, idempotency_key, cost=cost_estimate, now=self._clock.now()
            )
        except SQLAlchemyError:
            logger.exception("budget ledger unavailable; refusing %s", capability)
            return BudgetVerdict(allowed=False, reason=Reason.LEDGER_ERROR.value)
        return result.verdict()

    def commit(self, capability: str, idempotency_key: str, *, actual: Money | None) -> None:
        self._ledger.commit(capability, idempotency_key, actual=actual, now=self._clock.now())

    def release(self, capability: str, idempotency_key: str, *, reason: str) -> None:
        self._ledger.release(capability, idempotency_key, reason=reason, now=self._clock.now())


def budget_gate_for(
    settings: ExecutionSettings,
    session_factory: Callable[[], Session],
    *,
    clock: Clock,
    fx: FxTable | None = None,
) -> BudgetGate:
    """The budget gate a service's write guard uses.

    With ``PLM_BUDGET_LEDGER_CURRENCY`` configured, live writes reserve against the
    persisted ledger. Without it the default :class:`NoBudgetLedger` refuses every live
    write: no ledger is never read as unlimited budget.
    """
    if settings.budget_currency is None:
        return NoBudgetLedger()
    ledger = BudgetLedger(session_factory, currency=settings.budget_currency, fx=fx)
    return PersistentBudgetGate(ledger, clock=clock)
