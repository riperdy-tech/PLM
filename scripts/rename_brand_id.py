"""Rename a brand id in every identifier column that carries it (dev DB, 2026-11-01).

The channel's internal brand id ``neon_seoul`` became ``longform_signal`` (ADR-0044).
Only identifier columns (``brand_id``, ``brand_profile_id``) are renamed. JSON
snapshots, idempotency responses and storage URIs keep the old id: they record what was
sent or where an object lives, and rewriting them would break their checksums or
paths.

Dry run by default: prints the rows each column would change. ``--apply`` runs every
update in one transaction and refuses if any row already carries the new id (so
``--from``/``--to`` swapped reverses it exactly). Take a database backup first.

    python scripts/rename_brand_id.py --from neon_seoul --to longform_signal [--apply]
"""

from __future__ import annotations

import argparse
import sys

import sqlalchemy as sa
from plm_platform.db import build_engine

COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("ar", "ar_allocation_cycle", "brand_id"),
    ("ar", "ar_arm_feature_snapshot", "brand_id"),
    ("ar", "ar_arm_posterior", "brand_id"),
    ("ar", "ar_content_arm_state", "brand_id"),
    ("ar", "ar_baseline_config", "brand_id"),
    ("ar", "ar_channel_state", "brand_id"),
    ("distribution", "artists", "brand_id"),
    ("experiments", "content_packages", "brand_id"),
    ("experiments", "playlist_specs", "brand_id"),
    ("experiments", "track_specs", "brand_profile_id"),
    ("market", "market_arms", "brand_id"),
    ("media", "media_packages", "brand_id"),
    ("media", "playlist_sessions", "brand_id"),
    ("media", "visual_sessions", "brand_id"),
    ("ops", "pipeline_runs", "brand_id"),
    ("youtube", "channel_profiles", "brand_id"),
    ("youtube", "publish_manifests", "brand_id"),
)


def _matches(column: str, value: str) -> str:
    # brand_profile_id carries "<brand>_profile"
    return (f'"{column}" IN (:value, :value_profile)' if column == "brand_profile_id"
            else f'"{column}" = :value')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="old", required=True)
    parser.add_argument("--to", dest="new", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    engine = build_engine()
    old = {"value": args.old, "value_profile": f"{args.old}_profile"}
    new = {"value": args.new, "value_profile": f"{args.new}_profile"}
    with engine.begin() as conn:
        clashes = []
        for schema, table, column in COLUMNS:
            found = conn.execute(sa.text(
                f'SELECT count(*) FROM "{schema}"."{table}" WHERE {_matches(column, args.new)}'),
                new).scalar_one()
            if found:
                clashes.append(f"{schema}.{table}.{column}: {found}")
        if clashes:
            print("refused: rows already carry the new id:\n  " + "\n  ".join(clashes))
            return 2
        total = 0
        for schema, table, column in COLUMNS:
            where = _matches(column, args.old)
            count = conn.execute(sa.text(
                f'SELECT count(*) FROM "{schema}"."{table}" WHERE {where}'), old).scalar_one()
            total += count
            print(f"{schema}.{table}.{column}: {count}")
            if args.apply and count:
                if column == "brand_profile_id":
                    conn.execute(sa.text(
                        f'UPDATE "{schema}"."{table}" SET "{column}" = CASE "{column}" '
                        f"WHEN :value THEN :new ELSE :new_profile END WHERE {where}"),
                        {**old, "new": args.new, "new_profile": f"{args.new}_profile"})
                else:
                    conn.execute(sa.text(
                        f'UPDATE "{schema}"."{table}" SET "{column}" = :new WHERE {where}'),
                        {**old, "new": args.new})
        print(f"total: {total} rows {'renamed' if args.apply else 'would be renamed (dry run)'}")
        if args.apply:
            left = sum(conn.execute(sa.text(
                f'SELECT count(*) FROM "{s}"."{t}" WHERE {_matches(c, args.old)}'), old
            ).scalar_one() for s, t, c in COLUMNS)
            if left:
                raise RuntimeError(f"{left} rows still carry {args.old}; rolled back")
    return 0


if __name__ == "__main__":
    sys.exit(main())
