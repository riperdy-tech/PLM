# tests/unit/market/test_channel_hydration.py
"""R2: channel statistics reach the observations, so demand (25% weight) can be known.

`connectors/youtube.py::fetch_channels` existed but nothing called it, and every
video observation carried `subscriber_count = unknown`, so §17.3's demand component
was unknown for every arm. Hydration now fetches each batch's distinct channels once
and stamps the channel id and subscriber count on the video observations.

These tests drive `hydrate_and_store` over the recorded YouTube fixtures through
`MockHTTP` (no network, SQLite market schema). They prove the collection path's
behaviour against recorded provider shapes, not the live provider.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from plm_platform.config import Secret
from plm_platform.storage import StoredObject, sha256_digest
from plm_platform.testing.http import MockHTTP

from services.market_intelligence.collection.collectors import hydrate_and_store
from services.market_intelligence.collection.rate_limit import (
    QuotaBudget,
    QuotaExceededError,
    RequestBudget,
)
from services.market_intelligence.config import load_market_settings
from services.market_intelligence.connectors.youtube import (
    BASE_URL,
    YouTubeCredentials,
    YouTubeDataAPIConnector,
)
from services.market_intelligence.domain.enums import ConnectorType, ProviderName
from services.market_intelligence.features.arm_features import compute_arm_features
from services.market_intelligence.storage import repositories

NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)
SETTINGS = load_market_settings()
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "market" / "youtube"
CHANNEL_A = "UCaaaaaaaaaaaaaaaaaaaaaa"  # 820,000 subscribers in the fixture
CHANNEL_B = "UCbbbbbbbbbbbbbbbbbbbbbb"  # hiddenSubscriberCount: true


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _memory_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    def put(prefix, resource_path, data, *, content_type="application/octet-stream",
            settings=None, client=None) -> StoredObject:
        digest = sha256_digest(data)
        return StoredObject(key=f"{prefix}{resource_path}/{digest}", digest=digest,
                            size=len(data))

    monkeypatch.setattr("services.market_intelligence.storage.raw_store.put_content", put)


def _source(session) -> str:
    return repositories.upsert_source(
        session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )


def _connector(mock: MockHTTP, *, units: int = 1_000) -> YouTubeDataAPIConnector:
    return YouTubeDataAPIConnector(
        client=mock.client(base_url=BASE_URL),
        credentials=YouTubeCredentials(api_key=Secret("unit-test")),
        quota=QuotaBudget(units), request_budget=RequestBudget(50),
        sleep=lambda _seconds: None, observed_at=lambda: NOW,
    )


def _hydrate(session, mock: MockHTTP, video_ids: list[str], *, units: int = 1_000):
    source_id = _source(session)
    run_id = repositories.start_collection_run(
        session, source_id=source_id, query_id=None, software_version="p1-v1", started_at=NOW,
    )
    summary = hydrate_and_store(
        session, connector=_connector(mock, units=units), source_id=source_id, run_id=run_id,
        video_ids=video_ids, settings=SETTINGS, now=NOW,
    )
    return source_id, summary


def _aware(session, source_id: str):
    """SQLite hands timestamps back naive; Postgres returns them aware (UTC)."""
    def utc(moment):
        return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=UTC)

    return [replace(o, observed_at=utc(o.observed_at), published_at=utc(o.published_at))
            for o in repositories.load_observations(session, source_id=source_id)]


def test_hydration_fetches_the_batch_channels_once_and_stamps_subscribers(
    market_session,
) -> None:
    mock = MockHTTP()
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))
    mock.enqueue_json(_fixture("channels_lofi_batch.json"))

    source_id, summary = _hydrate(market_session, mock, ["vid00000001", "vid00000002"])

    assert summary.status.value == "succeeded"
    assert len(mock.requests) == 2
    mock.assert_request(1, path="/youtube/v3/channels")
    mock.assert_query_params(1, id=f"{CHANNEL_A},{CHANNEL_B}")
    observations = list(repositories.load_observations(market_session, source_id=source_id))
    assert len(observations) == 2  # channel payloads are evidence, not arm observations
    by_channel = {o.channel_id: o for o in observations}
    assert set(by_channel) == {CHANNEL_A, CHANNEL_B}
    assert by_channel[CHANNEL_A].subscriber_count.unwrap() == 820_000
    # a hidden subscriber count is unknown, never zero
    assert by_channel[CHANNEL_B].subscriber_count.is_unknown
    assert summary.raw_count == 4  # two video payloads + two channel payloads


def test_demand_is_known_once_channels_are_fetched(market_session) -> None:
    """The R2 finding end to end: demand needs subscribers, and now it has them."""
    mock = MockHTTP()
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))
    mock.enqueue_json(_fixture("channels_lofi_batch.json"))
    source_id, _summary = _hydrate(market_session, mock, ["vid00000001", "vid00000002"])

    observations = _aware(market_session, source_id)
    features = compute_arm_features({"arm": observations}, as_of=NOW, settings=SETTINGS)
    demand = features["arm"].demand
    assert demand.median_pull_per_day.is_known
    assert demand.active_channel_audience.unwrap() == 820_000


def test_a_failed_channel_fetch_leaves_subscribers_unknown_and_the_run_partial(
    market_session,
) -> None:
    mock = MockHTTP()
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))
    mock.enqueue_status(404)

    source_id, summary = _hydrate(market_session, mock, ["vid00000001", "vid00000002"])

    assert summary.status.value == "partial"
    assert summary.error_classes == ("NOT_FOUND",)
    observations = _aware(market_session, source_id)
    assert len(observations) == 2  # the videos are kept
    assert all(o.subscriber_count.is_unknown for o in observations)
    assert {o.channel_id for o in observations} == {CHANNEL_A, CHANNEL_B}
    features = compute_arm_features({"arm": observations}, as_of=NOW, settings=SETTINGS)
    assert features["arm"].demand.median_pull_per_day.is_unknown


def test_channels_shared_by_several_videos_are_requested_once(market_session) -> None:
    videos = _fixture("videos_multi_batch.json")
    ids = [item["id"] for item in videos["items"]]
    mock = MockHTTP()
    mock.enqueue_json(videos)
    mock.enqueue_json(_fixture("channels_lofi_batch.json"))

    source_id, _summary = _hydrate(market_session, mock, ids)

    assert len(mock.requests) == 2
    mock.assert_query_params(1, id=f"{CHANNEL_A},{CHANNEL_B}")
    observations = repositories.load_observations(market_session, source_id=source_id)
    from_a = [o for o in observations if o.channel_id == CHANNEL_A]
    assert len(from_a) == 3
    assert all(o.subscriber_count.unwrap() == 820_000 for o in from_a)


def test_a_payload_for_a_channel_that_was_not_requested_is_not_attributed(
    market_session,
) -> None:
    channels = _fixture("channels_lofi_batch.json")
    channels["items"][0]["id"] = "UCzzzzzzzzzzzzzzzzzzzzzz"  # not in the batch
    mock = MockHTTP()
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))
    mock.enqueue_json(channels)

    source_id, _summary = _hydrate(market_session, mock, ["vid00000001", "vid00000002"])

    observations = repositories.load_observations(market_session, source_id=source_id)
    assert all(o.subscriber_count.is_unknown for o in observations)


def test_running_out_of_budget_at_the_channel_call_keeps_the_videos_and_reraises(
    market_session,
) -> None:
    mock = MockHTTP()
    mock.enqueue_json(_fixture("videos_lofi_batch.json"))
    source_id = _source(market_session)
    run_id = repositories.start_collection_run(
        market_session, source_id=source_id, query_id=None, software_version="p1-v1",
        started_at=NOW,
    )
    with pytest.raises(QuotaExceededError):
        hydrate_and_store(
            market_session, connector=_connector(mock, units=1), source_id=source_id,
            run_id=run_id, video_ids=["vid00000001", "vid00000002"], settings=SETTINGS,
            now=NOW,
        )
    assert len(mock.requests) == 1  # the channels call never left
    observations = repositories.load_observations(market_session, source_id=source_id)
    assert len(observations) == 2
    assert all(o.subscriber_count.is_unknown for o in observations)
