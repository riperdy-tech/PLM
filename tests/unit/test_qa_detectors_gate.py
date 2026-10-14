"""Gate R4 (P5 rights and policy): the engine with injected detectors and controlled data.

Every test drives ``run_candidate_evaluation`` end to end on real files: numpy-generated
audio written as WAV, the real technical/musical evaluators, the real fingerprint and
lyric detectors, and in-memory reference sources standing in for the catalog tables
(the SQL sources are covered in ``test_qa_detectors_catalog.py``). What these prove is
the gate's decision logic and detector behaviour on synthetic fixtures; they do not
prove detection quality on real music, which no fixture here can show (ADR-0034).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from plm_platform.clock import FixedClock
from plm_platform.exceptions import HoldClass, load_hold_class_policy
from quality_gate.detectors import (
    DetectorOutcome,
    DetectorUnavailableError,
    FingerprintSet,
    LyricCorpus,
)
from quality_gate.domain.enums import GateDisposition
from quality_gate.evaluators.rights import GenerationProvenanceFacts, SourceInput
from quality_gate.gate.engine import GateEngineResult, run_candidate_evaluation

from tests.support.rights_detectors import (
    SAMPLE_RATE,
    clean_generation_facts,
    configured_detectors,
    reference_entry,
    synthetic_music,
    write_wav,
)
from tests.support.seams import candidate_event, rights_fields

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

ORIGINAL_LYRICS = (
    "paper boats are drifting slowly down the gutter after school "
    "we chase them past the bakery and the lights begin to glow "
    "nobody on our street remembers how the summer used to go"
)
REFERENCE_SONG = (
    "under neon rivers i keep dancing on my own tonight "
    "every siren sings your name and every window holds your light "
    "hold me closer than the thunder hold me till the morning bright "
    "we were golden we were reckless we were burning out of sight"
)


def _event(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "requested_duration_seconds": 6,
        "requested_duration_range": {"min_seconds": 5, "max_seconds": 8},
        "measured_audio": {"duration_seconds": 6.0, "sample_rate": SAMPLE_RATE, "channels": 2},
        "vocal_mode": "vocal",
        "lyrics_text": ORIGINAL_LYRICS,
        "prompt_text": "warm lofi rnb with rhodes and soft vocals",
    }
    fields.update(overrides)
    return candidate_event(**fields)


def _evaluate(
    audio: Path,
    *,
    event: Any = None,
    detectors: Any = "configured",
    facts: GenerationProvenanceFacts | None | str = "clean",
) -> GateEngineResult:
    base = event if event is not None else _event()
    provenance = clean_generation_facts() if facts == "clean" else facts
    if provenance is None:  # every P28 fact P4 could state, stated as unknown
        provenance = GenerationProvenanceFacts(model_version=None, generation_timestamp=None,
                                               source_inputs=None, human_contribution=None)
    return run_candidate_evaluation(
        event=base.model_copy(update={"generation_provenance": provenance}),
        audio_path=audio,
        clock=FixedClock(NOW),
        detectors=configured_detectors() if detectors == "configured" else detectors,
    )


def _reasons(result: GateEngineResult) -> list[str]:
    assert result.quarantine_case is not None, result.disposition
    return result.quarantine_case.reason_codes


def _hold_class(code: str) -> HoldClass:
    classification = load_hold_class_policy().classify(code)
    assert classification.mapped, code
    return classification.rule.hold_class


def _blocked(result: GateEngineResult) -> None:
    """Not cleared: quarantined, no manifest, nothing for P6 to assemble."""
    assert result.disposition is GateDisposition.QUARANTINED
    assert result.release_manifest is None


@pytest.fixture
def clean_audio(tmp_path: Path) -> Path:
    return write_wav(tmp_path / "original.wav", synthetic_music(seed=2, seconds=6.0))


# ----------------------------------------------------------------- clean clears


def test_clean_original_audio_lyrics_and_complete_provenance_clears(clean_audio: Path) -> None:
    """Proves: with every detector configured and finding nothing, original lyrics and all
    twelve P28 fields declared, the gate clears and issues a manifest; every detector ran
    (NO_MATCH, none UNKNOWN) and left an evidence item with its score and the snapshot of
    the source it compared against."""
    result = _evaluate(clean_audio)

    assert result.disposition in (GateDisposition.PASSED, GateDisposition.PASSED_WITH_WARNINGS)
    assert result.release_manifest is not None
    assert result.evaluation.provenance_status == "COMPLETE"
    assert {(r.source, r.outcome) for r in result.detector_results} == {
        ("internal_catalog", DetectorOutcome.NO_MATCH),
        ("reference_set", DetectorOutcome.NO_MATCH),
        ("reference_corpus", DetectorOutcome.NO_MATCH),
    }
    evidence = {e.evidence_type: e for e in result.evaluation.evidence_items}
    for kind in (
        "audio_fingerprint_internal_catalog",
        "audio_fingerprint_reference_set",
        "lyric_similarity_reference_corpus",
        "p28_provenance_record",
    ):
        assert kind in evidence
    assert '"score":' in evidence["audio_fingerprint_reference_set"].summary
    assert result.candidate_fingerprint is not None
    assert result.snapshot.catalog_snapshot_ref.startswith("det:")


def test_instrumental_without_lyrics_is_not_applicable_not_unknown(clean_audio: Path) -> None:
    """Proves: an instrumental with no lyric text records NOT_APPLICABLE for lyric
    similarity (not UNKNOWN), so it neither holds nor pretends a check ran, and clears."""
    result = _evaluate(clean_audio, event=_event(vocal_mode="instrumental", lyrics_text=None))
    lyric = next(r for r in result.detector_results if r.detector == "lyric_similarity")
    assert lyric.outcome is DetectorOutcome.NOT_APPLICABLE
    assert result.release_manifest is not None


# ---------------------------------------------------------- known audio match


def test_candidate_reusing_a_reference_recording_is_held_as_a_hard_stop(tmp_path: Path) -> None:
    """Proves: a candidate cut from a reference recording at a 1.37 s offset, at -8 dB and
    with added noise, is flagged REFERENCE_AUDIO_MATCH against that reference, quarantined
    with no manifest, and the code is a P29 hard stop; the evidence records the score and
    the recovered offset."""
    reference = synthetic_music(seed=11, seconds=10.0)
    start = int(1.37 * SAMPLE_RATE)
    clip = reference[start : start + 6 * SAMPLE_RATE] * 0.4
    clip = clip + np.random.default_rng(5).normal(0.0, 0.004, clip.shape)
    audio = write_wav(tmp_path / "reuse.wav", clip)
    detectors = configured_detectors(
        references=[
            reference_entry("ref_hit_song", reference),
            reference_entry("ref_other", synthetic_music(seed=12, seconds=10.0)),
        ]
    )

    result = _evaluate(audio, detectors=detectors)

    _blocked(result)
    assert "REFERENCE_AUDIO_MATCH" in _reasons(result)
    assert _hold_class("REFERENCE_AUDIO_MATCH") is HoldClass.HARD_STOP
    match = next(r for r in result.detector_results if r.source == "reference_set")
    assert match.outcome is DetectorOutcome.MATCH
    assert match.matched_ref == "ref_hit_song"
    assert match.score is not None and match.score >= 0.05
    assert abs(float(match.metrics["offset_seconds"]) - 1.37) < 0.05
    check = next(
        c for c in result.evaluation.rights_checks
        if c.check_type == "audio_fingerprint_reference_set"
    )
    assert check.matched_reference_id == "ref_hit_song"
    assert check.similarity_score == match.score


def test_candidate_duplicating_a_released_catalog_track_is_held_for_review(
    tmp_path: Path,
) -> None:
    """Proves: audio that duplicates a candidate P5 already released (internal catalog) is
    INTERNAL_CATALOG_AUDIO_MATCH, quarantined as a reviewable hold; the candidate's own
    earlier release is excluded, so re-evaluating a released candidate does not self-match."""
    released = synthetic_music(seed=21, seconds=6.0)
    audio = write_wav(tmp_path / "dup.wav", released * 0.7)
    catalog = [reference_entry("eval_released", released, candidate_id="cand_released")]

    dup = _evaluate(audio, detectors=configured_detectors(catalog=catalog))
    _blocked(dup)
    assert "INTERNAL_CATALOG_AUDIO_MATCH" in _reasons(dup)
    assert _hold_class("INTERNAL_CATALOG_AUDIO_MATCH") is HoldClass.REVIEWABLE_QUARANTINE

    itself = _evaluate(
        audio,
        event=_event(candidate_id="cand_released"),
        detectors=configured_detectors(catalog=catalog),
    )
    catalog_result = next(r for r in itself.detector_results if r.source == "internal_catalog")
    assert catalog_result.outcome is DetectorOutcome.NO_MATCH


# ------------------------------------------------------------ known lyric overlap


def test_lyrics_copying_a_reference_chorus_are_flagged(clean_audio: Path) -> None:
    """Proves: lyrics that embed a reference's chorus inside new lines (different case and
    punctuation) are LYRIC_SIMILARITY_HIGH against that reference, a P29 hard stop, and the
    candidate is quarantined with the score recorded."""
    copied = (
        "Paper boats are drifting slowly down the gutter after school! "
        "Hold me closer than the thunder, hold me till the morning bright; "
        "we were golden, we were reckless, we were burning out of sight."
    )
    detectors = configured_detectors(lyrics={"ref_neon_rivers": REFERENCE_SONG})
    result = _evaluate(clean_audio, event=_event(lyrics_text=copied), detectors=detectors)

    _blocked(result)
    assert "LYRIC_SIMILARITY_HIGH" in _reasons(result)
    assert _hold_class("LYRIC_SIMILARITY_HIGH") is HoldClass.HARD_STOP
    lyric = next(r for r in result.detector_results if r.detector == "lyric_similarity")
    assert lyric.outcome is DetectorOutcome.MATCH
    assert lyric.matched_ref == "ref_neon_rivers"


# -------------------------------------------------------- missing provenance


def test_no_generation_facts_quarantines_with_incomplete_p28(clean_audio: Path) -> None:
    """Proves: without P4's declared generation facts, the four P28 fields C4 does not
    carry are missing; the gate names them, quarantines, and never defaults them."""
    result = _evaluate(clean_audio, facts=None)
    _blocked(result)
    assert "INCOMPLETE_P28_PROVENANCE" in _reasons(result)
    assert result.evaluation.provenance_status == "PARTIAL"
    check = next(
        c for c in result.evaluation.provenance_checks
        if c.check_type == "p28_provenance_completeness"
    )
    assert check.details["missing_fields"] == [
        "model_version", "generation_timestamp", "source_inputs", "human_contribution"
    ]


@pytest.mark.parametrize(
    "missing", ["model_version", "generation_timestamp", "source_inputs", "human_contribution"]
)
def test_each_missing_p28_fact_alone_quarantines(clean_audio: Path, missing: str) -> None:
    """Proves: any single missing P28 generation fact blocks clearance on its own."""
    facts = clean_generation_facts().model_copy(update={missing: None})
    result = _evaluate(clean_audio, facts=facts)
    _blocked(result)
    assert "INCOMPLETE_P28_PROVENANCE" in _reasons(result)
    check = next(
        c for c in result.evaluation.provenance_checks
        if c.check_type == "p28_provenance_completeness"
    )
    assert check.details["missing_fields"] == [missing]


def test_no_rights_snapshot_quarantines_with_incomplete_r6(clean_audio: Path) -> None:
    """Proves: no declared rights snapshot leaves the four rights-derived P28 fields
    missing: INCOMPLETE_R6_PROVENANCE and UNKNOWN_COMMERCIAL_LICENSE, provenance MISSING."""
    result = _evaluate(clean_audio, event=_event(rights=None))
    _blocked(result)
    assert {"INCOMPLETE_R6_PROVENANCE", "UNKNOWN_COMMERCIAL_LICENSE"} <= set(_reasons(result))
    assert "INCOMPLETE_P28_PROVENANCE" not in _reasons(result)  # R6 code names those fields
    assert result.evaluation.provenance_status == "MISSING"


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("audio_sample", "SAMPLE_PROVENANCE_UNCERTAIN"),
        ("voice_model", "UNAUTHORIZED_VOICE_CLONE_DETECTED"),
    ],
)
def test_unlicensed_source_input_is_a_hard_stop(clean_audio: Path, kind: str, code: str) -> None:
    """Proves: a declared source input with no license is a P29 hard stop: a sample is
    SAMPLE_PROVENANCE_UNCERTAIN, a voice input UNAUTHORIZED_VOICE_CLONE_DETECTED; the same
    input with a license reference clears."""
    unlicensed = clean_generation_facts(
        source_inputs=(SourceInput(kind=kind, ref="input_01", license_ref=None),)
    )
    result = _evaluate(clean_audio, facts=unlicensed)
    _blocked(result)
    assert code in _reasons(result)
    assert _hold_class(code) is HoldClass.HARD_STOP

    licensed = clean_generation_facts(
        source_inputs=(SourceInput(kind=kind, ref="input_01", license_ref="lic_input_01"),)
    )
    assert _evaluate(clean_audio, facts=licensed).release_manifest is not None


# ---------------------------------------------- detector unavailable ⇒ UNKNOWN


class _Unavailable:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def load_fingerprints(self, *, exclude_candidate_id: str | None) -> FingerprintSet:
        raise self._exc

    def load_lyric_corpus(self) -> LyricCorpus:
        raise self._exc


UNKNOWN_CASES: dict[str, Callable[[], Any]] = {
    "nothing_configured": lambda: None,
    "reference_set_not_configured": lambda: configured_detectors(references=None),
    "reference_set_empty": lambda: configured_detectors(references=[]),
    "catalog_unreachable": lambda: configured_detectors(
        catalog=_Unavailable(DetectorUnavailableError("db down"))
    ),
    "reference_store_crashes": lambda: configured_detectors(
        references=_Unavailable(RuntimeError("boom"))
    ),
}


@pytest.mark.parametrize("case", sorted(UNKNOWN_CASES))
def test_audio_detector_that_cannot_run_is_unknown_and_quarantines(
    clean_audio: Path, case: str
) -> None:
    """Proves: an unconfigured, empty, unreachable or crashing audio source gives UNKNOWN,
    which quarantines with AUDIO_FINGERPRINT_UNKNOWN; it is never clearance, even though
    the audio is original and every other fact is complete."""
    result = _evaluate(clean_audio, detectors=UNKNOWN_CASES[case]())
    _blocked(result)
    assert "AUDIO_FINGERPRINT_UNKNOWN" in _reasons(result)
    assert any(r.outcome is DetectorOutcome.UNKNOWN for r in result.detector_results)


@pytest.mark.parametrize(
    "detectors",
    [
        pytest.param(lambda: configured_detectors(lyrics=None), id="corpus_not_configured"),
        pytest.param(lambda: configured_detectors(lyrics={}), id="corpus_empty"),
        pytest.param(
            lambda: configured_detectors(lyrics=_Unavailable(DetectorUnavailableError("x"))),
            id="corpus_unreadable",
        ),
    ],
)
def test_lyric_detector_that_cannot_run_is_unknown_and_quarantines(
    clean_audio: Path, detectors: Callable[[], Any]
) -> None:
    """Proves: a vocal candidate whose corpus is missing, empty or unreadable is
    LYRIC_SIMILARITY_UNKNOWN and quarantined, not cleared."""
    result = _evaluate(clean_audio, detectors=detectors())
    _blocked(result)
    assert "LYRIC_SIMILARITY_UNKNOWN" in _reasons(result)


def test_vocal_track_without_lyric_text_is_unknown_not_not_applicable(clean_audio: Path) -> None:
    """Proves: a vocal track that arrives with no lyric text is UNKNOWN (there are words P5
    cannot see), unlike an instrumental, and is quarantined."""
    result = _evaluate(clean_audio, event=_event(vocal_mode="vocal", lyrics_text=None))
    _blocked(result)
    assert "LYRIC_SIMILARITY_UNKNOWN" in _reasons(result)


def test_unreadable_audio_makes_the_fingerprint_unknown(tmp_path: Path) -> None:
    """Proves: audio the fingerprinter cannot decode is UNKNOWN for both sources (and the
    technical gate rejects it), so no fingerprint is stored and nothing is cleared."""
    garbage = tmp_path / "garbage.wav"
    garbage.write_bytes(b"RIFF not really a wave file")
    result = _evaluate(garbage)
    assert result.release_manifest is None
    assert result.candidate_fingerprint is None
    audio = [r for r in result.detector_results if r.detector == "audio_fingerprint"]
    assert {r.reason for r in audio} == {"audio_unreadable"}


def test_detector_unknown_holds_are_classified_reviewable_never_unmapped() -> None:
    """Proves: the UNKNOWN codes are classified (reviewable, rights_reviewer, evidence
    required), so a bare approve can never release an unrun check."""
    for code in ("AUDIO_FINGERPRINT_UNKNOWN", "LYRIC_SIMILARITY_UNKNOWN"):
        rule = load_hold_class_policy().classify(code).rule
        assert rule.hold_class is HoldClass.REVIEWABLE_QUARANTINE
        assert rule.required_evidence


# ------------------------------------ technical pass, rights or policy fail


@pytest.mark.parametrize(
    ("label", "code"),
    [
        ("protected_artist_prompt", "PROTECTED_ARTIST_REFERENCE_DETECTED"),
        ("protected_artist_lyrics", "PROTECTED_ARTIST_REFERENCE_DETECTED"),
        ("ai_policy_conflict", "AI_POLICY_CONFLICT"),
        ("conditional_license", "COMMERCIAL_LICENSE_UNCERTAIN"),
        ("lyric_copy", "LYRIC_SIMILARITY_HIGH"),
    ],
)
def test_technical_pass_but_rights_or_policy_fail_is_not_cleared(
    clean_audio: Path, label: str, code: str
) -> None:
    """Proves: audio that passes every technical check is still not cleared when a rights
    or policy check fails: the decision is QUARANTINED with the code, no manifest."""
    events = {
        "protected_artist_prompt": _event(prompt_text="moody rnb sung like Taylor Swift"),
        "protected_artist_lyrics": _event(lyrics_text=ORIGINAL_LYRICS + " just like beyoncé"),
        "ai_policy_conflict": _event(prompt_text="an AI cover with a cloned voice"),
        "conditional_license": _event(rights=rights_fields(commercial_use_status="conditional")),
        "lyric_copy": _event(lyrics_text=REFERENCE_SONG),
    }
    detectors = configured_detectors(lyrics={"ref_neon_rivers": REFERENCE_SONG})
    result = _evaluate(clean_audio, event=events[label], detectors=detectors)
    assert result.evaluation.technical_status == "PASSED"
    _blocked(result)
    assert code in _reasons(result)


# --------------------------------------------- conditional / expired license


@pytest.mark.parametrize("status", ["conditional", "expired_or_unverified"])
def test_conditional_or_expired_license_is_a_hard_stop(clean_audio: Path, status: str) -> None:
    """Proves: a declared but conditional or expired license is COMMERCIAL_LICENSE_UNCERTAIN
    (HARD_STOP, no override for any role), not the reviewable UNKNOWN_COMMERCIAL_LICENSE."""
    result = _evaluate(clean_audio, event=_event(rights=rights_fields(commercial_use_status=status)))
    _blocked(result)
    assert "COMMERCIAL_LICENSE_UNCERTAIN" in _reasons(result)
    assert "UNKNOWN_COMMERCIAL_LICENSE" not in _reasons(result)
    rule = load_hold_class_policy().classify("COMMERCIAL_LICENSE_UNCERTAIN").rule
    assert rule.hold_class is HoldClass.HARD_STOP
    assert rule.override_permitted is False


def test_declared_unknown_license_stays_reviewable(clean_audio: Path) -> None:
    """Proves: a declared 'unknown' status (no usable declaration) is the reviewable
    UNKNOWN_COMMERCIAL_LICENSE, not the hard stop."""
    result = _evaluate(clean_audio, event=_event(rights=rights_fields(commercial_use_status="unknown")))
    _blocked(result)
    assert "UNKNOWN_COMMERCIAL_LICENSE" in _reasons(result)
    assert "COMMERCIAL_LICENSE_UNCERTAIN" not in _reasons(result)
    assert _hold_class("UNKNOWN_COMMERCIAL_LICENSE") is HoldClass.REVIEWABLE_QUARANTINE
