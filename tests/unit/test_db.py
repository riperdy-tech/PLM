"""W0.5: database engine plumbing, URL redaction and phase-schema scoping.

Hermetic like ``tests/unit/test_config.py``: settings are injected and the dotenv
path points at an absent file, so a developer's real ``.env`` cannot influence the
suite. Nothing here connects to a database — ``build_engine`` is lazy, and the
schema-scope check exercises only the validation path.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from plm_platform.config import Settings, load_settings
from plm_platform.db import (
    SCHEMAS,
    SchemaScopeError,
    build_engine,
    database_url,
    redacted_database_url,
    scoped_session,
)

ABSENT_DOTENV = Path(__file__).resolve().parent / "no-such.env"
POSTGRES_SENTINEL = "postgres-secret-sentinel"
MINIO_SENTINEL = "minio-secret-sentinel"


def env(**overrides: str) -> dict[str, str]:
    """A complete, hand-written process environment — no file, no defaults involved."""
    values = {
        "DATABASE_URL": f"postgresql://plm:{POSTGRES_SENTINEL}@db.internal:5432/plm",
        "POSTGRES_PASSWORD": POSTGRES_SENTINEL,
        "MINIO_ENDPOINT": "objects.internal:9000",
        "MINIO_ROOT_USER": "plm-root",
        "MINIO_ROOT_PASSWORD": MINIO_SENTINEL,
        "MINIO_BUCKET": "plm-media",
        "TEMPORAL_HOST": "temporal.internal:7233",
    }
    values.update(overrides)
    return values


def settings(**overrides: str) -> Settings:
    return load_settings(env=env(**overrides), dotenv_path=ABSENT_DOTENV)


class TestPhaseSchemas:
    def test_nine_phase_schemas_in_dependency_order(self) -> None:
        assert SCHEMAS == (
            "market",
            "ar",
            "experiments",
            "music",
            "qa",
            "media",
            "youtube",
            "distribution",
            "analytics",
        )


class TestConnectionString:
    def test_plain_postgresql_url_is_upgraded_to_the_psycopg_driver(self) -> None:
        assert database_url(settings()).drivername == "postgresql+psycopg"

    def test_explicit_psycopg_url_passes_through(self) -> None:
        explicit = f"postgresql+psycopg://plm:{POSTGRES_SENTINEL}@db.internal:5432/plm"
        assert database_url(settings(DATABASE_URL=explicit)).drivername == "postgresql+psycopg"

    def test_explicit_other_driver_is_left_alone(self) -> None:
        explicit = "postgresql+asyncpg://plm:secret@db.internal:5432/plm"
        assert database_url(settings(DATABASE_URL=explicit)).drivername == "postgresql+asyncpg"

    def test_redacted_form_replaces_the_password_with_stars(self) -> None:
        redacted = redacted_database_url(settings())
        assert POSTGRES_SENTINEL not in redacted
        assert "***" in redacted
        assert "db.internal:5432/plm" in redacted

    def test_url_repr_never_carries_the_password(self) -> None:
        assert POSTGRES_SENTINEL not in repr(database_url(settings()))

    def test_engine_creation_logs_the_redacted_form(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="plm_platform.db"):
            build_engine(settings())
        assert POSTGRES_SENTINEL not in caplog.text
        assert "db.internal:5432" in caplog.text


class TestSchemaScope:
    def test_unlisted_schema_is_refused_before_any_connection_is_built(self) -> None:
        with pytest.raises(SchemaScopeError, match="not_a_schema"), scoped_session("not_a_schema"):
            pass
