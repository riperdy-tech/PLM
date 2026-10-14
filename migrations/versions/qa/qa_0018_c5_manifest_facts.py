"""qa + media schemas: C5 manifests and media packages carry lineage and artifact facts

Revision ID: 0018_c5_manifest_facts
Revises: 0017_music_c4_facts
Create Date: 2026-10-14

ADR-0028:

* ``qa.release_manifests`` gains the experiment lineage, the artifact URI and checksum,
  the measured duration and a separate override record, so P5 serves the full C5 wire
  and an override never rewrites the automated disposition.
* ``media.media_packages`` gains the tracks' common lineage, carried on to C6.

Rows stored before this revision keep NULL; P5 refuses to serve such a manifest (409).
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_c5_manifest_facts"
down_revision = "0017_music_c4_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("release_manifests", sa.Column("lineage_json", sa.JSON(), nullable=True), schema="qa")
    op.add_column("release_manifests", sa.Column("artifact_uri", sa.String(512), nullable=True), schema="qa")
    op.add_column("release_manifests", sa.Column("artifact_sha256", sa.String(64), nullable=True), schema="qa")
    op.add_column(
        "release_manifests",
        sa.Column("measured_duration_seconds", sa.Float(), nullable=True),
        schema="qa",
    )
    op.add_column("release_manifests", sa.Column("override_json", sa.JSON(), nullable=True), schema="qa")
    op.add_column("media_packages", sa.Column("lineage_json", sa.JSON(), nullable=True), schema="media")


def downgrade() -> None:
    op.drop_column("media_packages", "lineage_json", schema="media")
    for column in (
        "override_json",
        "measured_duration_seconds",
        "artifact_sha256",
        "artifact_uri",
        "lineage_json",
    ):
        op.drop_column("release_manifests", column, schema="qa")
