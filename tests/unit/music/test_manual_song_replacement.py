"""Replacing one rejected owner-made song (review 2026-11-26 #47).

P5 rejected a song: ``POST /music/candidates/{id}/replace`` marks it replaced; the next
execute of its plan asks on the sheet for ``<track>-<variant>-r2.wav`` (then ``-r3``...)
and waits; importing the new file makes a new candidate in its place. The rejected
song's own audio is not accepted again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from plm_platform.clock import FixedClock
from plm_platform.config import load_settings

from services.music_factory.api.app import create_app
from services.music_factory.candidates.bank import replace_candidate
from services.music_factory.config import load_music_factory_settings
from services.music_factory.domain.enums import CandidateStatus, PlanStatus
from services.music_factory.storage.repositories import get_candidate
from tests.support.music_db import create_music_engine, music_session
from tests.unit.music.test_suno_manual_import import (
    ENV,
    HEADERS,
    NOW,
    FreeBudget,
    _drop,
    _execute,
    _guard,
    _spec,
    _stems,
)


def _complete(inbox: Path, budget: FreeBudget, session: Any) -> Any:
    _execute(inbox, budget, session)  # the sheet
    for tone, stem in enumerate(_stems(inbox), start=1):
        _drop(inbox, stem, tone=tone)
    result, _ = _execute(inbox, budget, session)
    assert result.status is PlanStatus.COMPLETED
    return result


def test_a_replaced_song_is_asked_for_again_and_its_import_takes_its_place(
    tmp_path: Path,
) -> None:
    budget = FreeBudget()
    db = create_music_engine(static_pool=True)
    with music_session(db) as session:
        first = _complete(tmp_path, budget, session)
        rejected, kept = first.candidates
        replace_candidate(session, rejected.candidate_id, now=NOW)
        waiting, _ = _execute(tmp_path, budget, session)
        assert waiting.status is PlanStatus.RUNNING
        assert waiting.awaiting_audio == ["trk_manual_01-v1-r2.wav is missing",
                                          ("trk_manual_01-v1-r2.lyrics.txt is missing "
                                          "(the lyrics as sung)")]
        sheet = (tmp_path / "trk_manual_01.prompts.txt").read_text(encoding="utf-8")
        assert "Save as: trk_manual_01-v1-r2.wav" in sheet
        assert "Replaces: trk_manual_01-v1.wav (rejected; make a new song)" in sheet
        _drop(tmp_path, "trk_manual_01-v1-r2", tone=1)  # the rejected song's own audio
        again, _ = _execute(tmp_path, budget, session)
        assert any(p.startswith("trk_manual_01-v1-r2.wav is the same audio as")
                   for p in again.awaiting_audio)
        _drop(tmp_path, "trk_manual_01-v1-r2", tone=9)
        done, _ = _execute(tmp_path, budget, session)
        assert done.status is PlanStatus.COMPLETED
        ids = [c.candidate_id for c in done.candidates]
        assert kept.candidate_id in ids and rejected.candidate_id not in ids and len(ids) == 2
        stored = get_candidate(session, rejected.candidate_id)
        assert stored is not None and stored.status is CandidateStatus.REJECTED
        replace_candidate(session, next(c for c in ids if c != kept.candidate_id), now=NOW)
        third, _ = _execute(tmp_path, budget, session)
        assert third.awaiting_audio[0] == "trk_manual_01-v1-r3.wav is missing"


def test_the_replace_route_marks_the_song_replaced(tmp_path: Path, monkeypatch: Any) -> None:
    from services.music_factory.storage.artifacts import MinioArtifactStorage
    from tests.support.fake_minio import FakeMinio

    monkeypatch.setenv("PLM_SUNO_MANUAL_INBOX", str(tmp_path))
    platform = load_settings(env=dict(ENV))
    engine = create_music_engine(static_pool=True)
    client = TestClient(create_app(
        session_factory=lambda: music_session(engine),
        settings=load_music_factory_settings(platform=platform, default_provider="suno_manual"),
        storage=MinioArtifactStorage(platform, client=FakeMinio()),  # type: ignore[arg-type]
        write_guard=_guard(FreeBudget()), clock=FixedClock(NOW)))
    plan = client.post("/music/plans", json=_spec(), headers=HEADERS).json()
    url = f"/music/plans/{plan['plan_id']}/execute"
    client.post(url, json=_spec(), headers={**HEADERS, "Idempotency-Key": "a"})
    for tone, stem in enumerate(_stems(tmp_path), start=1):
        _drop(tmp_path, stem, tone=tone)
    done = client.post(url, json=_spec(), headers={**HEADERS, "Idempotency-Key": "b"}).json()
    candidate = done["candidates"][0]["candidate_id"]
    replaced = client.post(f"/music/candidates/{candidate}/replace", headers=HEADERS,
                           json={"reason": "P5: TEMPO_OUTSIDE_IDENTITY"})
    assert replaced.status_code == 200 and replaced.json()["status"] == "rejected"
    assert client.post(f"/music/candidates/{candidate}/replace",
                       headers=HEADERS).status_code == 200  # idempotent
    assert client.post("/music/candidates/nope/replace", headers=HEADERS).status_code == 404
    waiting = client.post(url, json=_spec(), headers={**HEADERS, "Idempotency-Key": "c"})
    assert waiting.status_code == 409
    assert "trk_manual_01-v1-r2.wav is missing" in waiting.json()["detail"]["awaiting"]
