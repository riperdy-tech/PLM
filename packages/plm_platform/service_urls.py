"""Where each phase API listens (R3 runtime): one base URL per phase, set once.

``PLM_SERVICE_URL__<PHASE>`` (``MARKET``, ``AR``, ``EXPERIMENTS``, ``MUSIC``, ``QA``,
``MEDIA``, ``YOUTUBE``, ``DISTRIBUTION``, ``ANALYTICS``) overrides a phase's base URL.
Unset, a caller gets the default for where it runs:

* inside the compose network (the pipeline worker): the compose service name, e.g.
  ``http://api-ar:8082`` (:data:`COMPOSE_HOST`);
* on the host (``plm doctor``): ``http://127.0.0.1:8082`` (:data:`LOOPBACK_HOST`), since
  compose publishes every API on IPv4 loopback only.

Values come from the process environment, then ``.env``; nothing else reads them.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

from .config import SettingsError
from .dotenv import load_env_file
from .temporal import Phase

__all__ = [
    "COMPOSE_HOST",
    "LOOPBACK_HOST",
    "SERVICE_PORTS",
    "SERVICE_URL_PREFIX",
    "service_name",
    "service_url",
    "service_urls",
]

SERVICE_URL_PREFIX: Final[str] = "PLM_SERVICE_URL__"

SERVICE_PORTS: Final[Mapping[Phase, int]] = MappingProxyType({
    Phase.MARKET: 8081,
    Phase.AR: 8082,
    Phase.EXPERIMENTS: 8083,
    Phase.MUSIC: 8004,
    Phase.QA: 8005,
    Phase.MEDIA: 8006,
    Phase.YOUTUBE: 8007,
    Phase.DISTRIBUTION: 8008,
    Phase.ANALYTICS: 8009,
})
"""Each phase API's port, as ``docker-compose.yml`` publishes it."""

COMPOSE_HOST: Final[str] = "compose"
LOOPBACK_HOST: Final[str] = "127.0.0.1"


def service_name(phase: Phase) -> str:
    """The compose service of a phase API: ``api-ar``."""
    return f"api-{phase.name.lower()}"


def _default(phase: Phase, host: str) -> str:
    target = service_name(phase) if host == COMPOSE_HOST else host
    return f"http://{target}:{SERVICE_PORTS[phase]}"


def service_url(
    phase: Phase,
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
    default_host: str = COMPOSE_HOST,
) -> str:
    """The base URL of one phase API (no trailing slash)."""
    return service_urls(env=env, dotenv_path=dotenv_path, default_host=default_host)[phase]


def service_urls(
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
    default_host: str = COMPOSE_HOST,
) -> dict[Phase, str]:
    """Every phase's base URL; an override that is not ``http(s)://...`` is refused."""
    process_env: Mapping[str, str] = os.environ if env is None else env
    dotenv_values = load_env_file(Path(".env") if dotenv_path is None else dotenv_path)
    urls: dict[Phase, str] = {}
    for phase in Phase:
        name = f"{SERVICE_URL_PREFIX}{phase.name}"
        raw = process_env.get(name) or dotenv_values.get(name) or ""
        value = raw.strip()
        if not value:
            urls[phase] = _default(phase, default_host)
            continue
        if not value.startswith(("http://", "https://")):
            raise SettingsError(f"invalid value for {name!r}: expected an http(s) URL")
        urls[phase] = value.rstrip("/")
    return urls
