"""analytics schema: decimal money with a currency, FX rates, observation kinds

Revision ID: 0022_analytics_money
Revises: 0021_market_channel_id
Create Date: 2026-10-14

ADR-0031:

* Every ``*_usd`` float becomes an exact ``Numeric(20, 6)`` amount plus a ``String(3)``
  currency. Rows stored before this revision never declared a currency, so their
  currency is NULL: currency unknown, excluded from every money aggregation and counted
  as unreconciled. Nothing is relabelled as USD.
* ``fx_rates`` records conversion rates (base, quote, rate, source, as_of), unique per
  pair, instant and source. The P9 reporting currency (KRW by default) is reached only
  through recorded rates.
* ``video_metrics`` and ``track_metrics`` declare an ``observation_kind``. Video rows are
  cumulative snapshots per window; track rows are distributor statement lines, which are
  incremental per period, so their placeholder ``window`` ('lifetime') becomes NULL.
* Unmeasured metrics are NULL, not 0: the metric columns become nullable and lose their
  ``0`` server defaults. Legacy zeros cannot be told apart from unmeasured values and
  are left as stored.
* ``experiment_outcomes`` records the currency of ``primary_value`` and the
  ``published_at`` its maturity was derived from (NULL on legacy rows).

Downgrade refuses to run while any amount is in a currency other than USD (or unknown):
the old schema cannot represent it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_analytics_money"
down_revision = "0021_market_channel_id"
branch_labels = None
depends_on = None

SCHEMA = "analytics"
AMOUNT = sa.Numeric(20, 6)

# (table, old float column, new amount column, currency column, amount nullable)
MONEY_COLUMNS = (
    ("video_metrics", "estimated_revenue_usd", "estimated_revenue_amount", "estimated_revenue_currency", True),
    ("track_metrics", "reported_revenue_usd", "reported_revenue_amount", "reported_revenue_currency", False),
    ("revenue_statements", "total_revenue_usd", "total_revenue_amount", "currency", False),
    ("revenue_statements", "unattributed_revenue_usd", "unattributed_revenue_amount", "currency", False),
    ("cost_items", "amount_usd", "amount", "currency", False),
)

VIDEO_METRIC_COLUMNS = (
    ("views", sa.Integer()),
    ("watch_time_minutes", sa.Float()),
    ("average_view_duration_seconds", sa.Float()),
    ("average_percentage_viewed", sa.Float()),
    ("subscribers_gained", sa.Integer()),
    ("subscribers_lost", sa.Integer()),
)


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("base", sa.String(3), nullable=False),
        sa.Column("quote", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(24, 12), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("base", "quote", "as_of", "source", name="uq_fx_rates_pair_instant_source"),
        sa.CheckConstraint("rate > 0", name="ck_fx_rates_rate_positive"),
        schema=SCHEMA,
    )
    op.create_index("ix_analytics_fx_rates_base", "fx_rates", ["base"], schema=SCHEMA)
    op.create_index("ix_analytics_fx_rates_quote", "fx_rates", ["quote"], schema=SCHEMA)

    added_currency: set[tuple[str, str]] = set()
    for table, old, amount, currency, nullable in MONEY_COLUMNS:
        op.add_column(table, sa.Column(amount, AMOUNT, nullable=True), schema=SCHEMA)
        if (table, currency) not in added_currency:
            # NULL = currency never declared (pre-ADR-0031): unknown, never USD.
            op.add_column(table, sa.Column(currency, sa.String(3), nullable=True), schema=SCHEMA)
            added_currency.add((table, currency))
        op.execute(sa.text(f"UPDATE {SCHEMA}.{table} SET {amount} = CAST({old} AS NUMERIC(20, 6))"))
        if not nullable:
            op.alter_column(table, amount, nullable=False, schema=SCHEMA)
        op.drop_column(table, old, schema=SCHEMA)

    for column, type_ in VIDEO_METRIC_COLUMNS:
        op.alter_column(
            "video_metrics", column, existing_type=type_, nullable=True, server_default=None,
            schema=SCHEMA,
        )
    op.alter_column(
        "track_metrics", "streams", existing_type=sa.Integer(), nullable=True, server_default=None,
        schema=SCHEMA,
    )

    op.add_column(
        "video_metrics",
        sa.Column("observation_kind", sa.String(32), nullable=False,
                  server_default="cumulative_snapshot"),
        schema=SCHEMA,
    )
    op.alter_column("video_metrics", "observation_kind", server_default=None, schema=SCHEMA)
    op.add_column(
        "track_metrics",
        sa.Column("observation_kind", sa.String(32), nullable=False, server_default="incremental"),
        schema=SCHEMA,
    )
    op.alter_column("track_metrics", "observation_kind", server_default=None, schema=SCHEMA)
    op.alter_column("track_metrics", "window", existing_type=sa.String(32), nullable=True, schema=SCHEMA)
    op.execute(
        sa.text(f"UPDATE {SCHEMA}.track_metrics SET \"window\" = NULL "
                "WHERE observation_kind = 'incremental'")
    )

    op.add_column(
        "experiment_outcomes",
        sa.Column("primary_value_currency", sa.String(3), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "experiment_outcomes",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    bind = op.get_bind()
    checked: set[tuple[str, str]] = set()
    for table, _old, _amount, currency, _nullable in MONEY_COLUMNS:
        if (table, currency) in checked:
            continue
        checked.add((table, currency))
        foreign = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {SCHEMA}.{table} "
                    f"WHERE {currency} IS NOT NULL AND {currency} <> 'USD'")
        ).scalar_one()
        if foreign:
            raise RuntimeError(
                f"cannot downgrade 0022_analytics_money: {SCHEMA}.{table} holds {foreign} "
                "non-USD amounts that the *_usd float columns cannot represent"
            )

    op.drop_column("experiment_outcomes", "published_at", schema=SCHEMA)
    op.drop_column("experiment_outcomes", "primary_value_currency", schema=SCHEMA)

    op.execute(sa.text(f"UPDATE {SCHEMA}.track_metrics SET \"window\" = 'lifetime' WHERE \"window\" IS NULL"))
    op.alter_column("track_metrics", "window", existing_type=sa.String(32), nullable=False, schema=SCHEMA)
    op.drop_column("track_metrics", "observation_kind", schema=SCHEMA)
    op.drop_column("video_metrics", "observation_kind", schema=SCHEMA)

    op.execute(sa.text(f"UPDATE {SCHEMA}.track_metrics SET streams = 0 WHERE streams IS NULL"))
    op.alter_column(
        "track_metrics", "streams", existing_type=sa.Integer(), nullable=False,
        server_default=sa.text("0"), schema=SCHEMA,
    )
    for column, type_ in VIDEO_METRIC_COLUMNS:
        op.execute(sa.text(f"UPDATE {SCHEMA}.video_metrics SET {column} = 0 WHERE {column} IS NULL"))
        op.alter_column(
            "video_metrics", column, existing_type=type_, nullable=False,
            server_default=sa.text("0"), schema=SCHEMA,
        )

    for table, old, amount, _currency, _nullable in MONEY_COLUMNS:
        op.add_column(table, sa.Column(old, sa.Float(), nullable=True), schema=SCHEMA)
        op.execute(
            sa.text(f"UPDATE {SCHEMA}.{table} "
                    f"SET {old} = COALESCE(CAST({amount} AS DOUBLE PRECISION), 0)")
        )
        # cost_items.amount_usd never had a server default; the others defaulted to 0.
        default = None if table == "cost_items" else sa.text("0")
        op.alter_column(
            table, old, existing_type=sa.Float(), nullable=False, server_default=default,
            schema=SCHEMA,
        )
        op.drop_column(table, amount, schema=SCHEMA)
    for table, currency in sorted({(t, c) for t, _o, _a, c, _n in MONEY_COLUMNS}):
        op.drop_column(table, currency, schema=SCHEMA)

    op.drop_index("ix_analytics_fx_rates_quote", table_name="fx_rates", schema=SCHEMA)
    op.drop_index("ix_analytics_fx_rates_base", table_name="fx_rates", schema=SCHEMA)
    op.drop_table("fx_rates", schema=SCHEMA)
