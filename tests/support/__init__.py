"""Shared test harness for the PLM program (W0.9).

This package is the one place test infrastructure lives, so nine phases do not
invent nine versions of it. Nothing here is imported by production code.

Modules
-------
``tests.support.http``
    :class:`~tests.support.http.MockHTTP` — a scriptable ``httpx.MockTransport``
    for testing connectors (P1 YouTube, P7 YouTube, P8 distributor, P9
    connectors) without network access.
``tests.support.golden``
    :func:`~tests.support.golden.assert_golden` — compare produced artifacts
    against stored expectations, with a documented update path (P6 rendering).
``tests.support.clock``
    :func:`~tests.support.clock.frozen_time` and
    :func:`~tests.support.clock.seed` — freeze/advance time and pin RNGs.
``tests.support.contract``
    :func:`~tests.support.contract.assert_seam_payload` — the seam-contract
    check the template in ``tests/contract/`` builds on.

Fixtures built on these helpers live in :mod:`tests.support.fixtures`
(``mock_http``, ``frozen_clock``, ``seeded_rng``, ``contract_registry``); they
are re-exported tree-wide by ``tests/conftest.py``.
"""

from __future__ import annotations

from tests.support.clock import DEFAULT_SEED, DEFAULT_START, FrozenClock, frozen_time, seed
from tests.support.contract import REGISTRY_PATH, SeamRegistry, assert_seam_payload
from tests.support.golden import UPDATE_ENV_VAR, assert_golden, golden_path, update_enabled
from tests.support.http import MockHTTP, RecordedRequest
from tests.support.paths import REPO_ROOT, display_path

__all__ = [
    "DEFAULT_SEED",
    "DEFAULT_START",
    "REGISTRY_PATH",
    "REPO_ROOT",
    "UPDATE_ENV_VAR",
    "FrozenClock",
    "MockHTTP",
    "RecordedRequest",
    "SeamRegistry",
    "assert_golden",
    "assert_seam_payload",
    "display_path",
    "frozen_time",
    "golden_path",
    "seed",
    "update_enabled",
]
