"""Hermetic platform settings for the unit tier.

CI runs the unit tier with no ``.env`` and no service variables ("the unit tests inject
their settings"). A developer machine usually has a ``.env``, so a test that silently
read it passed locally and failed in CI. ``isolate_platform_env`` (autouse for every
test not marked ``integration``) hides the ``.env`` file and the platform variables, so
local runs see exactly what CI sees. A test that exercises code reading platform settings
opts in with the ``platform_env`` fixture, which sets the documented ``.env.example``
values explicitly.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from tests.support.paths import REPO_ROOT

PLATFORM_VARS: Final[tuple[str, ...]] = (
    "ENV", "LOG_LEVEL", "POSTGRES_PASSWORD", "DATABASE_URL", "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD", "MINIO_ENDPOINT", "MINIO_BUCKET", "TEMPORAL_HOST",
)


def example_env() -> dict[str, str]:
    """The documented local values from ``.env.example``."""
    values: dict[str, str] = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _, value = stripped.partition("=")
            values[key.strip()] = value.strip()
    return values


@pytest.fixture(autouse=True)
def isolate_platform_env(request: pytest.FixtureRequest,
                         monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    if request.node.get_closest_marker("integration") is not None:
        yield
        return
    from plm_platform import config

    monkeypatch.setattr(config, "_DEFAULT_DOTENV", Path("__no_dotenv_in_unit_tests__"))
    for name in PLATFORM_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def platform_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Opt in: set the ``.env.example`` values for code that loads platform settings."""
    values = example_env()
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values
