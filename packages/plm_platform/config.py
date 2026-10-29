"""Layered configuration and secret hygiene (W0.4).

Precedence: process environment → ``.env`` file → documented defaults. The ``.env``
file is never required: a missing file, a missing key or an empty value all fall
through to the next layer.

* Deployment values — ``DATABASE_URL``, ``POSTGRES_PASSWORD``, ``MINIO_ENDPOINT``,
  ``MINIO_ROOT_USER``, ``MINIO_ROOT_PASSWORD``, ``MINIO_BUCKET``, ``TEMPORAL_HOST``
  (:data:`REQUIRED_VARS`) — have no default: a missing one fails at load time with the
  variable's *name* in the message and never its value. ``.env.example`` (W0.2)
  documents the canonical local values, and a test binds the two files together.
* Operator knobs — ``ENV`` (default ``local``) and ``LOG_LEVEL`` (default ``INFO``) —
  carry documented defaults (:data:`DEFAULTS`) and are intentionally not part of
  ``.env.example``.
* Provider credentials — ``YOUTUBE_*``, ``MUSIC_PROVIDER_API_KEY``,
  ``DISTRIBUTOR_API_KEY``, ``SPOTIFY_*`` (:data:`PROVIDER_SECRET_VARS`) — are optional
  and default to unset so mock-first development runs without them;
  :meth:`Settings.is_mock_mode` reports whether *all* of them are absent.
* Internal service token — ``PLM_SERVICE_TOKEN`` (:data:`SERVICE_TOKEN_VAR`, R15) — is
  optional too, but it is not a provider credential: an unconfigured token makes the
  phase APIs answer ``503 blocked_pending_credentials`` instead of serving openly.

Secret values are :class:`~plm_platform.secrets.Secret` wrappers: ``repr``, ``str``,
formatting and logging show ``**********`` and the real value is reachable only through
:meth:`Secret.get_secret_value`. No FastAPI or SQLAlchemy imports live here — this
module is imported by everyone, including CLI and CI scripts.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final

from .dotenv import load_env_file
from .secrets import Secret

__all__ = [
    "DEFAULTS",
    "PROVIDER_SECRET_VARS",
    "REQUIRED_VARS",
    "SERVICE_TOKEN_HEADER",
    "SERVICE_TOKEN_VAR",
    "Env",
    "LogLevel",
    "Secret",
    "Settings",
    "SettingsError",
    "load_settings",
]

REQUIRED_VARS: Final[tuple[str, ...]] = (
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
    "MINIO_ENDPOINT",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "MINIO_BUCKET",
    "TEMPORAL_HOST",
)
"""Deployment values with no default. Missing ones are named in the failure."""

PROVIDER_SECRET_VARS: Final[tuple[str, ...]] = (
    "YOUTUBE_API_KEY",
    "YOUTUBE_OAUTH_CLIENT_ID",
    "YOUTUBE_OAUTH_CLIENT_SECRET",
    "MUSIC_PROVIDER_API_KEY",
    "DISTRIBUTOR_API_KEY",
    "SPOTIFY_CLIENT_ID",
    "SPOTIFY_CLIENT_SECRET",
)
"""External provider credentials; optional, unset by default, mock-first."""

SERVICE_TOKEN_VAR: Final[str] = "PLM_SERVICE_TOKEN"
"""Shared service token for internal phase APIs (R15); optional, never a provider key."""

SERVICE_TOKEN_HEADER: Final[str] = "X-PLM-Service-Token"
"""Header carrying the service token; the name is defined once, here."""

DEFAULTS: Final[Mapping[str, str]] = MappingProxyType({"ENV": "local", "LOG_LEVEL": "INFO"})
"""Documented defaults for the operator knobs (there are no defaults for secrets)."""

_DEFAULT_DOTENV: Final[Path] = Path(".env")


class Env(StrEnum):
    """Deployment environment token (``ENV``)."""

    LOCAL = "local"
    CI = "ci"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Python logging level token (``LOG_LEVEL``); input is case-normalised."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class SettingsError(RuntimeError):
    """Configuration could not be loaded. Messages name variables, never values."""


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated settings. Secret fields are :class:`Secret`: safe to repr and log."""

    env: Env
    log_level: LogLevel
    database_url: Secret
    postgres_password: Secret
    minio_endpoint: str
    minio_root_user: str
    minio_root_password: Secret
    minio_bucket: str
    temporal_host: str
    service_token: Secret | None
    youtube_api_key: Secret | None
    youtube_oauth_client_id: Secret | None
    youtube_oauth_client_secret: Secret | None
    music_provider_api_key: Secret | None
    distributor_api_key: Secret | None
    spotify_client_id: Secret | None
    spotify_client_secret: Secret | None

    def is_mock_mode(self) -> bool:
        """True while no external provider credential is configured (mock-first default).

        Whole-platform branching switch: a phase that needs one specific provider should
        branch on that provider's field instead (``None`` means "not configured").
        """
        return all(getattr(self, name.lower()) is None for name in PROVIDER_SECRET_VARS)


def load_settings(
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> Settings:
    """Load and validate settings from the process environment, ``.env`` and defaults.

    ``env`` defaults to ``os.environ``; ``dotenv_path`` defaults to ``.env`` in the
    current working directory and a missing file is not an error. Raises
    :class:`SettingsError` — naming variables, never values — when a required variable
    is missing or an enumerable value is invalid.
    """
    process_env: Mapping[str, str] = os.environ if env is None else env
    dotenv_values = load_env_file(_DEFAULT_DOTENV if dotenv_path is None else dotenv_path)
    layers: tuple[Mapping[str, str], ...] = (process_env, dotenv_values)
    return Settings(
        env=_parse_env_token(_resolve("ENV", layers, DEFAULTS["ENV"])),
        log_level=_parse_log_level(_resolve("LOG_LEVEL", layers, DEFAULTS["LOG_LEVEL"])),
        database_url=_resolve_secret("DATABASE_URL", layers),
        postgres_password=_resolve_secret("POSTGRES_PASSWORD", layers),
        minio_endpoint=_resolve("MINIO_ENDPOINT", layers),
        minio_root_user=_resolve("MINIO_ROOT_USER", layers),
        minio_root_password=_resolve_secret("MINIO_ROOT_PASSWORD", layers),
        minio_bucket=_resolve("MINIO_BUCKET", layers),
        temporal_host=_resolve("TEMPORAL_HOST", layers),
        service_token=_resolve_optional_secret(SERVICE_TOKEN_VAR, layers),
        youtube_api_key=_resolve_optional_secret("YOUTUBE_API_KEY", layers),
        youtube_oauth_client_id=_resolve_optional_secret("YOUTUBE_OAUTH_CLIENT_ID", layers),
        youtube_oauth_client_secret=_resolve_optional_secret(
            "YOUTUBE_OAUTH_CLIENT_SECRET", layers
        ),
        music_provider_api_key=(
            _resolve_optional_secret("MUSIC_PROVIDER_API_KEY", layers)
            or _resolve_optional_secret("SUNO_API_KEY", layers)
        ),
        distributor_api_key=_resolve_optional_secret("DISTRIBUTOR_API_KEY", layers),
        spotify_client_id=_resolve_optional_secret("SPOTIFY_CLIENT_ID", layers),
        spotify_client_secret=_resolve_optional_secret("SPOTIFY_CLIENT_SECRET", layers),
    )


def _lookup(name: str, layers: tuple[Mapping[str, str], ...]) -> str | None:
    """First non-empty value in precedence order; empty or blank means unset."""
    for layer in layers:
        candidate = layer.get(name)
        if candidate is not None and candidate.strip():
            return candidate
    return None


def _resolve(
    name: str,
    layers: tuple[Mapping[str, str], ...],
    default: str | None = None,
) -> str:
    value = _lookup(name, layers)
    if value is not None:
        return value
    if default is not None:
        return default
    raise SettingsError(
        f"missing required environment variable {name!r}: set it in the process "
        "environment or in .env (see .env.example for the documented local values)"
    )


def _resolve_secret(
    name: str,
    layers: tuple[Mapping[str, str], ...],
    default: str | None = None,
) -> Secret:
    return Secret(_resolve(name, layers, default))


def _resolve_optional_secret(name: str, layers: tuple[Mapping[str, str], ...]) -> Secret | None:
    value = _lookup(name, layers)
    return None if value is None else Secret(value)


def _parse_env_token(raw: str) -> Env:
    try:
        return Env(raw.lower())
    except ValueError:
        allowed = ", ".join(member.value for member in Env)
        raise SettingsError(f"invalid value for 'ENV': expected one of {allowed}") from None


def _parse_log_level(raw: str) -> LogLevel:
    try:
        return LogLevel(raw.upper())
    except ValueError:
        allowed = ", ".join(member.value for member in LogLevel)
        raise SettingsError(
            f"invalid value for 'LOG_LEVEL': expected one of {allowed}"
        ) from None
