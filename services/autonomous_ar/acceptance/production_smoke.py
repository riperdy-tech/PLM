# services/autonomous_ar/acceptance/production_smoke.py
"""§114 P2.16: one small allocation cycle against real P1 data, downstream disabled.

Honest scope: P2.16's spec assumes P3+ exist to *not* consume the directives. In this
build there is no P3 to consume them, no P4/P5 to produce any content, no P9, so
`POST /ar/results` has no live producer (R11), and no P10 (deferred, R12), so recursive
optimization cannot run. There is also no manual strategy to diff against, so §116's
shadow-mode comparison table is Wave-6 work: this smoke persists everything a comparison
needs (cycles, decisions, directives, feature snapshots) and never claims the comparison
happened. Without a live P1 and the service token there is no real read path either (§105
stores no credentials and none are simulated), so that outcome is `blocked`, never faked.

The smoke therefore proves exactly four things — a real P1 read with the service token,
real dry-run decisions, real persistence, and zero actionable directives (the ADR-0024
activation gate: DRY_RUN/SHADOW cycles stay auditable, never served). It returns
`blocked: …` — never a silent pass — whenever any of the four cannot be shown; anything
beyond them is Wave 6 work and is labelled blocked, not passed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from plm_contracts import ChannelActivationState

from ..config import production_settings
from ..decisioning.cycle import CycleRequest, MarketSnapshot, run_allocation_cycle
from ..ingestion.p1_adapter import MarketUnavailable, P1Client, adapt_opportunity
from ..storage import repositories

__all__ = ["run_production_smoke"]

_TOKEN_REASON = "blocked: pending P1 service token (PLM_SERVICE_TOKEN)"


def run_production_smoke(*, base_url: str | None = None,
                         brand_id: str | None = None) -> str | None:
    """Returns None on success, or the exact blocked reason string."""
    from plm_platform.config import load_settings
    from plm_platform.db import build_engine, scoped_session

    platform = load_settings()
    if platform.service_token is None:
        return _TOKEN_REASON
    settings = production_settings(platform=platform)
    brand = brand_id or "neon_seoul"
    client = P1Client(base_url or settings.market_base_url,
                      platform.service_token.get_secret_value(),
                      timeout_seconds=settings.market_timeout_seconds)
    now = datetime.now(UTC)
    try:
        page = client.fetch_opportunities(limit=200)
    except MarketUnavailable as exc:
        return f"blocked: P1 unreachable ({exc.reason})"
    if not page.items:
        return "blocked: P1 returned no opportunities"
    # The storage probe follows the P1 read on purpose: with both dependencies down the
    # blocked path must name P1 (pinned by tests/unit/ar/test_smoke_blocked.py).
    engine = build_engine(platform)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1 FROM ar.ar_allocation_cycle LIMIT 1")
    except Exception as exc:  # noqa: BLE001 - any reach-or-read failure is the blocked condition
        return f"blocked: postgres unavailable or ar schema missing ({exc.__class__.__name__})"
    snapshot = MarketSnapshot(
        arms=tuple(adapt_opportunity(item, now=now) for item in page.items),
        as_of=page.as_of, source="live", score_version=page.score_version)
    request = CycleRequest(brand_id=brand, as_of=now, slot_count=settings.default_slot_count,
                           budget=settings.default_budget, policy_version=settings.policy_version,
                           activation_state=ChannelActivationState.DRY_RUN)
    with scoped_session("ar", factory=None) as session:
        policy = repositories.load_policy(session, settings.policy_version)
        if policy is None:
            return (f"blocked: policy {settings.policy_version} not configured "
                    "(no validated policy row for this version: insert one before the smoke)")
        result = run_allocation_cycle(session, request, settings=settings, market=snapshot,
                                      now=now)
        session.commit()
        actionable = repositories.list_queued_directives(
            session, brand_id=brand, activation_states=(ChannelActivationState.AUTONOMOUS.value,))
    if actionable:
        return "blocked: DRY_RUN produced actionable directives — the activation gate is broken"
    print(f"dry-run cycle {result.cycle_id}: status={result.status.value} "
          f"decisions={len(result.directives)} actionable=0")
    return None
