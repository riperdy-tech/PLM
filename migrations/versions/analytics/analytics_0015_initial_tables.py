"""analytics schema initial tables (PHASE-P9 §14, §15, §16, §53, §54, Ruling R10, R11).

Revision ID: 0015_analytics_initial
Revises: 0014_distribution_initial
Create Date: 2026-09-26 21:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_analytics_initial"
down_revision: str | None = "0014_distribution_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. video_metrics
    op.create_table(
        "video_metrics",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("video_id", sa.String(64), nullable=False, index=True),
        sa.Column("channel_id", sa.String(64), nullable=False, index=True),
        sa.Column("window", sa.String(32), nullable=False, index=True),
        sa.Column("date_str", sa.String(10), nullable=False, index=True),
        sa.Column("views", sa.Integer, nullable=False, server_default="0"),
        sa.Column("watch_time_minutes", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("average_view_duration_seconds", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("average_percentage_viewed", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("subscribers_gained", sa.Integer, nullable=False, server_default="0"),
        sa.Column("subscribers_lost", sa.Integer, nullable=False, server_default="0"),
        sa.Column("estimated_revenue_usd", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("revenue_status", sa.String(32), nullable=False, server_default="estimated"),
        sa.Column("source", sa.String(32), nullable=False, server_default="youtube_analytics"),
        sa.Column("status", sa.String(32), nullable=False, server_default="valid"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        schema="analytics",
    )

    # 2. track_metrics
    op.create_table(
        "track_metrics",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("isrc", sa.String(32), nullable=False, index=True),
        sa.Column("track_id", sa.String(64), nullable=False, index=True),
        sa.Column("release_id", sa.String(64), nullable=False, index=True),
        sa.Column("window", sa.String(32), nullable=False, index=True),
        sa.Column("date_str", sa.String(10), nullable=False, index=True),
        sa.Column("streams", sa.Integer, nullable=False, server_default="0"),
        sa.Column("reported_revenue_usd", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("provider", sa.String(64), nullable=False, index=True),
        sa.Column("revenue_status", sa.String(32), nullable=False, server_default="reported"),
        sa.Column("source", sa.String(32), nullable=False, server_default="distributor_statement"),
        sa.Column("status", sa.String(32), nullable=False, server_default="valid"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        schema="analytics",
    )

    # 3. revenue_statements (Ruling R10)
    op.create_table(
        "revenue_statements",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False, index=True),
        sa.Column("period", sa.String(32), nullable=False, index=True),
        sa.Column("total_revenue_usd", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("total_streams", sa.Integer, nullable=False, server_default="0"),
        sa.Column("file_hash", sa.String(64), nullable=False, index=True),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        schema="analytics",
    )

    # 4. cost_items
    op.create_table(
        "cost_items",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("entity_id", sa.String(64), nullable=False, index=True),
        sa.Column("entity_type", sa.String(32), nullable=False, index=True),
        sa.Column("category", sa.String(32), nullable=False, index=True),
        sa.Column("amount_usd", sa.Float, nullable=False),
        sa.Column("period", sa.String(32), nullable=False, index=True),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("incurred_at", sa.DateTime(timezone=True), nullable=False),
        schema="analytics",
    )

    # 5. experiment_outcomes
    op.create_table(
        "experiment_outcomes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
        sa.Column("experiment_family_id", sa.String(64), nullable=False),
        sa.Column("content_arm_id", sa.String(64), nullable=False, index=True),
        sa.Column("content_genome_id", sa.String(64), nullable=False),
        sa.Column("unit_id", sa.String(64), nullable=False, index=True),
        sa.Column("observation_window", sa.String(32), nullable=False, index=True),
        sa.Column("primary_metric", sa.String(64), nullable=False, server_default="net_contribution"),
        sa.Column("primary_value", sa.Float, nullable=False),
        sa.Column("normalized_reward", sa.Float, nullable=False),
        sa.Column("reward_version", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("attribution_confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("maturity", sa.String(32), nullable=False, server_default="MATURE"),
        sa.Column("quality_status", sa.String(32), nullable=False, server_default="ELIGIBLE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="analytics",
    )

    # 6. rewards_posted (Ruling R11 feedback ledger)
    op.create_table(
        "rewards_posted",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
        sa.Column("arm_id", sa.String(64), nullable=False, index=True),
        sa.Column("content_id", sa.String(64), nullable=False, index=True),
        sa.Column("observation_window", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("normalized_reward", sa.Float, nullable=False),
        sa.Column("reward_version", sa.String(32), nullable=False),
        sa.Column("attribution_confidence", sa.Float, nullable=False),
        sa.Column("raw_metrics", sa.JSON, nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        schema="analytics",
    )


def downgrade() -> None:
    op.drop_table("rewards_posted", schema="analytics")
    op.drop_table("experiment_outcomes", schema="analytics")
    op.drop_table("cost_items", schema="analytics")
    op.drop_table("revenue_statements", schema="analytics")
    op.drop_table("track_metrics", schema="analytics")
    op.drop_table("video_metrics", schema="analytics")
