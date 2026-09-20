# services/market_intelligence/api/routes.py
"""§25's read-only API. P2 never sees a table (AC-09).

Every endpoint here reads; nothing in this module writes to the database, starts a
collection run or triggers work — the read-only constraint is the seam's meaning.

No ``from __future__ import annotations`` here on purpose: FastAPI resolves
``Annotated[..., Depends(...)]`` metadata by evaluating the annotation, and the
dependencies are per-app closures that only the eager form can see.
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from plm_contracts import ErrorShape
from sqlalchemy.orm import Session

from ..config import MarketSettings
from ..domain.value_objects import classify_freshness
from ..storage.raw_store import load_raw_observation
from ..storage.repositories import latest_scores, load_run
from .schemas import (
    ArmOut,
    ComponentsOut,
    EvidenceOut,
    ObservationOut,
    OpportunityDetail,
    OpportunityListResponse,
    OpportunityOut,
    RunOut,
    TaxonomyOut,
    decode_cursor,
    encode_cursor,
)

__all__ = ["build_router"]

SessionFactory = Callable[[], Session]


def build_router(*, session_factory: SessionFactory | None,
                 settings: MarketSettings) -> APIRouter:
    """Auth first, always: `_auth` is the first dependency on every route, so a
    request that fails the token check never reaches the database dependency."""
    router = APIRouter()
    dependency = _dependencies(session_factory)

    @router.get("/opportunities", response_model=OpportunityListResponse)
    def list_opportunities(
        _: Annotated[None, Depends(_auth(settings))],
        session: Annotated[Session, Depends(dependency)],
        limit: int = Query(20, ge=1, le=200),
        cursor: str | None = None,
        min_score: float | None = Query(None, ge=0.0, le=100.0),
        min_confidence: float | None = Query(None, ge=0.0, le=1.0),
        freshness: str | None = Query(None, pattern="^(fresh|normal|stale|expired)$"),
        country: str | None = None,
        language: str | None = None,
        genre: str | None = None,
        use_case: str | None = None,
    ) -> OpportunityListResponse:
        rows = latest_scores(session, limit=limit * 3, min_score=min_score,
                             min_confidence=min_confidence,
                             min_basis_weight=settings.min_rank_basis_weight)
        if country is not None:
            raise HTTPException(status_code=422, detail=ErrorShape(
                code="unsupported_parameter", field_paths=["country"],
                message=("the frozen C1 wire carries no country field; P1 refuses the filter "
                         "rather than silently returning an empty list (registry C1)"),
            ).model_dump())
        filtered = [row for row in rows if _matches(row, language=language, genre=genre,
                                                    use_case=use_case)]
        now = datetime.now(UTC)
        if freshness is not None:
            filtered = [row for row in filtered
                        if classify_freshness(
                            as_of=_instant(row["as_of"]), now=now,
                            fresh_hours=settings.freshness_fresh_hours,
                            normal_hours=settings.freshness_normal_hours,
                            stale_hours=settings.freshness_stale_hours,
                        ).bucket.value == freshness]
        if cursor is not None:
            score_floor, arm_floor = decode_cursor(cursor)
            filtered = [row for row in filtered if _after_cursor(row, score_floor, arm_floor)]
        items = filtered[:limit]
        next_cursor = None
        if len(filtered) > limit and items:
            last = items[-1]
            next_cursor = encode_cursor(float(last["market_opportunity_score"]), last["arm_id"])
        as_of = max((_instant(item["as_of"]) for item in items), default=now)
        return OpportunityListResponse(
            items=[_opportunity(row) for row in items], next_cursor=next_cursor, as_of=as_of,
            score_version=settings.score_version,
        )

    @router.get("/opportunities/{arm_id}", response_model=OpportunityDetail)
    def opportunity_detail(arm_id: str, _: Annotated[None, Depends(_auth(settings))],
                           session: Annotated[Session, Depends(dependency)]) -> OpportunityDetail:
        rows = [row for row in latest_scores(session, limit=200) if row["arm_id"] == arm_id]
        if not rows:
            raise _not_found("arm", arm_id)
        row = rows[0]
        return OpportunityDetail(
            arm=_arm(row), score=float(row["market_opportunity_score"]),
            component_scores=_components(row["components_json"]),
            confidence=float(row["confidence"]),
            evidence_summary=_evidence(row["evidence_json"]),
            observation_counts=int(row["evidence_json"].get("observation_count", 0)),
            source_counts=int(row["evidence_json"].get("source_count", 0)),
            last_updated=_instant(row["computed_at"]),
        )

    @router.get("/observations/{observation_id}", response_model=ObservationOut)
    def observation(observation_id: str, _: Annotated[None, Depends(_auth(settings))],
                    session: Annotated[Session, Depends(dependency)]) -> ObservationOut:
        row = session.execute(
            sa.text("SELECT * FROM market.market_observations WHERE id = :id"),
            {"id": observation_id},
        ).mappings().one_or_none()
        if row is None:
            raise _not_found("observation", observation_id)
        raw = load_raw_observation(session, str(row["raw_observation_id"])) or {}
        return ObservationOut(
            id=str(row["id"]), entity_id=str(row["entity_id"]), source_id=str(row["source_id"]),
            observed_at=_instant(row["observed_at"]),
            published_at=None if row["published_at"] is None else _instant(row["published_at"]),
            metrics={name: row[name] for name in
                     ("views", "likes", "comments", "subscriber_count",
                      "video_duration_seconds", "playlist_item_count", "channel_video_count")},
            text_title=row["text_title"], text_description=row["text_description"],
            provenance={"raw_observation_id": str(row["raw_observation_id"]),
                        "payload_uri": raw.get("payload_uri"),
                        "payload_hash": raw.get("payload_hash"),
                        "request_context_hash": raw.get("request_context_hash"),
                        "observed_at": _instant(row["observed_at"])},
        )

    @router.get("/runs/{run_id}", response_model=RunOut)
    def run(run_id: str, _: Annotated[None, Depends(_auth(settings))],
            session: Annotated[Session, Depends(dependency)]) -> RunOut:
        row = load_run(session, run_id)
        if row is None:
            raise _not_found("run", run_id)
        return RunOut(
            id=str(row["id"]), status=row["status"], started_at=_instant(row["started_at"]),
            finished_at=None if row["finished_at"] is None else _instant(row["finished_at"]),
            counts={"request_count": row["request_count"], "success_count": row["success_count"],
                    "error_count": row["error_count"], "rate_limit_count": row["rate_limit_count"],
                    "raw_observation_count": row["raw_observation_count"],
                    "normalized_observation_count": row["normalized_observation_count"]},
            costs={"api_calls": row["cost_api_calls"], "llm_tokens": row["cost_llm_tokens"],
                   "llm_cost_estimate": row["cost_llm_cost_estimate"],
                   "storage_bytes": row["cost_storage_bytes"]},
            error_summary=row["error_summary"], software_version=row["software_version"],
        )

    return router


def _dependencies(session_factory: SessionFactory | None):
    def session_dependency() -> Session:
        if session_factory is None:
            raise HTTPException(status_code=503, detail=ErrorShape(
                code="no_database", field_paths=[], message="no session factory configured",
            ).model_dump())
        with session_factory() as session:
            yield session

    return session_dependency


def _auth(settings: MarketSettings):
    from .service_token import require_service_token

    return require_service_token(settings.platform)


def _not_found(kind: str, identifier: str) -> HTTPException:
    return HTTPException(status_code=404, detail=ErrorShape(
        code="not_found", field_paths=[kind], message=f"{kind} {identifier!r} does not exist",
    ).model_dump())


def _instant(value: datetime) -> datetime:
    """SQLite round-trips `timezone=True` columns naive; the C1 wire never is naive.

    Postgres returns aware instants; SQLite (the unit/seam fixture) drops the offset on
    bind, so a stored aware instant must be re-attached as UTC at the read boundary
    before it becomes a response (P2 derives `freshness_seconds` from `as_of`).
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _opportunity(row) -> OpportunityOut:
    taxonomy = row["taxonomy_json"]
    return OpportunityOut(
        arm_id=row["arm_id"], market_opportunity_score=float(row["market_opportunity_score"]),
        confidence=float(row["confidence"]),
        as_of=_instant(row["as_of"]), taxonomy=TaxonomyOut(**_taxonomy_payload(taxonomy)),
        components=_components(row["components_json"]),
        evidence=_evidence(row["evidence_json"]),
        score_version=row["score_version"],
    )


def _evidence(stored: Mapping[str, Any]) -> EvidenceOut:
    """The frozen wire view of stored evidence.

    Stored rows now carry the full input set (`observation_ids`) and a score basis
    (`basis_weight`) besides the three registry fields; `EvidenceOut` forbids extras,
    so the wire takes exactly its own keys and nothing else (registry C1).
    """
    return EvidenceOut(
        observation_count=int(stored.get("observation_count", 0)),
        source_count=int(stored.get("source_count", 0)),
        top_observation_ids=list(stored.get("top_observation_ids", [])),
    )


def _components(stored: Mapping[str, Any]) -> ComponentsOut:
    """Stored Tri values (`{"state": ..., "value": ...}` or bare numbers) → wire.

    A `known` component becomes its number; `unknown`/`not_ready` becomes `None`.
    There is no fabricated zero: §3.6 says preserve the metric, so the C1 payload
    carries `null` for a component the evidence does not support.
    """
    values: dict[str, float | None] = {}
    for name, raw in stored.items():
        if isinstance(raw, Mapping):
            values[name] = float(raw["value"]) if raw.get("state") == "known" else None
        else:
            values[name] = float(raw)
    return ComponentsOut(**values)


def _taxonomy_payload(stored: dict) -> dict:
    """Map the stored 13-dimension profile onto the frozen 9-key wire object."""
    moods = _split(stored.get("mood"))
    use_cases = _split(stored.get("use_case"))
    return {
        "genre": _single(stored.get("genre")), "subgenre": _single(stored.get("subgenre")),
        "mood": moods, "use_case": use_cases, "language": _single(stored.get("language")),
        "vocal_mode": _single(stored.get("vocal_mode")),
        "duration_bucket": _single(stored.get("duration_bucket")),
        "visual_world": _single(stored.get("visual_world")),
        "title_archetype": _single(stored.get("title_archetype")),
    }


def _single(value: object) -> str | None:
    return None if value in (None, "none", "") else str(value)


def _split(value: object) -> list[str]:
    if value in (None, "none", ""):
        return []
    return [part for part in str(value).split("+") if part]


def _arm(row) -> ArmOut:
    taxonomy = row["taxonomy_json"]
    return ArmOut(
        arm_id=row["arm_id"], fingerprint=row.get("fingerprint", row["arm_id"]),
        status=row.get("status", "candidate"),
        genre=_single(taxonomy.get("genre")), subgenre=_single(taxonomy.get("subgenre")),
        mood=_split(taxonomy.get("mood")),
        use_case=_single(taxonomy.get("use_case")) or None,
        language=_single(taxonomy.get("language")), vocal_mode=_single(taxonomy.get("vocal_mode")),
        vocal_gender=_single(taxonomy.get("vocal_gender")),
        energy=_single(taxonomy.get("energy")), bpm_range=_single(taxonomy.get("bpm_range")),
        duration_bucket=_single(taxonomy.get("duration_bucket")),
        visual_world=_single(taxonomy.get("visual_world")),
        audience_intent=_single(taxonomy.get("audience_intent")),
        title_archetype=_single(taxonomy.get("title_archetype")),
        support_count=int(row.get("support_count", 0)),
    )


def _after_cursor(row, score_floor: float, arm_floor: str) -> bool:
    """Keyset position under `(score desc, arm_id asc)`."""
    score = float(row["market_opportunity_score"])
    if score != score_floor:
        return score < score_floor
    return row["arm_id"] > arm_floor


def _matches(row, *, language: str | None, genre: str | None, use_case: str | None) -> bool:
    taxonomy = row["taxonomy_json"] or {}
    if language is not None and _single(taxonomy.get("language")) != language:
        return False
    if genre is not None and _single(taxonomy.get("genre")) != genre:
        return False
    return use_case is None or use_case in _split(taxonomy.get("use_case"))
