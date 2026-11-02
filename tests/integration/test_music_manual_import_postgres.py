"""Two runs of one manual-import plan at once import each song once, on PostgreSQL.

Requires PLM_MUSIC_PG_ISOLATED=1 (see ``test_music_paid_attempt_checkpoints_postgres``).
Proves the plan-row lock that a free import run holds until it commits: the second run
waits for the first, then finds every song imported and imports nothing.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from plm_platform.clock import FixedClock
from sqlalchemy.orm import Session

from services.music_factory.domain.enums import PlanStatus
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.providers.suno_manual import SunoManualAdapter
from services.music_factory.storage.artifacts import InMemoryArtifactStorage
from services.music_factory.storage.repositories import list_generation_attempts
from tests.integration.test_music_paid_attempt_checkpoints_postgres import (
    isolated_music_db,  # noqa: F401  (fixture)
)
from tests.unit.music.test_suno_manual_import import (
    NOW,
    FreeBudget,
    _adapter,
    _drop,
    _guard,
    _spec,
    _stems,
)

pytestmark = pytest.mark.integration


class _Gated(SunoManualAdapter):
    """Counts imports; the first one waits until the test lets it go."""

    def __init__(self, settings: Any, gate: threading.Event | None) -> None:
        super().__init__(settings)
        self.gate = gate
        self.imports = 0

    def generate(self, request: Any) -> Any:
        self.imports += 1
        if self.gate is not None and self.imports == 1:
            assert self.gate.wait(30)
        return super().generate(request)


def test_a_concurrent_run_waits_and_imports_nothing_twice(
    isolated_music_db: sa.Engine,  # noqa: F811
    tmp_path: Path,
) -> None:
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    budget = FreeBudget()
    settings = _adapter(tmp_path)._settings

    def run(adapter: SunoManualAdapter, out: dict[str, Any]) -> None:
        engine = GenerationEngine(clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                                  write_guard=_guard(budget))
        with Session(isolated_music_db) as session:
            out["result"] = engine.execute_plan(session, plan=plan, variants=variants,
                                                spec=spec, provider_adapter=adapter,
                                                checkpoint=session.commit)
            session.commit()

    run(_adapter(tmp_path), {})  # writes the sheet, imports nothing
    for tone, stem in enumerate(_stems(tmp_path), start=1):
        _drop(tmp_path, stem, tone=tone)

    gate = threading.Event()
    first, second = _Gated(settings, gate), _Gated(settings, None)
    first_out: dict[str, Any] = {}
    second_out: dict[str, Any] = {}
    a = threading.Thread(target=run, args=(first, first_out))
    a.start()
    while first.imports == 0 and a.is_alive():
        a.join(0.05)
    b = threading.Thread(target=run, args=(second, second_out))
    b.start()
    b.join(2)
    assert b.is_alive() and second.imports == 0  # waiting on the plan row
    gate.set()
    a.join(30)
    b.join(30)

    assert first_out["result"].status is PlanStatus.COMPLETED
    assert second_out["result"].status is PlanStatus.COMPLETED
    assert (first.imports, second.imports) == (2, 0)
    with Session(isolated_music_db) as session:
        assert len(list_generation_attempts(session, plan.plan_id)) == 2
