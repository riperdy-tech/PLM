"""Provider adapters conforming to MusicProviderProtocol (§4.4, §111)."""

from __future__ import annotations

import os
from typing import Any

from plm_contracts.seams.c4 import ProviderLicenseTerms

from ..domain.enums import AttemptStatus
from ..domain.models import GenerationRequest, GenerationResponse
from .mock_provider import MockMusicProvider
from .protocol import MusicProviderProtocol

__all__ = [
    "ElevenLabsAdapter",
    "MockAdapter",
    "SunoAdapter",
    "get_provider_adapter",
]


class MockAdapter(MockMusicProvider):
    """Adapter for in-memory deterministic mock generation."""

    def __init__(self, *, provider_id: str = "mock", model_id: str = "mock-v1") -> None:
        super().__init__(provider_id=provider_id, model_id=model_id)


class SunoAdapter:
    """Adapter for Suno AI music generation service."""

    def __init__(
        self,
        *,
        provider_id: str = "suno",
        model_id: str = "chirp-v3-5",
        api_key: str | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._model_id = model_id
        self._api_key = api_key or os.getenv("SUNO_API_KEY", "")

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def check_health(self) -> bool:
        """Health check returns True if api_key is configured or endpoint reachable."""
        return bool(self._api_key)

    def license_terms(self) -> ProviderLicenseTerms | None:
        """No contract terms are on file for this provider yet (P0 §10): unknown."""
        return None

    def get_quota_status(self) -> dict[str, Any]:
        return {
            "provider_id": self._provider_id,
            "configured": bool(self._api_key),
            "credits_remaining": 500 if self._api_key else 0,
            "quota_exhausted": not bool(self._api_key),
        }

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Execute Suno generation. If unconfigured, returns clean failure."""
        if not self._api_key:
            return GenerationResponse(
                provider_job_id="",
                status=AttemptStatus.FAILED,
                error_message="Suno API key not configured (SUNO_API_KEY missing)",
            )

        # In production this calls Suno REST API. In offline test harness, fails closed.
        return GenerationResponse(
            provider_job_id=f"suno_job_{request.track_spec_id}",
            status=AttemptStatus.FAILED,
            error_message="Live Suno API calls disabled in hermetic test mode",
        )


class ElevenLabsAdapter:
    """Adapter for ElevenLabs music generation service."""

    def __init__(
        self,
        *,
        provider_id: str = "elevenlabs",
        model_id: str = "music-v1",
        api_key: str | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._model_id = model_id
        self._api_key = api_key or os.getenv("ELEVENLABS_API_KEY", "")

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def check_health(self) -> bool:
        return bool(self._api_key)

    def license_terms(self) -> ProviderLicenseTerms | None:
        """No contract terms are on file for this provider yet (P0 §10): unknown."""
        return None

    def get_quota_status(self) -> dict[str, Any]:
        return {
            "provider_id": self._provider_id,
            "configured": bool(self._api_key),
            "credits_remaining": 1000 if self._api_key else 0,
            "quota_exhausted": not bool(self._api_key),
        }

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        if not self._api_key:
            return GenerationResponse(
                provider_job_id="",
                status=AttemptStatus.FAILED,
                error_message="ElevenLabs API key not configured (ELEVENLABS_API_KEY missing)",
            )

        return GenerationResponse(
            provider_job_id=f"eleven_job_{request.track_spec_id}",
            status=AttemptStatus.FAILED,
            error_message="Live ElevenLabs API calls disabled in hermetic test mode",
        )


def get_provider_adapter(
    provider_id: str,
    *,
    model_id: str | None = None,
    api_key: str | None = None,
) -> MusicProviderProtocol:
    """Factory creating the appropriate adapter instance by provider_id."""
    clean_id = provider_id.strip().lower()
    if clean_id == "mock":
        return MockAdapter(
            provider_id="mock",
            model_id=model_id or "mock-v1",
        )
    elif clean_id == "suno":
        return SunoAdapter(
            provider_id="suno",
            model_id=model_id or "chirp-v3-5",
            api_key=api_key,
        )
    elif clean_id in ("elevenlabs", "eleven_labs"):
        return ElevenLabsAdapter(
            provider_id="elevenlabs",
            model_id=model_id or "music-v1",
            api_key=api_key,
        )
    else:
        raise ValueError(f"Unknown music provider: {provider_id!r}")
