"""Service token authentication dependency for `/youtube` API (Ruling R15)."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Annotated

from fastapi import Header, HTTPException
from plm_contracts import ErrorShape
from plm_platform.config import SERVICE_TOKEN_HEADER, SERVICE_TOKEN_VAR, Settings

__all__ = ["require_service_token"]


def require_service_token(platform: Settings | None) -> Callable[[str | None], None]:
    """Build the dependency for one app instance; `None` settings refuse all calls."""

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
                        "the youtube publishing API is closed until a service token is configured: "
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
