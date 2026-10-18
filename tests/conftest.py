"""Fixtures shared by every test under ``tests/``.

The definitions live in ``tests/support/fixtures.py`` (kept next to the
helpers they build on); they are imported here so the whole tree can use
``mock_http``, ``frozen_clock``, ``seeded_rng`` and ``contract_registry``
without importing anything. Keep this list identical to
``tests/support/conftest.py`` and ``tests/support/fixtures.py``.
"""

import pytest

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


FULL_CHAIN_MARKER = "full_chain"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """The Gate R5 full chain (``full_chain``, also ``integration``) runs only when the
    marker expression names it (``pytest -m full_chain``, its own CI job): it takes about
    40 minutes at production content scale, so the normal ``-m integration`` run skips it
    with this reason instead of silently growing by that much."""
    if FULL_CHAIN_MARKER in (config.getoption("markexpr", "") or ""):
        return
    skip = pytest.mark.skip(reason="Gate R5 full chain: select it with -m full_chain "
                                   "(its own CI job; about 40 min)")
    for item in items:
        if FULL_CHAIN_MARKER in item.keywords:
            item.add_marker(skip)
