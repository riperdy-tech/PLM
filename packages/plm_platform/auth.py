"""Fail-closed service-token authentication for every PLM HTTP surface (Ruling R15).

One implementation, parameterised by the surface name that appears in the 503 message so
an operator knows which API closed. Phase services keep a thin ``api/service_token.py``
wrapper that only names their surface; they never import each other.

* No settings, or no ``PLM_SERVICE_TOKEN`` configured: every call is refused with
  ``503 blocked_pending_credentials``. Nothing is served unauthenticated.
* Missing or wrong ``X-PLM-Service-Token`` header: ``401 invalid_service_token``.
  Comparison is constant-time.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import Header, HTTPException
from plm_contracts import ErrorShape

from .config import SERVICE_TOKEN_HEADER, SERVICE_TOKEN_VAR, Settings

__all__ = ["require_service_token"]


def require_service_token(
    platform: Settings | None, *, surface: str
) -> Callable[[str | None], None]:
    """Build the auth dependency for one app instance of ``surface``."""

    def dependency(
        token: Annotated[str | None, Header(alias=SERVICE_TOKEN_HEADER)] = None,
    ) -> None:
        if platform is None or platform.service_token is None:
            raise HTTPException(
                status_code=503,
                detail=ErrorShape(
                    code="blocked_pending_credentials",
                    field_paths=[SERVICE_TOKEN_VAR],
                    message=(
                        f"the {surface} API is closed until a service token is configured: "
                        f"set {SERVICE_TOKEN_VAR} (R15). Nothing is served unauthenticated."
                    ),
                ).model_dump(),
            )
        expected = platform.service_token.get_secret_value()
        if token is None or not hmac.compare_digest(token.encode(), expected.encode()):
            raise HTTPException(
                status_code=401,
                detail=ErrorShape(
                    code="invalid_service_token",
                    field_paths=[SERVICE_TOKEN_HEADER],
                    message="missing or invalid service token",
                ).model_dump(),
            )

    return dependency
