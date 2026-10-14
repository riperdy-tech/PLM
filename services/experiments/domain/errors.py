"""Typed refusals raised by P3 design, genome and validation code (P3 §81, AGENTS.md §3).

Every refusal carries a machine-readable :class:`~plm_contracts.ErrorShape` so the API
can return it unchanged. A refusal is never converted into a placeholder value.
"""

from __future__ import annotations

from collections.abc import Sequence

from plm_contracts import ErrorShape

__all__ = [
    "ExperimentDesignError",
    "ExperimentStateError",
    "GenomeBuildError",
    "LegacyExperimentError",
    "P3RefusalError",
]


class P3RefusalError(ValueError):
    """P3 refused to produce an artefact; ``error`` carries the reason code."""

    def __init__(self, code: str, message: str, *, field_paths: Sequence[str] = ()) -> None:
        self.error = ErrorShape(code=code, field_paths=list(field_paths), message=message)
        super().__init__(f"{code}: {message}")

    @property
    def code(self) -> str:
        return self.error.code


class GenomeBuildError(P3RefusalError):
    """A genome could not be built from the directive (unknown genre, bad template...)."""


class ExperimentDesignError(P3RefusalError):
    """An experiment could not be designed without inventing or duplicating treatments."""


class ExperimentStateError(P3RefusalError):
    """An experiment is not in a state that allows the requested step."""


class LegacyExperimentError(P3RefusalError):
    """A stored experiment predates the measurement plan / budget record and is refused."""
