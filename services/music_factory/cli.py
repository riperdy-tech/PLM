"""CLI entry points and commands for Music Generation Factory service (§4.5, §111)."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn
from plm_platform.config import load_settings
from plm_platform.db import session_factory as platform_session_factory

from .candidates.bank import list_candidates_by_experiment
from .config import load_music_factory_settings
from .generation.engine import GenerationEngine
from .generation.planner import plan_generation
from .ingestion.p3_adapter import adapt_track_spec
from .providers.adapters import get_provider_adapter
from .providers.registry import list_providers

music_cli = typer.Typer(help="Music generation factory CLI commands")


@music_cli.command(name="providers")
def cli_providers() -> None:
    """List available and registered music generation providers."""
    providers = list_providers()
    typer.echo(f"Registered Providers ({len(providers)}):")
    for p in providers:
        typer.echo(f"  - {p.provider_id}/{p.model_id} (max: {p.max_duration_seconds}s)")


@music_cli.command(name="generate")
def cli_generate(
    spec_path: str = typer.Option(
        ..., "--spec", "-s", help="Path to JSON file containing TrackSpec"
    ),
    provider: str = typer.Option(
        "mock", "--provider", "-p", help="Provider to use (mock, suno, elevenlabs)"
    ),
) -> None:
    """Execute music generation for a given TrackSpec JSON file."""
    path = Path(spec_path)
    if not path.exists():
        typer.echo(f"Error: Spec file not found: {spec_path}", err=True)
        raise typer.Exit(code=1)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        typer.echo(f"Error reading JSON: {exc}", err=True)
        raise typer.Exit(code=1)

    spec = adapt_track_spec(data)
    plan, variants = plan_generation(spec)

    typer.echo(f"Created Generation Plan: {plan.plan_id} ({len(variants)} variants)")

    factory = platform_session_factory()
    session = factory()

    adapter = get_provider_adapter(provider)
    engine = GenerationEngine(default_provider_id=provider)

    try:
        result = engine.execute_plan(
            session,
            plan=plan,
            variants=variants,
            spec=spec,
            provider_adapter=adapter,
        )
        session.commit()
        typer.echo(f"Generation Status: {result.status.value}")
        typer.echo(f"Generated {len(result.candidates)} candidates:")
        for cand in result.candidates:
            typer.echo(f"  - Candidate ID: {cand.candidate_id} (status: {cand.status.value})")
    finally:
        session.close()


@music_cli.command(name="candidates")
def cli_candidates(
    experiment_id: str = typer.Option(
        ..., "--experiment-id", "-e", help="Experiment ID to list candidates for"
    ),
) -> None:
    """List generated candidates for an experiment."""
    factory = platform_session_factory()
    session = factory()
    try:
        cands = list_candidates_by_experiment(session, experiment_id)
        typer.echo(f"Candidates for {experiment_id} ({len(cands)}):")
        for c in cands:
            typer.echo(
                f"  - {c.candidate_id} | track: {c.track_spec_id} | status: {c.status.value}"
            )
    finally:
        session.close()


def serve(host: str = "127.0.0.1", port: int = 8004) -> None:
    """Serve the `/music` FastAPI application using uvicorn."""
    settings = load_music_factory_settings(platform=load_settings(), host=host, port=port)
    factory = platform_session_factory()

    from .api.app import create_app

    app = create_app(session_factory=factory, settings=settings)
    uvicorn.run(app, host=host, port=port)
