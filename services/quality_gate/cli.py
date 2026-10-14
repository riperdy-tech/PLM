"""CLI entry points and commands for Quality, Rights & Release Gate service (P5 §94)."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn
from plm_platform.clock import SystemClock
from plm_platform.config import load_settings
from plm_platform.db import session_factory as platform_session_factory

from .config import load_quality_gate_settings
from .domain.enums import ResolutionCode
from .gate.engine import run_candidate_evaluation
from .gate.persistence import persist_gate_result
from .gate.quarantine import resolve_quarantine
from .ingestion.c4_adapter import CandidateEventRejected, consume_c4_event
from .storage.repositories import (
    get_release_manifest_by_candidate,
    list_open_quarantine_cases,
)

qa_cli = typer.Typer(help="Quality gate CLI commands")


@qa_cli.command(name="evaluate")
def cli_evaluate(
    event_path: str = typer.Option(
        ..., "--event", "-e", help="Path to a Seam C4 candidate-ready event (JSON)"
    ),
    audio_path: str = typer.Option(..., "--audio", "-f", help="Path to WAV audio file"),
) -> None:
    """Evaluate a candidate, described by its C4 event, against the release gates.

    Every candidate fact (lineage, expected duration, declared rights) comes from the
    event; nothing is defaulted on the command line (ADR-0027).
    """
    path = Path(audio_path)
    if not path.is_file():
        typer.echo(f"Error: audio file {audio_path} does not exist.", err=True)
        raise typer.Exit(code=1)
    try:
        event = consume_c4_event(json.loads(Path(event_path).read_text(encoding="utf-8")))
    except CandidateEventRejected as exc:
        typer.echo(f"Error: C4 event refused: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    result = run_candidate_evaluation(event=event, audio_path=path, clock=SystemClock())

    typer.echo(f"Evaluation ID: {result.evaluation.id}")
    typer.echo(f"Disposition:   {result.decision.disposition.value}")
    typer.echo(f"Technical:     {result.evaluation.technical_status}")
    typer.echo(f"Quality:       {result.evaluation.quality_status}")
    typer.echo(f"Rights:        {result.evaluation.rights_status}")
    typer.echo(f"Provenance:    {result.evaluation.provenance_status}")
    typer.echo(f"Policy:        {result.evaluation.policy_status}")

    if result.quarantine_case:
        typer.echo(f"Quarantine Case: {result.quarantine_case.id} (Reasons: {result.quarantine_case.reason_codes})")
    if result.release_manifest:
        typer.echo(f"Release Manifest ID: {result.release_manifest.p5_manifest_id}")

    # Persist to DB
    factory = platform_session_factory()
    with factory() as session:
        persist_gate_result(session, result)
        session.commit()


@qa_cli.command(name="manifest")
def cli_manifest(
    candidate_id: str = typer.Option(..., "--candidate-id", "-c", help="Candidate ID to lookup manifest for"),
) -> None:
    """Retrieve the Seam C5 release manifest for a cleared candidate."""
    factory = platform_session_factory()
    with factory() as session:
        manifest = get_release_manifest_by_candidate(session, candidate_id)
        if not manifest:
            typer.echo(f"No release manifest found for candidate {candidate_id}.", err=True)
            raise typer.Exit(code=1)

        typer.echo(f"Manifest ID:  {manifest.p5_manifest_id}")
        typer.echo(f"Candidate:    {manifest.candidate_id}")
        typer.echo(f"Disposition:  {manifest.disposition.value}")
        typer.echo(f"Technical:    {manifest.technical_status}")
        typer.echo(f"Commercial:   {manifest.commercial_use_status}")
        typer.echo(f"Disclosure:   {manifest.ai_disclosure_required}")


@qa_cli.command(name="review")
def cli_review() -> None:
    """List open quarantine cases in the review queue."""
    factory = platform_session_factory()
    with factory() as session:
        cases = list_open_quarantine_cases(session)
        typer.echo(f"Open Quarantine Cases ({len(cases)}):")
        for c in cases:
            typer.echo(f"  - Case {c.id} | Candidate: {c.candidate_id} | Priority: {c.priority} | Reasons: {c.reason_codes}")


@qa_cli.command(name="resolve")
def cli_resolve(
    case_id: str = typer.Option(..., "--case-id", help="Quarantine case ID"),
    resolution: str = typer.Option("CLEARED_BY_OVERRIDE", "--resolution", help="Resolution: CLEARED_BY_OVERRIDE or CONFIRMED_REJECTED"),
    auditor_id: str = typer.Option("auditor_cli", "--auditor", help="Auditor identity"),
    notes: str = typer.Option("", "--notes", help="Audit notes"),
) -> None:
    """Resolve an open quarantine case."""
    try:
        res_code = ResolutionCode(resolution)
    except ValueError:
        typer.echo(f"Invalid resolution: {resolution}", err=True)
        raise typer.Exit(code=1)

    factory = platform_session_factory()
    with factory() as session:
        case, manifest = resolve_quarantine(
            session=session,
            case_id=case_id,
            resolution=res_code,
            auditor_id=auditor_id,
            notes=notes,
            now=SystemClock().now(),
        )
        session.commit()
        typer.echo(f"Case {case.id} resolved with {case.resolution.value if case.resolution else 'none'}")
        if manifest:
            typer.echo(f"Generated override release manifest: {manifest.p5_manifest_id}")


def serve(host: str = "127.0.0.1", port: int = 8005) -> None:
    """Serve the `/qa` FastAPI application using uvicorn."""
    settings = load_quality_gate_settings(platform=load_settings(), host=host, port=port)
    factory = platform_session_factory()

    from .api.app import create_app

    app = create_app(session_factory=factory, settings=settings)
    uvicorn.run(app, host=host, port=port)
