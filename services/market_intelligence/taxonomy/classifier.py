# services/market_intelligence/taxonomy/classifier.py
"""§15/§28/§29: classify with a cached, versioned, validated output.

`RuleBasedClassifier` is the deterministic default (and the only classifier the
mock-first build needs). An LLM-backed classifier implements the same protocol and
is blocked on provider credentials — the numeric layer never calls it directly, it
reads the persisted classification (§30).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from ..domain.enums import TaxonomyDimension as D
from ..normalization.canonical import normalize_text
from ..storage.repositories import insert_classification, load_classification
from .synonym_map import SYNONYMS, lookup_synonym
from .validators import (
    CLASSIFICATION_SCHEMA_VERSION,
    ClassificationDraft,
    classification_input_fingerprint,
    validate_classification,
)

__all__ = [
    "ClassificationInput", "Classifier", "RuleBasedClassifier", "classify_observation",
]


@dataclass(frozen=True, slots=True)
class ClassificationInput:
    title: str | None
    description: str | None


class Classifier(Protocol):
    name: str
    version: str

    def classify(self, text: ClassificationInput) -> ClassificationDraft: ...


class RuleBasedClassifier:
    """Deterministic keyword classifier over the synonym map.

    Confidence is a function of how much evidence the text carried — never a claim
    about correctness; a text with no hits gets 0.0 and an unmapped term list.
    """

    name = "rule_based"
    version = "1"

    def classify(self, text: ClassificationInput) -> ClassificationDraft:
        haystack = normalize_text(" ".join(filter(None, (text.title, text.description))))
        title_text = normalize_text(text.title or "")
        found: dict[D, list[str]] = {}
        unmatched: set[str] = set()
        for phrase, (dimension, value) in sorted(SYNONYMS.items(), key=lambda kv: -len(kv[0])):
            # A language is claimed only where the work declares it (its title):
            # prose like "Korean city vibes" describes content, not the language of
            # the track, and §15's example keeps `language` English-or-absent there.
            source = title_text if dimension is D.LANGUAGE else haystack
            if phrase in source:
                found.setdefault(dimension, []).append(value)
        for token in haystack.split():
            if len(token) >= 4 and token not in {p.split()[-1] for p in SYNONYMS} \
                    and lookup_synonym(token) is None and not any(
                        token in phrase for phrase in SYNONYMS
                    ):
                unmatched.add(token)
        dimensions = {dimension: tuple(dict.fromkeys(values))
                      for dimension, values in found.items()}
        hits = sum(len(values) for values in dimensions.values())
        confidence = min(0.9, 0.2 + 0.15 * hits) if hits else 0.0
        raw = {dimension.value: list(values) for dimension, values in dimensions.items()}
        raw["confidence"] = confidence
        raw["unmapped_terms"] = sorted(unmatched)
        return validate_classification(raw)


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
    fingerprint = classification_input_fingerprint(text.title, text.description)
    cached = load_classification(
        session, content_fingerprint=fingerprint, taxonomy_version=taxonomy_version
    )
    if cached is not None:
        return str(cached["id"]), False
    draft = classifier.classify(text)
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
