# migrations/versions/ar/ar_0008_initial_tables.py
"""P2 initial tables — the `ar` schema.

Revision ID: 0008_ar_initial
Revises: 0007_market_snapshot_bodies
Create Date: 2026-09-20

Table names and the three tables beyond §44's twelve are recorded in ADR-0024. Enum
values are `Text` validated in Python (no native Postgres enums); every instant is
`timezone=True`; `downgrade()` drops exactly what `upgrade()` creates.

The plan printed `op.create_table("name", schema=SCHEMA, _pk(), ...)`; that is a
SyntaxError ("positional argument follows keyword argument"), so `schema=SCHEMA`
closes each call instead.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_ar_initial"
down_revision = "0007_market_snapshot_bodies"
branch_labels = None
depends_on = None

_uuid = sa.Uuid(as_uuid=False)
_ts = sa.DateTime(timezone=True)
_json = sa.JSON
SCHEMA = "ar"


def _pk() -> sa.Column:
    return sa.Column("id", _uuid, primary_key=True)


def upgrade() -> None:
    op.create_table(
        "ar_content_arm_state",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False, unique=True),
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
        schema=SCHEMA,
    )
    op.create_table(
        "ar_arm_feature_snapshot",
        _pk(),
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
        schema=SCHEMA,
    )
    op.create_table(
        "ar_arm_posterior",
        _pk(),
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
        schema=SCHEMA,
    )
    op.create_table(
        "ar_experiment_result",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("experiment_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("content_id", sa.Text, nullable=False),
        sa.Column("observation_window", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("observed_at", _ts, nullable=False),
        sa.Column("raw_metrics_json", _json, nullable=False),
        sa.Column("normalized_reward", sa.Float, nullable=True),
        sa.Column("normalized_reward_state", sa.Text, nullable=False),
        sa.Column("reward_version", sa.Text, nullable=False),
        sa.Column("attribution_confidence", sa.Float, nullable=False),
        sa.Column("source_ids", _json, nullable=False),
        sa.UniqueConstraint("experiment_id", "observation_window",
                            name="uq_ar_experiment_result_window"),
        schema=SCHEMA,
    )
    op.create_table(
        "ar_reward_observation",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("experiment_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("content_id", sa.Text, nullable=False),
        sa.Column("observation_window", sa.Text, nullable=False),
        sa.Column("maturity", sa.Text, nullable=False),
        sa.Column("observed_at", _ts, nullable=False),
        sa.Column("raw_metrics_json", _json, nullable=False),
        sa.Column("normalized_reward", sa.Float, nullable=True),
        sa.Column("normalized_reward_state", sa.Text, nullable=False),
        sa.Column("reward_version", sa.Text, nullable=False),
        sa.Column("attribution_confidence", sa.Float, nullable=False),
        sa.Column("quality_status", sa.Text, nullable=True),
        sa.Column("economic_terms_json", _json, nullable=True),
        sa.Column("received_at", _ts, nullable=False),
        sa.UniqueConstraint("experiment_id", "observation_window",
                            name="uq_ar_reward_observation_window"),
        schema=SCHEMA,
    )
    op.create_table(
        "ar_allocation_policy",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("version", sa.Text, nullable=False, unique=True),
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
        schema=SCHEMA,
    )
    op.create_table(
        "ar_allocation_cycle",
        _pk(),
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
        sa.Column("activation_state", sa.Text, nullable=False),
        sa.Column("random_seed", sa.BigInteger, nullable=False),
        sa.Column("warning_codes_json", _json, nullable=False),
        schema=SCHEMA,
    )
    op.create_table(
        "ar_allocation_decision",
        _pk(),
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
        schema=SCHEMA,
    )
    op.create_table(
        "ar_production_directive",
        _pk(),
        sa.Column("decision_id", _uuid, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("brief_json", _json, nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("expires_at", _ts, nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        sa.Column("consumed_at", _ts, nullable=True),
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
        schema=SCHEMA,
    )
    op.create_table(
        "ar_arm_similarity",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id_a", sa.Text, nullable=False),
        sa.Column("arm_id_b", sa.Text, nullable=False),
        sa.Column("similarity_score", sa.Float, nullable=False),
        sa.Column("similarity_method", sa.Text, nullable=False),
        sa.Column("feature_version", sa.Text, nullable=False),
        sa.Column("computed_at", _ts, nullable=False),
        sa.UniqueConstraint("arm_id_a", "arm_id_b", name="uq_ar_arm_similarity_pair"),
        schema=SCHEMA,
    )
    op.create_table(
        "ar_arm_exposure",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("window_start", _ts, nullable=False),
        sa.Column("window_end", _ts, nullable=False),
        sa.Column("selection_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("content_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("estimated_audience_exposure", sa.Float, nullable=True),
        sa.Column("exposure_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("calculation_version", sa.Text, nullable=False),
        sa.UniqueConstraint("arm_id", "window_start", "window_end",
                            name="uq_ar_arm_exposure_window"),
        schema=SCHEMA,
    )
    op.create_table(
        "ar_baseline_config",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("added_at", _ts, nullable=False),
        sa.Column("removed_at", _ts, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("config_version", sa.Text, nullable=False),
        sa.UniqueConstraint("brand_id", "arm_id", name="uq_ar_baseline_arm"),
        schema=SCHEMA,
    )
    op.create_table(
        "ar_posterior_update_log",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("reward_observation_id", _uuid, nullable=False),
        sa.Column("arm_id", sa.Text, nullable=False),
        sa.Column("posterior_version_before", sa.Integer, nullable=False),
        sa.Column("posterior_version_after", sa.Integer, nullable=False),
        sa.Column("update_status", sa.Text, nullable=False),
        sa.Column("effective_weight", sa.Float, nullable=False),
        sa.Column("updated_at", _ts, nullable=False),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_ar_posterior_update_applied", "ar_posterior_update_log",
        ["reward_observation_id"], unique=True, schema=SCHEMA,
        sqlite_where=sa.text("update_status = 'applied'"),
        postgresql_where=sa.text("update_status = 'applied'"),
    )
    op.create_table(
        "ar_channel_state",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False, unique=True),
        sa.Column("activation_state", sa.Text, nullable=False),
        sa.Column("updated_at", _ts, nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("updated_by", sa.Text, nullable=True),
        schema=SCHEMA,
    )
    op.create_table(
        "ar_manual_override",
        _pk(),
        sa.Column("brand_id", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=False),
        sa.Column("target_id", sa.Text, nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", _ts, nullable=False),
        sa.Column("expires_at", _ts, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for table in (
        "ar_manual_override", "ar_channel_state", "ar_posterior_update_log",
        "ar_baseline_config", "ar_arm_exposure", "ar_arm_similarity",
        "ar_production_directive", "ar_allocation_decision", "ar_allocation_cycle",
        "ar_allocation_policy", "ar_reward_observation", "ar_experiment_result",
        "ar_arm_posterior", "ar_arm_feature_snapshot", "ar_content_arm_state",
    ):
        op.drop_table(table, schema=SCHEMA)
