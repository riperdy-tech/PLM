"""CLI commands implementation for Phase P9 Analytics & Economics Engine.

Missing facts are refused with exit code 2, never filled in: no default provider,
period, currency, identifier or metric value (ADR-0031, AGENTS.md "unknown is not zero").
JSON amounts are parsed straight to ``Decimal``.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, NoReturn

import httpx
import typer
import uvicorn
from plm_contracts.measurement import MeasurementKindError
from plm_contracts.money import FxRateMissingError, Money
from plm_platform.clock import SystemClock, require_aware
from plm_platform.config import SERVICE_TOKEN_VAR, load_settings
from plm_platform.db import build_engine, session_factory

from .api.app import create_app
from .config import load_analytics_settings
from .domain.amounts import AmountParseError, parse_amount
from .domain.enums import CostCategory, ObservationWindow
from .economics.ledger import entity_economics
from .evaluation.delivery import dispatch_rewards
from .ingestion.cost import ingest_cost_entry
from .ingestion.distributor import StatementRejected, ingest_distributor_statement
from .ingestion.youtube import ingest_youtube_metrics
from .storage.repositories import (
    get_costs_for_entity,
    get_experiment_outcomes,
    get_track_metrics,
    get_video_metrics,
    load_fx_table,
)

MISSING_FACT_EXIT_CODE = 2


def _refuse(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=MISSING_FACT_EXIT_CODE)


def _required(data: dict[str, Any], key: str) -> str:
    raw = data.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        _refuse(f"{key} is required; it is never defaulted")
    return str(raw).strip()


def _required_instant(data: dict[str, Any], key: str) -> datetime:
    raw = _required(data, key)
    try:
        return require_aware(datetime.fromisoformat(raw))
    except ValueError as exc:
        _refuse(f"{key} is not a timezone-aware ISO-8601 instant: {exc}")


def _optional_number(data: dict[str, Any], key: str, kind: type[int | float]) -> Any:
    raw = data.get(key)
    if raw is None:
        return None
    if isinstance(raw, bool) or (kind is int and isinstance(raw, Decimal) and raw != raw.to_integral_value()):
        _refuse(f"{key} must be a {kind.__name__}, got {raw!r}")
    try:
        return kind(raw)
    except (TypeError, ValueError):
        _refuse(f"{key} must be a number, got {raw!r}")


def _money(data: dict[str, Any], amount_key: str, currency_key: str, *, required: bool) -> Money | None:
    try:
        amount = parse_amount(data.get(amount_key))
    except AmountParseError as exc:
        _refuse(f"{amount_key}: {exc}")
    if amount is None:
        if required:
            _refuse(f"{amount_key} is required")
        return None
    currency = data.get(currency_key)
    if not currency:
        _refuse(f"{amount_key} needs {currency_key}; no currency is assumed")
    try:
        return Money.of(amount, str(currency))
    except ValueError as exc:
        _refuse(f"{currency_key}: {exc}")


def _load_json(content: str) -> dict[str, Any]:
    try:
        data = json.loads(content, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        _refuse(f"not valid JSON: {exc}")
    if not isinstance(data, dict):
        _refuse("the file must hold one JSON object")
    return data


def cli_ingest(
    source: str = typer.Option("distributor", "--source", "-s", help="Source type: youtube, distributor, cost"),
    file_path: str = typer.Option(..., "--file", "-f", help="Path to statement or metrics data file"),
    provider: str | None = None,
    period: str | None = None,
    currency: str | None = None,
) -> None:
    """Ingest a metrics, statement, or cost file into the analytics ledger.

    * ``distributor``: a CSV statement; ``provider``, ``period`` and ``currency`` (the
      statement's denomination) are required.
    * ``youtube``: one JSON snapshot with ``video_id``, ``channel_id``, ``date_str``,
      ``window`` and ``observed_at``; metrics absent from the file are not measured.
      ``estimated_revenue`` needs ``revenue_currency``.
    * ``cost``: one JSON entry with ``entity_id``, ``entity_type``, ``category``,
      ``amount``, ``currency``, ``period`` and ``incurred_at``.
    """
    path = Path(file_path)
    if not path.is_file():
        typer.echo(f"Error: file {file_path} not found", err=True)
        raise typer.Exit(code=1)
    content = path.read_text(encoding="utf-8")

    if source == "distributor":
        facts = {"provider": provider, "period": period, "currency": currency}
        for key in ("provider", "period", "currency"):
            _required(facts, key)

        def run(session: Any) -> str:
            assert provider and period and currency
            try:
                stmt, tracks = ingest_distributor_statement(
                    session,
                    provider=provider,
                    period=period,
                    currency=currency,
                    raw_content=content,
                    source_reference=path.name,
                    now=SystemClock().now(),
                )
            except StatementRejected as exc:
                _refuse(str(exc))
            return (
                f"Ingested distributor statement: {stmt.id} ({len(tracks)} tracks, "
                f"{stmt.total_revenue_amount} {stmt.currency})"
            )
    elif source == "youtube":
        data = _load_json(content)
        video_id = _required(data, "video_id")
        channel_id = _required(data, "channel_id")
        date_str = _required(data, "date_str")
        window_raw = _required(data, "window")
        try:
            window = ObservationWindow(window_raw)
        except ValueError:
            _refuse(f"window {window_raw!r} is not one of {[w.value for w in ObservationWindow]}")
        observed_at = _required_instant(data, "observed_at")
        revenue = _money(data, "estimated_revenue", "revenue_currency", required=False)
        metrics = {
            "views": _optional_number(data, "views", int),
            "watch_time_minutes": _optional_number(data, "watch_time_minutes", float),
            "average_view_duration_seconds": _optional_number(data, "average_view_duration_seconds", float),
            "average_percentage_viewed": _optional_number(data, "average_percentage_viewed", float),
            "subscribers_gained": _optional_number(data, "subscribers_gained", int),
            "subscribers_lost": _optional_number(data, "subscribers_lost", int),
        }

        def run(session: Any) -> str:
            obs = ingest_youtube_metrics(
                session,
                video_id=video_id,
                channel_id=channel_id,
                date_str=date_str,
                window=window,
                observed_at=observed_at,
                estimated_revenue=revenue,
                **metrics,
            )
            return f"Ingested YouTube snapshot: {obs.id} (window={obs.window.value})"
    elif source == "cost":
        data = _load_json(content)
        entity_id = _required(data, "entity_id")
        entity_type = _required(data, "entity_type")
        category_raw = _required(data, "category")
        try:
            category = CostCategory(category_raw)
        except ValueError:
            _refuse(f"category {category_raw!r} is not one of {[c.value for c in CostCategory]}")
        amount = _money(data, "amount", "currency", required=True)
        assert amount is not None
        cost_period = _required(data, "period")
        incurred_at = _required_instant(data, "incurred_at")

        def run(session: Any) -> str:
            cost = ingest_cost_entry(
                session,
                entity_id=entity_id,
                entity_type=entity_type,
                category=category,
                amount=amount,
                period=cost_period,
                incurred_at=incurred_at,
            )
            return f"Ingested cost item: {cost.id} ({amount.amount} {amount.currency})"
    else:
        typer.echo(f"Unknown source: {source}", err=True)
        raise typer.Exit(code=1)

    factory = session_factory(build_engine())
    with factory() as session:
        message = run(session)
        session.commit()
    typer.echo(message)


def cli_report(entity_id: str, entity_type: str = "video", window: str = "lifetime") -> None:
    """Show the economics report for an entity in the reporting currency."""
    if entity_type not in ("video", "track"):
        _refuse(f"entity_type must be video or track, got {entity_type!r}")
    try:
        snapshot_window = ObservationWindow(window)
    except ValueError:
        _refuse(f"window {window!r} is not one of {[w.value for w in ObservationWindow]}")
    settings = load_analytics_settings()
    factory = session_factory(build_engine())

    with factory() as session:
        try:
            econ = entity_economics(
                entity_id=entity_id,
                entity_type="video" if entity_type == "video" else "track",
                video_metrics=get_video_metrics(session, entity_id) if entity_type == "video" else [],
                track_metrics=get_track_metrics(session, entity_id) if entity_type == "track" else [],
                costs=get_costs_for_entity(session, entity_id),
                window=snapshot_window,
                reporting_currency=settings.reporting_currency,
                fx=load_fx_table(session),
                as_of=SystemClock().now(),
            )
        except (FxRateMissingError, MeasurementKindError) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    def show(money: Money | None) -> str:
        return "unknown" if money is None else f"{money.to_minor_units()} {money.currency}"

    typer.echo(f"Economics for Entity: {entity_id} ({econ.reporting_currency})")
    typer.echo(f"Estimated Revenue ({econ.window.value}, platform estimate): {show(econ.estimated_revenue)}")
    typer.echo(f"Realized Revenue (reported/final): {show(econ.realized_revenue)}")
    typer.echo(f"Allocated Cost: {show(econ.allocated_cost)}")
    if econ.net_contribution is None:
        typer.echo(f"Net Contribution: unknown ({econ.net_contribution_blocked_reason})")
    else:
        typer.echo(f"Net Contribution: {show(econ.net_contribution)}")
    typer.echo(f"Profit Margin: {econ.profit_margin if econ.profit_margin is not None else 'n/a'}")
    typer.echo(f"ROI: {econ.roi if econ.roi is not None else 'n/a'}")
    status = econ.payback_status.value if econ.payback_status is not None else "unknown"
    typer.echo(f"Payback Status: {status}")
    if econ.unreconciled_record_ids:
        typer.echo(f"Unreconciled (no currency): {', '.join(econ.unreconciled_record_ids)}")


def cli_rewards(experiment_id: str) -> None:
    """Show experiment feedback rewards (the engagement-contribution proxy)."""
    engine = build_engine()
    factory = session_factory(engine)

    with factory() as session:
        outcomes = get_experiment_outcomes(session, experiment_id)
        if not outcomes:
            typer.echo(f"No experiment outcomes found for: {experiment_id}", err=True)
            raise typer.Exit(code=1)

        for outc in outcomes:
            currency = outc.primary_value_currency or "currency unknown"
            typer.echo(
                f"Experiment: {outc.experiment_id} | Arm: {outc.content_arm_id} | "
                f"Window: {outc.observation_window.value} | Maturity: {outc.maturity.value} | "
                f"Reward proxy ({outc.reward_version}): {outc.normalized_reward:.4f} | "
                f"Net: {outc.primary_value:.2f} {currency}"
            )


def cli_dispatch(limit: int = 100, ar_base_url: str | None = None) -> None:
    """One outbox pass: deliver due C7 leg-2 rewards to P2 and reconcile uncertain ones.

    Refuses (exit 2) without ``PLM_SERVICE_TOKEN``: P2 would answer 401/503 to every
    send, and a pass that cannot authenticate proves nothing.
    """
    if limit < 1:
        _refuse("--limit must be positive")
    platform = load_settings()
    if platform.service_token is None:
        _refuse(f"blocked_pending_credentials: set {SERVICE_TOKEN_VAR} to deliver rewards to P2")
    overrides: dict[str, object] = {} if ar_base_url is None else {"ar_base_url": ar_base_url}
    settings = load_analytics_settings(platform=platform, **overrides)
    factory = session_factory(build_engine(platform))
    with httpx.Client(base_url=settings.ar_base_url, timeout=settings.ar_timeout_seconds) as client:
        report = dispatch_rewards(
            factory, client=client, token=platform.service_token.get_secret_value(),
            clock=SystemClock(), limit=limit,
        )
    typer.echo(
        f"Reward outbox pass: claimed={report.claimed} delivered={report.delivered} "
        f"failed={report.failed} retry_scheduled={report.retry_scheduled} "
        f"uncertain={report.uncertain} reconciled_present={report.reconciled_present} "
        f"reconciled_absent={report.reconciled_absent} "
        f"reconcile_unknown={report.reconcile_unknown} skipped={report.skipped}"
    )


def serve(host: str = "127.0.0.1", port: int = 8009) -> None:
    """Serve the `/analytics` FastAPI application."""
    app = create_app()
    uvicorn.run(app, host=host, port=port)
