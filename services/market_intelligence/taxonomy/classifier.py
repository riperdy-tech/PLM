# services/market_intelligence/taxonomy/classifier.py
"""§15/§28/§29: classify with a cached, versioned, validated output.

`RuleBasedClassifier` is the deterministic default (and the only classifier the
mock-first build needs). An LLM-backed classifier implements the same protocol and
is blocked on provider credentials — the numeric layer never calls it directly, it
reads the persisted classification (§30).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Final, Protocol

from sqlalchemy.orm import Session

from ..domain.enums import TaxonomyDimension as D
from ..normalization.canonical import normalize_text
from ..storage.repositories import insert_classification, load_classification
from .ontology import DIMENSION_SPECS, SUBGENRE_PARENT_GENRE
from .synonym_map import SYNONYMS, lookup_synonym
from .validators import (
    CLASSIFICATION_SCHEMA_VERSION,
    ClassificationDraft,
    classification_input_fingerprint,
    measured_duration_bucket,
    validate_classification,
)

__all__ = [
    "ClassificationInput", "Classifier", "RuleBasedClassifier", "classify_observation",
]


@dataclass(frozen=True, slots=True)
class ClassificationInput:
    title: str | None
    description: str | None
    video_duration_seconds: float | None = None


class Classifier(Protocol):
    name: str
    version: str

    def classify(self, text: ClassificationInput) -> ClassificationDraft: ...


class RuleBasedClassifier:
    """Deterministic keyword classifier over the synonym map.

    Phrases match whole words only (an optional plural `s` aside), so `chill` never
    fires inside `chillstep` nor `sleep` inside `sleepless`. Where two phrases of one
    dimension cover the same words, the longer one is the reading (`no vocals` is
    instrumental, not vocal). A single-valued dimension that still names two values
    keeps one — title over description, then the longer phrase, then the earlier
    one — and records each dropped value as `conflict:<dimension>:<value>` among the
    unmapped terms, so the choice stays auditable instead of failing the whole text.

    Confidence is a function of how much evidence the text carried — never a claim
    about correctness; a text with no hits gets 0.0 and an unmapped term list.
    """

    name = "rule_based"
    version = "3-word-boundary"

    def classify(self, text: ClassificationInput) -> ClassificationDraft:
        haystack = normalize_text(" ".join(filter(None, (text.title, text.description))))
        title_text = normalize_text(text.title or "")
        description_text = normalize_text(text.description or "")
        matches: list[_Match] = []
        for phrase, dimension, value, pattern in _PATTERNS:
            # Playlist/video length is measured, never inferred from a label
            # in a title, description, or fake numeric claim (ADR-0042).
            if dimension is D.DURATION_BUCKET:
                continue
            # A language is claimed only where the work declares it (its title):
            # prose like "Korean city vibes" describes content, not the language of
            # the track, and §15's example keeps `language` English-or-absent there.
            sources = (title_text,) if dimension is D.LANGUAGE else (title_text, description_text)
            for rank, source in enumerate(sources):
                matches.extend(_Match(dimension, value, rank, hit.start(), hit.end())
                               for hit in pattern.finditer(source))
        kept = [match for match in matches if not any(match.inside(other) for other in matches)]
        found: dict[D, list[str]] = {}
        for match in kept:
            found.setdefault(match.dimension, []).append(match.value)
        conflicts: set[str] = set()
        for dimension, values in found.items():
            if DIMENSION_SPECS[dimension].multi or len(set(values)) == 1:
                continue
            winner = min((match for match in kept if match.dimension is dimension),
                         key=lambda match: (match.rank, match.start - match.end, match.start))
            conflicts.update(f"conflict:{dimension.value}:{value}"
                             for value in values if value != winner.value)
            found[dimension] = [winner.value]
        hits = sum(len(dict.fromkeys(values)) for values in found.values())
        # A sub-style implies its genre; a genre the text names is never overridden.
        parent = SUBGENRE_PARENT_GENRE.get(found.get(D.SUBGENRE, [""])[0])
        if parent is not None and D.GENRE not in found:
            found[D.GENRE] = [parent]
        duration_bucket = measured_duration_bucket(text.video_duration_seconds)
        if duration_bucket is not None:
            found[D.DURATION_BUCKET] = [duration_bucket]
            hits += 1
        unmatched: set[str] = set(conflicts)
        for token in haystack.split():
            if len(token) >= 4 and token not in {p.split()[-1] for p in SYNONYMS} \
                    and lookup_synonym(token) is None and not any(
                        token in phrase for phrase in SYNONYMS
                    ):
                unmatched.add(token)
        dimensions = {dimension: tuple(dict.fromkeys(values))
                      for dimension, values in found.items()}
        confidence = min(0.9, 0.2 + 0.15 * hits) if hits else 0.0
        raw: dict[str, Any] = {dimension.value: list(values)
                               for dimension, values in dimensions.items()}
        raw["confidence"] = confidence
        raw["unmapped_terms"] = sorted(unmatched)
        return validate_classification(raw)


@dataclass(frozen=True, slots=True)
class _Match:
    dimension: D
    value: str
    rank: int  # 0 = title, 1 = description
    start: int
    end: int

    def inside(self, other: _Match) -> bool:
        """True when a longer phrase of the same dimension covers these words."""
        return (other.dimension is self.dimension and other.rank == self.rank
                and other.start <= self.start and self.end <= other.end
                and other.end - other.start > self.end - self.start)


# `&` and `-` are word characters, as in `normalize_text`: `r&b` and `lo-fi` are
# terms, and `vocal` does not fire inside `vocal-free`. Longest phrase first, so a
# multi-valued dimension lists its values in the same order as before word
# boundaries existed (arm identity reads the first use case).
_PATTERNS: Final[tuple[tuple[str, D, str, re.Pattern[str]], ...]] = tuple(
    (phrase, dimension, value,
     re.compile(rf"(?<![\w&-]){re.escape(normalize_text(phrase))}s?(?![\w&-])"))
    for phrase, (dimension, value) in sorted(SYNONYMS.items(), key=lambda kv: -len(kv[0]))
)


def classify_observation(
    session: Session,
    *,
    entity_id: str | None,
    observation_id: str | None,
    text: ClassificationInput,
    classifier: Classifier,
    taxonomy_version: str,
    prompt_version: str = "p1-classifier-prompt-v1",
    schema_version: str = CLASSIFICATION_SCHEMA_VERSION,
) -> tuple[str, bool]:
    """§28 cache first, §29 versions on every row; returns (classification_id, created)."""
    fingerprint = classification_input_fingerprint(
        text.title, text.description, video_duration_seconds=text.video_duration_seconds,
    )
    cached = load_classification(
        session, content_fingerprint=fingerprint, taxonomy_version=taxonomy_version
    )
    if cached is not None:
        return str(cached["id"]), False
    draft = replace(classifier.classify(text), content_fingerprint=fingerprint)
    classification_id = insert_classification(
        session, observation_id=observation_id, entity_id=entity_id,
        content_fingerprint=fingerprint, taxonomy_version=taxonomy_version,
        classifier_model=classifier.name, classifier_version=classifier.version,
        classification_prompt_version=prompt_version, classification_schema_version=schema_version,
        dimensions={dimension.value: list(values)
                    for dimension, values in draft.dimensions.items()},
        confidence=draft.confidence, unmapped_terms=list(draft.unmapped_terms),
    )
    return classification_id, True
