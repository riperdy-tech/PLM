"""experiments schema: the playlist spec declares its duration range and planned supply

Revision ID: 0034_experiments_playlist_range
Revises: 0033_youtube_manifest_tracks
Create Date: 2026-10-15

Owner decision 2026-10-15 (R5): a playlist video's length is a declared range (the
directive bucket ``45_75m`` is 45-75 minutes); the target is only its planning midpoint.

* ``playlist_specs`` gains the range (``duration_min_seconds`` / ``duration_max_seconds``),
  the planning midpoint and the supply P3 planned for it (planned track length, base and
  spare counts, the extension bound, the spare ratio and the supply policy version). The
  exact ``total_duration_minutes`` is dropped. The new columns are nullable only because
  rows written earlier lack them; P3 refuses such a package on load
  (``legacy_package_without_duration_range``) rather than giving it a range.
* ``track_specs`` gains ``spec_index`` (package order) and ``extension_id`` (the top-up
  that added it; NULL for specs planned at materialization).
* ``package_extensions`` records each top-up (``POST /experiments/v1/packages/{id}/extend``),
  unique per (package, idempotency key).

Downgrade restores ``total_duration_minutes`` from the planning midpoint (rounded down to
whole minutes; NULL where no midpoint was stored) and drops the rest.
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_experiments_playlist_range"
down_revision = "0033_youtube_manifest_tracks"
branch_labels = None
depends_on = None

SCHEMA = "experiments"

_PLAYLIST_COLUMNS = (
    ("duration_min_seconds", sa.Integer()),
    ("duration_max_seconds", sa.Integer()),
    ("target_duration_seconds", sa.Integer()),
    ("planned_track_duration_seconds", sa.Integer()),
    ("base_track_count", sa.Integer()),
    ("spare_track_count", sa.Integer()),
    ("max_extension_track_count", sa.Integer()),
    ("spare_ratio", sa.String(16)),
    ("supply_policy_version", sa.String(64)),
)


def upgrade() -> None:
    for name, type_ in _PLAYLIST_COLUMNS:
        op.add_column("playlist_specs", sa.Column(name, type_, nullable=True), schema=SCHEMA)
    op.drop_column("playlist_specs", "total_duration_minutes", schema=SCHEMA)

    op.add_column("track_specs", sa.Column("spec_index", sa.Integer(), nullable=True),
                  schema=SCHEMA)
    op.add_column("track_specs", sa.Column("extension_id", sa.String(64), nullable=True),
                  schema=SCHEMA)
    op.create_index("ix_experiments_track_specs_extension_id", "track_specs",
                    ["extension_id"], schema=SCHEMA)

    op.create_table(
        "package_extensions",
        sa.Column("extension_id", sa.String(64), primary_key=True),
        sa.Column("package_id", sa.String(64),
                  sa.ForeignKey(f"{SCHEMA}.content_packages.package_id"), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("track_count", sa.Integer(), nullable=False),
        sa.Column("track_spec_ids_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("package_id", "idempotency_key", name="uq_package_extensions_key"),
        schema=SCHEMA,
    )
    op.create_index("ix_experiments_package_extensions_package_id", "package_extensions",
                    ["package_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_experiments_package_extensions_package_id",
                  table_name="package_extensions", schema=SCHEMA)
    op.drop_table("package_extensions", schema=SCHEMA)
    op.drop_index("ix_experiments_track_specs_extension_id", table_name="track_specs",
                  schema=SCHEMA)
    op.drop_column("track_specs", "extension_id", schema=SCHEMA)
    op.drop_column("track_specs", "spec_index", schema=SCHEMA)

    op.add_column("playlist_specs",
                  sa.Column("total_duration_minutes", sa.Integer(), nullable=True),
                  schema=SCHEMA)
    op.execute(
        f"UPDATE {SCHEMA}.playlist_specs SET total_duration_minutes = "
        "target_duration_seconds / 60 WHERE target_duration_seconds IS NOT NULL"
    )
    for name, _ in reversed(_PLAYLIST_COLUMNS):
        op.drop_column("playlist_specs", name, schema=SCHEMA)
