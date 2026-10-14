"""The persisted cost ledger and budget ceiling (R3), in process on SQLite.

What these prove: the ledger's reservation rules (fit, refuse, replay, release, commit,
currency, unknown cost), that the external-write guard denies an unfunded write before
the provider is reached (P7/P8 engines with spy clients that declare themselves REAL),
and that the engines commit a funded write after the provider call. The race test runs
two threads on a file-based SQLite, where serialisation is SQLite's write lock
(``BEGIN IMMEDIATE``). The PostgreSQL ``SELECT ... FOR UPDATE`` path is exercised only
by ``tests/integration/test_ledger_postgres.py``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from distribution.engine import DistributionEngine
from plm_contracts import ExecutionPolicy, ProviderMode
from plm_contracts.money import FxRate, FxTable, Money
from plm_platform.clock import FixedClock
from plm_platform.config import SettingsError
from plm_platform.execution import (
    ExecutionSettings,
    ExternalWriteGuard,
    ExternalWriteRefusedError,
    NoBudgetLedger,
    load_execution_settings,
)
from plm_platform.ledger import (
    GLOBAL_SCOPE,
    BudgetLedger,
    CeilingConflictError,
    EntryStatus,
    LedgerStateError,
    PersistentBudgetGate,
    Reason,
    budget_gate_for,
    capability_scope,
)
from sqlalchemy.orm import Session
from typer.testing import CliRunner
from youtube_publishing.domain.enums import FailureReasonCode, PublishState

from tests.support.distribution_db import distribution_db_session
from tests.support.ops_db import create_ops_engine, ops_session_factory
from tests.unit.test_distribution_engine import IDENTITY, _create_sample_package
from tests.unit.test_execution_policy import (
    _YT_WRITES,
    NO_DOTENV,
    NOW,
    SpyRealDistributor,
    _grant,
    _publish,
    _yt_writes,
)

PERIOD_START = datetime(2026, 10, 1, tzinfo=UTC)
PERIOD_END = datetime(2026, 11, 1, tzinfo=UTC)
YT_CAPS = ("youtube.upload", "youtube.thumbnail", "youtube.playlist")


def usd(amount: str) -> Money:
    return Money.of(amount, "USD")


def _ledger(engine: sa.Engine | None = None, *, fx: FxTable | None = None) -> BudgetLedger:
    return BudgetLedger(ops_session_factory(engine or create_ops_engine()), currency="USD", fx=fx)


def _ceiling(ledger: BudgetLedger, amount: str, *, scope: str = GLOBAL_SCOPE) -> None:
    ledger.set_ceiling(
        limit=usd(amount), period_start=PERIOD_START, period_end=PERIOD_END,
        scope=scope, set_by="operator@test", now=NOW,
    )


def _rate(ledger: BudgetLedger, capability: str, amount: str) -> None:
    ledger.set_rate(
        capability=capability, cost=usd(amount), effective_from=PERIOD_START,
        set_by="operator@test", now=NOW,
    )


def _remaining(ledger: BudgetLedger, scope: str = GLOBAL_SCOPE) -> Decimal:
    status = ledger.ceiling_status(now=NOW, scope=scope)
    assert status is not None
    return status.remaining.amount


def _live_guard(ledger: BudgetLedger, caps: tuple[str, ...]) -> ExternalWriteGuard:
    return ExternalWriteGuard(
        ExecutionSettings(policies={c: ExecutionPolicy.LIVE for c in caps}),
        authorizations=tuple(_grant(c) for c in caps),
        budget=PersistentBudgetGate(ledger, clock=FixedClock(NOW)),
    )


# --- reserve -> commit ---------------------------------------------------------------------


def test_a_fitting_reservation_is_held_then_committed_at_the_actual_cost() -> None:
    ledger = _ledger()
    _ceiling(ledger, "10")
    result = ledger.reserve("distribution.submit", "k1", cost=usd("4"), now=NOW)
    assert result.allowed and result.reason is Reason.RESERVED
    assert result.remaining == usd("6")
    assert _remaining(ledger) == Decimal(6)

    entry = ledger.commit("distribution.submit", "k1", actual=usd("3.50"), now=NOW)
    assert entry.status is EntryStatus.COMMITTED
    assert entry.actual == usd("3.50") and entry.actual_basis == "provider_reported"
    status = ledger.ceiling_status(now=NOW)
    assert status is not None
    assert status.reserved == usd("0") and status.committed == usd("3.50")
    assert status.remaining == usd("6.50")


def test_commit_without_a_provider_cost_records_the_estimate_labelled_as_such() -> None:
    ledger = _ledger()
    _ceiling(ledger, "10")
    ledger.reserve("youtube.upload", "k1", cost=usd("2"), now=NOW)
    entry = ledger.commit("youtube.upload", "k1", actual=None, now=NOW)
    assert entry.actual == usd("2") and entry.actual_basis == "reserved_estimate"
    # committing again with the same outcome is a no-op; a different actual is refused
    assert ledger.commit("youtube.upload", "k1", actual=None, now=NOW) == entry
    with pytest.raises(LedgerStateError):
        ledger.commit("youtube.upload", "k1", actual=usd("5"), now=NOW)


def test_p7_live_publish_reserves_from_the_rate_card_and_commits_each_write() -> None:
    ledger = _ledger()
    _ceiling(ledger, "10")
    _rate(ledger, "youtube.upload", "2.00")
    _rate(ledger, "youtube.thumbnail", "0.50")
    _rate(ledger, "youtube.playlist", "0")  # a stated zero, not an unknown

    result, client = _publish(_live_guard(ledger, YT_CAPS))

    assert result.is_success
    assert set(_yt_writes(client)) == _YT_WRITES
    entries = ledger.entries()
    assert {e.capability for e in entries} == set(YT_CAPS)
    assert all(e.status is EntryStatus.COMMITTED for e in entries)
    assert all(e.estimate_source == "rate_card" for e in entries)
    assert _remaining(ledger) == Decimal("7.50")


def test_p8_submit_commits_its_reservation_after_the_distributor_call() -> None:
    ledger = _ledger()
    _ceiling(ledger, "50")
    _rate(ledger, "distribution.submit", "9.99")
    client = SpyRealDistributor()
    engine = DistributionEngine(client, write_guard=_live_guard(ledger, ("distribution.submit",)))
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)
        engine.submit_release(session, pkg, artist=artist, now=NOW, **IDENTITY)
    assert client.writes == ["submit_release"]
    (entry,) = ledger.entries()
    assert entry.status is EntryStatus.COMMITTED and entry.actual == usd("9.99")
    assert _remaining(ledger) == Decimal("40.01")


# --- refusals: the guard denies before the provider ---------------------------------------


def test_exhausted_ceiling_refuses_and_the_guard_denies_before_the_provider() -> None:
    ledger = _ledger()
    _ceiling(ledger, "1")
    for cap in YT_CAPS:
        _rate(ledger, cap, "2")

    result, client = _publish(_live_guard(ledger, YT_CAPS))

    assert _yt_writes(client) == []
    assert result.attempt.state is PublishState.FAILED_RETRYABLE
    assert result.attempt.error_code == FailureReasonCode.EXTERNAL_WRITE_NOT_AUTHORIZED.value
    assert "budget_exhausted" in (result.attempt.error_message or "")
    assert ledger.entries() == []
    assert _remaining(ledger) == Decimal(1)


def test_p8_exhausted_ceiling_refuses_the_submission_and_persists_nothing() -> None:
    ledger = _ledger()
    _ceiling(ledger, "5")
    _rate(ledger, "distribution.submit", "9.99")
    client = SpyRealDistributor()
    engine = DistributionEngine(client, write_guard=_live_guard(ledger, ("distribution.submit",)))
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)
        with pytest.raises(ExternalWriteRefusedError) as exc:
            engine.submit_release(session, pkg, artist=artist, now=NOW, **IDENTITY)
    assert exc.value.decision.reason == "budget_exhausted"
    assert client.writes == []


def test_no_ceiling_configured_refuses() -> None:
    ledger = _ledger()
    result = ledger.reserve("youtube.upload", "k1", cost=usd("1"), now=NOW)
    assert not result.allowed and result.reason is Reason.NO_CEILING
    _ceiling(ledger, "10")
    after_period = ledger.reserve("youtube.upload", "k1", cost=usd("1"), now=PERIOD_END)
    assert after_period.reason is Reason.NO_CEILING  # the period is half-open
    assert ledger.entries() == []


def test_currency_mismatch_refuses_without_a_recorded_rate_and_converts_with_one() -> None:
    ledger = _ledger()
    _ceiling(ledger, "10")
    refused = ledger.reserve("distribution.submit", "k1", cost=Money.of("3", "EUR"), now=NOW)
    assert not refused.allowed and refused.reason is Reason.CURRENCY_MISMATCH

    rate = FxRate(base="EUR", quote="USD", rate=Decimal("1.10"), source="test-ecb",
                  as_of=NOW - timedelta(days=1))
    with_fx = BudgetLedger(ledger._session_factory, currency="USD", fx=FxTable([rate]))
    result = with_fx.reserve("distribution.submit", "k1", cost=Money.of("3", "EUR"), now=NOW)
    assert result.allowed and result.entry is not None
    assert result.entry.reserved == usd("3.30")
    assert result.entry.original == Money.of("3", "EUR")
    assert result.entry.fx_rate_id == rate.rate_id


def test_unknown_cost_refuses_and_a_bare_decimal_is_refused_as_currency_less() -> None:
    ledger = _ledger()
    _ceiling(ledger, "10")
    unknown = ledger.reserve("youtube.upload", "k1", cost=None, now=NOW)
    assert not unknown.allowed and unknown.reason is Reason.COST_UNKNOWN

    gate = PersistentBudgetGate(ledger, clock=FixedClock(NOW))
    verdict = gate.reserve("youtube.upload", "k2", Decimal(1))
    assert not verdict.allowed and verdict.reason == "cost_currency_unspecified"
    assert ledger.entries() == []


def test_capability_ceiling_is_enforced_alongside_the_global_one() -> None:
    ledger = _ledger()
    _ceiling(ledger, "100")
    _ceiling(ledger, "3", scope=capability_scope("youtube.upload"))
    assert ledger.reserve("youtube.upload", "k1", cost=usd("2"), now=NOW).allowed
    second = ledger.reserve("youtube.upload", "k2", cost=usd("2"), now=NOW)
    assert not second.allowed and second.reason is Reason.CAPABILITY_EXHAUSTED
    assert ledger.reserve("distribution.submit", "k3", cost=usd("2"), now=NOW).allowed
    assert _remaining(ledger) == Decimal(96)
    assert _remaining(ledger, capability_scope("youtube.upload")) == Decimal(1)


def test_a_ledger_failure_refuses_the_write() -> None:
    engine = create_ops_engine(with_tables=False)  # ops tables never migrated
    gate = PersistentBudgetGate(_ledger(engine), clock=FixedClock(NOW))
    verdict = gate.reserve("youtube.upload", "k1", usd("1"))
    assert not verdict.allowed and verdict.reason == "budget_ledger_error"


# --- idempotency, release, concurrency ------------------------------------------------------


def test_a_replayed_key_returns_the_original_verdict_and_never_double_reserves() -> None:
    ledger = _ledger()
    _ceiling(ledger, "10")
    first = ledger.reserve("youtube.upload", "k1", cost=usd("4"), now=NOW)
    replay = ledger.reserve("youtube.upload", "k1", cost=usd("4"), now=NOW + timedelta(minutes=5))
    assert first.allowed and replay.allowed
    assert replay.replayed and replay.reason is first.reason
    assert replay.entry == first.entry
    assert len(ledger.entries()) == 1
    assert _remaining(ledger) == Decimal(6)


def test_a_released_reservation_frees_its_headroom() -> None:
    ledger = _ledger()
    _ceiling(ledger, "5")
    assert ledger.reserve("distribution.submit", "a", cost=usd("4"), now=NOW).allowed
    blocked = ledger.reserve("distribution.submit", "b", cost=usd("4"), now=NOW)
    assert blocked.reason is Reason.EXHAUSTED

    released = ledger.release("distribution.submit", "a", reason="not_attempted", now=NOW)
    assert released.status is EntryStatus.RELEASED
    assert _remaining(ledger) == Decimal(5)
    assert ledger.reserve("distribution.submit", "b", cost=usd("4"), now=NOW).allowed
    # a released key is re-reserved from current headroom only: none is left now
    again = ledger.reserve("distribution.submit", "a", cost=usd("4"), now=NOW)
    assert not again.allowed and again.reason is Reason.EXHAUSTED


def test_settled_entries_cannot_cross_over() -> None:
    ledger = _ledger()
    _ceiling(ledger, "10")
    ledger.reserve("youtube.upload", "done", cost=usd("1"), now=NOW)
    ledger.commit("youtube.upload", "done", actual=None, now=NOW)
    with pytest.raises(LedgerStateError):
        ledger.release("youtube.upload", "done", reason="oops", now=NOW)
    ledger.reserve("youtube.upload", "dropped", cost=usd("1"), now=NOW)
    ledger.release("youtube.upload", "dropped", reason="not_attempted", now=NOW)
    with pytest.raises(LedgerStateError):
        ledger.commit("youtube.upload", "dropped", actual=None, now=NOW)


def test_overlapping_ceiling_periods_of_one_scope_are_refused() -> None:
    ledger = _ledger()
    _ceiling(ledger, "10")
    with pytest.raises(CeilingConflictError):
        ledger.set_ceiling(
            limit=usd("5"), period_start=PERIOD_START + timedelta(days=10),
            period_end=PERIOD_END + timedelta(days=10), set_by="operator@test", now=NOW,
        )
    _ceiling(ledger, "20")  # same period: changes the amount
    assert _remaining(ledger) == Decimal(20)


@contextmanager
def _slow_entry_reads(engine: sa.Engine) -> Iterator[None]:
    """Widen the read-then-write window inside every reservation, so two racing
    reservations overlap unless the ledger serialises them."""

    def _delay(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if "budget_entries" in statement and statement.lstrip().upper().startswith("SELECT"):
            time.sleep(0.1)

    sa.event.listen(engine, "before_cursor_execute", _delay)
    try:
        yield
    finally:
        sa.event.remove(engine, "before_cursor_execute", _delay)


def _race(ledger: BudgetLedger) -> list[Any]:
    barrier = threading.Barrier(2)
    results: list[Any] = [None, None]

    def run(i: int) -> None:
        barrier.wait()
        results[i] = ledger.reserve("distribution.submit", f"race-{i}", cost=usd("4"), now=NOW)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def test_two_reservations_racing_for_the_last_headroom_exactly_one_passes(tmp_path: Path) -> None:
    engine = create_ops_engine(tmp_path)
    ledger = _ledger(engine)
    _ceiling(ledger, "5")  # fits one 4.00 reservation, not two
    with _slow_entry_reads(engine):
        results = _race(ledger)
    assert sorted(r.allowed for r in results) == [False, True]
    assert {r.reason for r in results} == {Reason.RESERVED, Reason.EXHAUSTED}
    assert len(ledger.entries()) == 1
    assert _remaining(ledger) == Decimal(1)
    engine.dispose()


class _UnserialisedLedger(BudgetLedger):
    """Control: the same ledger without the write lock."""

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        with self._session_factory() as session:
            yield session


def test_the_race_test_detects_a_ledger_without_serialisation(tmp_path: Path) -> None:
    engine = create_ops_engine(tmp_path)
    ledger = _UnserialisedLedger(ops_session_factory(engine), currency="USD")
    _ceiling(ledger, "5")
    with _slow_entry_reads(engine):
        results = _race(ledger)
    assert all(r.allowed for r in results)  # both passed: over-reserved
    assert _remaining(ledger) == Decimal(-3)
    engine.dispose()


# --- wiring and configuration -----------------------------------------------------------------


def test_services_get_the_persistent_gate_only_when_a_ledger_is_configured() -> None:
    factory = ops_session_factory(create_ops_engine())
    clock = FixedClock(NOW)
    assert isinstance(budget_gate_for(ExecutionSettings(), factory, clock=clock), NoBudgetLedger)
    gate = budget_gate_for(ExecutionSettings(budget_currency="USD"), factory, clock=clock)
    assert isinstance(gate, PersistentBudgetGate) and gate.ledger.currency == "USD"


def test_ledger_currency_loads_from_env_and_is_validated() -> None:
    settings = load_execution_settings(
        env={"PLM_BUDGET_LEDGER_CURRENCY": "usd"}, dotenv_path=NO_DOTENV
    )
    assert settings.budget_currency == "USD"
    assert load_execution_settings(env={}, dotenv_path=NO_DOTENV).budget_currency is None
    with pytest.raises(SettingsError, match="PLM_BUDGET_LEDGER_CURRENCY"):
        load_execution_settings(env={"PLM_BUDGET_LEDGER_CURRENCY": "dollars"}, dotenv_path=NO_DOTENV)


def test_settling_a_non_executed_decision_is_a_no_op() -> None:
    ledger = _ledger()
    guard = ExternalWriteGuard(budget=PersistentBudgetGate(ledger, clock=FixedClock(NOW)))
    decision = guard.authorize_external_write(
        "youtube.upload", provider_mode=ProviderMode.REAL,
        idempotency_key="k1", now=NOW,
    )
    assert not guard.settle_external_write(decision, performed=True)
    assert ledger.entries() == []


# --- operator CLI ------------------------------------------------------------------------------


def test_budget_cli_sets_a_ceiling_and_rate_and_reports_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plm_platform import cli

    ledger = _ledger()
    monkeypatch.setattr(cli, "_budget_ledger", lambda currency: ledger)
    runner = CliRunner()

    out = runner.invoke(cli.app, [
        "budget", "set-ceiling", "--amount", "25", "--currency", "USD",
        "--from", PERIOD_START.isoformat(), "--until", PERIOD_END.isoformat(),
        "--set-by", "operator@test",
    ])
    assert out.exit_code == 0, out.output
    out = runner.invoke(cli.app, [
        "budget", "set-rate", "--capability", "youtube.upload", "--amount", "2",
        "--currency", "USD", "--from", PERIOD_START.isoformat(), "--set-by", "operator@test",
    ])
    assert out.exit_code == 0, out.output
    ledger.reserve("youtube.upload", "k1", cost=None, now=NOW)

    out = runner.invoke(cli.app, ["budget", "remaining", "--at", NOW.isoformat()])
    assert out.exit_code == 0, out.output
    assert "remaining 23.000000 USD" in out.output
    out = runner.invoke(cli.app, ["budget", "entries", "--status", "reserved"])
    assert out.exit_code == 0 and "youtube.upload" in out.output and "rate_card" in out.output
    out = runner.invoke(cli.app, ["budget", "release", "youtube.upload", "k1", "--reason", "op"])
    assert out.exit_code == 0, out.output
    assert _remaining(ledger) == Decimal(25)


def test_budget_cli_without_a_covering_ceiling_reports_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plm_platform import cli

    monkeypatch.setattr(cli, "_budget_ledger", lambda currency: _ledger())
    out = CliRunner().invoke(cli.app, ["budget", "remaining", "--at", NOW.isoformat()])
    assert out.exit_code == cli.BLOCKED_EXIT_CODE
    assert "no_budget_ceiling" in out.output
