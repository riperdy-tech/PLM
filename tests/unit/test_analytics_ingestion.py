"""Unit tests for metrics and statement ingestion (P9 §16, §53, §54, Ruling R10)."""

from __future__ import annotations

from analytics.domain.enums import CostCategory, ObservationWindow
from analytics.ingestion.cost import ingest_cost_entry
from analytics.ingestion.distributor import ingest_distributor_statement
from analytics.ingestion.youtube import ingest_youtube_metrics
from analytics.storage.repositories import (
    get_costs_for_entity,
    get_track_metrics,
    get_video_metrics,
)

from tests.support.analytics_db import analytics_db_session


def test_youtube_metrics_ingestion() -> None:
    with analytics_db_session() as session:
        obs = ingest_youtube_metrics(
            session,
            video_id="vid_yt_001",
            channel_id="chan_001",
            date_str="2026-09-26",
            views=25000,
            watch_time_minutes=850.2,
            average_view_duration_seconds=122.5,
            average_percentage_viewed=68.2,
            subscribers_gained=45,
            subscribers_lost=3,
            estimated_revenue_usd=82.40,
            window=ObservationWindow.W_24H,
        )

        assert obs.video_id == "vid_yt_001"
        assert obs.views == 25000

        # Retrieve
        fetched = get_video_metrics(session, "vid_yt_001")
        assert len(fetched) == 1
        assert fetched[0].views == 25000
        assert fetched[0].estimated_revenue_usd == 82.40


def test_distributor_statement_ingestion_and_deduplication() -> None:
    with analytics_db_session() as session:
        csv_data = "isrc,track_id,streams,revenue_usd\nUSPLM2600001,trk_01,10000,40.00\nUSPLM2600002,trk_02,5000,20.00\n"

        stmt1, tracks1 = ingest_distributor_statement(
            session,
            provider="spotify",
            period="2026-09",
            raw_content=csv_data,
            source_reference="spotify_statement_09.csv",
        )

        assert stmt1.total_revenue_usd == 60.0
        assert stmt1.total_streams == 15000
        assert len(tracks1) == 2

        # Second ingestion of identical content is deduplicated
        stmt2, tracks2 = ingest_distributor_statement(
            session,
            provider="spotify",
            period="2026-09",
            raw_content=csv_data,
            source_reference="spotify_statement_09.csv",
        )
        assert stmt2.id == stmt1.id
        assert len(tracks2) == 0  # no duplicates inserted

        # Verify track metrics
        t_metrics = get_track_metrics(session, "trk_01")
        assert len(t_metrics) == 1
        assert t_metrics[0].streams == 10000
        assert t_metrics[0].reported_revenue_usd == 40.00


def test_cost_item_ingestion() -> None:
    with analytics_db_session() as session:
        cost = ingest_cost_entry(
            session,
            entity_id="vid_yt_001",
            entity_type="video",
            category=CostCategory.COMPUTE,
            amount_usd=15.75,
            period="2026-09",
            description="GPU rendering compute cost",
        )

        assert cost.amount_usd == 15.75

        costs = get_costs_for_entity(session, "vid_yt_001")
        assert len(costs) == 1
        assert costs[0].category == CostCategory.COMPUTE
