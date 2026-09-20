"""Scaffold smoke tests: every package must be importable (W0.1)."""

import importlib

import pytest

PACKAGE_NAMES = [
    "plm_contracts",
    "plm_platform",
    "market_intelligence",
    "autonomous_ar",
    "experiments",
    "music_factory",
    "quality_gate",
    "media_factory",
    "youtube_publishing",
    "distribution",
    "analytics",
]


@pytest.mark.parametrize("name", PACKAGE_NAMES)
def test_package_is_importable(name: str) -> None:
    importlib.import_module(name)
