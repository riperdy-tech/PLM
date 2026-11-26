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
from tests.support.sqlite_lengths import enforce_varchar_lengths

enforce_varchar_lengths()

__all__ = [
    "contract_registry", "frozen_clock", "isolate_platform_env", "mock_http",
    "platform_env", "seeded_rng",
]


FULL_CHAIN_MARKER = "full_chain"


INTEGRATION_MARKER = "integration"
ISOLATED_STACK_VAR = "PLM_INTEGRATION_ISOLATED_STACK"


def _isolated_stack_refusal() -> str | None:
    """Why integration tests may not run here (``None``: they may).

    Integration tests write to the database and Temporal named by the environment or the
    checkout's ``.env``. On the owner's machine that is the real working database, and
    rows written there (append-only pipeline history, holds, cycles) cannot be removed,
    which is how fake runs dated 2026-11-15 reached the Studio. They run only on a stack
    the caller declares disposable (``PLM_INTEGRATION_ISOLATED_STACK=1``, as CI does)
    whose database passes the same target rule as ``plm migrate``.
    """
    import os
    from pathlib import Path

    from plm_platform.dotenv import load_env_file
    from plm_platform.migration_safety import is_disposable_migration_target
    from sqlalchemy.engine import make_url

    if os.environ.get(ISOLATED_STACK_VAR) != "1":
        return (f"integration tests need a disposable stack: set {ISOLATED_STACK_VAR}=1 "
                "only on a throwaway compose stack (never the owner's real database)")
    raw = (os.environ.get("DATABASE_URL")
           or load_env_file(Path(__file__).resolve().parents[1] / ".env").get("DATABASE_URL")
           or "")
    try:
        url = make_url(raw)
    except Exception:  # noqa: BLE001 - any unreadable URL is not a disposable target
        return "DATABASE_URL is missing or unreadable"
    if not is_disposable_migration_target(
            configured_database=url.database, connected_database=url.database,
            host=url.host, port=url.port):
        return (f"DATABASE_URL names {url.database!r} on {url.host}:{url.port}, not a "
                "disposable database (plm_migration_disposable_*/plm_r5_* on loopback, "
                "not port 5432)")
    return None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Two gates on collection.

    Integration tests (``integration``) are skipped with the reason unless the stack is
    declared disposable (:func:`_isolated_stack_refusal`), so a plain ``pytest`` on the
    owner's machine never writes to the real database or Temporal.

    The Gate R5 full chain (``full_chain``, also ``integration``) runs only when the
    marker expression names it (``pytest -m full_chain``, its own CI job): it takes about
    40 minutes at production content scale, so the normal ``-m integration`` run skips it
    with this reason instead of silently growing by that much."""
    if any(INTEGRATION_MARKER in item.keywords for item in items):
        refusal = _isolated_stack_refusal()
        if refusal is not None:
            unsafe = pytest.mark.skip(reason=f"not run: {refusal}")
            for item in items:
                if INTEGRATION_MARKER in item.keywords:
                    item.add_marker(unsafe)
    if FULL_CHAIN_MARKER in (config.getoption("markexpr", "") or ""):
        return
    skip = pytest.mark.skip(reason="Gate R5 full chain: select it with -m full_chain "
                                   "(its own CI job; about 40 min)")
    for item in items:
        if FULL_CHAIN_MARKER in item.keywords:
            item.add_marker(skip)
