"""Shingled lyric similarity (R4, ADR-0034) and its UNKNOWN / NOT_APPLICABLE states."""

from __future__ import annotations

from quality_gate.detectors import (
    DetectorOutcome,
    InMemoryLyricCorpus,
    LyricReference,
    RightsDetectors,
    detect_lyrics,
    load_detector_policy,
    lyric_shingles,
    match_lyrics,
)

POLICY = load_detector_policy()
PARAMS = POLICY.lyric_similarity
REFERENCE = (
    "under neon rivers i keep dancing on my own tonight "
    "every siren sings your name and every window holds your light "
    "hold me closer than the thunder hold me till the morning bright"
)
ORIGINAL = (
    "paper boats are drifting slowly down the gutter after school "
    "we chase them past the bakery and the lights begin to glow"
)


def _corpus(texts: dict[str, str]) -> list[LyricReference]:
    return list(InMemoryLyricCorpus.from_texts(texts, PARAMS).load_lyric_corpus().entries)


def _match(text: str, entries: list[LyricReference]) -> object:
    return match_lyrics(text, entries, PARAMS, source="reference_corpus",
                        thresholds_version="t", snapshot_ref="s")


def test_normalisation_ignores_case_punctuation_and_diacritics() -> None:
    """Proves: the documented normalisation makes cosmetic edits invisible."""
    a = lyric_shingles("Hold me closer than the thunder, hold me till the morning bright!",
                       PARAMS.shingle_size)
    b = lyric_shingles("hold ME closer than thé thunder hold me -- till the morning bright",
                       PARAMS.shingle_size)
    assert a == b and a


def test_verbatim_copy_matches_and_original_does_not() -> None:
    """Proves: a copy matches its reference; original lyrics score below threshold."""
    entries = _corpus({"ref_a": REFERENCE, "ref_b": ORIGINAL + " extra words here today"})
    copy = _match(REFERENCE.upper(), entries)
    assert copy.outcome is DetectorOutcome.MATCH  # type: ignore[attr-defined]
    assert copy.matched_ref == "ref_a"  # type: ignore[attr-defined]
    fresh = _match("silver kites are tangled in the branches of the old oak tree", entries)
    assert fresh.outcome is DetectorOutcome.NO_MATCH  # type: ignore[attr-defined]


def test_a_copied_chorus_inside_a_long_new_lyric_matches_on_shared_shingles() -> None:
    """Proves: the absolute shared-shingle threshold catches a chorus embedded in a much
    longer new lyric, where the containment ratio alone would be low."""
    long_new = " ".join([ORIGINAL] * 1) + " " + " ".join(
        f"verse line number {i} about the harbour and the gulls" for i in range(30)
    )
    chorus = "hold me closer than the thunder hold me till the morning bright"
    result = _match(long_new + " " + chorus, _corpus({"ref_a": REFERENCE}))
    assert result.metrics["containment"] < PARAMS.match_min_score  # type: ignore[attr-defined]
    assert result.outcome is DetectorOutcome.MATCH  # type: ignore[attr-defined]


def test_empty_corpus_short_lyrics_and_stale_entries_are_unknown() -> None:
    """Proves: an empty corpus, a lyric too short to shingle, and an entry built under
    another corpus version are each UNKNOWN, never NO_MATCH."""
    assert _match(ORIGINAL, []).outcome is DetectorOutcome.UNKNOWN  # type: ignore[attr-defined]
    short = _match("la la la", _corpus({"ref_a": REFERENCE}))
    assert short.reason == "insufficient_candidate_shingles"  # type: ignore[attr-defined]
    stale = LyricReference("old", "other-normalisation+n5", frozenset({1, 2, 3}))
    assert _match(ORIGINAL, [stale]).reason == "corpus_version_mismatch"  # type: ignore[attr-defined]


def test_instrumental_without_lyrics_is_not_applicable_and_vocal_is_unknown() -> None:
    """Proves: no lyric text is NOT_APPLICABLE only for an instrumental; a vocal track
    with no text is UNKNOWN; an unconfigured corpus is UNKNOWN."""
    detectors = RightsDetectors.unconfigured()
    inst = detect_lyrics(None, vocal_mode="instrumental", detectors=detectors)
    assert inst.outcome is DetectorOutcome.NOT_APPLICABLE
    vocal = detect_lyrics("  ", vocal_mode="vocal", detectors=detectors)
    assert vocal.outcome is DetectorOutcome.UNKNOWN
    unconfigured = detect_lyrics(ORIGINAL, vocal_mode="vocal", detectors=detectors)
    assert unconfigured.outcome is DetectorOutcome.UNKNOWN
    assert unconfigured.reason == "not_configured"
