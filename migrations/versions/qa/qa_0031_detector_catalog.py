"""qa: rights-detector catalog (candidate fingerprints, reference audio, reference lyrics)

Revision ID: 0031_qa_detector_catalog
Revises: 0030_ops_pipeline_runs
Create Date: 2026-10-15

R4 / ADR-0034. P5's audio fingerprint and lyric similarity detectors read from:

* ``qa.candidate_audio_fingerprints``: one fingerprint per evaluation. The internal
  catalog is the subset whose evaluation issued a release manifest.
* ``qa.reference_audio_fingerprints``: registered reference recordings, one row per
  (ref_id, fingerprint_version).
* ``qa.reference_lyrics``: the reference lyric corpus as shingle hashes only, one row per
  (ref_id, corpus_version).

``quality_gate.storage.detector_catalog`` declares the same tables.
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_qa_detector_catalog"
down_revision = "0030_ops_pipeline_runs"
branch_labels = None
depends_on = None

SCHEMA = "qa"


def upgrade() -> None:
    op.create_table(
        "candidate_audio_fingerprints",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("evaluation_id", sa.String(64), nullable=False, unique=True),
        sa.Column("candidate_id", sa.String(64), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("fingerprint_version", sa.String(64), nullable=False),
        sa.Column("hash_count", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_qa_candidate_audio_fingerprints_candidate_id",
        "candidate_audio_fingerprints",
        ["candidate_id"],
        schema=SCHEMA,
    )
    op.create_table(
        "reference_audio_fingerprints",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("ref_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(256), nullable=True),
        sa.Column("source_note", sa.Text(), nullable=False),
        sa.Column("fingerprint_version", sa.String(64), nullable=False),
        sa.Column("hash_count", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("fingerprint", sa.LargeBinary(), nullable=False),
        sa.Column("fingerprint_sha256", sa.String(64), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "ref_id", "fingerprint_version", name="uq_reference_audio_ref_version"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_qa_reference_audio_fingerprints_ref_id",
        "reference_audio_fingerprints",
        ["ref_id"],
        schema=SCHEMA,
    )
    op.create_table(
        "reference_lyrics",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("ref_id", sa.String(64), nullable=False),
        sa.Column("label", sa.String(256), nullable=True),
        sa.Column("source_note", sa.Text(), nullable=False),
        sa.Column("corpus_version", sa.String(64), nullable=False),
        sa.Column("shingle_count", sa.Integer(), nullable=False),
        sa.Column("shingles", sa.LargeBinary(), nullable=False),
        sa.Column("shingles_sha256", sa.String(64), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ref_id", "corpus_version", name="uq_reference_lyrics_ref_version"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_qa_reference_lyrics_ref_id", "reference_lyrics", ["ref_id"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_qa_reference_lyrics_ref_id", table_name="reference_lyrics", schema=SCHEMA)
    op.drop_table("reference_lyrics", schema=SCHEMA)
    op.drop_index(
        "ix_qa_reference_audio_fingerprints_ref_id",
        table_name="reference_audio_fingerprints",
        schema=SCHEMA,
    )
    op.drop_table("reference_audio_fingerprints", schema=SCHEMA)
    op.drop_index(
        "ix_qa_candidate_audio_fingerprints_candidate_id",
        table_name="candidate_audio_fingerprints",
        schema=SCHEMA,
    )
    op.drop_table("candidate_audio_fingerprints", schema=SCHEMA)
