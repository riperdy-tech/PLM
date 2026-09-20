# services/market_intelligence/taxonomy/validators.py
"""§15: "The application must validate the JSON against a schema before writing it
to the database." Nothing below trusts a model."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from plm_contracts import ErrorShape

from ..domain.enums import TaxonomyDimension as D
from ..normalization.canonical import normalize_text
from .ontology import DIMENSION_SPECS

__all__ = [
    "CLASSIFICATION_SCHEMA_VERSION", "ClassificationDraft", "ClassificationSchemaError",
    "classification_input_fingerprint", "validate_classification",
]

CLASSIFICATION_SCHEMA_VERSION: Final[str] = "p1-classification-v1"

_KEY_ALIASES: Final[dict[str, D]] = {
    "title_archetypes": D.TITLE_ARCHETYPE,
    "title_archetype": D.TITLE_ARCHETYPE,
    "use_cases": D.USE_CASE,
    "visual_worlds": D.VISUAL_WORLD,
}
_ALLOWED_META: Final[frozenset[str]] = frozenset({"confidence", "unmapped_terms"})


class ClassificationSchemaError(ValueError):
    """The payload does not satisfy §15/§29's schema; carries a machine-readable shape."""

    def __init__(self, error: ErrorShape) -> None:
        super().__init__(error.message)
        self.error = error


@dataclass(frozen=True, slots=True)
class ClassificationDraft:
    dimensions: Mapping[D, tuple[str, ...]]
    confidence: float
    unmapped_terms: tuple[str, ...] = ()
    schema_version: str = CLASSIFICATION_SCHEMA_VERSION
    # The content cache key the classifier keyed this draft on (P1.9's arms need it
    # to carry the observation ids their members came from); empty when the caller
    # validated raw model output and has no input text.
    content_fingerprint: str = ""


def validate_classification(raw: Mapping[str, Any]) -> ClassificationDraft:
    """Validate one classifier output; unknown keys are refused, not ignored.

    A dimension present but empty (`[]`) is treated as *absent*; a value outside the
    ontology is a failure, because §14.3 forbids silent nearest-label mapping. An
    output that names no dimension *and* records no unmapped term is not a
    classification at all and is refused too — missing data may not pass as a result.
    """
    field_paths: list[str] = []
    message_holder: list[str] = []
    confidence = raw.get("confidence", 0.0)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) \
            or not 0.0 <= float(confidence) <= 1.0:
        field_paths.append("confidence")
        message_holder.append("confidence must be a number within 0–1")
    unmapped = raw.get("unmapped_terms", [])
    if not isinstance(unmapped, Sequence) or isinstance(unmapped, str) or not all(
        isinstance(term, str) for term in unmapped
    ):
        field_paths.append("unmapped_terms")
        message_holder.append("unmapped_terms must be a list of strings")

    dimensions: dict[D, tuple[str, ...]] = {}
    for key, value in raw.items():
        if key in _ALLOWED_META:
            continue
        dimension = _dimension_for(key)
        if dimension is None:
            field_paths.append(key)
            message_holder.append(f"unknown dimension key {key!r}")
            continue
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, Sequence) or isinstance(values, str):
            field_paths.append(key)
            message_holder.append(f"{key} must be a string or list of strings")
            continue
        accepted: list[str] = []
        for index, item in enumerate(values):
            if not isinstance(item, str):
                field_paths.append(f"{key}[{index}]")
                message_holder.append(f"{key}[{index}] must be a string")
                continue
            token = normalize_text(item).replace(" ", "_")
            if token not in DIMENSION_SPECS[dimension].values:
                field_paths.append(f"{key}[{index}]")
                message_holder.append(f"{item!r} is not a controlled value for {dimension.value}")
                continue
            accepted.append(token)
        if not DIMENSION_SPECS[dimension].multi and len(accepted) > 1:
            field_paths.append(key)
            message_holder.append(f"{key} accepts a single controlled value")
        if accepted:
            dimensions[dimension] = tuple(dict.fromkeys(accepted))

    if field_paths:
        raise ClassificationSchemaError(ErrorShape(
            code="classification_schema_invalid",
            field_paths=field_paths,
            message="; ".join(message_holder) or "classification failed validation",
        ))
    if not dimensions and not unmapped:
        raise ClassificationSchemaError(ErrorShape(
            code="classification_schema_invalid",
            field_paths=["dimensions"],
            message="a classification must name a dimension or record its unmapped terms",
        ))
    return ClassificationDraft(dimensions=dimensions, confidence=float(confidence),
                               unmapped_terms=tuple(unmapped))


def classification_input_fingerprint(title: str | None, description: str | None) -> str:
    """§28's cache key input: content, not observation id."""
    payload = json.dumps(
        {"title": title or "", "description": description or ""}, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _dimension_for(key: str) -> D | None:
    if key in _KEY_ALIASES:
        return _KEY_ALIASES[key]
    try:
        return D(key)
    except ValueError:
        return None
