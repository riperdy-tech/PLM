"""Pytest fixtures built on the ``tests.support`` helpers.

Fixtures are defined here and re-exported by name from ``tests/conftest.py``
(tree-wide) and ``tests/support/conftest.py`` (this subtree). Pytest does not
allow ``pytest_plugins`` in a non-root conftest, and the conftest itself must
be at a level that covers the tests, so the two lists are kept identical —
add a fixture here and to *both* conftest files.

Available:

* ``mock_http`` — a fresh :class:`~tests.support.http.MockHTTP`.
* ``frozen_clock`` — a :class:`~tests.support.clock.FrozenClock` stopped at
  ``tests.support.clock.DEFAULT_START`` for the whole test.
* ``seeded_rng`` — a deterministic ``random.Random(DEFAULT_SEED)``.
* ``contract_registry`` — the :class:`~tests.support.contract.SeamRegistry`.
"""

from __future__ import annotations

from collections.abc import Iterator
from random import Random

import pytest

from tests.support.clock import DEFAULT_SEED, DEFAULT_START, FrozenClock, frozen_time
from tests.support.contract import SeamRegistry
from tests.support.http import MockHTTP


@pytest.fixture
def mock_http() -> MockHTTP:
    """A fresh mock transport per test; enqueue outcomes, assert on the recording."""
    return MockHTTP()


@pytest.fixture
def frozen_clock() -> Iterator[FrozenClock]:
    """The clock stopped at ``DEFAULT_START``; advance it deliberately."""
    with frozen_time(DEFAULT_START) as clock:
        yield clock


@pytest.fixture
def seeded_rng() -> Random:
    """A locally seeded ``random.Random`` — call it twice, get the same stream."""
    return Random(DEFAULT_SEED)


@pytest.fixture
def contract_registry() -> SeamRegistry:
    """The frozen contract registry (skips only when the entry is required and absent)."""
    return SeamRegistry()


__all__ = ["contract_registry", "frozen_clock", "mock_http", "seeded_rng"]
