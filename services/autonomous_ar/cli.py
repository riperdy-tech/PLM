# services/autonomous_ar/cli.py
"""Implementations behind the `plm ar …` commands (declared in `plm_platform.cli`).

Every command builds `load_ar_settings(platform=load_settings())` and — unless a factory
is injected — sessions over the platform database; the service never reads `os.environ`.
Determinism: `run_cycle_once` stamps the cycle instant once when the caller supplies none
(the same boundary rule the scheduled activity follows — re-running with the same `as_of`
replays the stored cycle); every other instant this module uses comes from stored rows,
never from a clock.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from plm_platform.config import load_settings
from plm_platform.db import session_factory as platform_session_factory
from sqlalchemy.orm import Session

from .config import ARSettings, load_ar_settings
from .decisioning.cycle import CycleRequest, MarketSnapshot, run_allocation_cycle
from .ingestion.p1_adapter import MarketUnavailable, P1Client, adapt_opportunity
from .observability.explanation import explain_decision
from .simulation.metrics import (
    coverage,
    cumulative_reward,
    exploration_ratio,
    winner_concentration,
)
from .simulation.scenarios import ALL_SCENARIOS
from .simulation.simulator import SimConfig, run_simulation
from .storage import repositories

__all__ = [
    "DEFAULT_BRAND_ID", "explain", "list_arms", "run_cycle_once", "serve", "show_cycle",
    "simulate",
]

DEFAULT_BRAND_ID: Final[str] = "neon_seoul"
"""The deployment's brand coordinate (ADR-0019); the operator commands run for this brand."""

SessionFactory = Callable[[], Session]


def run_cycle_once(*, session_factory: SessionFactory | None = None,
                   market_source: Callable[[CycleRequest], MarketSnapshot] | None = None,
                   slot_count: int | None = None, budget: float | None = None,
                   policy_version: str | None = None,
                   as_of: datetime | str | None = None,
                   brand_id: str | None = None) -> dict[str, Any]:
    """One allocation cycle in-process — the same engine call the job and the API make.

    `market_source` is the test seam (the same one `create_app` takes). Without it the
    snapshot comes from `/market/v1` with §102's stored fallback, so an operator can run a
    degraded cycle offline; when neither live nor stored data exists the refusal propagates.
    """
    settings = load_ar_settings(platform=load_settings())
    moment = _parse_instant(as_of)
    request = CycleRequest(
        brand_id=brand_id or DEFAULT_BRAND_ID, as_of=moment,
        slot_count=slot_count if slot_count is not None else settings.default_slot_count,
        budget=budget if budget is not None else settings.default_budget,
        policy_version=policy_version or settings.policy_version,
    )
    with _session(session_factory) as session:
        market = _resolve_snapshot(session, request, settings=settings,
                                   market_source=market_source)
        result = run_allocation_cycle(session, request, settings=settings, market=market,
                                      now=moment)
        session.commit()
    return {"cycle_id": result.cycle_id, "status": result.status.value,
            "selected_count": len(result.directives), "replay": result.replay,
            "warnings": list(result.warnings)}


def show_cycle(cycle_id: str, *, session_factory: SessionFactory | None = None) -> str:
    """One cycle's stored status and counts (budget is in cycle budget units, ADR-0023 #1)."""
    with _session(session_factory) as session:
        row = repositories.load_cycle(session, cycle_id)
        if row is None:
            raise LookupError(f"cycle {cycle_id!r} does not exist")
        decision_count = len(repositories.load_decisions(session, cycle_id))
    return "\n".join([
        f"cycle {row['id']} [{row['status']}]",
        f"  brand {row['brand_id']} · activation {row['activation_state']}",
        (f"  slots {row['slot_count']} · budget {row['budget']} (cycle budget units, "
         "ADR-0023 #1)"),
        f"  p1 snapshot {row['p1_snapshot_time']} · seed {row['random_seed']}",
        (f"  decisions {row['decision_count']} (stored {decision_count}) · "
         f"selected {row['selected_count']} · rejected {row['rejected_count']}"),
        f"  warnings {list(row['warning_codes_json'] or [])}",
    ])


def list_arms(*, session_factory: SessionFactory | None = None) -> str:
    """Arm states with their posterior summaries, in deterministic `arm_id` order."""
    with _session(session_factory) as session:
        rows = repositories.load_arm_states(session)
        posteriors = {row["arm_id"]: repositories.load_posterior(session, row["arm_id"])
                      for row in rows}
    lines: list[str] = []
    for row in rows:
        posterior = posteriors[row["arm_id"]]
        # absent stays absent: no posterior row reads "unmeasured", never a fabricated 0.0
        summary = ("unmeasured (no posterior row)"
                   if posterior is None else
                   f"observations {posterior.observation_count} · mean "
                   f"{posterior.posterior_mean:.4f} · variance "
                   f"{posterior.posterior_variance:.6f}")
        lines.append(
            f"{row['arm_id']}: {row['status']} · selections {row['selection_count']} · "
            f"mature {row['mature_result_count']} · provisional "
            f"{row['provisional_result_count']} · {summary}"
        )
    return "\n".join(lines) if lines else "no arms recorded"


def explain(decision_id: str, *, session_factory: SessionFactory | None = None) -> str:
    """§109 prose reconstructed from the decision row and its directive (when selected)."""
    with _session(session_factory) as session:
        decision = repositories.load_decision(session, decision_id)
        if decision is None:
            raise LookupError(f"decision {decision_id!r} does not exist")
        directive = repositories.load_directive_for_decision(session, decision_id)
    return explain_decision(decision, directive=directive)


def simulate(scenario: str = "clear_winner", cycles: int = 80, seed: int = 1) -> str:
    """One §69 scenario over the real policy functions; §99's metrics, units named."""
    settings = load_ar_settings(platform=load_settings())
    factories = {factory().name: factory for factory in ALL_SCENARIOS}
    if scenario not in factories:
        raise LookupError(f"unknown scenario {scenario!r}: expected one of {sorted(factories)}")
    trace = run_simulation(factories[scenario](),
                           SimConfig(cycles=cycles, slot_count=settings.default_slot_count,
                                     seed=seed))
    return "\n".join([
        (f"scenario {scenario} · cycles {cycles} · slots {settings.default_slot_count} · "
         f"seed {seed}"),
        f"  cumulative_reward {cumulative_reward(trace.rewards):.4f} (normalized 0-1, summed)",
        f"  regret_proxy {trace.regret_proxy:.4f} (normalized reward units)",
        (f"  exploration_ratio {exploration_ratio(trace.exploration_by_cycle):.4f} "
         "(share of selections in the ADR-0023 #2 exploration classes)"),
        (f"  winner_concentration {winner_concentration(trace.selection_counts):.4f} "
         "(HHI, 1/n..1)"),
        f"  coverage {coverage(trace.selection_counts):.4f} (share of arms selected at least once)",
    ])


def serve(host: str = "127.0.0.1", port: int = 8082) -> None:
    """Serve the `/ar` API (§71) with uvicorn over the platform database."""
    import uvicorn

    from .api.app import create_app

    settings = load_ar_settings(platform=load_settings())
    app = create_app(session_factory=platform_session_factory(), settings=settings)
    uvicorn.run(app, host=host, port=port)


def _session(session_factory: SessionFactory | None = None) -> Session:
    """A session over the platform engine (or the injected factory); callers commit."""
    factory: SessionFactory = (platform_session_factory() if session_factory is None
                               else session_factory)
    return factory()


def _parse_instant(value: datetime | str | None) -> datetime:
    """ISO string → aware datetime; absent value → the boundary stamp, once (as the job)."""
    if value is None:
        return datetime.now(UTC)
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _resolve_snapshot(session: Session, request: CycleRequest, *, settings: ARSettings,
                      market_source: Callable[[CycleRequest], MarketSnapshot] | None,
                      ) -> MarketSnapshot:
    """The injected seam, or live P1 with §102's stored fallback — never invented data."""
    if market_source is not None:
        return market_source(request)
    from .jobs.cycle import _stored_snapshot  # §102's rebuild is the job's own path

    token = (settings.platform.service_token.get_secret_value()
             if settings.platform is not None and settings.platform.service_token is not None
             else None)
    client = P1Client(settings.market_base_url, token,
                      timeout_seconds=settings.market_timeout_seconds)
    try:
        page = client.fetch_opportunities(limit=200)
    except MarketUnavailable:
        stored = _stored_snapshot(session, brand_id=request.brand_id, as_of=request.as_of)
        if stored is None:
            raise  # §102: no snapshot and no live data means an explicit failure
        snapshot: MarketSnapshot = stored
        return snapshot
    return MarketSnapshot(
        arms=tuple(adapt_opportunity(item, now=request.as_of) for item in page.items),
        as_of=page.as_of, source="live", score_version=page.score_version,
    )
