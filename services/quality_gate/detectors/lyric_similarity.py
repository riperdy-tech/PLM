"""Shingled lyric similarity against a reference corpus (R4, ADR-0034).

Normalisation is the one P4 and P5 already share for artist screening
(:func:`plm_platform.protected_artists.normalise_for_screening`: NFKD, combining marks
dropped, casefolded, every run of non-alphanumerics one space). The text is then split
into word tokens, and every run of ``shingle_size`` consecutive tokens is a shingle,
hashed with blake2b to 64 bits. References are stored as those hashes only, so the
corpus never has to hold copyrighted lyric text.

For a candidate C and reference R (sets of shingle hashes):

* ``containment = |C ∩ R| / |C|``: how much of the candidate comes from the reference;
* ``coverage = |C ∩ R| / |R|``: how much of the reference the candidate reproduces;
* ``score = max(containment, coverage)``; ``jaccard`` is recorded for audit.

MATCH when ``score >= match_min_score`` or ``|C ∩ R| >= match_min_shared_shingles``
(a copied chorus inside a long new lyric). Thresholds are data (``detector_policy.json``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from plm_platform.protected_artists import normalise_for_screening

from .config import LyricSimilarityParams
from .outcome import DetectorOutcome, DetectorResult

__all__ = [
    "LYRIC_DETECTOR",
    "LyricReference",
    "corpus_ref",
    "lyric_shingles",
    "match_lyrics",
]

LYRIC_DETECTOR: Final[str] = "lyric_similarity"


def lyric_shingles(text: str, shingle_size: int) -> frozenset[int]:
    """The 64-bit hashes of every ``shingle_size``-token run of the normalised text."""
    tokens = normalise_for_screening(text).split()
    out: set[int] = set()
    for i in range(len(tokens) - shingle_size + 1):
        shingle = " ".join(tokens[i : i + shingle_size]).encode("utf-8")
        out.add(int.from_bytes(hashlib.blake2b(shingle, digest_size=8).digest(), "big"))
    return frozenset(out)


@dataclass(frozen=True, slots=True)
class LyricReference:
    """One corpus entry: shingle hashes under one corpus version."""

    ref_id: str
    corpus_version: str
    shingles: frozenset[int]
    label: str | None = None

    def digest(self) -> str:
        h = hashlib.sha256(self.corpus_version.encode() + b"\0")
        for value in sorted(self.shingles):
            h.update(value.to_bytes(8, "big"))
        return h.hexdigest()


def corpus_ref(entries: Sequence[LyricReference]) -> str:
    """Content hash of a corpus: the ordered entry ids and digests."""
    h = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: e.ref_id):
        h.update(entry.ref_id.encode() + b"\0" + entry.digest().encode() + b"\n")
    return f"lyrset:{h.hexdigest()}"


def match_lyrics(
    lyrics_text: str,
    entries: Sequence[LyricReference],
    params: LyricSimilarityParams,
    *,
    source: str,
    thresholds_version: str,
    snapshot_ref: str,
) -> DetectorResult:
    """Compare candidate lyrics against every corpus entry.

    UNKNOWN when the corpus is empty, an entry was built under another corpus version,
    or the candidate is too short to produce ``min_candidate_shingles`` shingles.
    """
    version = params.corpus_version
    candidate = lyric_shingles(lyrics_text, params.shingle_size)
    base: dict[str, object] = {
        "candidate_shingles": len(candidate),
        "entries": len(entries),
        "shingle_size": params.shingle_size,
        "match_min_score": params.match_min_score,
        "match_min_shared_shingles": params.match_min_shared_shingles,
    }

    def result(
        outcome: DetectorOutcome,
        *,
        metrics: dict[str, object],
        score: float | None = None,
        matched_ref: str | None = None,
        reason: str | None = None,
    ) -> DetectorResult:
        return DetectorResult(
            detector=LYRIC_DETECTOR,
            source=source,
            outcome=outcome,
            version=version,
            thresholds_version=thresholds_version,
            score=score,
            matched_ref=matched_ref,
            source_snapshot_ref=snapshot_ref,
            reason=reason,
            metrics=metrics,
        )

    stale = sorted(e.ref_id for e in entries if e.corpus_version != version)
    if stale:
        return result(
            DetectorOutcome.UNKNOWN,
            reason="corpus_version_mismatch",
            metrics={**base, "stale_entries": stale[:20], "stale_count": len(stale)},
        )
    usable = [e for e in entries if e.shingles]
    if not usable:
        return result(DetectorOutcome.UNKNOWN, reason="corpus_empty", metrics=base)
    if len(candidate) < params.min_candidate_shingles:
        return result(DetectorOutcome.UNKNOWN, reason="insufficient_candidate_shingles",
                      metrics=base)

    best: tuple[float, int, str] | None = None
    best_metrics: dict[str, object] = {}
    for entry in sorted(usable, key=lambda e: e.ref_id):
        shared = len(candidate & entry.shingles)
        containment = shared / len(candidate)
        coverage = shared / len(entry.shingles)
        score = max(containment, coverage)
        key = (score, shared, entry.ref_id)
        # highest score, then most shared shingles; ties keep the first ref_id
        if best is None or (key[0], key[1]) > (best[0], best[1]):
            best = key
            best_metrics = {
                "shared_shingles": shared,
                "containment": round(containment, 6),
                "coverage": round(coverage, 6),
                "jaccard": round(shared / len(candidate | entry.shingles), 6),
                "best_ref": entry.ref_id,
                "best_ref_shingles": len(entry.shingles),
            }
    assert best is not None
    score, shared, ref_id = best
    is_match = score >= params.match_min_score or shared >= params.match_min_shared_shingles
    return result(
        DetectorOutcome.MATCH if is_match else DetectorOutcome.NO_MATCH,
        score=round(score, 6),
        matched_ref=ref_id if is_match else None,
        metrics={**base, **best_metrics},
    )
