# services/autonomous_ar/storage/models.py
"""The `ar` schema: fifteen tables, text enums only, ids derived from stable identity.

§44 lists twelve tables; the three extra ones carry state the same section requires
(§74's update log, §117's activation state, §107/§108's override event). Every instant
column is `timezone=True`; every enum-valued column is `Text` validated by
`domain.enums` (ledger ruling L4 — no native Postgres enums). A `Tri` measurement never
collapses on the way in: raw metrics keep their tagged JSON dump, and
`normalized_reward` is paired with `normalized_reward_state` so an absent measurement,
a measured zero and a `not_ready` stay distinguishable in storage and on read.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

__all__ = ["AR_METADATA", "AR_SCHEMA", "AR_TABLES"]

AR_SCHEMA = "ar"
AR_METADATA = sa.MetaData(schema=AR_SCHEMA)

_uuid = sa.Uuid(as_uuid=False)
_ts = sa.DateTime(timezone=True)
_json = sa.JSON


def _id_column() -> sa.Column[Any]:
    return sa.Column("id", _uuid, primary_key=True)


ar_content_arm_state = sa.Table(
    "ar_content_arm_state", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False, unique=True),  # P1's id, verbatim (R15)
    sa.Column("status", sa.Text, nullable=False, server_default="candidate"),
    sa.Column("first_selected_at", _ts, nullable=True),
    sa.Column("last_selected_at", _ts, nullable=True),
    sa.Column("selection_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("mature_result_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("provisional_result_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("recent_exposure_score", sa.Float, nullable=False, server_default="0"),
    sa.Column("retirement_reason", sa.Text, nullable=True),
    sa.Column("cooldown_until", _ts, nullable=True),
    sa.Column("last_updated_at", _ts, nullable=False),
    sa.Column("state_version", sa.Integer, nullable=False, server_default="1"),
)

ar_arm_feature_snapshot = sa.Table(
    "ar_arm_feature_snapshot", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("captured_at", _ts, nullable=False),
    sa.Column("p1_score", sa.Float, nullable=False),
    sa.Column("p1_confidence", sa.Float, nullable=False),
    sa.Column("demand_score", sa.Float, nullable=True),
    sa.Column("growth_score", sa.Float, nullable=True),
    sa.Column("competition_score", sa.Float, nullable=True),
    sa.Column("cross_platform_score", sa.Float, nullable=True),
    sa.Column("production_fit_score", sa.Float, nullable=True),
    sa.Column("freshness_seconds", sa.Float, nullable=False),
    sa.Column("feature_json", _json, nullable=False),
    sa.Column("feature_version", sa.Text, nullable=False),
)

ar_arm_posterior = sa.Table(
    "ar_arm_posterior", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False, unique=True),
    sa.Column("model_type", sa.Text, nullable=False),
    sa.Column("model_version", sa.Text, nullable=False),
    sa.Column("prior_mean", sa.Float, nullable=False),
    sa.Column("prior_variance", sa.Float, nullable=False),
    sa.Column("posterior_mean", sa.Float, nullable=False),
    sa.Column("posterior_variance", sa.Float, nullable=False),
    sa.Column("observation_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("effective_observation_count", sa.Float, nullable=False, server_default="0"),
    sa.Column("last_update_at", _ts, nullable=False),
    sa.Column("update_version", sa.Integer, nullable=False, server_default="0"),
    sa.Column("context_state_json", _json, nullable=True),
)

ar_experiment_result = sa.Table(
    "ar_experiment_result", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("experiment_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("content_id", sa.Text, nullable=False),
    sa.Column("observation_window", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("observed_at", _ts, nullable=False),
    sa.Column("raw_metrics_json", _json, nullable=False),
    sa.Column("normalized_reward", sa.Float, nullable=True),
    # `known` | `unknown` | `not_ready` (TriState): NULL in the column above is then
    # never ambiguous between "absent" and "not ready" (P1's hard-won not_ready column).
    sa.Column("normalized_reward_state", sa.Text, nullable=False),
    sa.Column("reward_version", sa.Text, nullable=False),
    sa.Column("attribution_confidence", sa.Float, nullable=False),
    sa.Column("source_ids", _json, nullable=False),
    sa.UniqueConstraint("experiment_id", "observation_window",
                        name="uq_ar_experiment_result_window"),
)

ar_reward_observation = sa.Table(
    "ar_reward_observation", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("experiment_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("content_id", sa.Text, nullable=False),
    sa.Column("observation_window", sa.Text, nullable=False),
    sa.Column("maturity", sa.Text, nullable=False),
    sa.Column("observed_at", _ts, nullable=False),
    sa.Column("raw_metrics_json", _json, nullable=False),
    sa.Column("normalized_reward", sa.Float, nullable=True),  # NULL = unknown, not zero
    # See `ar_experiment_result`: the state column keeps `unknown` ≠ `not_ready`.
    sa.Column("normalized_reward_state", sa.Text, nullable=False),
    sa.Column("reward_version", sa.Text, nullable=False),
    sa.Column("attribution_confidence", sa.Float, nullable=False),
    sa.Column("quality_status", sa.Text, nullable=True),
    sa.Column("economic_terms_json", _json, nullable=True),   # reserved C7 terms, stored as-is
    sa.Column("received_at", _ts, nullable=False),
    sa.UniqueConstraint("experiment_id", "observation_window",
                        name="uq_ar_reward_observation_window"),
)

ar_allocation_policy = sa.Table(
    "ar_allocation_policy", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("version", sa.Text, nullable=False, unique=True),  # immutable once used (§92)
    sa.Column("exploit_fraction", sa.Float, nullable=False),
    sa.Column("adjacent_explore_fraction", sa.Float, nullable=False),
    sa.Column("radical_explore_fraction", sa.Float, nullable=False),
    sa.Column("baseline_fraction", sa.Float, nullable=False),
    sa.Column("max_exact_arm_share", sa.Float, nullable=False),
    sa.Column("max_cluster_share", sa.Float, nullable=False),
    sa.Column("max_genre_share", sa.Float, nullable=False),
    sa.Column("max_use_case_share", sa.Float, nullable=False),
    sa.Column("max_language_share", sa.Float, nullable=False),
    sa.Column("fatigue_config_json", _json, nullable=False),
    sa.Column("nonstationarity_config_json", _json, nullable=False),
    sa.Column("retirement_config_json", _json, nullable=False),
    sa.Column("created_at", _ts, nullable=False),
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.false()),
)

ar_allocation_cycle = sa.Table(
    "ar_allocation_cycle", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("started_at", _ts, nullable=False),
    sa.Column("finished_at", _ts, nullable=True),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("slot_count", sa.Integer, nullable=False),
    sa.Column("budget", sa.Float, nullable=False),
    sa.Column("policy_id", _uuid, nullable=False),
    sa.Column("p1_snapshot_time", _ts, nullable=False),
    sa.Column("decision_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("selected_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("rejected_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("warning_count", sa.Integer, nullable=False, server_default="0"),
    # additions ADR-0024 records: persistence-only, never wire fields (C2 stays frozen)
    sa.Column("activation_state", sa.Text, nullable=False),
    sa.Column("random_seed", sa.BigInteger, nullable=False),
    sa.Column("warning_codes_json", _json, nullable=False),
)

ar_allocation_decision = sa.Table(
    "ar_allocation_decision", AR_METADATA,
    _id_column(),
    sa.Column("cycle_id", _uuid, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("allocation_type", sa.Text, nullable=False),
    sa.Column("sampled_reward", sa.Float, nullable=False),
    sa.Column("posterior_expected_reward", sa.Float, nullable=False),
    sa.Column("selection_priority", sa.Float, nullable=False),
    sa.Column("rank_before_constraints", sa.Integer, nullable=True),
    sa.Column("rank_after_constraints", sa.Integer, nullable=True),
    sa.Column("constraint_adjustments_json", _json, nullable=False),
    sa.Column("reason_codes", _json, nullable=False),
    sa.Column("policy_version", sa.Text, nullable=False),
    sa.Column("model_version", sa.Text, nullable=False),
    sa.Column("feature_version", sa.Text, nullable=False),
    sa.Column("normalization_version", sa.Text, nullable=False),
    sa.Column("p1_score_version", sa.Text, nullable=True),
    sa.Column("feature_snapshot_id", _uuid, nullable=False),
    sa.Column("created_at", _ts, nullable=False),
)

ar_production_directive = sa.Table(
    "ar_production_directive", AR_METADATA,
    _id_column(),
    sa.Column("decision_id", _uuid, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("quantity", sa.Integer, nullable=False),
    sa.Column("brief_json", _json, nullable=False),
    sa.Column("priority", sa.Integer, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default="queued"),
    sa.Column("expires_at", _ts, nullable=False),
    sa.Column("created_at", _ts, nullable=False),
    sa.Column("consumed_at", _ts, nullable=True),
    # wire copies, persisted so GET /ar/directives can serve the frozen C2 payload
    sa.Column("allocation_type", sa.Text, nullable=False),
    sa.Column("expected_value", sa.Float, nullable=False),
    sa.Column("uncertainty", sa.Float, nullable=False),
    sa.Column("budget_limit", sa.Float, nullable=False),
    sa.Column("market_context_json", _json, nullable=False),
    sa.Column("selection_reason_json", _json, nullable=False),
    sa.Column("directive_version", sa.Text, nullable=False),
    sa.Column("policy_version", sa.Text, nullable=False),
    sa.Column("exploration_fraction", sa.Float, nullable=False),
    sa.Column("value_model", sa.Text, nullable=False),
)

ar_arm_similarity = sa.Table(
    "ar_arm_similarity", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id_a", sa.Text, nullable=False),
    sa.Column("arm_id_b", sa.Text, nullable=False),
    sa.Column("similarity_score", sa.Float, nullable=False),
    sa.Column("similarity_method", sa.Text, nullable=False),
    sa.Column("feature_version", sa.Text, nullable=False),
    sa.Column("computed_at", _ts, nullable=False),
    sa.UniqueConstraint("arm_id_a", "arm_id_b", name="uq_ar_arm_similarity_pair"),
)

ar_arm_exposure = sa.Table(
    "ar_arm_exposure", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("window_start", _ts, nullable=False),
    sa.Column("window_end", _ts, nullable=False),
    sa.Column("selection_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("content_count", sa.Integer, nullable=False, server_default="0"),
    sa.Column("estimated_audience_exposure", sa.Float, nullable=True),  # unknown ≠ 0 (§54)
    sa.Column("exposure_score", sa.Float, nullable=False, server_default="0"),
    sa.Column("calculation_version", sa.Text, nullable=False),
    sa.UniqueConstraint("arm_id", "window_start", "window_end",
                        name="uq_ar_arm_exposure_window"),
)

ar_baseline_config = sa.Table(
    "ar_baseline_config", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("added_at", _ts, nullable=False),
    sa.Column("removed_at", _ts, nullable=True),
    sa.Column("reason", sa.Text, nullable=True),
    sa.Column("config_version", sa.Text, nullable=False),
    sa.UniqueConstraint("brand_id", "arm_id", name="uq_ar_baseline_arm"),
)

ar_posterior_update_log = sa.Table(
    "ar_posterior_update_log", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("reward_observation_id", _uuid, nullable=False),
    sa.Column("arm_id", sa.Text, nullable=False),
    sa.Column("posterior_version_before", sa.Integer, nullable=False),
    sa.Column("posterior_version_after", sa.Integer, nullable=False),
    sa.Column("update_status", sa.Text, nullable=False),
    sa.Column("effective_weight", sa.Float, nullable=False),
    sa.Column("updated_at", _ts, nullable=False),
)

# §74: an `applied` update exists at most once per observation; failed/quarantined
# attempts stay in the log and are retried with a new row.
sa.Index(
    "uq_ar_posterior_update_applied", ar_posterior_update_log.c.reward_observation_id,
    unique=True,
    sqlite_where=sa.text("update_status = 'applied'"),
    postgresql_where=sa.text("update_status = 'applied'"),
)

ar_channel_state = sa.Table(
    "ar_channel_state", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False, unique=True),
    sa.Column("activation_state", sa.Text, nullable=False),
    sa.Column("updated_at", _ts, nullable=False),
    sa.Column("reason", sa.Text, nullable=True),
    sa.Column("updated_by", sa.Text, nullable=True),
)

ar_manual_override = sa.Table(
    "ar_manual_override", AR_METADATA,
    _id_column(),
    sa.Column("brand_id", sa.Text, nullable=False),
    sa.Column("target_type", sa.Text, nullable=False),
    sa.Column("target_id", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("created_by", sa.Text, nullable=False),
    sa.Column("created_at", _ts, nullable=False),
    sa.Column("expires_at", _ts, nullable=True),   # §108: never an indefinite hidden override
    sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
)

AR_TABLES: tuple[sa.Table, ...] = (
    ar_content_arm_state, ar_arm_feature_snapshot, ar_arm_posterior, ar_experiment_result,
    ar_reward_observation, ar_allocation_policy, ar_allocation_cycle, ar_allocation_decision,
    ar_production_directive, ar_arm_similarity, ar_arm_exposure, ar_baseline_config,
    ar_posterior_update_log, ar_channel_state, ar_manual_override,
)
