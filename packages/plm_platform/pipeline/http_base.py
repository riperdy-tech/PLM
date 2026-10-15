"""What every HTTP step class shares: the phase clients and the deployment configuration."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from ..temporal import Phase, ValidationFailure
from .config import PipelineConfig
from .phase_client import PhaseClient

__all__ = ["HttpStepsBase", "instant"]


def instant(value: Any, where: str) -> datetime:
    """A timezone-aware ISO 8601 instant, or a refusal (a naive time is ambiguous)."""
    if not isinstance(value, str):
        raise ValidationFailure(f"{where}: {value!r} is not an ISO 8601 instant")
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise ValidationFailure(f"{where}: {value!r} is not an ISO 8601 instant") from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValidationFailure(f"{where}: {value!r} carries no timezone")
    return moment


class HttpStepsBase:
    def __init__(self, clients: Mapping[Phase, PhaseClient], *,
                 config: PipelineConfig | None = None) -> None:
        self._clients = dict(clients)
        self._config = config if config is not None else PipelineConfig()

    def _client(self, phase: Phase) -> PhaseClient:
        client = self._clients.get(phase)
        if client is None:
            raise ValidationFailure(f"no client configured for {phase.name}")
        return client
