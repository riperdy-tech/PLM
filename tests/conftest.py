"""Fixtures shared by every test under ``tests/``.

The definitions live in ``tests/support/fixtures.py`` (kept next to the
helpers they build on); they are imported here so the whole tree can use
``mock_http``, ``frozen_clock``, ``seeded_rng`` and ``contract_registry``
without importing anything. Keep this list identical to
``tests/support/conftest.py`` and ``tests/support/fixtures.py``.
"""

from tests.support.fixtures import (
    contract_registry,
    frozen_clock,
    mock_http,
    seeded_rng,
)
from tests.support.platform_env import isolate_platform_env, platform_env

__all__ = [
    "contract_registry", "frozen_clock", "isolate_platform_env", "mock_http",
    "platform_env", "seeded_rng",
]
