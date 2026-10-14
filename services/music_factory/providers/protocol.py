"""Protocol definition for AI Music Generation Providers (§4.4, §111)."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from plm_contracts.seams.c4 import ProviderLicenseTerms

from ..domain.models import GenerationRequest, GenerationResponse

__all__ = ["MusicProviderProtocol"]


@runtime_checkable
class MusicProviderProtocol(Protocol):
    """Protocol that all music generation adapters must satisfy."""

    @property
    def provider_id(self) -> str:
        """Unique identifier of the provider (e.g. 'suno', 'elevenlabs', 'mock')."""
        ...

    @property
    def model_id(self) -> str:
        """Model or checkpoint identifier used by this provider instance."""
        ...

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Execute or poll music generation deterministically or via remote provider."""
        ...

    def check_health(self) -> bool:
        """Return True if provider endpoint is healthy and reachable."""
        ...

    def get_quota_status(self) -> dict[str, Any]:
        """Return current quota/credit usage metadata."""
        ...

    def license_terms(self) -> ProviderLicenseTerms | None:
        """The license terms this provider's output is produced under (R6, ADR-0027).

        ``None`` means no terms are on file: P4 records no rights snapshot and P5
        treats commercial use as unknown. An adapter never guesses its terms."""
        ...
