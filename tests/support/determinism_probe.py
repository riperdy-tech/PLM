"""Run a slice of the pipeline and print a canonical JSON digest of everything it produced.

`tests/unit/test_cross_process_determinism.py` runs this module in two interpreters with
different `PYTHONHASHSEED` values and requires byte-identical output. Anything that leaks
process-randomised state (builtin `hash()`, set iteration order, `uuid4`, the wall clock)
into an id, seed, checksum or decision shows up as a diff.

Covered: P2 allocation simulation, P3 experiment design and materialisation, P4 planning
and mock generation (seeds, attempt ids, candidate ids, artifact digests).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from plm_contracts.seams.c2 import ProductionDirectivePayload
from plm_platform.clock import FixedClock

from services.autonomous_ar.simulation.scenarios import scenario_clear_winner
from services.autonomous_ar.simulation.simulator import SimConfig, run_simulation
from services.experiments.experiments.engine import design_experiment
from services.experiments.genome.engine import build_genome_from_directive
from services.experiments.ingestion.p2_adapter import adapt_directive
from services.experiments.materialization.builder import materialize_package
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.storage.artifacts import InMemoryArtifactStorage
from tests.support.music_db import music_db_session
from tests.support.seams import directive_fields

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _p2() -> dict[str, Any]:
    trace = run_simulation(scenario_clear_winner(), SimConfig(cycles=12, slot_count=6, seed=23))
    return {
        "selected_by_cycle": [list(cycle) for cycle in trace.selected_by_cycle],
        "posterior_means_final": dict(sorted(trace.posterior_means_final.items())),
        "rewards": list(trace.rewards),
    }


def _p3_p4() -> dict[str, Any]:
    directive = adapt_directive(ProductionDirectivePayload.build(**directive_fields()).to_wire())
    genome = build_genome_from_directive(directive)
    experiment = design_experiment(directive, genome, seed=7, created_at=T0)
    package = materialize_package(
        experiment, experiment.treatments[0], genome, created_at=T0, budget_envelope=30.0
    )
    spec_wire = package.track_specs[0].model_dump(mode="json")
    spec = adapt_track_spec(spec_wire)
    plan, variants = plan_generation(spec, created_at=T0)
    with music_db_session() as session:
        result = GenerationEngine(
            clock=FixedClock(T0), storage=InMemoryArtifactStorage()
        ).execute_plan(session, plan=plan, variants=variants, spec=spec)
    return {
        "experiment": experiment.model_dump(mode="json"),
        "package_id": package.package_id,
        "track_specs": [ts.model_dump(mode="json") for ts in package.track_specs],
        "plan_id": plan.plan_id,
        "variants": [v.variant_id for v in variants],
        "attempts": [(a.attempt_id, a.request_hash, a.provider_job_id) for a in result.attempts],
        "candidates": [c.candidate_id for c in result.candidates],
        "artifacts": [(a.artifact_id, a.sha256) for a in result.artifacts],
    }


def digest() -> str:
    payload = {"p2": _p2(), "p3_p4": _p3_p4()}
    return json.dumps(payload, sort_keys=True, default=str)


if __name__ == "__main__":
    text = digest()
    print(hashlib.sha256(text.encode("utf-8")).hexdigest())
    print(text)
