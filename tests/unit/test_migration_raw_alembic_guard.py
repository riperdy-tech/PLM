"""Alembic's own entry point holds the code-only 0039 rollout.

No real database, Docker or Alembic migration is used by these tests.
"""
from __future__ import annotations

import runpy
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import alembic.context as alembic_context
import pytest
import sqlalchemy
from alembic.config import Config
from plm_platform.secrets import Secret

ROOT = Path(__file__).resolve().parents[2]
ENV_PY = ROOT / "migrations/env.py"
DB = "plm_migration_disposable_safetytest"


def _configure_env(
    monkeypatch: pytest.MonkeyPatch, database: str, *, port: int,
) -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("version_locations", str(ROOT / "migrations/versions"))
    cfg.set_main_option("recursive_version_locations", "true")
    cfg.set_main_option("path_separator", "os")
    monkeypatch.setattr(alembic_context, "config", cfg, raising=False)
    monkeypatch.setattr(alembic_context, "is_offline_mode", lambda: False)
    settings = SimpleNamespace(
        database_url=Secret(
            f"postgresql+psycopg://unprivileged:unit-secret@127.0.0.1:{port}/{database}"
        )
    )
    monkeypatch.setattr("plm_platform.config.load_settings", lambda **kwargs: settings)


def test_raw_alembic_on_shared_database_refuses_before_creating_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_env(monkeypatch, "plm", port=5432)
    monkeypatch.setattr(
        sqlalchemy, "create_engine",
        lambda *args, **kwargs: pytest.fail("created engine for shared target"),
    )
    with pytest.raises(RuntimeError, match="code ONLY"):
        runpy.run_path(str(ENV_PY))


def test_raw_alembic_rejects_wrong_connected_disposable_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_env(monkeypatch, DB, port=55432)
    seen: list[str] = []

    class Connection:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            seen.append("close")

        def execute(self, statement: object) -> Any:
            assert str(statement) == "SELECT current_database()"
            return SimpleNamespace(scalar_one=lambda: "plm")

        def rollback(self) -> None:
            seen.append("rollback")

    class Engine:
        def connect(self) -> Connection:
            return Connection()

        def dispose(self) -> None:
            seen.append("dispose")

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(
        alembic_context, "configure",
        lambda **kwargs: pytest.fail("configured unsafe Alembic connection"),
    )
    with pytest.raises(RuntimeError, match="non-disposable"):
        runpy.run_path(str(ENV_PY))
    assert seen == ["rollback", "close", "dispose"]


def test_raw_alembic_preserves_disposable_rehearsal_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_env(monkeypatch, DB, port=55432)
    seen: list[str] = []

    class Connection:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            seen.append("close")

        def execute(self, statement: object) -> Any:
            assert str(statement) == "SELECT current_database()"
            return SimpleNamespace(scalar_one=lambda: DB)

        def rollback(self) -> None:
            seen.append("rollback")

    class Engine:
        def connect(self) -> Connection:
            return Connection()

        def dispose(self) -> None:
            seen.append("dispose")

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(
        alembic_context, "configure",
        lambda **kwargs: seen.append("configure"),
    )
    monkeypatch.setattr(alembic_context, "begin_transaction", lambda: nullcontext())
    monkeypatch.setattr(alembic_context, "run_migrations", lambda: seen.append("migrate"))
    runpy.run_path(str(ENV_PY))
    assert seen == ["rollback", "configure", "migrate", "close", "dispose"]


@pytest.mark.parametrize("step", [
    ("0042_media_render_evidence", "0042_media_render_evidence"),  # not the head
    ("not_a_revision", "0043_youtube_localizations"),
    "0043_youtube_localizations",
])
def test_approved_local_connection_refuses_a_step_other_than_start_to_head(
    monkeypatch: pytest.MonkeyPatch, step: object,
) -> None:
    from sqlalchemy.engine import Connection

    _configure_env(monkeypatch, "plm", port=5432)
    alembic_context.config.attributes["plm_approved_local_connection"] = (
        object.__new__(Connection)
    )
    alembic_context.config.attributes["plm_approved_local_step"] = step
    monkeypatch.setattr(
        alembic_context, "configure",
        lambda **kwargs: pytest.fail("configured an unproven approved connection"),
    )
    with pytest.raises(RuntimeError, match="approved local migration connection"):
        runpy.run_path(str(ENV_PY))
