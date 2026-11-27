"""Finished runs' songs leave the inbox (review 2026-11-26 #52).

A plan whose songs were all imported moves its sheet, record and files to
``<inbox>/imported/<plan_id>/``; nothing is deleted, a name already there is kept
beside the new one, and the moved files still count for duplicate audio and for a
later replacement. Everything runs in ``tmp_path``.
"""

from __future__ import annotations

from pathlib import Path

from music_factory.candidates.bank import replace_candidate
from music_factory.domain.enums import PlanStatus
from music_factory.generation.engine import GenerationEngine
from music_factory.generation.planner import plan_generation
from music_factory.ingestion.p3_adapter import adapt_track_spec
from music_factory.providers.suno_manual import archive_track
from music_factory.storage.artifacts import InMemoryArtifactStorage
from music_factory.storage.repositories import list_generation_attempts
from plm_platform.clock import FixedClock

from tests.support.music_db import create_music_engine, music_session
from tests.unit.music.test_suno_manual_import import (
    NOW,
    FreeBudget,
    _adapter,
    _drop,
    _guard,
    _spec,
    _stems,
    _wav,
)


def _run(inbox: Path, budget: FreeBudget, session: object) -> object:
    spec = adapt_track_spec(_spec())
    plan, variants = plan_generation(spec, created_at=NOW)
    engine = GenerationEngine(clock=FixedClock(NOW), storage=InMemoryArtifactStorage(),
                              write_guard=_guard(budget))
    return engine.execute_plan(session, plan=plan, variants=variants, spec=spec,  # type: ignore[arg-type]
                               provider_adapter=_adapter(inbox), checkpoint=session.commit)  # type: ignore[attr-defined]


def test_an_imported_plan_moves_its_files_and_is_never_imported_again(tmp_path: Path) -> None:
    budget = FreeBudget()
    with music_session(create_music_engine(static_pool=True)) as session:
        first = _run(tmp_path, budget, session)
        stems = _stems(tmp_path)
        for tone, stem in enumerate(stems, start=1):
            _drop(tmp_path, stem, tone=tone)
        done = _run(tmp_path, budget, session)
        assert done.status is PlanStatus.COMPLETED  # type: ignore[attr-defined]
        plan_id = done.plan_id  # type: ignore[attr-defined]
        folder = tmp_path / "imported" / plan_id
        assert sorted(p.name for p in tmp_path.iterdir()) == ["imported"]
        assert sorted(p.name for p in folder.iterdir()) == sorted(
            ["trk_manual_01.prompts.txt", "trk_manual_01.songs.json",
             *(f"{s}.wav" for s in stems), *(f"{s}.lyrics.txt" for s in stems)])
        again = _run(tmp_path, budget, session)
        assert again.status is PlanStatus.COMPLETED  # type: ignore[attr-defined]
        assert len(list_generation_attempts(session, plan_id)) == 2  # nothing re-imported
        assert not (tmp_path / "trk_manual_01.prompts.txt").exists()
    assert first is not None


def test_moved_audio_counts_as_a_duplicate_and_a_replacement_keeps_its_name(
    tmp_path: Path,
) -> None:
    budget = FreeBudget()
    with music_session(create_music_engine(static_pool=True)) as session:
        _run(tmp_path, budget, session)
        stems = _stems(tmp_path)
        for tone, stem in enumerate(stems, start=1):
            _drop(tmp_path, stem, tone=tone)
        done = _run(tmp_path, budget, session)
        plan_id = done.plan_id  # type: ignore[attr-defined]
        replace_candidate(session, done.candidates[0].candidate_id, now=NOW)  # type: ignore[attr-defined]
        session.commit()
        waiting = _run(tmp_path, budget, session)
        assert waiting.awaiting_audio[0] == f"{stems[0]}-r2.wav is missing"  # type: ignore[attr-defined]
        (tmp_path / f"{stems[0]}-r2.wav").write_bytes(_wav(tone=1))  # the moved song's audio
        (tmp_path / f"{stems[0]}-r2.lyrics.txt").write_text("la", encoding="utf-8")
        again = _run(tmp_path, budget, session)
        assert again.awaiting_audio == [  # type: ignore[attr-defined]
            f"{stems[0]}-r2.wav is the same audio as imported/{plan_id}/{stems[0]}.wav"]


def test_archiving_is_idempotent_and_never_overwrites(tmp_path: Path) -> None:
    (tmp_path / "trk_a.prompts.txt").write_text("Plan: gen_plan_a\n", encoding="utf-8")
    (tmp_path / "trk_a-v1.wav").write_bytes(b"new")
    (tmp_path / "trk_ab-v1.wav").write_bytes(b"another track")
    folder = tmp_path / "cancelled" / "gen_plan_a"
    folder.mkdir(parents=True)
    (folder / "trk_a-v1.wav").write_bytes(b"old")
    moved = archive_track(tmp_path, "trk_a", "gen_plan_a", "cancelled")
    second = tmp_path / "cancelled" / "gen_plan_a-2"
    assert sorted(moved) == [second / "trk_a-v1.wav", second / "trk_a.prompts.txt"]
    assert (folder / "trk_a-v1.wav").read_bytes() == b"old"
    assert (second / "trk_a-v1.wav").read_bytes() == b"new"
    assert (tmp_path / "trk_ab-v1.wav").is_file()  # another track's song stays
    assert archive_track(tmp_path, "trk_a", "gen_plan_a", "cancelled") == []
