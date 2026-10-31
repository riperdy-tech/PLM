import pytest

# tests/unit/ar/test_cli_ar.py
"""P2.14: the CLI is a thin wrapper — the engine and the session factory are injected."""

from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from services.autonomous_ar.decisioning.cycle import CycleRequest, MarketSnapshot

# Exercises code that loads platform settings; the values are set explicitly.
pytestmark = pytest.mark.usefixtures("platform_env")


def test_run_cycle_once_uses_the_injected_factory(
        ar_factory: sessionmaker[Session],
        market_source: Callable[[CycleRequest], MarketSnapshot]) -> None:
    from services.autonomous_ar.cli import run_cycle_once

    summary = run_cycle_once(session_factory=ar_factory, market_source=market_source,
                             slot_count=1, budget=1.0)
    assert summary["status"] in {"completed", "partial"} and summary["selected_count"] == 1


def test_simulate_prints_metrics_for_every_scenario() -> None:
    from services.autonomous_ar.cli import simulate

    for scenario in ("clear_winner", "sparse_observations", "regime_change"):
        output = simulate(scenario, cycles=10, seed=3)
        assert "regret_proxy" in output and "exploration_ratio" in output


def test_show_and_explain_read_the_stored_rows_through_the_injected_factory(
        ar_factory: sessionmaker[Session],
        market_source: Callable[[CycleRequest], MarketSnapshot]) -> None:
    from services.autonomous_ar.cli import explain, run_cycle_once, show_cycle
    from services.autonomous_ar.storage import repositories

    summary = run_cycle_once(session_factory=ar_factory, market_source=market_source,
                             slot_count=1, budget=1.0)
    shown = show_cycle(summary["cycle_id"], session_factory=ar_factory)
    assert summary["cycle_id"] in shown and "completed" in shown

    with ar_factory() as session:
        decision_id = repositories.load_decisions(session, summary["cycle_id"])[0]["id"]
    text = explain(decision_id, session_factory=ar_factory)
    assert "radical_explore" in text and "p2-v2" in text
