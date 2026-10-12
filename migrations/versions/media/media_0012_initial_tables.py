"""media schema initial tables (PHASE-P6 §8, R7, R8, R9).

Revision ID: 0012_media_initial
Revises: 0011_qa_initial
Create Date: 2026-09-26 00:00:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_media_initial"
down_revision: str | None = "0011_qa_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. playlist_sessions
    op.create_table(
        "playlist_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("release_manifest_id", sa.String(64), nullable=False, index=True),
        sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
        sa.Column("content_arm_id", sa.String(64), nullable=False),
        sa.Column("brand_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_duration_seconds", sa.Float(), nullable=False),
        sa.Column("actual_duration_seconds", sa.Float(), nullable=False),
        sa.Column("sequence_version", sa.String(32), nullable=False),
        sa.Column("sequence_checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="media",
    )

    # 2. playlist_tracks
    op.create_table(
        "playlist_tracks",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("playlist_session_id", sa.String(64), nullable=False, index=True),
        sa.Column("song_id", sa.String(64), nullable=False, index=True),
        sa.Column("source_artifact_id", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("start_offset_seconds", sa.Float(), nullable=False),
        sa.Column("end_offset_seconds", sa.Float(), nullable=False),
        sa.Column("transition_type", sa.String(32), nullable=False),
        sa.Column("transition_duration_seconds", sa.Float(), nullable=False),
        sa.Column("sequence_reason", sa.String(128), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("audio_uri", sa.String(512), nullable=False),
        sa.Column("audio_checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="media",
    )

    # 3. visual_sessions
    op.create_table(
        "visual_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("playlist_session_id", sa.String(64), nullable=False, index=True),
        sa.Column("brand_id", sa.String(64), nullable=False),
        sa.Column("brand_version", sa.String(32), nullable=False),
        sa.Column("visual_profile", sa.String(64), nullable=False),
        sa.Column("visual_profile_version", sa.String(32), nullable=False),
        sa.Column("visual_plan_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="media",
    )

    # 4. visual_assets
    op.create_table(
        "visual_assets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("visual_session_id", sa.String(64), nullable=False, index=True),
        sa.Column("asset_type", sa.String(32), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_asset_id", sa.String(64), nullable=False),
        sa.Column("uri", sa.String(512), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("fps", sa.Float(), nullable=False),
        sa.Column("license_status", sa.String(32), nullable=False),
        sa.Column("provenance_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="media",
    )

    # 5. thumbnail_variants
    op.create_table(
        "thumbnail_variants",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("media_package_id", sa.String(64), nullable=False, index=True),
        sa.Column("variant_key", sa.String(64), nullable=False),
        sa.Column("source_assets_json", sa.Text(), nullable=False),
        sa.Column("layout_version", sa.String(32), nullable=False),
        sa.Column("uri", sa.String(512), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("text_payload", sa.Text(), nullable=False),
        sa.Column("predicted_ctr_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="media",
    )

    # 6. render_jobs
    op.create_table(
        "render_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("media_package_id", sa.String(64), nullable=False, index=True),
        sa.Column("render_profile", sa.String(64), nullable=False),
        sa.Column("render_version", sa.String(32), nullable=False),
        sa.Column("job_status", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_manifest_hash", sa.String(64), nullable=False),
        sa.Column("output_checksum", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="media",
    )

    # 7. media_packages
    op.create_table(
        "media_packages",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("media_package_id", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("release_manifest_id", sa.String(64), nullable=False, index=True),
        sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
        sa.Column("content_arm_id", sa.String(64), nullable=False),
        sa.Column("brand_id", sa.String(64), nullable=False),
        sa.Column("playlist_session_id", sa.String(64), nullable=False),
        sa.Column("visual_session_id", sa.String(64), nullable=False),
        sa.Column("video_master_uri", sa.String(512), nullable=False),
        sa.Column("video_checksum", sa.String(64), nullable=False),
        sa.Column("video_duration_seconds", sa.Float(), nullable=False),
        sa.Column("render_profile", sa.String(64), nullable=False),
        sa.Column("audio_master_uri", sa.String(512), nullable=False),
        sa.Column("audio_checksum", sa.String(64), nullable=False),
        sa.Column("thumbnails_json", sa.Text(), nullable=False),
        sa.Column("tracklist_json", sa.Text(), nullable=False),
        sa.Column("chapters_json", sa.Text(), nullable=False),
        sa.Column("metadata_candidates_json", sa.Text(), nullable=False),
        sa.Column("short_source_assets_json", sa.Text(), nullable=False),
        sa.Column("render_report_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("package_version", sa.String(32), nullable=False),
        sa.Column("manifest_uri", sa.String(512), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="media",
    )


def downgrade() -> None:
    op.drop_table("media_packages", schema="media")
    op.drop_table("render_jobs", schema="media")
    op.drop_table("thumbnail_variants", schema="media")
    op.drop_table("visual_assets", schema="media")
    op.drop_table("visual_sessions", schema="media")
    op.drop_table("playlist_tracks", schema="media")
    op.drop_table("playlist_sessions", schema="media")
