"""R15 service-token auth for the `/ar` API: the shared fail-closed check, named."""

from __future__ import annotations

from collections.abc import Callable

from plm_platform.auth import require_service_token as _require_service_token
from plm_platform.config import Settings

__all__ = ["require_service_token"]


def require_service_token(platform: Settings | None) -> Callable[[str | None], None]:
    """Build the dependency for one app instance; `None` settings refuse all calls."""
    return _require_service_token(platform, surface="ar")
