"""Owner-made Flow Music (Lyria 3.5) songs import like manual Suno, under their own identity.

D10 as changed 2026-10-31: Suno's 20 downloads a month cannot supply an album, so the
owner makes the songs in Google Flow Music. A Flow Music song must never be recorded as
Suno, nor carry Suno's terms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from plm_contracts import CommercialUseEligibility, ProviderMode
from plm_contracts.seams.c3 import TrackSpecPayload
from plm_platform.clock import FixedClock
from plm_platform.execution import ExecutionSettings

from services.music_factory.api.events import (
    FLOW_MUSIC_HUMAN_CONTRIBUTION,
    build_candidate_ready_event,
)
from services.music_factory.config import default_provider_for
from services.music_factory.domain.enums import PlanStatus
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.providers.adapters import get_provider_adapter
from services.music_factory.providers.flow_music_manual import (
    MANUAL_MODEL_ID,
    FlowMusicManualAdapter,
    load_flow_music_manual_settings,
)
from services.music_factory.providers.suno_manual import IMPORT_CAPABILITY, SunoManualSettings
from services.music_factory.storage.artifacts import InMemoryArtifactStorage
from services.music_factory.storage.repositories import list_generation_attempts
from tests.support.music_db import music_db_session
from tests.support.seams import lineage_fields, track_spec_fields
from tests.unit.music.test_suno_manual_import import NOW, FreeBudget, _drop, _guard, _stems


def _spec() -> dict[str, Any]:
    fields = track_spec_fields(
        track_spec_id="trk_manual_01", requested_duration=20,
        duration_range={"min_seconds": 10, "max_seconds": 30}, candidate_target=2,
        lineage=lineage_fields(experiment_id="exp_manual_01"),
        provider_policy={"allowed_providers": ["lyria"]},
    )
    return TrackSpecPayload.build(**fields).to_wire()


def _adapter(inbox: Path, *, tier: str | None = "google_ai_pro") -> FlowMusicManualAdapter:
    return FlowMusicManualAdapter(SunoManualSettings(
        inbox=inbox, plan_tier=tier, account_ref="google-account:owner" if tier else None,
        model_version="lyria-3.5",
    ))


def _execute(inbox: Path, budget: FreeBudget, session: Any, *,
             tier: str | None = "google_ai_pro") -> tuple[Any, Any]:
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = GenerationEngine(clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                              write_guard=_guard(budget))
    result = engine.execute_plan(session, plan=plan, variants=variants, spec=spec,
                                 provider_adapter=_adapter(inbox, tier=tier))
    return result, plan


def test_the_sheet_says_flow_music_and_gives_one_prompt(tmp_path: Path) -> None:
    budget = FreeBudget()
    with music_db_session() as session:
        result, _ = _execute(tmp_path, budget, session)
    assert result.status is PlanStatus.RUNNING and budget.reserved == []
    sheet = (tmp_path / "trk_manual_01.prompts.txt").read_text(encoding="utf-8")
    assert "Google Flow Music" in sheet and "Instrument: lyria-3.5" in sheet
    assert "suno" not in sheet.lower() and "Styles" not in sheet
    prompts = [line for line in sheet.splitlines() if line.startswith("With vocals, about 20")]
    assert len(prompts) == 2
    assert len(_stems(tmp_path)) == 2


def test_usable_files_import_as_lyria_with_flow_music_terms(tmp_path: Path) -> None:
    budget = FreeBudget()
    with music_db_session() as session:
        _execute(tmp_path, budget, session)
        for tone, stem in enumerate(_stems(tmp_path), start=1):
            _drop(tmp_path, stem, tone=tone)
        result, plan = _execute(tmp_path, budget, session)
        attempts = list_generation_attempts(session, plan.plan_id)
        events = [build_candidate_ready_event(session, c.candidate_id)
                  for c in result.candidates]
    assert result.status is PlanStatus.COMPLETED and result.awaiting_audio == []
    assert [d.capability for d in result.write_decisions] == [IMPORT_CAPABILITY] * 2
    assert {(a.provider_id, a.model_id) for a in attempts} == {("lyria", MANUAL_MODEL_ID)}
    assert {a.model_version for a in attempts} == {"lyria-3.5"}
    for event in events:
        assert event.generation_provenance.human_contribution == FLOW_MUSIC_HUMAN_CONTRIBUTION
        assert event.rights is not None
        assert event.rights.commercial_use_status is CommercialUseEligibility.ELIGIBLE
        assert event.rights.plan_or_tier == "google_ai_pro"
        assert "googleone" in (event.rights.license_reference or "")
        assert "suno" not in (event.rights.terms_version or "").lower()


@pytest.mark.parametrize("tier", [None, "free", "pro"])
def test_only_a_declared_google_ai_pro_plan_has_terms(tmp_path: Path, tier: str | None) -> None:
    assert _adapter(tmp_path, tier=tier).license_terms() is None


def test_the_flow_music_inbox_selects_it_over_suno(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "PLM_FLOW_MUSIC_MANUAL_INBOX=data/flow-music-inbox\n"
        "PLM_FLOW_MUSIC_PLAN_TIER=Google_AI_Pro\n"
        "PLM_FLOW_MUSIC_ACCOUNT_REF=owner@example.com\n"
        "PLM_FLOW_MUSIC_MODEL_VERSION=lyria-3.5\n", encoding="utf-8")
    settings = load_flow_music_manual_settings(env={}, dotenv_path=dotenv)
    assert settings == SunoManualSettings(
        inbox=Path("data/flow-music-inbox"), plan_tier="google_ai_pro",
        account_ref="owner@example.com", model_version="lyria-3.5")
    real = ExecutionSettings(provider_modes={"music": ProviderMode.REAL})
    assert default_provider_for(real, manual_inbox=True, flow_music_inbox=True) == \
        "flow_music_manual"
    assert default_provider_for(ExecutionSettings(), flow_music_inbox=True) == "mock"
    adapter = get_provider_adapter("flow_music_manual")
    assert isinstance(adapter, FlowMusicManualAdapter)
    assert adapter.provider_id == "lyria" and adapter.model_id == MANUAL_MODEL_ID
