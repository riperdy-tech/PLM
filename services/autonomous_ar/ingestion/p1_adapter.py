# services/autonomous_ar/ingestion/p1_adapter.py
"""P2's consumer side of C1: read `/market/v1`, map into `ContentArm`. Never a table (AC-09).

This module imports no database library and reads no `market` schema: the adapter talks
HTTP with the service token (R15) and the registry's flat envelope (ADR-0022). `freshness`
is not on the wire — it is derived here from an aware `as_of`.
"""

from __future__ import annotations

from datetime import datetime

import httpx
from plm_platform.config import SERVICE_TOKEN_HEADER
from pydantic import BaseModel, ConfigDict, Field

from ..domain.arm import ArmDimensions, ContentArm

__all__ = [
    "MARKET_OPPORTUNITIES_PATH", "MarketUnavailable", "OpportunityPage", "P1Client",
    "adapt_opportunity",
]

MARKET_OPPORTUNITIES_PATH = "/market/v1/opportunities"
"""The frozen C1 endpoint (registry C1; base path fixed by R15)."""

_ENVELOPE_KEYS = frozenset({"items", "next_cursor", "as_of", "score_version"})


class MarketUnavailable(RuntimeError):
    """P1 could not be read. §102: a degraded cycle or a stop — never invented data."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaxonomyPayload(_Frozen):
    genre: str | None = None
    subgenre: str | None = None
    mood: list[str] = Field(default_factory=list)
    use_case: list[str] = Field(default_factory=list)
    language: str | None = None
    vocal_mode: str | None = None
    duration_bucket: str | None = None
    visual_world: str | None = None
    title_archetype: str | None = None


class ComponentsPayload(_Frozen):
    demand: float | None
    growth: float | None
    competitor_velocity: float | None
    competition: float | None
    audience_quality: float | None
    cross_platform: float | None
    production_fit: float | None


class EvidencePayload(_Frozen):
    observation_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    top_observation_ids: list[str] = Field(default_factory=list)


class _OpportunityPayload(_Frozen):
    """P2's transcription of the frozen C1 object. Exported as `OpportunityPayload` in
    P2.2A, where the consumer seam test pins it against the registry field list."""

    arm_id: str = Field(min_length=1)
    market_opportunity_score: float = Field(ge=0.0, le=100.0)
    confidence: float = Field(ge=0.0, le=1.0)
    as_of: datetime
    taxonomy: TaxonomyPayload
    components: ComponentsPayload
    evidence: EvidencePayload
    score_version: str = Field(min_length=1)


class OpportunityPage(_Frozen):
    items: tuple[_OpportunityPayload, ...]
    next_cursor: str | None
    as_of: datetime
    score_version: str


class P1Client:
    """The only P1 read path (R15): HTTP + service token, flat envelope, no tables."""

    def __init__(self, base_url: str, token: str | None, *, timeout_seconds: float = 10.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds
        self._transport = transport

    def fetch_opportunities(self, *, limit: int = 100,
                            cursor: str | None = None) -> OpportunityPage:
        if not self._token:
            raise MarketUnavailable(
                "no service token configured: set PLM_SERVICE_TOKEN (R15); an unconfigured "
                "P1 closes its API with 503 and P2 must not read that as empty data"
            )
        params: dict[str, str | int] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        try:
            with httpx.Client(base_url=self._base_url, timeout=self._timeout,
                              transport=self._transport) as client:
                response = client.get(MARKET_OPPORTUNITIES_PATH, params=params,
                                      headers={SERVICE_TOKEN_HEADER: self._token})
        except httpx.HTTPError as exc:
            raise MarketUnavailable(f"GET {MARKET_OPPORTUNITIES_PATH} failed: {exc}") from exc
        if response.status_code != 200:
            raise MarketUnavailable(
                f"GET {MARKET_OPPORTUNITIES_PATH} answered {response.status_code}"
            )
        body = response.json()
        if set(body) != _ENVELOPE_KEYS:
            raise MarketUnavailable(
                "C1 envelope drifted: expected "
                f"{sorted(_ENVELOPE_KEYS)}, got {sorted(body)} (ADR-0022: the flat object)"
            )
        return OpportunityPage.model_validate(body)


def adapt_opportunity(payload: _OpportunityPayload, *, now: datetime) -> ContentArm:
    """C1 payload → `ContentArm`; `market_opportunity_score → p1_score` per registry C1."""
    age = (now - payload.as_of).total_seconds()
    dimensions = ArmDimensions(
        genre=payload.taxonomy.genre, subgenre=payload.taxonomy.subgenre,
        moods=tuple(payload.taxonomy.mood), use_cases=tuple(payload.taxonomy.use_case),
        language=payload.taxonomy.language, vocal_mode=payload.taxonomy.vocal_mode,
        duration_bucket=payload.taxonomy.duration_bucket,
        visual_world=payload.taxonomy.visual_world,
        title_archetype=payload.taxonomy.title_archetype,
    )
    return ContentArm(
        arm_id=payload.arm_id, dimensions=dimensions,
        p1_score=payload.market_opportunity_score, p1_confidence=payload.confidence,
        component_scores=payload.components.model_dump(),
        observation_count=payload.evidence.observation_count,
        source_count=payload.evidence.source_count, as_of=payload.as_of,
        score_version=payload.score_version,
        freshness_seconds=max(0.0, age),  # clock skew clamps at zero, never negative
    )
