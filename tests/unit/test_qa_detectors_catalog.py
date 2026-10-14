"""The detector catalog tables (migration 0031) and their SQL sources (R4, ADR-0034).

Runs on in-memory SQLite with the real P5 persistence path; proves storage and the
catalog/reference/corpus queries, not Postgres behaviour.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from plm_platform.clock import FixedClock
from quality_gate.detectors import (
    DetectorOutcome,
    InMemoryLyricCorpus,
    RightsDetectors,
    load_detector_policy,
)
from quality_gate.domain.enums import GateDisposition
from quality_gate.gate.engine import GateEngineResult, run_candidate_evaluation
from quality_gate.gate.persistence import persist_gate_result
from quality_gate.storage.detector_catalog import (
    SqlInternalCatalog,
    SqlLyricCorpus,
    SqlReferenceAudioSet,
    candidate_audio_fingerprints_table,
    reference_audio_fingerprints_table,
    reference_lyrics_table,
    register_reference_audio,
    register_reference_lyrics,
)
from quality_gate.storage.models import release_manifests_table
from sqlalchemy.orm import Session

from tests.support.qa_db import create_qa_engine, qa_session
from tests.support.rights_detectors import (
    UNRELATED_REFERENCE_LYRIC,
    synthetic_music,
    write_wav,
)
from tests.support.seams import candidate_event

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
POLICY = load_detector_policy()
REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "migrations" / "versions" / "qa" / "qa_0031_detector_catalog.py"
DETECTOR_TABLES = (
    candidate_audio_fingerprints_table,
    reference_audio_fingerprints_table,
    reference_lyrics_table,
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_qa_engine(static_pool=True)
    db = qa_session(engine)
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _sql_detectors(db: Session) -> RightsDetectors:
    return RightsDetectors(
        internal_catalog=SqlInternalCatalog(db),
        reference_audio=SqlReferenceAudioSet(db, POLICY.audio_fingerprint),
        lyric_corpus=SqlLyricCorpus(db, POLICY.lyric_similarity),
    )


def _evaluate(db: Session, audio: Path, candidate_id: str, **event: object) -> GateEngineResult:
    fields: dict[str, object] = {
        "candidate_id": candidate_id,
        "requested_duration_seconds": 6,
        "requested_duration_range": {"min_seconds": 5, "max_seconds": 8},
        "measured_audio": {"duration_seconds": 6.0, "sample_rate": 44100, "channels": 2},
    }
    fields.update(event)
    return run_candidate_evaluation(
        event=candidate_event(**fields),
        audio_path=audio,
        clock=FixedClock(NOW),
        detectors=_sql_detectors(db),
    )


def _register_unrelated_reference(db: Session, tmp_path: Path) -> None:
    register_reference_audio(
        db, ref_id="ref_unrelated", audio_path=write_wav(tmp_path / "ref.wav", synthetic_music(77)),
        source_note="synthetic test reference", params=POLICY.audio_fingerprint, now=NOW,
    )


def test_migration_0031_matches_the_runtime_tables() -> None:
    """Proves: migration 0031 and detector_catalog declare the same tables and columns, and
    downgrade removes them (drift guard on SQLite)."""
    spec = importlib.util.spec_from_file_location("qa_0031_detector_catalog", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0031_qa_detector_catalog"
    assert module.down_revision == "0030_ops_pipeline_runs"
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS qa")
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        inspector = sa.inspect(connection)
        assert set(inspector.get_table_names(schema="qa")) == {t.name for t in DETECTOR_TABLES}
        for table in DETECTOR_TABLES:
            columns = {c["name"] for c in inspector.get_columns(table.name, schema="qa")}
            assert columns == {c.name for c in table.columns}, table.name
        module.downgrade()
        assert set(sa.inspect(connection).get_table_names(schema="qa")) == set()


def test_released_candidate_joins_the_catalog_and_a_duplicate_is_caught(
    session: Session, tmp_path: Path
) -> None:
    """Proves: persisting an evaluation stores its fingerprint; once that evaluation has a
    manifest it is in the internal catalog, so a later candidate with the same audio
    (quieter) is INTERNAL_CATALOG_AUDIO_MATCH and quarantined, read through SQL."""
    _register_unrelated_reference(session, tmp_path)
    register_reference_lyrics(
        session, ref_id="ref_lyric", text=UNRELATED_REFERENCE_LYRIC, source_note="synthetic",
        params=POLICY.lyric_similarity, now=NOW,
    )
    music = synthetic_music(55)
    first = _evaluate(session, write_wav(tmp_path / "a.wav", music), "cand_first",
                      vocal_mode="instrumental", lyrics_text=None)
    assert first.release_manifest is not None
    persist_gate_result(session, first)
    session.commit()
    stored = session.execute(sa.select(candidate_audio_fingerprints_table)).mappings().all()
    assert [r["candidate_id"] for r in stored] == ["cand_first"]

    second = _evaluate(session, write_wav(tmp_path / "b.wav", music * 0.5), "cand_second",
                       vocal_mode="instrumental", lyrics_text=None)
    assert second.disposition is GateDisposition.QUARANTINED
    assert second.quarantine_case is not None
    assert "INTERNAL_CATALOG_AUDIO_MATCH" in second.quarantine_case.reason_codes
    hit = next(r for r in second.detector_results if r.source == "internal_catalog")
    assert hit.matched_ref == first.evaluation.id
    assert hit.metrics["best_ref_candidate_id"] == "cand_first"


def test_a_quarantined_candidate_is_not_in_the_catalog_until_a_manifest_exists(
    session: Session, tmp_path: Path
) -> None:
    """Proves: the catalog is defined by issued manifests: a quarantined evaluation's
    fingerprint is stored but not matched against, and joins the catalog as soon as a
    manifest row exists for it (an audited override issues one)."""
    music = synthetic_music(56)
    held = _evaluate(session, write_wav(tmp_path / "held.wav", music), "cand_held",
                     vocal_mode="instrumental", lyrics_text=None)  # no references: UNKNOWN
    assert held.disposition is GateDisposition.QUARANTINED
    persist_gate_result(session, held)
    session.commit()
    catalog = SqlInternalCatalog(session)
    assert catalog.load_fingerprints(exclude_candidate_id=None).entries == ()

    session.execute(
        release_manifests_table.insert().values(
            id="rm_1", p5_manifest_id="rel_man_1", release_manifest_version="1.0",
            candidate_id="cand_held", canonical_artifact_id="art", experiment_id="exp",
            track_spec_id="trk", content_arm_id="arm", disposition="QUARANTINED",
            technical_status="PASSED", commercial_use_status="eligible",
            provenance_status="COMPLETE", ai_disclosure_required=True, rights_risk="LOW",
            policy_status="COMPLIANT", evaluation_id=held.evaluation.id,
            evaluation_policy_version="p5-v1", warning_codes_json="[]",
            evidence_bundle_id="evi", manifest_hash="0" * 64, created_at=NOW,
        )
    )
    entries = catalog.load_fingerprints(exclude_candidate_id=None).entries
    assert [e.candidate_id for e in entries] == ["cand_held"]
    assert catalog.load_fingerprints(exclude_candidate_id="cand_held").entries == ()


def test_registered_reference_audio_is_matched_through_sql(
    session: Session, tmp_path: Path
) -> None:
    """Proves: a reference registered in qa.reference_audio_fingerprints is what the
    reference-set source returns, and a candidate re-using it is REFERENCE_AUDIO_MATCH."""
    reference = synthetic_music(57, seconds=9.0)
    register_reference_audio(
        session, ref_id="ref_hit", audio_path=write_wav(tmp_path / "hit.wav", reference),
        source_note="synthetic", params=POLICY.audio_fingerprint, now=NOW,
    )
    clip = reference[44100 : 44100 * 7] * 0.6
    result = _evaluate(session, write_wav(tmp_path / "clip.wav", clip), "cand_clip",
                       vocal_mode="instrumental", lyrics_text=None)
    assert result.quarantine_case is not None
    assert "REFERENCE_AUDIO_MATCH" in result.quarantine_case.reason_codes


def test_a_reference_stored_only_under_an_old_version_makes_the_set_unknown(
    session: Session, tmp_path: Path
) -> None:
    """Proves: after a fingerprint-version change, a reference not yet re-registered is
    reported (UNKNOWN), not silently dropped from the set."""
    _register_unrelated_reference(session, tmp_path)
    session.execute(
        reference_audio_fingerprints_table.update().values(
            fingerprint_version="p5-landmark-v0+000000000000"
        )
    )
    result = _evaluate(session, write_wav(tmp_path / "c.wav", synthetic_music(58)), "cand_c",
                       vocal_mode="instrumental", lyrics_text=None)
    ref = next(r for r in result.detector_results if r.source == "reference_set")
    assert ref.outcome is DetectorOutcome.UNKNOWN
    assert ref.reason == "fingerprint_version_mismatch"


def test_reference_lyrics_are_stored_as_hashes_only_and_still_match(session: Session) -> None:
    """Proves: the corpus table holds shingle hashes, no lyric text, and the SQL corpus
    gives the same shingles as building the corpus in memory."""
    text = UNRELATED_REFERENCE_LYRIC
    register_reference_lyrics(
        session, ref_id="ref_lyric", text=text, source_note="synthetic",
        params=POLICY.lyric_similarity, now=NOW,
    )
    row = session.execute(sa.select(reference_lyrics_table)).mappings().one()
    assert text.encode() not in row["shingles"]
    assert not any(isinstance(v, str) and "lantern" in v for v in row.values())
    (sql_entry,) = SqlLyricCorpus(session, POLICY.lyric_similarity).load_lyric_corpus().entries
    (mem_entry,) = InMemoryLyricCorpus.from_texts(
        {"ref_lyric": text}, POLICY.lyric_similarity
    ).load_lyric_corpus().entries
    assert sql_entry.shingles == mem_entry.shingles


def test_registration_refuses_a_reference_without_its_origin(
    session: Session, tmp_path: Path
) -> None:
    """Proves: a reference must say where it came from; an over-long id is refused."""
    path = write_wav(tmp_path / "r.wav", synthetic_music(59))
    with pytest.raises(ValueError):
        register_reference_audio(session, ref_id="r", audio_path=path, source_note=" ",
                                 params=POLICY.audio_fingerprint, now=NOW)
    with pytest.raises(ValueError):
        register_reference_lyrics(session, ref_id="x" * 65, text=UNRELATED_REFERENCE_LYRIC,
                                  source_note="s", params=POLICY.lyric_similarity, now=NOW)
