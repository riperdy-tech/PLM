# services/market_intelligence/connectors/browser.py
"""§4.4 browser automation is a last resort and stays off until explicitly enabled."""

from __future__ import annotations

from typing import Any

from ..domain.enums import ConnectorErrorClass, ProviderName
from .base import ConnectorError

__all__ = ["BrowserConnector"]


class BrowserConnector:
    provider_name = ProviderName.BROWSER_PUBLIC_SEARCH

    def __init__(self, *, research_allowed: bool = False) -> None:
        self.research_allowed = research_allowed

    def fetch_public_page(self, url: str) -> dict[str, Any]:
        if not self.research_allowed:
            raise ConnectorError(
                ConnectorErrorClass.UNSUPPORTED_OPERATION,
                "browser automation is disabled: enable it explicitly for approved public "
                "research only (PHASE-P1 §4.4)",
            )
        raise ConnectorError(
            ConnectorErrorClass.UNSUPPORTED_OPERATION,
            "no browser backend is configured in this build",
        )
