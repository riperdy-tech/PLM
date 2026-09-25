"""Tests for provider registry and capability discovery (Task P4.3)."""

from __future__ import annotations

import pytest

from services.music_factory.domain.models import ProviderCapabilities
from services.music_factory.providers.registry import (
    get_capabilities,
    list_registered_providers,
    register_provider,
    select_provider_and_model,
)


def test_default_registry_contents() -> None:
    providers = list_registered_providers()
    assert len(providers) >= 4
    provider_ids = {p.provider_id for p in providers}
    assert "mock" in provider_ids
    assert "suno" in provider_ids
    assert "elevenlabs" in provider_ids
    assert "stable_audio" in provider_ids


def test_capability_lookup() -> None:
    cap = get_capabilities("mock", "mock-v1")
    assert cap is not None
    assert cap.supports_stems is True
    assert cap.max_duration_seconds == 600

    missing = get_capabilities("nonexistent", "v1")
    assert missing is None


def test_provider_selection_matching() -> None:
    # 1. R&B vocal 180s -> matching Suno or Mock
    provider, model = select_provider_and_model(
        genre="rnb",
        vocal_mode="vocal",
        requested_duration=180,
        allowed_providers=["suno", "mock"],
    )
    assert provider in {"suno", "mock"}

    # 2. Electronic instrumental -> stable_audio
    provider, model = select_provider_and_model(
        genre="electronic",
        vocal_mode="instrumental",
        requested_duration=120,
        allowed_providers=["stable_audio"],
    )
    assert provider == "stable_audio"
    assert model == "stable-audio-2"

    # 3. Request exceeding duration limit of specific provider raises ValueError
    with pytest.raises(ValueError, match="No registered provider satisfies"):
        select_provider_and_model(
            genre="electronic",
            vocal_mode="instrumental",
            requested_duration=500,  # stable_audio max is 180
            allowed_providers=["stable_audio"],
        )


def test_custom_provider_registration() -> None:
    custom_cap = ProviderCapabilities(
        provider_id="custom_ai",
        model_id="custom-v1",
        max_duration_seconds=360,
        supports_stems=True,
        supports_lyrics=True,
        supports_instrumental=True,
        supported_genres=("jazz", "classical"),
        supported_vocal_modes=("instrumental",),
    )
    register_provider(custom_cap)

    p, m = select_provider_and_model(
        genre="jazz",
        vocal_mode="instrumental",
        requested_duration=200,
        allowed_providers=["custom_ai"],
    )
    assert p == "custom_ai"
    assert m == "custom-v1"
