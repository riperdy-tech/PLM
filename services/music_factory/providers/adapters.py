"""Provider adapters conforming to MusicProviderProtocol (§4.4, §111)."""

from __future__ import annotations

import os
from typing import Any

from plm_contracts import ProviderMode
from plm_contracts.seams.c4 import ProviderLicenseTerms

from ..domain.enums import AttemptStatus
from ..domain.models import GenerationRequest, GenerationResponse
from .mock_provider import MockMusicProvider
from .protocol import MusicProviderProtocol

__all__ = [
    "ElevenLabsAdapter",
    "MockAdapter",
    "get_provider_adapter",
]


class MockAdapter(MockMusicProvider):
    """Adapter for in-memory deterministic mock generation."""

    def __init__(self, *, provider_id: str = "mock", model_id: str = "mock-v1") -> None:
        super().__init__(provider_id=provider_id, model_id=model_id)




class ElevenLabsAdapter:
    """Adapter for ElevenLabs music generation service (a paid external call: REAL)."""

    provider_mode = ProviderMode.REAL

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
        """No verified billing endpoint is implemented for ElevenLabs.

        An API key only proves configuration, not any usable quota. Never
        fabricate 1,000 credits or classify an unknown balance as exhausted.
        """
        return {
            "provider_id": self._provider_id,
            "configured": bool(self._api_key),
            "credits_remaining": None,
            "quota_exhausted": None,
            "quota_known": False,
            "quota_status": "unknown" if self._api_key else "not_configured",
        }

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        if not self._api_key:
            return GenerationResponse(
                provider_job_id="",
                status=AttemptStatus.FAILED,
                error_message="ElevenLabs API key not configured (ELEVENLABS_API_KEY missing)",
                confirmed_unsubmitted=True,  # No external request.
            )

        return GenerationResponse(
            provider_job_id="",
            status=AttemptStatus.FAILED,
            error_message="Live ElevenLabs API calls disabled in hermetic test mode",
            confirmed_unsubmitted=True,  # Implementation never issues a provider request.
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
        # The automated client for Suno's private web API was removed (2026-11-26):
        # it used a browser session cookie and took audio from the video stream to get
        # around the plan's download limit. Songs are made by hand (suno_manual).
        raise ValueError("the automated Suno client was removed; use suno_manual "
                         "(songs the owner makes on suno.com)")
    elif clean_id == "suno_manual":
        from .suno_manual import SunoManualAdapter, load_suno_manual_settings

        return SunoManualAdapter(load_suno_manual_settings())
    elif clean_id == "flow_music_manual":
        from .flow_music_manual import FlowMusicManualAdapter, load_flow_music_manual_settings

        return FlowMusicManualAdapter(load_flow_music_manual_settings())
    elif clean_id in ("elevenlabs", "eleven_labs"):
        return ElevenLabsAdapter(
            provider_id="elevenlabs",
            model_id=model_id or "music-v1",
            api_key=api_key,
        )
    else:
        raise ValueError(f"Unknown music provider: {provider_id!r}")
