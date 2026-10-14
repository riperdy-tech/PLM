"""Seam C4 consumer (P5 §7, §83, R6, ADR-0025, ADR-0027).

Parses ``music_candidate_ready_for_p5`` with the shared
:class:`~plm_contracts.seams.c4.CandidateReadyPayload`. P5 never imports P4's code; the
seam model is the only thing the two services share.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from plm_contracts import ErrorShape
from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c4 import CandidateReadyPayload

__all__ = ["CandidateEventRejected", "consume_c4_event"]


class CandidateEventRejected(ValueError):
    """A C4 event was refused; ``error`` carries the reason code and fields."""

    def __init__(self, error: ErrorShape) -> None:
        self.error = error
        super().__init__(f"{error.code}: {error.message}")


def consume_c4_event(raw_event: Mapping[str, Any]) -> CandidateReadyPayload:
    """Parse an incoming C4 event or refuse it with a reason code."""
    try:
        return parse_seam(CandidateReadyPayload, raw_event)
    except SeamRejection as rejection:
        raise CandidateEventRejected(rejection.error) from rejection
