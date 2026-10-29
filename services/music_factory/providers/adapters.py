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
    "SunoAdapter",
    "get_provider_adapter",
]


class MockAdapter(MockMusicProvider):
    """Adapter for in-memory deterministic mock generation."""

    def __init__(self, *, provider_id: str = "mock", model_id: str = "mock-v1") -> None:
        super().__init__(provider_id=provider_id, model_id=model_id)


from .suno_client import SunoClient, SunoClientError


class SunoAdapter:
    """Adapter for Suno AI music generation service (a paid external call: REAL)."""

    provider_mode = ProviderMode.REAL

    def __init__(
        self,
        *,
        provider_id: str = "suno",
        model_id: str = "chirp-v3-5",
        api_key: str | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._model_id = model_id
        if api_key is None:
            api_key = os.getenv("SUNO_API_KEY", "") or os.getenv("MUSIC_PROVIDER_API_KEY", "")
        self._api_key = api_key or ""
        self._client = SunoClient(token=self._api_key)


    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def check_health(self) -> bool:
        """Health check returns True if api_key is configured and endpoint reachable."""
        return self._client.check_health()

    def license_terms(self) -> ProviderLicenseTerms | None:
        """A token proves API configuration, not account tier or commercial rights.

        The provider's identity, subscription and applicable generation-time
        terms have not been independently verified; P5 must hold the output as
        commercially unknown until an auditable entitlement flow exists.
        """
        return None

    def get_quota_status(self) -> dict[str, Any]:
        """Read observed quota only: absent or failing billing never invents credits."""
        unknown = {
            "provider_id": self._provider_id,
            "configured": bool(self._api_key),
            "credits_remaining": None,
            "quota_exhausted": None,
            "quota_known": False,
            "quota_status": "unknown" if self._api_key else "not_configured",
        }
        if not self._api_key:
            return unknown
        try:
            info = self._client.get_billing_info()
            raw = info.get("total_credits_left", info.get("credits_left"))
            if isinstance(raw, bool) or raw is None:
                return unknown
            if not isinstance(raw, (int, str)) or not str(raw).isdigit():
                return unknown
            credits_left = int(raw)
            return {
                "provider_id": self._provider_id,
                "configured": True,
                "credits_remaining": credits_left,
                "quota_exhausted": credits_left <= 0,
                "quota_known": True,
                "quota_status": "observed",
            }
        except (SunoClientError, RuntimeError, TypeError, ValueError, AttributeError):
            return unknown

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Execute live Suno generation and return standard WAV audio."""
        if not self._api_key:
            return GenerationResponse(
                provider_job_id="",
                status=AttemptStatus.FAILED,
                error_message="Suno API key not configured (SUNO_API_KEY missing)",
                confirmed_unsubmitted=True,  # No HTTP request was made.
            )

        # Keep only the provider's real receipt, if one was observed.
        # A failed submission may have been accepted even when no ID arrived.
        observed_clip_id = ""
        try:
            instrumental = request.vocal_mode == "instrumental"
            tags_parts = [request.genre]
            if request.style_tags:
                tags_parts.extend(request.style_tags)
            if request.bpm:
                tags_parts.append(f"{request.bpm}bpm")
            tags = " ".join(tags_parts)
            title = f"PLM_{request.track_spec_id[:8]}"

            clip_ids = self._client.submit_generation(
                prompt=request.prompt,
                tags=tags,
                title=title,
                instrumental=instrumental,
                model=self._model_id,
            )
            if not clip_ids:
                return GenerationResponse(
                    provider_job_id="",
                    status=AttemptStatus.FAILED,
                    error_message="Suno returned no clip IDs upon submission",
                )

            observed_clip_id = clip_ids[0]
            clip = self._client.poll_clip(observed_clip_id)
            wav_bytes, duration = self._client.download_clip_wav(observed_clip_id)

            return GenerationResponse(
                provider_job_id=observed_clip_id,
                status=AttemptStatus.SUCCEEDED,
                audio_data=wav_bytes,
                mime_type="audio/wav",
                actual_duration_seconds=duration,
                # The clip response does not state a billable credit usage.
                # Do not fabricate 10 or substitute zero for UNKNOWN.
                cost_credits=None,
                # Only the provider-reported model version is evidence.
                # A requested model name is not proof of what ran.
                model_version=clip.model_name.strip() or None,
                # A prompt is not the lyrics actually sung by the model.
                # This client supplies no generated lyric transcript:
                # P5 must treat vocal lyrics as UNKNOWN, not screened.
                lyrics_text=None,
            )
        except SunoClientError as exc:
            return GenerationResponse(
                # Never invent a provider job identity from the local spec.
                # A successful submit can have failed while polling or
                # downloading; keep its actual clip ID when observed.
                provider_job_id=observed_clip_id,
                status=AttemptStatus.FAILED,
                error_message=str(exc),
                # Missing ID is ambiguous, not a confirmed no-charge refusal.
                confirmed_unsubmitted=False,
            )



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
