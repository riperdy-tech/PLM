"""Provider response fixtures for R6 adapter conformance tests.

Each file under ``tests/fixtures/providers/<provider>/<name>.json`` is one HTTP
response a real provider gives, plus where its shape came from::

    {
      "_provenance": {
        "source": "docs-derived",            # or "recorded-live"
        "reference": "https://developers.google.com/...",
        "captured": "2026-10-30",
        "notes": "what the docs did not show, redactions"
      },
      "status": 200,
      "headers": {"Content-Type": "application/json"},   # optional
      "json": {...}                                       # or "text": "..."
    }

``docs-derived`` fixtures prove the adapter handles the documented shape, not
that the provider still answers that way; step 2 replaces them with
``recorded-live`` captures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from tests.support.http import MockHTTP
from tests.support.paths import REPO_ROOT

PROVIDER_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "providers"
SOURCES = frozenset({"docs-derived", "recorded-live"})


@dataclass(frozen=True, slots=True)
class ProviderFixture:
    path: Path
    provenance: dict[str, str]
    status: int
    headers: dict[str, str]
    json: Any = None
    text: str | None = None

    def response(self) -> httpx.Response:
        if self.text is not None:
            return httpx.Response(self.status, text=self.text, headers=self.headers)
        return httpx.Response(self.status, json=self.json, headers=self.headers)


def load(provider: str, name: str) -> ProviderFixture:
    """Read and validate one fixture; a missing provenance field is an error."""
    path = PROVIDER_FIXTURES / provider / f"{name}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    provenance = raw.get("_provenance")
    if not isinstance(provenance, dict) or provenance.get("source") not in SOURCES:
        raise ValueError(f"{path}: _provenance.source must be one of {sorted(SOURCES)}")
    for field in ("reference", "captured"):
        if not provenance.get(field):
            raise ValueError(f"{path}: _provenance.{field} is required")
    if ("json" in raw) == ("text" in raw):
        raise ValueError(f"{path}: exactly one of 'json' or 'text' is required")
    return ProviderFixture(path=path, provenance=provenance, status=int(raw["status"]),
                           headers=dict(raw.get("headers", {})), json=raw.get("json"),
                           text=raw.get("text"))


def enqueue(mock: MockHTTP, provider: str, *names: str) -> MockHTTP:
    """Serve the named fixtures in call order."""
    for name in names:
        mock.enqueue_response(load(provider, name).response())
    return mock


def all_fixtures() -> list[tuple[str, str]]:
    """Every ``(provider, name)`` on disk, for the provenance check."""
    return sorted((p.parent.name, p.stem) for p in PROVIDER_FIXTURES.glob("*/*.json"))
