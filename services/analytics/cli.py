"""CLI commands implementation for Phase P9 Analytics & Economics Engine."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
import uvicorn
from plm_platform.clock import SystemClock, require_aware
from plm_platform.db import build_engine, session_factory

from .api.app import create_app
from .domain.enums import CostCategory
from .economics.calculator import compute_unit_economics
from .ingestion.cost import ingest_cost_entry
from .ingestion.distributor import ingest_distributor_statement
from .ingestion.youtube import ingest_youtube_metrics
from .storage.repositories import get_costs_for_entity, get_experiment_outcomes, get_video_metrics


def _required_instant(data: dict[str, Any], key: str) -> datetime:
    raw = data.get(key)
    if not raw:
        typer.echo(f"Error: {key} is required (timezone-aware ISO-8601)", err=True)
        raise typer.Exit(code=2)
    try:
        return require_aware(datetime.fromisoformat(str(raw)))
    except ValueError as exc:
        typer.echo(f"Error: {key} is not a timezone-aware ISO-8601 instant: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def cli_ingest(
    source: str = typer.Option("distributor", "--source", "-s", help="Source type: youtube, distributor, cost"),
    file_path: str = typer.Option(..., "--file", "-f", help="Path to statement or metrics data file"),
) -> None:
    """Ingest a metrics, statement, or cost file into the analytics ledger."""
    path = Path(file_path)
    if not path.is_file():
        typer.echo(f"Error: file {file_path} not found", err=True)
        raise typer.Exit(code=1)

    content = path.read_text(encoding="utf-8")
    engine = build_engine()
    factory = session_factory(engine)

    with factory() as session:
        if source == "distributor":
            stmt, tracks = ingest_distributor_statement(
                session,
                provider="cli_provider",
                period="2026-09",
                raw_content=content,
                source_reference=path.name,
                now=SystemClock().now(),
            )
            session.commit()
            typer.echo(f"Ingested distributor statement: {stmt.id} ({len(tracks)} tracks, ${stmt.total_revenue_usd})")
        elif source == "youtube":
            data = json.loads(content)
            obs = ingest_youtube_metrics(
                session,
                video_id=data.get("video_id", "vid_cli"),
                channel_id=data.get("channel_id", "chan_cli"),
                date_str=data.get("date_str", "2026-09-26"),
                views=int(data.get("views", 0)),
                watch_time_minutes=float(data.get("watch_time_minutes", 0.0)),
                estimated_revenue_usd=float(data.get("estimated_revenue_usd", 0.0)),
                observed_at=_required_instant(data, "observed_at"),
            )
            session.commit()
            typer.echo(f"Ingested YouTube metrics: {obs.id} (views={obs.views}, rev=${obs.estimated_revenue_usd})")
        elif source == "cost":
            data = json.loads(content)
            cost = ingest_cost_entry(
                session,
                entity_id=data.get("entity_id", "entity_cli"),
                entity_type=data.get("entity_type", "video"),
                category=CostCategory(data.get("category", "compute")),
                amount_usd=float(data.get("amount_usd", 0.0)),
                period=data.get("period", "2026-09"),
                incurred_at=_required_instant(data, "incurred_at"),
            )
            session.commit()
            typer.echo(f"Ingested cost item: {cost.id} (${cost.amount_usd})")
        else:
            typer.echo(f"Unknown source: {source}", err=True)
            raise typer.Exit(code=1)


def cli_report(entity_id: str) -> None:
    """Show economics report for an entity."""
    engine = build_engine()
    factory = session_factory(engine)

    with factory() as session:
        v_metrics = get_video_metrics(session, entity_id)
        costs = get_costs_for_entity(session, entity_id)

        gross_rev = sum(m.estimated_revenue_usd for m in v_metrics)
        views = sum(m.views for m in v_metrics)
        total_cost = sum(c.amount_usd for c in costs)

        econ = compute_unit_economics(
            entity_id,
            "video",
            gross_revenue_usd=gross_rev,
            allocated_cost_usd=total_cost,
            views=views if views > 0 else None,
            as_of=SystemClock().now(),
        )

        typer.echo(f"Economics for Entity: {entity_id}")
        typer.echo(f"Gross Revenue: ${econ.gross_revenue_usd:.2f}")
        typer.echo(f"Allocated Cost: ${econ.allocated_cost_usd:.2f}")
        typer.echo(f"Net Contribution: ${econ.net_contribution_usd:.2f}")
        typer.echo(f"Profit Margin: {econ.profit_margin * 100:.1f}%")
        typer.echo(f"ROI: {econ.roi * 100:.1f}%")
        typer.echo(f"Payback Status: {econ.payback_status.value}")


def cli_rewards(experiment_id: str) -> None:
    """Show experiment feedback rewards."""
    engine = build_engine()
    factory = session_factory(engine)

    with factory() as session:
        outcomes = get_experiment_outcomes(session, experiment_id)
        if not outcomes:
            typer.echo(f"No experiment outcomes found for: {experiment_id}", err=True)
            raise typer.Exit(code=1)

        for outc in outcomes:
            typer.echo(
                f"Experiment: {outc.experiment_id} | Arm: {outc.content_arm_id} | "
                f"Window: {outc.observation_window.value} | Reward: {outc.normalized_reward:.4f} | "
                f"Net: ${outc.primary_value:.2f}"
            )


def serve(host: str = "127.0.0.1", port: int = 8009) -> None:
    """Serve the `/analytics` FastAPI application."""
    app = create_app()
    uvicorn.run(app, host=host, port=port)
