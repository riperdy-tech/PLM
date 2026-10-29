"""Read-only preflight for a separately approved 0038 disposable clone.

This does not create a clone, authorize Alembic or compare three preservation
snapshots. Its database-name refusal occurs before opening a connection.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tests.support.migration_review_compare import (
    _EMPTY,
    _REQUIRED_FIELDS,
    RELEASE,
    YOUTUBE,
    _valid_digest,
    _valid_manifest_integrity,
    _valid_manifest_schema,
    _valid_manifest_security,
    _valid_manifest_storage,
    _valid_review_access,
    _valid_review_schema,
    _valid_review_security,
    _valid_review_storage,
    _valid_selection_contract,
)
from tests.support.migration_review_snapshot import read_only_review_snapshot
from tests.support.migration_safety import destructive_migration_name_refusal

ROOT = Path(__file__).resolve().parents[2]


def preflight_clone_snapshot(
    snapshot: Mapping[str, Any], *, configured_database: str | None,
    intended_database: str, starting_revision: str,
) -> dict[str, Any]:
    """Fail closed on wrong target, sibling revision or historical structure."""
    failures: list[str] = []
    reason = destructive_migration_name_refusal(
        configured_database, intended_database,
    )
    if reason is not None:
        failures.append(reason)
    if starting_revision not in (RELEASE, YOUTUBE):
        failures.append("unrecognized historical 0038 starting revision")
    if set(snapshot) != _REQUIRED_FIELDS:
        failures.append("missing or unexpected snapshot evidence fields")
    if snapshot.get("database") != intended_database:
        failures.append("connected database is not the intended disposable clone")
    if snapshot.get("alembic_revision") != starting_revision:
        failures.append("database revision differs from requested 0038 starting branch")
    if (snapshot.get("read_only") is not True
            or snapshot.get("transaction_isolation") != "repeatable_read"):
        failures.append("snapshot is not confirmed read-only repeatable-read")

    for field, validator in (
        ("youtube_manifest_schema", _valid_manifest_schema),
        ("youtube_manifest_integrity", _valid_manifest_integrity),
        ("youtube_manifest_security", _valid_manifest_security),
        ("youtube_manifest_storage", _valid_manifest_storage),
    ):
        if not validator(snapshot.get(field)):
            failures.append(f"historical {field} is absent or unsafe")

    common = snapshot.get("youtube_manifest_common")
    expected_null = snapshot.get("youtube_all_null_selection")
    if not _valid_digest(common) or not _valid_digest(expected_null):
        failures.append("manifest row fingerprints unavailable")
    else:
        if common["count"] != expected_null["count"]:
            failures.append("manifest and NULL-selection row counts differ")
        for field, value in (
            ("manifest", common), ("NULL selection", expected_null),
        ):
            if value["count"] == 0 and value != _EMPTY:
                failures.append(f"{field} empty fingerprint invalid")

    expected_review = starting_revision == RELEASE
    for field, validator in (
        ("release_review_schema", _valid_review_schema),
        ("release_review_security", _valid_review_security),
        ("release_review_access", _valid_review_access),
        ("release_review_storage", _valid_review_storage),
    ):
        observed = snapshot.get(field)
        if expected_review and not validator(observed):
            failures.append(f"release-review {field} absent or unsafe")
        elif not expected_review and observed is not None:
            failures.append(f"YouTube-first branch unexpectedly has {field}")

    for field in ("release_reviews", "release_review_events"):
        observed = snapshot.get(field)
        if expected_review:
            if not _valid_digest(observed):
                failures.append(f"{field} row fingerprint missing")
            elif observed["count"] == 0 and observed != _EMPTY:
                failures.append(f"{field} empty fingerprint invalid")
        elif observed is not None:
            failures.append(f"YouTube-first branch unexpectedly has {field}")

    has_selection = snapshot.get("youtube_selection_column_present")
    selection_contract = snapshot.get("youtube_selection_column_contract")
    selection_digest = snapshot.get("youtube_selection")
    nonnull = snapshot.get("youtube_selection_nonnull")
    if starting_revision == RELEASE:
        if (has_selection is not False or selection_contract is not None
                or selection_digest is not None or nonnull is not None):
            failures.append("review-first branch unexpectedly has selection evidence")
    elif starting_revision == YOUTUBE:
        if has_selection is not True or not _valid_selection_contract(selection_contract):
            failures.append("YouTube-first selection column structure invalid")
        if not _valid_digest(selection_digest):
            failures.append("YouTube-first selection row fingerprint unavailable")
        else:
            if (not _valid_digest(common)
                    or selection_digest["count"] != common["count"]):
                failures.append("YouTube-first selection row count differs")
            if (selection_digest["count"] == 0
                    and selection_digest != _EMPTY):
                failures.append("YouTube-first empty selection fingerprint invalid")
            if (type(nonnull) is not int
                    or not 0 <= nonnull <= selection_digest["count"]):
                failures.append("YouTube-first selection non-NULL count invalid")

    # Explicitly not an approval, even for a fully matching disposable clone.
    return {
        "passed": not failures,
        "authorizes_migration": False,
        "failures": failures,
    }


def main(arguments: list[str] | None = None) -> int:
    """Inspect the selected PostgreSQL only after strict pre-connection refusal."""
    args = sys.argv[1:] if arguments is None else arguments
    if len(args) != 2 or args[1] not in (RELEASE, YOUTUBE):
        print(json.dumps({"passed": False, "authorizes_migration": False,
                          "failures": ["provide exact disposable DB and 0038 revision"]}))
        return 2

    target, revision = args
    try:
        from plm_platform.config import load_settings
        from plm_platform.db import database_url

        settings = load_settings(dotenv_path=ROOT / ".env")
        configured = database_url(settings).database
        reason = destructive_migration_name_refusal(configured, target)
        if reason is not None:
            result = {"passed": False, "authorizes_migration": False,
                      "failures": [reason]}
        else:
            # Same resolved settings object as the pre-connection check.
            snapshot = read_only_review_snapshot(settings=settings)
            result = preflight_clone_snapshot(
                snapshot, configured_database=configured,
                intended_database=target, starting_revision=revision,
            )
    except Exception:  # noqa: BLE001 - fail closed without credentials/SQL leakage
        result = {"passed": False, "authorizes_migration": False,
                  "failures": ["cannot inspect the selected database read-only"]}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
