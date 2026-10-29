"""Compile 0038 sibling + 0039 draft PostgreSQL DDL without connecting to any DB.

These are review-only proposal files, *not* installed in migrations/versions.
An offline SQL preview is not a clone migration, a data-preservation test, or
permission to change the development/production schema.
"""
from __future__ import annotations

import importlib.util
import io
from hashlib import sha256
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "docs/proposals/plm_0038_release_reviews_reference.py"
YOUTUBE = ROOT / "migrations/versions/youtube/youtube_0038_candidate_selection.py"
MERGE = ROOT / "docs/proposals/plm_0039_merge_0038_branches.py"
INSTALLED_REVIEW = ROOT / "migrations/versions/plm/plm_0038_release_reviews.py"
INSTALLED_MERGE = ROOT / "migrations/versions/plm/plm_0039_merge_0038_branches.py"
REVIEW_SHA = "58ba6d410af2ff96ad2faf0e9e915e945df12b773677cdae4cd5b89d722b65a6"


def _module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "offline_review_" + path.stem, path
    )
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    return revision


def _compile(path: Path, *, downgrade: bool = False) -> str:
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    assert context.as_sql is True
    revision = _module(path)
    revision.op = Operations(context)
    if downgrade:
        revision.downgrade()
    else:
        revision.upgrade()
    return output.getvalue()


def _statements(sql: str) -> list[str]:
    statements = [
        " ".join(chunk.split()).strip()
        for chunk in sql.split(";") if chunk.strip()
    ]
    assert all(not item.startswith("--") for item in statements)
    return statements


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (YOUTUBE, [
            "ALTER TABLE youtube.publish_manifests ADD COLUMN selection_json JSON",
        ]),
        (REVIEW, [
            "CREATE TABLE ops.release_reviews",
            "CREATE TABLE ops.release_review_events",
            "CREATE INDEX ix_release_review_events_package ON ops.release_review_events",
        ]),
    ],
)
def test_parent_upgrade_emits_only_expected_postgresql_ddl(
    source: Path, expected: list[str],
) -> None:
    assert sha256(REVIEW.read_bytes()).hexdigest() == REVIEW_SHA
    actual = _statements(_compile(source))
    assert len(actual) == len(expected)
    assert all(statement.startswith(prefix) for statement, prefix in zip(
        actual, expected, strict=True
    ))
    assert all(
        not statement.startswith(("DROP ", "UPDATE ", "DELETE ", "INSERT ", "TRUNCATE "))
        for statement in actual
    )
    assert not any("CREATE SCHEMA" in statement for statement in actual)
    if source == REVIEW:
        assert "package_id" in actual[0] and "PRIMARY KEY" in actual[0]
        assert "event_id" in actual[1] and "PRIMARY KEY" in actual[1]
        assert "ix_release_review_events_package" in actual[2]
        assert "event_id" not in actual[0]
    else:
        assert "NOT NULL" not in actual[0]  # Historical rows must remain valid.


def test_review_branch_has_expected_nullable_fields_and_index() -> None:
    sql = _compile(REVIEW)
    assert "CREATE TABLE ops.release_reviews" in sql
    assert "video_ref TEXT" in sql
    assert "thumbnail_ref TEXT" in sql
    assert "decided_at TIMESTAMP WITH TIME ZONE" in sql
    assert "CREATE TABLE ops.release_review_events" in sql
    assert "CREATE INDEX ix_release_review_events_package" in sql


def test_installed_merge_compiles_no_postgresql_schema_sql() -> None:
    assert INSTALLED_REVIEW.read_bytes() == REVIEW.read_bytes()
    assert sha256(INSTALLED_REVIEW.read_bytes()).hexdigest() == REVIEW_SHA
    assert _compile(INSTALLED_REVIEW) == _compile(REVIEW)
    assert _statements(_compile(MERGE)) == []
    assert _statements(_compile(MERGE, downgrade=True)) == []
    assert _statements(_compile(INSTALLED_MERGE)) == []
    assert _statements(_compile(INSTALLED_MERGE, downgrade=True)) == []


def test_proposed_sibling_downgrades_are_destructive_not_safe_on_shared_db() -> None:
    review = _statements(_compile(REVIEW, downgrade=True))
    youtube = _statements(_compile(YOUTUBE, downgrade=True))
    assert len(review) == 3
    assert review[0].startswith("DROP INDEX ops.ix_release_review_events_package")
    assert review[1].startswith("DROP TABLE ops.release_review_events")
    assert review[2].startswith("DROP TABLE ops.release_reviews")
    assert youtube == ["ALTER TABLE youtube.publish_manifests DROP COLUMN selection_json"]


def test_sql_preview_is_stable_and_uses_no_database_connection() -> None:
    # An offline MigrationContext has a dialect, but no Engine/Connection/DSN.
    for source in (REVIEW, YOUTUBE, MERGE):
        first = _compile(source)
        assert first == _compile(source)
        assert "postgresql://" not in first
        assert "PASSWORD" not in first.upper()
