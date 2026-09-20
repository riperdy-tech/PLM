"""market schema: initial P1 tables

Revision ID: 0002_market_initial
Revises: 0001_baseline
Create Date: 2026-09-20

Self-contained DDL on purpose: a migration must reproduce this schema even after
`storage/models.py` moves on. `tests/integration/market/test_schema_matches_models.py`
fails when the two drift apart. Enum-ish columns are text (L4).
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_market_initial"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

SCHEMA = "market"

MARKET_TABLES = (
    "market_sources", "market_queries", "market_collection_runs", "market_raw_observations",
    "market_seed_evidence", "market_entities", "market_entity_identities",
    "market_observations", "market_taxonomy_classifications", "market_arms",
    "market_opportunity_scores", "market_change_events", "market_quarantined_records",
    "market_snapshots",
)

TS = sa.DateTime(timezone=True)
UUID = sa.Uuid(as_uuid=False)
JSON = sa.JSON


def _pk() -> sa.Column:
    return sa.Column("id", UUID, primary_key=True)


def _created_at() -> sa.Column:
    return sa.Column("created_at", TS, nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "market_sources",
        _pk(),
        sa.Column("provider_name", sa.Text, nullable=False, unique=True),
        sa.Column("connector_type", sa.Text, nullable=False),
        sa.Column("base_identifier", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("configuration_version", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_table(
        "market_queries",
        _pk(),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=False),
        sa.Column("query_text", sa.Text, nullable=False),
        sa.Column("query_fingerprint", sa.Text, nullable=False),
        sa.Column("query_type", sa.Text, nullable=False),
        sa.Column("locale", sa.Text, nullable=False),
        sa.Column("country_code", sa.Text, nullable=False),
        sa.Column("language", sa.Text, nullable=False),
        sa.Column("seed_group", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("priority", sa.Integer, nullable=False, server_default="50"),
        sa.Column("first_seen_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("last_run_at", TS, nullable=True),
        sa.Column("next_run_at", TS, nullable=True),
        sa.Column("query_version", sa.Text, nullable=False),
        sa.Column("parent_query_id", UUID,
                  sa.ForeignKey("market.market_queries.id"), nullable=True),
        sa.Column("proposed_by_model", sa.Text, nullable=True),
        sa.Column("model_name", sa.Text, nullable=True),
        sa.Column("model_version", sa.Text, nullable=True),
        sa.Column("proposal_confidence", sa.Float, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_id", "query_fingerprint",
                            name="uq_market_queries_fingerprint"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_collection_runs",
        _pk(),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=False),
        sa.Column("query_id", UUID, sa.ForeignKey("market.market_queries.id"), nullable=True),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("finished_at", TS, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("request_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rate_limit_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("raw_observation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("normalized_observation_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_summary", JSON, nullable=True),
        sa.Column("software_version", sa.Text, nullable=False),
        sa.Column("cost_api_calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_llm_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cost_llm_cost_estimate", sa.Float, nullable=False, server_default="0"),
        sa.Column("cost_storage_bytes", sa.BigInteger, nullable=False, server_default="0"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_raw_observations",
        _pk(),
        sa.Column("collection_run_id", UUID,
                  sa.ForeignKey("market.market_collection_runs.id"), nullable=False),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=False),
        sa.Column("provider_object_type", sa.Text, nullable=False),
        sa.Column("provider_object_id", sa.Text, nullable=False),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("request_context_hash", sa.Text, nullable=False),
        sa.Column("payload_json", JSON, nullable=False),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column("payload_uri", sa.Text, nullable=False),
        sa.Column("schema_version", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_id", "provider_object_type", "provider_object_id",
                            "observed_at", "payload_hash", name="uq_market_raw_dedupe"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_seed_evidence",
        _pk(),
        sa.Column("query_id", UUID, sa.ForeignKey("market.market_queries.id"), nullable=False),
        sa.Column("raw_observation_id", UUID,
                  sa.ForeignKey("market.market_raw_observations.id"), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("query_id", "raw_observation_id", name="uq_market_seed_evidence"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_entities",
        _pk(),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("canonical_name", sa.Text, nullable=False),
        sa.Column("normalized_name", sa.Text, nullable=False),
        sa.Column("canonical_url", sa.Text, nullable=True),
        sa.Column("language", sa.Text, nullable=True),
        sa.Column("country_code", sa.Text, nullable=True),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_entity_identities",
        _pk(),
        sa.Column("entity_id", UUID, sa.ForeignKey("market.market_entities.id"), nullable=False),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=False),
        sa.Column("provider_object_type", sa.Text, nullable=False),
        sa.Column("provider_object_id", sa.Text, nullable=False),
        sa.Column("first_seen_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.UniqueConstraint("source_id", "provider_object_type", "provider_object_id",
                            name="uq_market_entity_identity"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_observations",
        _pk(),
        sa.Column("entity_id", UUID, sa.ForeignKey("market.market_entities.id"), nullable=False),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=False),
        sa.Column("raw_observation_id", UUID,
                  sa.ForeignKey("market.market_raw_observations.id"), nullable=False),
        sa.Column("observed_at", TS, nullable=False),
        sa.Column("published_at", TS, nullable=True),
        sa.Column("views", sa.BigInteger, nullable=True),
        sa.Column("likes", sa.BigInteger, nullable=True),
        sa.Column("comments", sa.BigInteger, nullable=True),
        sa.Column("subscriber_count", sa.BigInteger, nullable=True),
        sa.Column("video_duration_seconds", sa.Float, nullable=True),
        sa.Column("playlist_item_count", sa.Integer, nullable=True),
        sa.Column("channel_video_count", sa.Integer, nullable=True),
        sa.Column("text_title", sa.Text, nullable=True),
        sa.Column("text_description", sa.Text, nullable=True),
        sa.Column("country_signal", sa.Text, nullable=True),
        sa.Column("language_signal", sa.Text, nullable=True),
        sa.Column("quality_status", sa.Text, nullable=False, server_default="VALID"),
        sa.UniqueConstraint("entity_id", "source_id", "observed_at",
                            name="uq_market_observation_point"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_taxonomy_classifications",
        _pk(),
        sa.Column("observation_id", UUID,
                  sa.ForeignKey("market.market_observations.id"), nullable=True),
        sa.Column("entity_id", UUID, sa.ForeignKey("market.market_entities.id"), nullable=True),
        sa.Column("content_fingerprint", sa.Text, nullable=False),
        sa.Column("taxonomy_version", sa.Text, nullable=False),
        sa.Column("classifier_model", sa.Text, nullable=False),
        sa.Column("classifier_version", sa.Text, nullable=False),
        sa.Column("classification_prompt_version", sa.Text, nullable=False),
        sa.Column("classification_schema_version", sa.Text, nullable=False),
        sa.Column("dimensions_json", JSON, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("unmapped_terms", JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("content_fingerprint", "taxonomy_version",
                            name="uq_market_classification_cache"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_arms",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False, unique=True),
        sa.Column("fingerprint", sa.Text, nullable=False, unique=True),
        sa.Column("status", sa.Text, nullable=False, server_default="candidate"),
        sa.Column("taxonomy_json", JSON, nullable=False),
        sa.Column("source_observation_ids", JSON, nullable=False),
        sa.Column("support_count", sa.Integer, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_table(
        "market_opportunity_scores",
        _pk(),
        sa.Column("arm_id", sa.Text, sa.ForeignKey("market.market_arms.arm_id"), nullable=False),
        sa.Column("computed_at", TS, nullable=False),
        sa.Column("as_of", TS, nullable=False),
        sa.Column("market_opportunity_score", sa.Float, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("components_json", JSON, nullable=False),
        sa.Column("evidence_json", JSON, nullable=False),
        sa.Column("score_version", sa.Text, nullable=False),
        sa.Column("feature_version", sa.Text, nullable=False),
        sa.Column("normalization_version", sa.Text, nullable=False),
        sa.UniqueConstraint("arm_id", "computed_at", name="uq_market_score_run"),
        schema=SCHEMA,
    )
    op.create_table(
        "market_change_events",
        _pk(),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=True),
        sa.Column("detected_at", TS, nullable=False),
        sa.Column("previous_value", sa.Float, nullable=True),
        sa.Column("current_value", sa.Float, nullable=True),
        sa.Column("delta", sa.Float, nullable=True),
        sa.Column("detail_json", JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_table(
        "market_quarantined_records",
        _pk(),
        sa.Column("source_id", UUID, sa.ForeignKey("market.market_sources.id"), nullable=True),
        sa.Column("raw_observation_id", UUID,
                  sa.ForeignKey("market.market_raw_observations.id"), nullable=True),
        sa.Column("collection_run_id", UUID,
                  sa.ForeignKey("market.market_collection_runs.id"), nullable=True),
        sa.Column("reason_code", sa.Text, nullable=False),
        sa.Column("detail", sa.Text, nullable=False),
        sa.Column("payload_uri", sa.Text, nullable=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )
    op.create_table(
        "market_snapshots",
        _pk(),
        sa.Column("as_of", TS, nullable=False, unique=True),
        sa.Column("artifact_uri", sa.Text, nullable=False),
        sa.Column("artifact_hash", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in reversed(MARKET_TABLES):
        op.drop_table(table, schema=SCHEMA)
