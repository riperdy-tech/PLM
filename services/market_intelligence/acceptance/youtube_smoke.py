# services/market_intelligence/acceptance/youtube_smoke.py
"""§40's live smoke test — a real checklist with a first-class blocked outcome.

Run only after a human has created the YouTube API project and OAuth client (P0 §10).
Until then every invocation reports `blocked: pending credentials`; the function does
not fall back to mock data, because a smoke test that passes without credentials is
worse than no smoke test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from plm_platform.config import Settings

__all__ = [
    "BLOCKED_REASON_TEMPLATE", "REQUIRED_YOUTUBE_VARS", "SmokeReport", "SmokeStep",
    "blocked_reason", "missing_credentials", "run_live_smoke",
]

REQUIRED_YOUTUBE_VARS: tuple[str, ...] = (
    "YOUTUBE_API_KEY", "YOUTUBE_OAUTH_CLIENT_ID", "YOUTUBE_OAUTH_CLIENT_SECRET",
)
BLOCKED_REASON_TEMPLATE = "blocked: pending credentials — set {variables}"


@dataclass(frozen=True, slots=True)
class SmokeStep:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SmokeReport:
    blocked_reason: str | None = None
    steps: tuple[SmokeStep, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.blocked_reason is None and bool(self.steps) and all(s.ok for s in self.steps)

    @property
    def failed_steps(self) -> tuple[SmokeStep, ...]:
        return tuple(step for step in self.steps if not step.ok)


def missing_credentials(platform: Settings) -> tuple[str, ...]:
    """Every required YouTube variable that is not configured, in variable order."""
    missing = []
    if platform.youtube_api_key is None:
        missing.append("YOUTUBE_API_KEY")
    if platform.youtube_oauth_client_id is None:
        missing.append("YOUTUBE_OAUTH_CLIENT_ID")
    if platform.youtube_oauth_client_secret is None:
        missing.append("YOUTUBE_OAUTH_CLIENT_SECRET")
    return tuple(missing)


def blocked_reason(platform: Settings) -> str | None:
    """The §40 blocked line naming every missing variable, or `None` when configured."""
    missing = missing_credentials(platform)
    if not missing:
        return None
    return BLOCKED_REASON_TEMPLATE.format(variables=", ".join(missing))


def run_live_smoke(*, platform: Settings, client: httpx.Client,
                   storage_client: object | None = None) -> SmokeReport:
    """§40's checklist against the real API with a quota-safe query set."""
    reason = blocked_reason(platform)
    if reason is not None:
        return SmokeReport(blocked_reason=reason)
    from . import _live_checklist

    return _live_checklist.run(platform=platform, client=client, storage_client=storage_client,
                               now=datetime.now(UTC))
