"""Experiment lineage: the identity every produced artifact carries back to its arm (R9).

The learning loop only closes if every candidate, manifest, media package, video,
release, metric and reward can be traced to the experiment and arm that caused it. These
identifiers used to travel as loose fields that each hop defaulted on its own
(``exp_unknown``, ``arm_unknown``, ``arm_placeholder``), so a single dropped field
silently detached revenue from its arm. They now travel as one required object. No field
has a default, and an empty or placeholder value is refused.
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints

__all__ = ["PLACEHOLDER_IDS", "ExperimentLineage", "LineageId"]

PLACEHOLDER_IDS: Final[frozenset[str]] = frozenset(
    {
        "unknown",
        "placeholder",
        "exp_unknown",
        "fam_unknown",
        "arm_unknown",
        "gen_unknown",
        "dir_unknown",
        "pkg_unknown",
        "rm_unknown",
        "arm_placeholder",
        "art_unknown",
    }
)
"""Values that earlier code invented for a missing identifier. Never a real id."""


def _not_placeholder(value: str) -> str:
    if value.lower() in PLACEHOLDER_IDS:
        raise ValueError(f"{value!r} is a placeholder, not an identifier")
    return value


LineageId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_not_placeholder),
]
"""A required, non-empty, non-placeholder identifier."""


class ExperimentLineage(BaseModel):
    """The five identifiers that attribute an artifact to its experiment and arm."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: LineageId
    experiment_family_id: LineageId
    content_arm_id: LineageId
    content_genome_id: LineageId
    production_directive_id: LineageId
