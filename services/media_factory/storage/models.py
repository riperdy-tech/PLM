"""SQLAlchemy metadata and table declarations for schema `media` (PHASE-P6 §8, R7, R8, R9)."""

from __future__ import annotations

import sqlalchemy as sa

MEDIA_METADATA = sa.MetaData(schema="media")

playlist_sessions_table = sa.Table(
    "playlist_sessions",
    MEDIA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("release_manifest_id", sa.String(64), nullable=False, index=True),
    sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
    sa.Column("content_arm_id", sa.String(64), nullable=False),
    sa.Column("brand_id", sa.String(64), nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("requested_duration_seconds", sa.Float, nullable=False),
    sa.Column("actual_duration_seconds", sa.Float, nullable=False),
    sa.Column("sequence_version", sa.String(32), nullable=False),
    sa.Column("sequence_checksum", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

playlist_tracks_table = sa.Table(
    "playlist_tracks",
    MEDIA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("playlist_session_id", sa.String(64), nullable=False, index=True),
    sa.Column("song_id", sa.String(64), nullable=False, index=True),
    sa.Column("source_artifact_id", sa.String(64), nullable=False),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("start_offset_seconds", sa.Float, nullable=False),
    sa.Column("end_offset_seconds", sa.Float, nullable=False),
    sa.Column("transition_type", sa.String(32), nullable=False),
    sa.Column("transition_duration_seconds", sa.Float, nullable=False),
    sa.Column("sequence_reason", sa.String(128), nullable=False),
    sa.Column("title", sa.String(256), nullable=False),
    sa.Column("audio_uri", sa.String(512), nullable=False),
    sa.Column("audio_checksum", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

visual_sessions_table = sa.Table(
    "visual_sessions",
    MEDIA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("playlist_session_id", sa.String(64), nullable=False, index=True),
    sa.Column("brand_id", sa.String(64), nullable=False),
    sa.Column("brand_version", sa.String(32), nullable=False),
    sa.Column("visual_profile", sa.String(64), nullable=False),
    sa.Column("visual_profile_version", sa.String(32), nullable=False),
    sa.Column("visual_plan_json", sa.Text, nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

visual_assets_table = sa.Table(
    "visual_assets",
    MEDIA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("visual_session_id", sa.String(64), nullable=False, index=True),
    sa.Column("asset_type", sa.String(32), nullable=False),
    sa.Column("source_type", sa.String(64), nullable=False),
    sa.Column("provider", sa.String(64), nullable=False),
    sa.Column("provider_asset_id", sa.String(64), nullable=False),
    sa.Column("uri", sa.String(512), nullable=False),
    sa.Column("checksum", sa.String(64), nullable=False),
    sa.Column("width", sa.Integer, nullable=False),
    sa.Column("height", sa.Integer, nullable=False),
    sa.Column("duration_seconds", sa.Float, nullable=False),
    sa.Column("fps", sa.Float, nullable=False),
    sa.Column("license_status", sa.String(32), nullable=False),
    sa.Column("provenance_id", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

thumbnail_variants_table = sa.Table(
    "thumbnail_variants",
    MEDIA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("media_package_id", sa.String(64), nullable=False, index=True),
    sa.Column("variant_key", sa.String(64), nullable=False),
    sa.Column("source_assets_json", sa.Text, nullable=False),
    sa.Column("layout_version", sa.String(32), nullable=False),
    sa.Column("uri", sa.String(512), nullable=False),
    sa.Column("checksum", sa.String(64), nullable=False),
    sa.Column("width", sa.Integer, nullable=False),
    sa.Column("height", sa.Integer, nullable=False),
    sa.Column("text_payload", sa.Text, nullable=False),
    sa.Column("predicted_ctr_score", sa.Float, nullable=True),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

render_jobs_table = sa.Table(
    "render_jobs",
    MEDIA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("media_package_id", sa.String(64), nullable=False, index=True),
    sa.Column("render_profile", sa.String(64), nullable=False),
    sa.Column("render_version", sa.String(32), nullable=False),
    sa.Column("job_status", sa.String(32), nullable=False),
    sa.Column("priority", sa.String(32), nullable=False),
    sa.Column("attempt_count", sa.Integer, nullable=False),
    sa.Column("worker_id", sa.String(64), nullable=True),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("input_manifest_hash", sa.String(64), nullable=False),
    sa.Column("output_checksum", sa.String(64), nullable=True),
    sa.Column("error_code", sa.String(64), nullable=True),
    sa.Column("error_detail", sa.Text, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

# B02 (ADR-0043): append-only evidence that one claimed render execution has
# stopped and that its claim-scoped artifacts were inventoried. Quarantine and
# retry staging require these rows instead of caller-asserted booleans.
render_recovery_evidence_table = sa.Table(
    "render_recovery_evidence",
    MEDIA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("render_job_id", sa.String(64), nullable=False, index=True),
    sa.Column("evidence_kind", sa.String(32), nullable=False),
    sa.Column("method", sa.String(64), nullable=False),
    sa.Column("worker_id", sa.String(64), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("input_manifest_hash", sa.String(64), nullable=False),
    sa.Column("evidence_ref", sa.Text, nullable=True),
    sa.Column("detail_json", sa.JSON, nullable=False),
    sa.Column("recorded_by", sa.String(128), nullable=False),
    sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "render_job_id", "evidence_kind", name="uq_render_recovery_evidence_job_kind",
    ),
)

media_packages_table = sa.Table(
    "media_packages",
    MEDIA_METADATA,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("media_package_id", sa.String(64), nullable=False, unique=True, index=True),
    sa.Column("release_manifest_id", sa.String(64), nullable=False, index=True),
    sa.Column("experiment_id", sa.String(64), nullable=False, index=True),
    sa.Column("content_arm_id", sa.String(64), nullable=False),
    # ExperimentLineage (ADR-0028); NULL only for packages stored before it.
    sa.Column("lineage_json", sa.JSON, nullable=True),
    sa.Column("brand_id", sa.String(64), nullable=False),
    sa.Column("playlist_session_id", sa.String(64), nullable=False),
    sa.Column("visual_session_id", sa.String(64), nullable=False),
    sa.Column("video_master_uri", sa.String(512), nullable=False),
    sa.Column("video_checksum", sa.String(64), nullable=False),
    sa.Column("video_duration_seconds", sa.Float, nullable=False),
    sa.Column("render_profile", sa.String(64), nullable=False),
    sa.Column("audio_master_uri", sa.String(512), nullable=False),
    sa.Column("audio_checksum", sa.String(64), nullable=False),
    sa.Column("thumbnails_json", sa.Text, nullable=False),
    sa.Column("tracklist_json", sa.Text, nullable=False),
    sa.Column("chapters_json", sa.Text, nullable=False),
    sa.Column("metadata_candidates_json", sa.Text, nullable=False),
    sa.Column("short_source_assets_json", sa.Text, nullable=False),
    sa.Column("render_report_json", sa.Text, nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("package_version", sa.String(32), nullable=False),
    sa.Column("manifest_uri", sa.String(512), nullable=False),
    sa.Column("manifest_hash", sa.String(64), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)
