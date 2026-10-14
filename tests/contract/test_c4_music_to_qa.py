# tests/contract/test_c4_music_to_qa.py
"""Seam C4 — PHASE-P4 → PHASE-P5 (candidate ready). FROZEN by R6, ADR-0025, ADR-0027.

Producer: P4's real generation engine (mock provider) and ``build_candidate_ready_event``,
which assembles the event only from stored facts. Consumer: P5's real
``consume_c4_event`` and gate engine. Both use
``plm_contracts.seams.c4.CandidateReadyPayload``.

What this proves: the lineage and the provider's declared rights reach P5 unchanged; the
shared commercial-use vocabulary means P5 reads P4's ``eligible`` as eligible (it used to
read every real candidate as unknown); a candidate whose provider declared no terms is
quarantined, never cleared; and P5 refuses incomplete, unknown-field or future events.
"""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from plm_contracts import CommercialUseEligibility, GateDisposition
from plm_contracts.seams.c3 import TrackSpecPayload
from plm_contracts.seams.c4 import CandidateReadyPayload
from plm_platform.clock import FixedClock

from services.music_factory.api.events import build_candidate_ready_event
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.storage.artifacts import InMemoryArtifactStorage
from services.quality_gate.gate.engine import run_candidate_evaluation
from services.quality_gate.ingestion.c4_adapter import CandidateEventRejected, consume_c4_event
from tests.support.contract import SeamRegistry, assert_refused, with_value, without
from tests.support.music_db import music_db_session
from tests.support.seams import track_spec_fields

FIXED_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

REGISTRY_ENTRY = "C4 · PHASE-P4 → PHASE-P5"
SPEC = TrackSpecPayload.build(**track_spec_fields(requested_duration=20)).to_wire()


def _produce(storage: InMemoryArtifactStorage) -> tuple[dict[str, Any], bytes]:
    """Run the real P4 path and return the serialised event plus the artifact bytes."""
    adapted = adapt_track_spec(SPEC)
    plan, variants = plan_generation(adapted, created_at=FIXED_NOW)
    with music_db_session() as session:
        result = GenerationEngine(storage=storage, clock=FixedClock(FIXED_NOW)).execute_plan(
            session, plan=plan, variants=variants, spec=adapted
        )
        cand = result.candidates[0]
        event = build_candidate_ready_event(session, cand.candidate_id)
        audio = storage.get_artifact(result.artifacts[0].object_uri)
    assert audio is not None
    return json.loads(json.dumps(event.to_wire())), audio


@pytest.fixture(scope="module")
def produced() -> tuple[dict[str, Any], bytes]:
    return _produce(InMemoryArtifactStorage())


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)


def test_producer_emits_exactly_the_shared_wire(produced: tuple[dict[str, Any], bytes]) -> None:
    wire, _ = produced
    assert tuple(wire) == tuple(CandidateReadyPayload.model_fields)
    assert wire["lineage"] == SPEC["lineage"]
    assert wire["requested_duration_seconds"] == SPEC["requested_duration"]
    assert wire["rights"]["commercial_use_status"] == CommercialUseEligibility.ELIGIBLE.value


def test_real_event_reaches_the_p5_gate_intact(produced: tuple[dict[str, Any], bytes]) -> None:
    wire, audio = produced
    event = consume_c4_event(wire)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidate.wav"
        path.write_bytes(audio)
        result = run_candidate_evaluation(event=event, audio_path=path, clock=FixedClock(FIXED_NOW))

    snapshot = result.snapshot.candidate_snapshot
    assert snapshot["lineage"] == wire["lineage"]
    # The vocabulary drift is gone: P4's declared "eligible" is eligible to P5.
    assert result.evaluation.rights_status == CommercialUseEligibility.ELIGIBLE.value
    assert result.evaluation.provenance_status == "COMPLETE"
    assert result.snapshot.license_snapshot["attempt_id"] == wire["rights"]["attempt_id"]


def test_undeclared_rights_are_quarantined_never_cleared(
    produced: tuple[dict[str, Any], bytes],
) -> None:
    wire, audio = produced
    event = consume_c4_event(with_value(wire, "rights", None))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidate.wav"
        path.write_bytes(audio)
        result = run_candidate_evaluation(event=event, audio_path=path, clock=FixedClock(FIXED_NOW))
    assert result.evaluation.rights_status == CommercialUseEligibility.UNKNOWN.value
    assert result.disposition in (GateDisposition.QUARANTINED, GateDisposition.REJECTED)
    assert result.release_manifest is None


REQUIRED_PATHS = (
    *(f for f in CandidateReadyPayload.model_fields),
    "lineage.content_arm_id",
    "lineage.content_genome_id",
    "measured_audio.duration_seconds",
    "rights.commercial_use_status",
    "rights.plan_or_tier",
    "rights.attempt_id",
)


@pytest.mark.parametrize("field", REQUIRED_PATHS)
def test_p5_refuses_an_event_missing_any_required_fact(
    produced: tuple[dict[str, Any], bytes], field: str
) -> None:
    wire, _ = produced
    exc = assert_refused(
        consume_c4_event, without(wire, field), CandidateEventRejected,
        because=f"{field} missing",
    )
    assert isinstance(exc, CandidateEventRejected)
    assert exc.error.code == "missing_required_field"
    assert field in exc.error.field_paths


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("lineage.content_arm_id", "arm_placeholder"),
        ("rights.commercial_use_status", "permitted"),  # P4's old private token
        ("artifact_sha256", "not-a-digest"),
        ("genre_tags", []),
        ("event_type", "something_else"),
        ("schema_version", "2.0"),
        ("surprise", True),
    ],
)
def test_p5_refuses_invalid_unknown_or_future_events(
    produced: tuple[dict[str, Any], bytes], field: str, value: Any
) -> None:
    wire, _ = produced
    assert_refused(
        consume_c4_event, with_value(wire, field, value), CandidateEventRejected,
        because=f"{field}={value!r}",
    )
