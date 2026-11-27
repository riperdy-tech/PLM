"""P1 collection failures stop, reschedule and never mark a query as run (review #41).

Before: YouTube's `quotaExceeded` was an ordinary connector error, so the run tried
every remaining query (each another refused request) and then marked them all as run;
a `RequestBudgetExceededError` was not caught at all, so the session rolled back and
Temporal's retry repeated the paid searches. Fixtures only: no YouTube call is made.
"""

from __future__ import annotations

from typing import Any

from market_intelligence.cli import _run_due_collection
from market_intelligence.collection.rate_limit import QuotaBudget, RequestBudget
from market_intelligence.connectors.youtube import (
    BASE_URL,
    YouTubeCredentials,
    YouTubeDataAPIConnector,
)
from market_intelligence.domain.enums import QueryType
from market_intelligence.storage import repositories
from sqlalchemy.orm import Session

from tests.support.http import MockHTTP
from tests.unit.market.test_daily_run_quota import (  # noqa: F401 - pytest fixture
    NOW,
    SETTINGS,
    _fixture,
    _install,
    memory_objects,
)

QUOTA_BODY = {"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}}


def _two_queries(market_session: Session) -> None:
    source_id = _install(market_session)
    repositories.upsert_query(
        market_session, source_id=source_id, query_text="minimal pulse focus",
        query_type=QueryType.MUSIC_GENRE, locale="en", country_code="US", language="english",
        seed_group="bootstrap", priority=40, query_version="p1-queries-v1",
    )


def _connector(mock: MockHTTP, *, requests: int = 50) -> YouTubeDataAPIConnector:
    from plm_platform.config import Secret

    return YouTubeDataAPIConnector(
        client=mock.client(base_url=BASE_URL),
        credentials=YouTubeCredentials(api_key=Secret("unit-test")),
        quota=QuotaBudget(SETTINGS.quota_units_per_run), request_budget=RequestBudget(requests),
    )


def _rows(market_session: Session) -> dict[str, Any]:
    return {row["query_text"]: row for row in repositories.load_queries(market_session)}


def test_provider_quota_exceeded_stops_the_run_and_marks_nothing_run(
        market_session: Session, memory_objects: object,  # noqa: F811
        platform_env: dict[str, str]) -> None:
    _two_queries(market_session)
    mock = MockHTTP(repeat_last=True)
    mock.enqueue_json(QUOTA_BODY, status=403)

    result = _run_due_collection(market_session, now=NOW, settings=SETTINGS,
                                 connector=_connector(mock))

    assert result["quota_exhausted"] is True
    assert len(mock.requests) == 1, "the refused quota is not asked again per query"
    rows = _rows(market_session)
    assert rows["lofi"]["last_run_at"] is None
    assert rows["minimal pulse focus"]["last_run_at"] is None
    assert all(row["next_run_at"] is not None for row in rows.values()), "rescheduled"
    run = repositories.load_run(market_session, result["run_id"])
    assert run is not None and run["error_summary"] == {"QUOTA_EXCEEDED": 2}


def test_the_request_budget_running_out_is_caught_not_retried(
        market_session: Session, memory_objects: object,  # noqa: F811
        platform_env: dict[str, str]) -> None:
    _install(market_session)
    mock = MockHTTP(repeat_last=True)
    mock.enqueue_json(_fixture("search_lofi_p1.json"))
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))

    # one request: the search fits, the videos.list hydration does not
    result = _run_due_collection(market_session, now=NOW, settings=SETTINGS,
                                 connector=_connector(mock, requests=1))

    assert result["quota_exhausted"] is True and result["status"] == "partial"
    assert _rows(market_session)["lofi"]["last_run_at"] is None, "hydration did not finish"
    run = repositories.load_run(market_session, result["run_id"])
    assert run is not None and run["error_summary"] == {"REQUEST_BUDGET_EXCEEDED": 1}


def test_a_failed_search_is_rescheduled_and_only_the_good_query_counts_as_run(
        market_session: Session, memory_objects: object,  # noqa: F811
        platform_env: dict[str, str]) -> None:
    _two_queries(market_session)
    mock = MockHTTP(repeat_last=True)
    mock.enqueue_status(400)  # the first (higher-priority) search is refused for good
    mock.enqueue_json(_fixture("search_lofi_p1.json"))
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))
    mock.enqueue_json(_fixture("channels_lofi_batch.json"))

    result = _run_due_collection(market_session, now=NOW, settings=SETTINGS,
                                 connector=_connector(mock))

    assert result["status"] == "partial"
    rows = _rows(market_session)
    assert rows["lofi"]["last_run_at"] is None
    assert rows["lofi"]["next_run_at"] is not None
    assert rows["minimal pulse focus"]["last_run_at"] is not None
