"""youtube schema initial tables (PHASE-P7 §8, Ruling R9).

Revision ID: 0013_youtube_initial
Revises: 0012_media_initial
Create Date: 2026-09-26 12:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_youtube_initial"
down_revision: str | None = "0012_media_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. channel_profiles
    op.create_table(
        "channel_profiles",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("channel_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("channel_name", sa.String(255), nullable=False),
        sa.Column("brand_id", sa.String(64), nullable=False, index=True),
        sa.Column("oauth_credential_ref", sa.String(128), nullable=False),
        sa.Column("default_language", sa.String(16), nullable=False, server_default="en"),
        sa.Column("default_country", sa.String(8), nullable=False, server_default="US"),
        sa.Column("default_category_id", sa.String(16), nullable=False, server_default="10"),
        sa.Column("default_privacy_mode", sa.String(32), nullable=False, server_default="private"),
        sa.Column("default_made_for_kids", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("default_contains_synthetic_media", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("default_publish_timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("operating_mode", sa.String(32), nullable=False, server_default="dry_run"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="youtube",
    )

    # 2. publish_manifests
    op.create_table(
        "publish_manifests",
        sa.Column("manifest_id", sa.String(64), primary_key=True),
        sa.Column("channel_id", sa.String(64), nullable=False, index=True),
        sa.Column("media_package_id", sa.String(64), nullable=False, index=True),
        sa.Column("release_manifest_id", sa.String(64), nullable=False, index=True),
        sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
        sa.Column("experiment_family_id", sa.String(64), nullable=False),
        sa.Column("content_arm_id", sa.String(64), nullable=False),
        sa.Column("content_genome_id", sa.String(64), nullable=False),
        sa.Column("production_directive_id", sa.String(64), nullable=False),
        sa.Column("brand_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("video_master_uri", sa.Text(), nullable=False),
        sa.Column("video_checksum", sa.String(64), nullable=False),
        sa.Column("video_duration_seconds", sa.Float(), nullable=False),
        sa.Column("thumbnail_uri", sa.Text(), nullable=False),
        sa.Column("thumbnail_checksum", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("category_id", sa.String(16), nullable=False, server_default="10"),
        sa.Column("privacy_status", sa.String(32), nullable=False),
        sa.Column("publish_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("contains_synthetic_media", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("made_for_kids", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("playlist_id", sa.String(64), nullable=True),
        sa.Column("playlist_title", sa.String(255), nullable=True),
        sa.Column("metadata_version", sa.String(32), nullable=False, server_default="v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="youtube",
    )

    # 3. publish_attempts
    op.create_table(
        "publish_attempts",
        sa.Column("attempt_id", sa.String(64), primary_key=True),
        sa.Column("manifest_id", sa.String(64), nullable=False, index=True),
        sa.Column("channel_id", sa.String(64), nullable=False, index=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False, index=True),
        sa.Column("state", sa.String(32), nullable=False, index=True),
        sa.Column("operating_mode", sa.String(32), nullable=False),
        sa.Column("upload_session_uri", sa.Text(), nullable=True),
        sa.Column("youtube_video_id", sa.String(64), nullable=True, index=True),
        sa.Column("youtube_playlist_id", sa.String(64), nullable=True),
        sa.Column("youtube_playlist_item_id", sa.String(64), nullable=True),
        sa.Column("thumbnail_uploaded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("bytes_uploaded", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema="youtube",
    )

    # 4. publication_records
    op.create_table(
        "publication_records",
        sa.Column("publication_record_id", sa.String(64), primary_key=True),
        sa.Column("youtube_video_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("channel_id", sa.String(64), nullable=False, index=True),
        sa.Column("publish_attempt_id", sa.String(64), nullable=False, index=True),
        sa.Column("media_package_id", sa.String(64), nullable=False, index=True),
        sa.Column("release_manifest_id", sa.String(64), nullable=False, index=True),
        sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
        sa.Column("experiment_family_id", sa.String(64), nullable=False),
        sa.Column("content_arm_id", sa.String(64), nullable=False),
        sa.Column("content_genome_id", sa.String(64), nullable=False),
        sa.Column("production_directive_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("privacy_status", sa.String(32), nullable=False),
        sa.Column("contains_synthetic_media", sa.Boolean(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("video_url", sa.Text(), nullable=False),
        sa.Column("thumbnail_url", sa.Text(), nullable=True),
        sa.Column("playlist_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="youtube",
    )


def downgrade() -> None:
    op.drop_table("publication_records", schema="youtube")
    op.drop_table("publish_attempts", schema="youtube")
    op.drop_table("publish_manifests", schema="youtube")
    op.drop_table("channel_profiles", schema="youtube")
