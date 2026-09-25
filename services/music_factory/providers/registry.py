"""Provider registry and capability discovery for AI music generation (§4, §65)."""

from __future__ import annotations

from ..domain.models import ProviderCapabilities

__all__ = [
    "DEFAULT_PROVIDER_CAPABILITIES",
    "get_capabilities",
    "list_registered_providers",
    "register_provider",
    "select_provider_and_model",
]

DEFAULT_PROVIDER_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "mock:mock-v1": ProviderCapabilities(
        provider_id="mock",
        model_id="mock-v1",
        max_duration_seconds=600,
        supports_stems=True,
        supports_lyrics=True,
        supports_instrumental=True,
        supported_genres=("rnb", "pop", "hiphop", "electronic", "lofi", "ambient", "jazz", "rock"),
        supported_vocal_modes=("vocal", "instrumental", "mixed"),
    ),
    "suno:chirp-v3-5": ProviderCapabilities(
        provider_id="suno",
        model_id="chirp-v3-5",
        max_duration_seconds=240,
        supports_stems=True,
        supports_lyrics=True,
        supports_instrumental=True,
        supported_genres=("rnb", "pop", "hiphop", "electronic", "rock", "country"),
        supported_vocal_modes=("vocal", "instrumental", "mixed"),
    ),
    "elevenlabs:music-v1": ProviderCapabilities(
        provider_id="elevenlabs",
        model_id="music-v1",
        max_duration_seconds=180,
        supports_stems=False,
        supports_lyrics=True,
        supports_instrumental=True,
        supported_genres=("rnb", "pop", "electronic", "ambient"),
        supported_vocal_modes=("vocal", "instrumental"),
    ),
    "stable_audio:stable-audio-2": ProviderCapabilities(
        provider_id="stable_audio",
        model_id="stable-audio-2",
        max_duration_seconds=180,
        supports_stems=False,
        supports_lyrics=False,
        supports_instrumental=True,
        supported_genres=("electronic", "ambient", "lofi", "soundtrack"),
        supported_vocal_modes=("instrumental",),
    ),
}

_REGISTRY: dict[str, ProviderCapabilities] = dict(DEFAULT_PROVIDER_CAPABILITIES)


def register_provider(capabilities: ProviderCapabilities) -> None:
    """Register or override a provider/model capabilities entry."""
    key = f"{capabilities.provider_id}:{capabilities.model_id}"
    _REGISTRY[key] = capabilities


def get_capabilities(provider_id: str, model_id: str) -> ProviderCapabilities | None:
    """Look up capabilities for a specific provider and model."""
    key = f"{provider_id}:{model_id}"
    return _REGISTRY.get(key)


def list_registered_providers() -> list[ProviderCapabilities]:
    """List all registered provider capability profiles."""
    return list(_REGISTRY.values())


def select_provider_and_model(
    *,
    genre: str,
    vocal_mode: str,
    requested_duration: int,
    allowed_providers: tuple[str, ...] | list[str] | None = None,
) -> tuple[str, str]:
    """Select the first matching registered provider and model satisfying requirements."""
    allowed = set(allowed_providers) if allowed_providers else None

    for cap in _REGISTRY.values():
        if allowed is not None and cap.provider_id not in allowed:
            continue
        if requested_duration > cap.max_duration_seconds:
            continue
        if vocal_mode not in cap.supported_vocal_modes:
            continue
        if genre and cap.supported_genres and genre not in cap.supported_genres:
            continue
        return cap.provider_id, cap.model_id

    # Fallback to mock if allowed or if no match
    if allowed is None or "mock" in allowed:
        return "mock", "mock-v1"

    raise ValueError(
        f"No registered provider satisfies genre='{genre}', vocal_mode='{vocal_mode}', "
        f"duration={requested_duration}, allowed={allowed_providers}"
    )
