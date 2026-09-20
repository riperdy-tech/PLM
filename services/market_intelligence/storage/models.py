# services/market_intelligence/storage/models.py
"""SQLAlchemy Core tables for the `market` schema (P1 owns this schema only).

Append-only by design: raw observations are never updated; corrected source values
arrive as new rows with a new `observed_at` (§3.2). Enum-ish columns are text (L4).
"""

from __future__ import annotations

import sqlalchemy as sa

MARKET_SCHEMA = "market"
MARKET_METADATA = sa.MetaData(schema=MARKET_SCHEMA)

_uuid = sa.Uuid(as_uuid=False)
_ts = sa.DateTime(timezone=True)
_json = sa.JSON


def _pk() -> sa.Column:
    return sa.Column("id", _uuid, primary_key=True)


def _now() -> sa.Column:
    return sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now())


sources = sa.Table(
    "market_sources", MARKET_METADATA,
    _pk(),
    sa.Column("provider_name", sa.Text, nullable=False, unique=True),
    sa.Column("connector_type", sa.Text, nullable=False),
    sa.Column("base_identifier", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
    sa.Column("configuration_version", sa.Text, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", _ts, nullable=False, server_default=sa.func.now()),
)

queries = sa.Table(
    "market_queries", MARKET_METADATA,
    _pk(),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=False),
    sa.Column("query_text", sa.Text, nullable=False),
    sa.Column("query_fingerprint", sa.Text, nullable=False),
    sa.Column("query_type", sa.Text, nullable=False),
    sa.Column("locale", sa.Text, nullable=False),
    sa.Column("country_code", sa.Text, nullable=False),
    sa.Column("language", sa.Text, nullable=False),
    sa.Column("seed_group", sa.Text, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("priority", sa.Integer, nullable=False, server_default="50"),
    sa.Column("first_seen_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.Column("last_run_at", _ts, nullable=True),
    sa.Column("next_run_at", _ts, nullable=True),
    sa.Column("query_version", sa.Text, nullable=False),
    sa.Column("parent_query_id", _uuid, sa.ForeignKey("market.market_queries.id"), nullable=True),
    sa.Column("proposed_by_model", sa.Text, nullable=True),
    sa.Column("model_name", sa.Text, nullable=True),
    sa.Column("model_version", sa.Text, nullable=True),
    sa.Column("proposal_confidence", sa.Float, nullable=True),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint("source_id", "query_fingerprint", name="uq_market_queries_fingerprint"),
)

seed_evidence = sa.Table(
    "market_seed_evidence", MARKET_METADATA,
    _pk(),
    sa.Column("query_id", _uuid, sa.ForeignKey("market.market_queries.id"), nullable=False),
    sa.Column(
        "raw_observation_id", _uuid,
        sa.ForeignKey("market.market_raw_observations.id"), nullable=False,
    ),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint("query_id", "raw_observation_id", name="uq_market_seed_evidence"),
)

collection_runs = sa.Table(
    "market_collection_runs", MARKET_METADATA,
    _pk(),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=False),
    sa.Column("query_id", _uuid, sa.ForeignKey("market.market_queries.id"), nullable=True),
    sa.Column("started_at", _ts, nullable=False),
    sa.Column("finished_at", _ts, nullable=True),
    sa.Column("status", sa.Text, nullable=False, server_default="queued"),
    sa.Column("request_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("success_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("rate_limit_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("raw_observation_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("normalized_observation_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("error_summary", _json, nullable=True),
    sa.Column("software_version", sa.Text, nullable=False),
    sa.Column("cost_api_calls", sa.Integer, nullable=False, server_default="0"),
    sa.Column("cost_llm_tokens", sa.Integer, nullable=False, server_default="0"),
    sa.Column("cost_llm_cost_estimate", sa.Float, nullable=False, server_default="0"),
    sa.Column("cost_storage_bytes", sa.BigInteger, nullable=False, server_default="0"),
)

raw_observations = sa.Table(
    "market_raw_observations", MARKET_METADATA,
    _pk(),
    sa.Column(
        "collection_run_id", _uuid,
        sa.ForeignKey("market.market_collection_runs.id"), nullable=False,
    ),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=False),
    sa.Column("provider_object_type", sa.Text, nullable=False),
    sa.Column("provider_object_id", sa.Text, nullable=False),
    sa.Column("observed_at", _ts, nullable=False),
    sa.Column("request_context_hash", sa.Text, nullable=False),
    sa.Column("payload_json", _json, nullable=False),
    sa.Column("payload_hash", sa.Text, nullable=False),
    sa.Column("payload_uri", sa.Text, nullable=False),
    sa.Column("schema_version", sa.Text, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint(
        "source_id", "provider_object_type", "provider_object_id", "observed_at",
        "payload_hash", name="uq_market_raw_dedupe",
    ),
)

entities = sa.Table(
    "market_entities", MARKET_METADATA,
    _pk(),
    sa.Column("entity_type", sa.Text, nullable=False),
    sa.Column("canonical_name", sa.Text, nullable=False),
    sa.Column("normalized_name", sa.Text, nullable=False),
    sa.Column("canonical_url", sa.Text, nullable=True),
    sa.Column("language", sa.Text, nullable=True),
    sa.Column("country_code", sa.Text, nullable=True),
    sa.Column("first_seen_at", _ts, nullable=False),
    sa.Column("last_seen_at", _ts, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="active"),
)

entity_identities = sa.Table(
    "market_entity_identities", MARKET_METADATA,
    _pk(),
    sa.Column("entity_id", _uuid, sa.ForeignKey("market.market_entities.id"), nullable=False),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=False),
    sa.Column("provider_object_type", sa.Text, nullable=False),
    sa.Column("provider_object_id", sa.Text, nullable=False),
    sa.Column("first_seen_at", _ts, nullable=False),
    sa.Column("last_seen_at", _ts, nullable=False),
    sa.UniqueConstraint(
        "source_id", "provider_object_type", "provider_object_id",
        name="uq_market_entity_identity",
    ),
)

observations = sa.Table(
    "market_observations", MARKET_METADATA,
    _pk(),
    sa.Column("entity_id", _uuid, sa.ForeignKey("market.market_entities.id"), nullable=False),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=False),
    sa.Column(
        "raw_observation_id", _uuid,
        sa.ForeignKey("market.market_raw_observations.id"), nullable=False,
    ),
    sa.Column("observed_at", _ts, nullable=False),
    sa.Column("published_at", _ts, nullable=True),
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
    sa.Column("normalization_version", sa.Text, nullable=False),
    # which metrics are `not_ready` (their numeric columns are NULL): NULL/absent ⇒ unknown
    sa.Column("not_ready_metrics", _json, nullable=True),
    sa.Column("quality_status", sa.Text, nullable=False, server_default="VALID"),
    sa.UniqueConstraint(
        "entity_id", "source_id", "observed_at", name="uq_market_observation_point"
    ),
)

classifications = sa.Table(
    "market_taxonomy_classifications", MARKET_METADATA,
    _pk(),
    sa.Column("observation_id", _uuid, sa.ForeignKey("market.market_observations.id"),
              nullable=True),
    sa.Column("entity_id", _uuid, sa.ForeignKey("market.market_entities.id"), nullable=True),
    sa.Column("content_fingerprint", sa.Text, nullable=False),
    sa.Column("taxonomy_version", sa.Text, nullable=False),
    sa.Column("classifier_model", sa.Text, nullable=False),
    sa.Column("classifier_version", sa.Text, nullable=False),
    sa.Column("classification_prompt_version", sa.Text, nullable=False),
    sa.Column("classification_schema_version", sa.Text, nullable=False),
    sa.Column("dimensions_json", _json, nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("unmapped_terms", _json, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.UniqueConstraint(
        "content_fingerprint", "taxonomy_version", name="uq_market_classification_cache"
    ),
)

arms = sa.Table(
    "market_arms", MARKET_METADATA,
    _pk(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False, unique=True),
    sa.Column("fingerprint", sa.Text, nullable=False, unique=True),
    sa.Column("status", sa.Text, nullable=False, server_default="candidate"),
    sa.Column("taxonomy_json", _json, nullable=False),
    sa.Column("source_observation_ids", _json, nullable=False),
    sa.Column("support_count", sa.Integer, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
    sa.Column("updated_at", _ts, nullable=False, server_default=sa.func.now()),
)

scores = sa.Table(
    "market_opportunity_scores", MARKET_METADATA,
    _pk(),
    sa.Column("arm_id", sa.Text, sa.ForeignKey("market.market_arms.arm_id"), nullable=False),
    sa.Column("computed_at", _ts, nullable=False),
    sa.Column("as_of", _ts, nullable=False),
    sa.Column("market_opportunity_score", sa.Float, nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("components_json", _json, nullable=False),
    sa.Column("evidence_json", _json, nullable=False),
    sa.Column("score_version", sa.Text, nullable=False),
    sa.Column("feature_version", sa.Text, nullable=False),
    sa.Column("normalization_version", sa.Text, nullable=False),
    sa.UniqueConstraint("arm_id", "computed_at", name="uq_market_score_run"),
)

change_events = sa.Table(
    "market_change_events", MARKET_METADATA,
    _pk(),
    sa.Column("event_type", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=True),
    sa.Column("detected_at", _ts, nullable=False),
    sa.Column("previous_value", sa.Float, nullable=True),
    sa.Column("current_value", sa.Float, nullable=True),
    sa.Column("delta", sa.Float, nullable=True),
    sa.Column("detail_json", _json, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
)

quarantined_records = sa.Table(
    "market_quarantined_records", MARKET_METADATA,
    _pk(),
    sa.Column("source_id", _uuid, sa.ForeignKey("market.market_sources.id"), nullable=True),
    sa.Column(
        "raw_observation_id", _uuid,
        sa.ForeignKey("market.market_raw_observations.id"), nullable=True,
    ),
    sa.Column(
        "collection_run_id", _uuid,
        sa.ForeignKey("market.market_collection_runs.id"), nullable=True,
    ),
    sa.Column("reason_code", sa.Text, nullable=False),
    sa.Column("detail", sa.Text, nullable=False),
    sa.Column("payload_uri", sa.Text, nullable=True),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
)

snapshots = sa.Table(
    "market_snapshots", MARKET_METADATA,
    _pk(),
    sa.Column("as_of", _ts, nullable=False, unique=True),
    sa.Column("artifact_uri", sa.Text, nullable=False),
    sa.Column("artifact_hash", sa.Text, nullable=False),
    sa.Column("created_at", _ts, nullable=False, server_default=sa.func.now()),
)

MARKET_TABLES: tuple[str, ...] = (
    "market_sources", "market_queries", "market_collection_runs", "market_raw_observations",
    "market_seed_evidence", "market_entities", "market_entity_identities",
    "market_observations", "market_taxonomy_classifications", "market_arms",
    "market_opportunity_scores", "market_change_events", "market_quarantined_records",
    "market_snapshots",
)
