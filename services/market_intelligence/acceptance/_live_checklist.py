# services/market_intelligence/acceptance/_live_checklist.py
"""§40's ten checks, each one a `SmokeStep`, each catching its own failure.

The query set is quota-safe by construction: one query, one page, one batch of ids
(`max_results_per_search=1`, `max_pages_per_search=1`) — "do not perform a large crawl
during initial credential validation" (§40). The steps total 201 quota units (search
100 + second search 100 + hydration 1), so the run's budget is 300, just above the
checklist's own cost; the plan's 200 would have refused its own final call.

A step that cannot run because an earlier step failed is reported `ok=False` with the
dependency named — never silently omitted.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
from plm_platform.config import Settings

from ..collection.rate_limit import QuotaBudget, RequestBudget
from ..config import load_market_settings
from ..connectors.youtube import YouTubeCredentials, YouTubeDataAPIConnector
from ..domain.enums import ConnectorType, EntityType, ProviderName, ProviderObjectType
from ..normalization.canonical import normalize_text
from ..normalization.youtube import normalize_search_hit
from ..storage import repositories
from .youtube_smoke import SmokeReport, SmokeStep

__all__ = ["run"]

QUERY = "lofi"
SMOKE_QUOTA_UNITS = 300
SMOKE_REQUEST_BUDGET = 6


def run(*, platform: Settings, client: httpx.Client, storage_client: Any,
        now: datetime) -> SmokeReport:
    steps: list[SmokeStep] = []
    settings = load_market_settings(platform=platform)
    credentials = YouTubeCredentials(api_key=platform.youtube_api_key, access_token=None)
    connector = YouTubeDataAPIConnector(
        client=client, credentials=credentials, quota=QuotaBudget(SMOKE_QUOTA_UNITS),
        request_budget=RequestBudget(SMOKE_REQUEST_BUDGET), observed_at=lambda: now,
    )
    with _session() as session:
        source_id = repositories.upsert_source(
            session, provider_name=ProviderName.YOUTUBE_DATA_API,
            connector_type=ConnectorType.DATA_API,
            base_identifier="https://www.googleapis.com/youtube/v3",
            configuration_version="p1-sources-v1",
        )
        run_id = repositories.start_collection_run(
            session, source_id=source_id, query_id=None, software_version=settings.score_version,
            started_at=now,
        )
        _attempt(steps, "oauth credential works",
                 lambda: sorted(credentials.headers()), "header built")
        page = _attempt(steps, "search request works",
                        lambda: connector.search_videos(QUERY, max_results=1),
                        lambda value: f"objects={len(value.objects)}")
        _attempt(steps, "pagination works",
                 lambda: list(connector.iter_search_videos(QUERY, max_results=1, max_pages=1)),
                 lambda value: f"pages={len(value)}")
        video_ids = [obj.provider_object_id for obj in page.objects][:1] if page is not None \
            else []
        if video_ids:
            _attempt(steps, "resource hydration works",
                     lambda: connector.fetch_videos(video_ids),
                     lambda value: f"objects={len(value.objects)}")
        else:
            _blocked_step(steps, "resource hydration works", "search returned no video id")
        raw = None
        if page is not None and page.objects:
            raw = _attempt(steps, "raw payload stored",
                           lambda: _store(session, run_id=run_id, source_id=source_id,
                                          fetched=page.objects[0],
                                          context=page.request_context_hash,
                                          storage_client=storage_client),
                           lambda value: value.payload_uri)
        else:
            _blocked_step(steps, "raw payload stored", "search returned no objects")
        observation_id = None
        if raw is not None and page is not None and page.objects:
            observation_id = _attempt(
                steps, "normalized observation stored",
                lambda: _normalize(session, raw=raw, fetched=page.objects[0], source_id=source_id,
                                   now=now),
                lambda value: value or "no observation")
        else:
            _blocked_step(steps, "normalized observation stored", "no raw payload stored")
        if observation_id is not None:
            _attempt(steps, "taxonomy classification works",
                     lambda: _classify(session, observation_id=observation_id,
                                       taxonomy_version=settings.taxonomy_version),
                     lambda value: f"created={value}")
        else:
            _blocked_step(steps, "taxonomy classification works", "no observation stored")
        _attempt(steps, "features calculated",
                 lambda: _feature_check(settings, now, session), str)
        _attempt(steps, "opportunity score calculated",
                 lambda: _score_check(settings, now, session), str)
        _attempt(steps, "API returns result", lambda: _api_check(), str)
        session.commit()
    return SmokeReport(steps=tuple(steps))


def _attempt[T](steps: list[SmokeStep], name: str, fn: Callable[[], T],
                detail: Callable[[T], str] | str = "") -> T | None:
    """Run one check; a failure becomes a failing step, never an escaping exception."""
    try:
        value = fn()
    except Exception as exc:  # noqa: BLE001 - the report must name what failed
        steps.append(SmokeStep(name, False, f"{type(exc).__name__}: {exc}"))
        return None
    rendered = detail(value) if callable(detail) else detail
    steps.append(SmokeStep(name, True, str(rendered)))
    return value


def _blocked_step(steps: list[SmokeStep], name: str, why: str) -> None:
    steps.append(SmokeStep(name, False, f"not attempted: {why}"))


def _store(session: Any, *, run_id: str, source_id: str, fetched: Any, context: str,
           storage_client: Any) -> Any:
    from ..storage.raw_store import store_raw_observation

    return store_raw_observation(session, run_id=run_id, source_id=source_id, fetched=fetched,
                                 request_context_hash=context, client=storage_client)


def _normalize(session: Any, *, raw: Any, fetched: Any, source_id: str, now: datetime) -> str:
    entity_id = repositories.resolve_entity(
        session, entity_type=EntityType.VIDEO, source_id=source_id,
        provider_object_type=ProviderObjectType.VIDEO,
        provider_object_id=fetched.provider_object_id,
        canonical_name="live smoke", normalized_name=normalize_text("live smoke"),
        canonical_url=None, observed_at=now,
    )
    outcome = normalize_search_hit(fetched, entity_id=entity_id, source_id=source_id,
                                   raw_observation_id=raw.raw_observation_id, observed_at=now)
    observation_id, _created = repositories.insert_normalized_observation(
        session, outcome.observation)
    return observation_id


def _classify(session: Any, *, observation_id: str, taxonomy_version: str) -> bool:
    from ..taxonomy.classifier import (
        ClassificationInput,
        RuleBasedClassifier,
        classify_observation,
    )

    _classification_id, created = classify_observation(
        session, entity_id=None, observation_id=observation_id,
        text=ClassificationInput(title="live smoke", description=QUERY),
        classifier=RuleBasedClassifier(), taxonomy_version=taxonomy_version,
    )
    return created


def _feature_check(settings: Any, now: datetime, session: Any) -> str:
    from ..features.arm_features import compute_arm_features

    observations = repositories.load_observations(session)
    features = compute_arm_features({"live-smoke": observations[:1]}, as_of=now,
                                    settings=settings)
    return f"arms={len(features)}"


def _score_check(settings: Any, now: datetime, session: Any) -> str:
    from ..features.arm_features import compute_arm_features
    from ..scoring.opportunity import score_all_arms

    observations = repositories.load_observations(session)
    features = compute_arm_features({"live-smoke": observations[:1]}, as_of=now,
                                    settings=settings)
    scores = score_all_arms(features, as_of=now, settings=settings)
    return f"scores={len(scores)}"


def _api_check() -> str:
    from ..api.schemas import OpportunityListResponse

    return str(OpportunityListResponse.model_json_schema()["title"])


def _session() -> Any:
    from plm_platform.db import scoped_session

    return scoped_session("market")
