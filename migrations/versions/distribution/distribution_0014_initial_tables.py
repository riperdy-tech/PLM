"""distribution schema initial tables (PHASE-P8 §6, §7, §8, Ruling R9, R10).

Revision ID: 0014_distribution_initial
Revises: 0013_youtube_initial
Create Date: 2026-09-26 18:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_distribution_initial"
down_revision: str | None = "0013_youtube_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. artists
    op.create_table(
        "artists",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("canonical_name", sa.String(255), nullable=False),
        sa.Column("normalized_name", sa.String(255), nullable=False, index=True),
        sa.Column("artist_type", sa.String(32), nullable=False, server_default="virtual"),
        sa.Column("brand_id", sa.String(64), nullable=False, index=True),
        sa.Column("spotify_artist_id", sa.String(64), nullable=True),
        sa.Column("apple_artist_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="distribution",
    )

    # 2. releases
    op.create_table(
        "releases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("release_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("artist_id", sa.String(64), nullable=False, index=True),
        sa.Column("release_type", sa.String(32), nullable=False, server_default="single"),
        sa.Column("release_title", sa.String(255), nullable=False),
        sa.Column("release_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, index=True, server_default="draft"),
        sa.Column("provider", sa.String(64), nullable=False, server_default="mock_distributor"),
        sa.Column("provider_release_id", sa.String(64), nullable=True),
        sa.Column("submission_fingerprint", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("deliveries", sa.JSON(), nullable=False),
        sa.Column("source_manifest_ids", sa.JSON(), nullable=False),
        sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
        sa.Column("experiment_family_id", sa.String(64), nullable=False),
        sa.Column("content_arm_id", sa.String(64), nullable=False),
        sa.Column("content_genome_id", sa.String(64), nullable=False),
        sa.Column("production_directive_id", sa.String(64), nullable=False),
        sa.Column("media_package_id", sa.String(64), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="distribution",
    )

    # 3. tracks
    op.create_table(
        "tracks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("track_id", sa.String(64), nullable=False, index=True),
        sa.Column("release_id", sa.String(64), nullable=False, index=True),
        sa.Column("track_number", sa.Integer(), nullable=False),
        sa.Column("disc_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("version_title", sa.String(255), nullable=True),
        sa.Column("isrc", sa.String(32), nullable=False, unique=True, index=True),
        sa.Column("isrc_source", sa.String(32), nullable=False, server_default="internal_allocation"),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("audio_asset_uri", sa.Text(), nullable=False),
        sa.Column("audio_checksum", sa.String(64), nullable=False),
        sa.Column("explicit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="distribution",
    )

    # 4. provider_reports (Ruling R10)
    op.create_table(
        "provider_reports",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("report_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("provider", sa.String(64), nullable=False, index=True),
        sa.Column("provider_period", sa.String(32), nullable=False, index=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("report_checksum", sa.String(64), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("raw_payload_uri", sa.Text(), nullable=False),
        sa.Column("report_type", sa.String(64), nullable=False, server_default="streaming_statement"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="distribution",
    )


def downgrade() -> None:
    op.drop_table("provider_reports", schema="distribution")
    op.drop_table("tracks", schema="distribution")
    op.drop_table("releases", schema="distribution")
    op.drop_table("artists", schema="distribution")
