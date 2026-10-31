"""Owner-made Suno songs are imported from the inbox, never fetched from Suno (R6, D10).

In-process: the engine and the FastAPI routes over SQLite with WAV files in ``tmp_path``,
and the pipeline's generate step over fake phase clients. Proves that a plan waits,
without any attempt or guard decision, until every song it still needs is in the inbox
as a usable WAV (with lyrics for a vocal track); that the wait writes the prompt sheet,
answers 409 ``manual_audio_awaited`` unrecorded under the key, and holds the pipeline as
``MANUAL_AUDIO_AWAITED`` after every plan ran; and that an import goes through the guard
as ``music.import`` and records Suno identity, the owner's declared tier and the manual
human contribution. It does not prove Suno's real downloads or the owner's plan.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from plm_contracts import CommercialUseEligibility, ExecutionPolicy, ProviderMode
from plm_contracts.money import Money
from plm_contracts.seams.c3 import TrackSpecPayload
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from plm_platform.exceptions import load_hold_class_policy
from plm_platform.execution import (
    BudgetVerdict,
    CostEstimate,
    ExecutionSettings,
    ExternalWriteGuard,
    WriteAuthorization,
)
from plm_platform.pipeline.http import HttpPhaseSteps
from plm_platform.pipeline.phase_client import MANUAL_AUDIO_HOLD_REASON, ExternalWriteRefused
from plm_platform.pipeline.types import StepRequest
from plm_platform.temporal import Phase

from services.music_factory.api.app import create_app
from services.music_factory.api.events import (
    MANUAL_HUMAN_CONTRIBUTION,
    build_candidate_ready_event,
)
from services.music_factory.config import default_provider_for, load_music_factory_settings
from services.music_factory.domain.enums import PlanStatus
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec
from services.music_factory.providers.suno_manual import (
    IMPORT_CAPABILITY,
    MANUAL_MODEL_ID,
    SunoManualAdapter,
    SunoManualSettings,
    load_suno_manual_settings,
)
from services.music_factory.storage.artifacts import InMemoryArtifactStorage
from services.music_factory.storage.repositories import list_generation_attempts
from tests.support.music_db import create_music_engine, music_db_session, music_session
from tests.support.seams import lineage_fields, track_spec_fields

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
TOKEN = "music-seam-token"
HEADERS = {SERVICE_TOKEN_HEADER: TOKEN}
ENV = {
    "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm",
    "POSTGRES_PASSWORD": "x",
    "MINIO_ENDPOINT": "127.0.0.1:9",
    "MINIO_ROOT_USER": "plm",
    "MINIO_ROOT_PASSWORD": "x",
    "MINIO_BUCKET": "plm-media",
    "TEMPORAL_HOST": "127.0.0.1:9",
    "PLM_SERVICE_TOKEN": TOKEN,
}


def _spec(track_spec_id: str = "trk_manual_01") -> dict[str, Any]:
    fields = track_spec_fields(
        track_spec_id=track_spec_id, requested_duration=20,
        duration_range={"min_seconds": 10, "max_seconds": 30}, candidate_target=2,
        lineage=lineage_fields(experiment_id="exp_manual_01"),
        provider_policy={"allowed_providers": ["suno"]},
    )
    return TrackSpecPayload.build(**fields).to_wire()


def _wav(seconds: float = 1.0, tone: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(bytes([tone % 256, 0]) * 2 * int(44100 * seconds))
    return buffer.getvalue()


@dataclass
class FreeBudget:
    """Funds every import at no cost and records what was reserved."""

    reserved: list[tuple[str, str]] = field(default_factory=list)
    committed: list[str] = field(default_factory=list)

    def reserve(self, capability: str, idempotency_key: str,
                cost_estimate: CostEstimate) -> BudgetVerdict:
        self.reserved.append((capability, idempotency_key))
        return BudgetVerdict(allowed=True, reason="reserved")

    def commit(self, capability: str, idempotency_key: str, *, actual: Money | None) -> None:
        self.committed.append(idempotency_key)

    def release(self, capability: str, idempotency_key: str, *, reason: str) -> None:
        raise AssertionError("an import is never released")


def _guard(budget: FreeBudget) -> ExternalWriteGuard:
    return ExternalWriteGuard(
        ExecutionSettings(policies={IMPORT_CAPABILITY: ExecutionPolicy.LIVE}),
        authorizations=[WriteAuthorization(IMPORT_CAPABILITY, granted_by="owner",
                                           granted_at=NOW)],
        budget=budget,
    )


def _adapter(inbox: Path, *, tier: str | None = "pro") -> SunoManualAdapter:
    return SunoManualAdapter(SunoManualSettings(
        inbox=inbox, plan_tier=tier, account_ref="suno-account:owner" if tier else None,
        model_version="v4.5",
    ))


def _stems(inbox: Path) -> list[str]:
    """The file stems the prompt sheet asks for, in order."""
    sheet = (inbox / "trk_manual_01.prompts.txt").read_text(encoding="utf-8")
    return [line.removeprefix("Save as: ").removesuffix(".wav")
            for line in sheet.splitlines() if line.startswith("Save as: ")]


def _drop(inbox: Path, stem: str, *, tone: int, lyrics: bool = True) -> None:
    (inbox / f"{stem}.wav").write_bytes(_wav(tone=tone))
    if lyrics:
        (inbox / f"{stem}.lyrics.txt").write_text("la la la\n", encoding="utf-8")


def _execute(inbox: Path, budget: FreeBudget, session: Any, *,
             tier: str | None = "pro") -> tuple[Any, Any]:
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = GenerationEngine(clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                              write_guard=_guard(budget))
    result = engine.execute_plan(session, plan=plan, variants=variants, spec=spec,
                                 provider_adapter=_adapter(inbox, tier=tier))
    return result, plan


# -- waiting -----------------------------------------------------------------------------


def test_an_empty_inbox_writes_the_prompt_sheet_and_attempts_nothing(tmp_path: Path) -> None:
    budget = FreeBudget()
    with music_db_session() as session:
        result, plan = _execute(tmp_path, budget, session)
        assert list_generation_attempts(session, plan.plan_id) == []
    assert result.status is PlanStatus.RUNNING
    assert result.write_decisions == [] and budget.reserved == []
    stems = _stems(tmp_path)
    assert len(stems) == 2 and len(set(stems)) == 2
    assert all(stem.startswith("trk_manual_01-") for stem in stems)
    assert sorted(result.awaiting_audio) == sorted(
        [f"{s}.wav is missing" for s in stems]
        + [f"{s}.lyrics.txt is missing (the lyrics as sung)" for s in stems])
    sheet = (tmp_path / "trk_manual_01.prompts.txt").read_text(encoding="utf-8")
    assert "model v4.5" in sheet and "Instrumental: no" in sheet
    assert "Length: about 20 seconds" in sheet


@pytest.mark.parametrize("problem", ["one_missing", "not_wav", "same_audio", "no_lyrics"])
def test_nothing_is_imported_until_every_song_is_usable(tmp_path: Path, problem: str) -> None:
    budget = FreeBudget()
    with music_db_session() as session:
        _execute(tmp_path, budget, session)  # writes the sheet
        first, second = _stems(tmp_path)
        _drop(tmp_path, first, tone=1)
        if problem == "not_wav":
            (tmp_path / f"{second}.wav").write_bytes(b"ID3\x04 an mp3 download")
            (tmp_path / f"{second}.lyrics.txt").write_text("la", encoding="utf-8")
        elif problem == "same_audio":
            _drop(tmp_path, second, tone=1)
        elif problem == "no_lyrics":
            _drop(tmp_path, second, tone=2, lyrics=False)
        result, plan = _execute(tmp_path, budget, session)
        assert list_generation_attempts(session, plan.plan_id) == []
    assert result.write_decisions == [] and budget.reserved == []
    expected = {
        "one_missing": f"{second}.wav is missing",
        "not_wav": f"{second}.wav is not a WAV file (download it from Suno as WAV)",
        "same_audio": f"{second}.wav is the same audio as {first}.wav",
        "no_lyrics": f"{second}.lyrics.txt is missing (the lyrics as sung)",
    }[problem]
    assert expected in result.awaiting_audio


# -- importing ---------------------------------------------------------------------------


def test_usable_files_import_as_suno_through_the_import_guard(tmp_path: Path) -> None:
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
    assert [(d.capability, d.decision.value) for d in result.write_decisions] == \
        [(IMPORT_CAPABILITY, "execute")] * 2
    assert [c for c, _ in budget.reserved] == [IMPORT_CAPABILITY] * 2
    assert len(budget.committed) == 2
    assert {(a.provider_id, a.model_id) for a in attempts} == {("suno", MANUAL_MODEL_ID)}
    assert {a.provider_job_id for a in attempts} == {
        f"manual:{art.sha256}" for art in result.artifacts}
    assert {a.model_version for a in attempts} == {"v4.5"}
    assert {a.lyrics_text for a in attempts} == {"la la la"}
    for event in events:
        assert event.generation_provenance.human_contribution == MANUAL_HUMAN_CONTRIBUTION
        assert event.rights is not None
        assert event.rights.commercial_use_status is CommercialUseEligibility.ELIGIBLE
        assert event.rights.plan_or_tier == "pro"
        assert event.rights.account_identity_ref == "suno-account:owner"


@pytest.mark.parametrize("tier", [None, "free"])
def test_without_a_declared_paid_tier_there_are_no_terms(tmp_path: Path,
                                                         tier: str | None) -> None:
    assert _adapter(tmp_path, tier=tier).license_terms() is None


def test_the_settings_come_from_env_and_the_inbox_selects_manual_suno(tmp_path: Path) -> None:
    settings = load_suno_manual_settings(
        env={"PLM_SUNO_MANUAL_INBOX": str(tmp_path), "PLM_SUNO_PLAN_TIER": "Premier",
             "PLM_SUNO_ACCOUNT_REF": "acct", "PLM_SUNO_MODEL_VERSION": "v5"},
        dotenv_path=tmp_path / "absent.env",
    )
    assert settings == SunoManualSettings(inbox=tmp_path, plan_tier="premier",
                                          account_ref="acct", model_version="v5")
    assert load_suno_manual_settings(env={}, dotenv_path=tmp_path / "absent.env") == \
        SunoManualSettings()
    real = ExecutionSettings(provider_modes={"music": ProviderMode.REAL})
    assert default_provider_for(real, manual_inbox=True) == "suno_manual"
    assert default_provider_for(real) == "suno"
    assert default_provider_for(ExecutionSettings(), manual_inbox=True) == "mock"


# -- the HTTP surface --------------------------------------------------------------------


def test_api_waits_unrecorded_then_imports_under_the_same_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from services.music_factory.storage.artifacts import MinioArtifactStorage
    from tests.support.fake_minio import FakeMinio

    monkeypatch.setenv("PLM_SUNO_MANUAL_INBOX", str(tmp_path))
    monkeypatch.setenv("PLM_SUNO_PLAN_TIER", "pro")
    monkeypatch.setenv("PLM_SUNO_ACCOUNT_REF", "acct")
    platform = load_settings(env=dict(ENV))
    engine = create_music_engine(static_pool=True)
    app = create_app(
        session_factory=lambda: music_session(engine),
        settings=load_music_factory_settings(platform=platform, default_provider="suno_manual"),
        storage=MinioArtifactStorage(platform, client=FakeMinio()),  # type: ignore[arg-type]
        write_guard=_guard(FreeBudget()), clock=FixedClock(NOW),
    )
    client = TestClient(app)
    keyed = {**HEADERS, "Idempotency-Key": "exec-manual"}
    plan = client.post("/music/plans", json=_spec(), headers=HEADERS).json()
    url = f"/music/plans/{plan['plan_id']}/execute"

    waiting = client.post(url, json=_spec(), headers=keyed)
    assert waiting.status_code == 409
    detail = waiting.json()["detail"]
    assert detail["code"] == "external_write_awaiting_input"
    assert detail["message"] == "music.import not performed: manual_audio_awaited"
    assert len(detail["awaiting"]) == 4

    for tone, stem in enumerate(_stems(tmp_path), start=1):
        _drop(tmp_path, stem, tone=tone)
    done = client.post(url, json=_spec(), headers=keyed)
    assert done.status_code == 200 and done.json()["status"] == "completed"
    assert [w["capability"] for w in done.json()["external_writes"]] == \
        [IMPORT_CAPABILITY] * 2


# -- the pipeline ------------------------------------------------------------------------


class _FakeP3:
    def get(self, path: str, *, idempotency_key: str, params: Any = None) -> Any:
        return {"track_specs": [{"track_spec_id": "trk_a"}, {"track_spec_id": "trk_b"}]}


@dataclass
class _WaitingP4:
    executed: list[str] = field(default_factory=list)

    def post(self, path: str, *, idempotency_key: str, body: Any = None, **_: Any) -> Any:
        if path == "/music/plans":
            return {"plan_id": f"plan_{body['track_spec_id']}"}
        self.executed.append(path)
        raise ExternalWriteRefused(Phase.MUSIC, path, 409, "external_write_awaiting_input",
                                   "music.import not performed: manual_audio_awaited")


def test_pipeline_runs_every_plan_then_holds_for_the_owners_files() -> None:
    p4 = _WaitingP4()
    steps = HttpPhaseSteps({Phase.EXPERIMENTS: _FakeP3(), Phase.MUSIC: p4})  # type: ignore[dict-item]
    request = StepRequest(workflow_id="pipeline.d", directive_id="d", brand_id="b",
                          step="generate", idempotency_key="pipeline.d:generate",
                          requested_at=NOW, inputs={"policy_check": {"package_ids": ["pkg"]}})
    with pytest.raises(ExternalWriteRefused) as raised:
        steps._generate(request)
    assert p4.executed == ["/music/plans/plan_trk_a/execute", "/music/plans/plan_trk_b/execute"]
    assert raised.value.hold_reason_code == MANUAL_AUDIO_HOLD_REASON
    classified = load_hold_class_policy().classify(MANUAL_AUDIO_HOLD_REASON)
    assert classified.mapped and classified.rule.hold_class.value == "REVIEWABLE_QUARANTINE"


def test_styles_text_keeps_every_descriptor_of_the_prompt() -> None:
    from services.music_factory.domain.models import GenerationRequest
    from services.music_factory.providers.suno_manual import suno_styles

    prompt = (
        "LOFI track. mood: calm, nostalgic, reflective. for study_focus. 75 BPM. "
        "instrumental, no vocals. harmonic key: F_major. production approach: tape_saturated. "
        "instrument palette: dusty_rhodes, vinyl_crackle, sidechain_kick. hook character: "
        "instrumental_riff. section structure: loop_progression. energy: low. foreground "
        "lead: dusty_rhodes. groove: restrained pulse with brief fills. melodic character: "
        "answering melody in a contrasting register. harmonic motion: open intervals and "
        "delayed resolutions. arrangement progression: gradual thinning toward the ending. "
        "Within the declared groove, leave space around the melody."
    )
    styles = suno_styles(GenerationRequest(
        track_spec_id="trk", experiment_id="exp", prompt=prompt, vocal_mode="instrumental"))

    assert styles.startswith("lofi, calm, nostalgic, reflective mood, for study focus music")
    for phrase in (
        "75 BPM", "instrumental, no vocals", "key of F major", "tape saturated production",
        "dusty rhodes, vinyl crackle, sidechain kick", "instrumental riff hook",
        "loop progression structure", "low energy", "dusty rhodes lead",
        "groove: restrained pulse with brief fills",
        "melody: answering melody in a contrasting register",
        "harmony: open intervals and delayed resolutions",
        "arrangement: gradual thinning toward the ending",
        "Within the declared groove, leave space around the melody",
    ):
        assert phrase in styles
    assert "_" not in styles
