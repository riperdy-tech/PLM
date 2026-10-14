"""Database engine, session factory and phase-schema scoping (W0.5).

The connection string is read from :mod:`plm_platform.config` only — this module
never reads ``os.environ`` directly. Everything it logs renders the password as
``***`` (:func:`redacted_database_url`), and SQLAlchemy's own ``URL`` reprs mask
the password too, so a connection string can never leak through a log line or an
exception raised here.

One PostgreSQL database, one schema per phase (P0 §4) — :data:`SCHEMAS` is the
single source of truth for the nine phase schemas, in creation order. Later
phases scope their sessions with :func:`scoped_session`; unqualified SQL then
resolves inside that phase's schema.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import cache
from typing import Final

from sqlalchemy import URL, Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, load_settings

__all__ = [
    "PLATFORM_SCHEMA",
    "SCHEMAS",
    "SchemaScopeError",
    "build_engine",
    "database_url",
    "redacted_database_url",
    "scoped_session",
    "session_factory",
]

logger = logging.getLogger(__name__)

SCHEMAS: Final[tuple[str, ...]] = (
    "market",  # P1
    "ar",  # P2
    "experiments",  # P3
    "music",  # P4
    "qa",  # P5
    "media",  # P6
    "youtube",  # P7
    "distribution",  # P8
    "analytics",  # P9
)
"""The nine phase schemas in dependency (creation) order — P0 §4, R13-era layout."""

PLATFORM_SCHEMA: Final[str] = "ops"
"""Cross-phase runtime state owned by ``plm_platform`` (ADR-0033): the cost ledger, the
exception queue and the decision log. No phase owns it; phases reach it only through
``plm_platform`` APIs, never by writing its tables directly."""


class SchemaScopeError(ValueError):
    """The requested schema is not one of the nine phase schemas."""


CONNECT_TIMEOUT_SECONDS: int = 5
"""Upper bound on establishing one postgres connection."""


def database_url(settings: Settings | None = None) -> URL:
    """The platform database URL, upgraded to the psycopg driver when needed.

    ``DATABASE_URL`` is documented in ``.env.example`` as ``postgresql://…``
    (SQLAlchemy's psycopg2 spelling). This normalises that to
    ``postgresql+psycopg://`` so the psycopg 3 driver is used; an explicit
    driver (e.g. ``postgresql+asyncpg://``) passes through untouched.
    """
    resolved = load_settings() if settings is None else settings
    url = make_url(resolved.database_url.get_secret_value())
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return url


def redacted_database_url(settings: Settings | None = None) -> str:
    """The connection string with its password rendered as ``***`` — safe to log."""
    return database_url(settings).render_as_string(hide_password=True)


def build_engine(settings: Settings | None = None) -> Engine:
    """Create an engine from configuration. Lazy: creating it does not connect.

    The redacted URL is logged at DEBUG; ``pool_pre_ping`` keeps long-lived
    workers honest after postgres restarts. A postgres connect gives up after
    :data:`CONNECT_TIMEOUT_SECONDS`: an unreachable database fails fast and names
    itself instead of blocking the caller (or a test run) indefinitely.
    """
    resolved = load_settings() if settings is None else settings
    url = database_url(resolved)
    logger.debug("creating database engine for %s", url.render_as_string(hide_password=True))
    connect_args: dict[str, int] = {}
    if url.drivername.startswith("postgresql"):
        connect_args["connect_timeout"] = CONNECT_TIMEOUT_SECONDS
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


@cache
def _default_engine() -> Engine:
    return build_engine()


@cache
def _default_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=_default_engine())


def session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """A session factory bound to ``engine`` (default: the process-wide engine)."""
    if engine is None:
        return _default_session_factory()
    return sessionmaker(bind=engine)


@contextmanager
def scoped_session(
    schema: str,
    *,
    factory: Callable[[], Session] | None = None,
) -> Iterator[Session]:
    """A session whose ``search_path`` is one phase schema.

    ``factory`` is called and nothing more (a ``sessionmaker`` is the usual shape),
    so the contract is any zero-argument callable returning a session.

    Scoping is transaction-local (``set_config(…, is_local => true)``), so it can
    never leak onto a pooled connection. The scope therefore lasts for the
    session's current transaction: after ``commit()``, re-enter the helper for
    further unqualified statements.

    Raises :class:`SchemaScopeError` for anything outside :data:`SCHEMAS`,
    before any engine or connection work happens.
    """
    if schema not in SCHEMAS:
        allowed = ", ".join(SCHEMAS)
        raise SchemaScopeError(f"unknown phase schema {schema!r}: expected one of {allowed}")
    maker = _default_session_factory() if factory is None else factory
    with maker() as session:
        session.execute(
            text("SELECT set_config('search_path', :schema, true)"), {"schema": schema}
        )
        yield session
