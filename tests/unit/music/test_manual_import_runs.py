"""Manual imports across runs: retries, the song tool per plan, inboxes, Flow Music terms.

Review 2026-11-26, P4 bullet: a failed import must not use up a variant's retries for
good; a run's ``?provider=`` choice must stick to its plan and two tools may not share
one inbox; Flow Music songs are commercially uncertain until the owner records Flow
Music's own terms.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from music_factory.api.app import create_app
from music_factory.config import load_music_factory_settings
from music_factory.domain.enums import AttemptStatus, PlanStatus
from music_factory.domain.models import GenerationRequest, GenerationResponse
from music_factory.generation.engine import GenerationEngine
from music_factory.generation.planner import plan_generation
from music_factory.ingestion.p3_adapter import adapt_track_spec
from music_factory.providers.flow_music_manual import (
    FlowMusicManualAdapter,
    load_flow_music_manual_settings,
)
from music_factory.providers.suno_manual import SunoManualAdapter, SunoManualSettings
from music_factory.storage.artifacts import InMemoryArtifactStorage
from plm_contracts import CommercialUseEligibility
from plm_platform.clock import FixedClock
from plm_platform.config import load_settings

from tests.support.music_db import create_music_engine, music_session
from tests.unit.music.test_suno_manual_import import (
    ENV,
    HEADERS,
    NOW,
    FreeBudget,
    _drop,
    _guard,
    _spec,
    _stems,
)


class _FailingImport(SunoManualAdapter):
    """The file vanished between the check and the import (refused before anything)."""

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(provider_job_id="", status=AttemptStatus.FAILED,
                                  error_message="cannot read it", confirmed_unsubmitted=True)


def test_a_failed_import_can_be_tried_again_after_a_fix(tmp_path: Path) -> None:
    settings = SunoManualSettings(inbox=tmp_path, plan_tier="pro", account_ref="acct",
                                  model_version="v5")
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = GenerationEngine(clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                              write_guard=_guard(FreeBudget()))
    db = create_music_engine(static_pool=True)
    with music_session(db) as session:
        engine.execute_plan(session, plan=plan, variants=variants, spec=spec,
                            provider_adapter=SunoManualAdapter(settings))  # the sheet
        for tone, stem in enumerate(_stems(tmp_path), start=1):
            _drop(tmp_path, stem, tone=tone)
        for _ in range(2):  # more failures than the one retry a variant has
            failed = engine.execute_plan(session, plan=plan, variants=variants, spec=spec,
                                         provider_adapter=_FailingImport(settings),
                                         checkpoint=session.commit)
            assert failed.status is PlanStatus.FAILED
        fixed = engine.execute_plan(session, plan=plan, variants=variants, spec=spec,
                                    provider_adapter=SunoManualAdapter(settings),
                                    checkpoint=session.commit)
    assert "provider_job_reconciliation_required" not in fixed.errors
    assert fixed.status is PlanStatus.COMPLETED and len(fixed.candidates) == 2


def _client(monkeypatch: pytest.MonkeyPatch, suno: Path, flow: Path) -> TestClient:
    monkeypatch.setenv("PLM_SUNO_MANUAL_INBOX", str(suno))
    monkeypatch.setenv("PLM_FLOW_MUSIC_MANUAL_INBOX", str(flow))
    from music_factory.storage.artifacts import MinioArtifactStorage

    from tests.support.fake_minio import FakeMinio

    platform = load_settings(env=dict(ENV))
    engine = create_music_engine(static_pool=True)
    return TestClient(create_app(
        session_factory=lambda: music_session(engine),
        settings=load_music_factory_settings(platform=platform,
                                             default_provider="flow_music_manual"),
        storage=MinioArtifactStorage(platform, client=FakeMinio()),  # type: ignore[arg-type]
        write_guard=_guard(FreeBudget()), clock=FixedClock(NOW)))


def test_a_plan_keeps_the_song_tool_its_run_chose(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    suno, flow = tmp_path / "suno", tmp_path / "flow"
    suno.mkdir()
    flow.mkdir()
    client = _client(monkeypatch, suno, flow)
    plan = client.post("/music/plans", json=_spec(), headers=HEADERS).json()
    url = f"/music/plans/{plan['plan_id']}/execute"
    first = client.post(url, json=_spec(), params={"provider": "suno_manual"},
                        headers={**HEADERS, "Idempotency-Key": "a"})
    assert first.json()["detail"]["reason"] == "manual_audio_awaited"
    again = client.post(url, json=_spec(), headers={**HEADERS, "Idempotency-Key": "b"})
    assert again.json()["detail"]["reason"] == "manual_audio_awaited"
    assert list(flow.iterdir()) == []  # the default tool never got this plan's songs
    switched = client.post(url, json=_spec(), params={"provider": "flow_music_manual"},
                           headers={**HEADERS, "Idempotency-Key": "c"})
    assert switched.status_code == 409
    assert switched.json()["detail"]["code"] == "music_provider_conflict"


def test_two_song_tools_may_not_share_one_inbox(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, tmp_path, tmp_path)
    plan = client.post("/music/plans", json=_spec(), headers=HEADERS).json()
    refused = client.post(f"/music/plans/{plan['plan_id']}/execute", json=_spec(),
                          headers={**HEADERS, "Idempotency-Key": "a"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "music_inboxes_shared"
    assert list(tmp_path.glob("*.prompts.txt")) == []


def test_flow_music_terms_are_uncertain_until_the_owner_records_them(tmp_path: Path) -> None:
    unread = FlowMusicManualAdapter(SunoManualSettings(
        inbox=tmp_path, plan_tier="google_ai_pro", account_ref="acct")).license_terms()
    assert unread is not None
    assert unread.commercial_use_status is CommercialUseEligibility.EXPIRED_OR_UNVERIFIED
    settings = load_flow_music_manual_settings(
        env={"PLM_FLOW_MUSIC_PLAN_TIER": "google_ai_pro", "PLM_FLOW_MUSIC_ACCOUNT_REF": "a",
             "PLM_FLOW_MUSIC_TERMS_REF": "labs.google/flow/terms read 2026-11-27"},
        dotenv_path=tmp_path / "absent.env")
    recorded = FlowMusicManualAdapter(settings).license_terms()
    assert recorded is not None
    assert recorded.commercial_use_status is CommercialUseEligibility.ELIGIBLE
    assert "labs.google/flow/terms read 2026-11-27" in recorded.terms_version
