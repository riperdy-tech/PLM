"""Offline graph review of the code-installed Alembic 0038 sibling merge.

The active revision files are copied into a temporary test tree for offline
inspection only. Database migration is NOT approved; no DB/SQL is executed.
"""
from __future__ import annotations

import importlib.util
from hashlib import sha256
from pathlib import Path
from shutil import copy2
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = ROOT / "migrations" / "versions"
ORIGINAL_REVIEW = ROOT / "docs" / "proposals" / "plm_0038_release_reviews_reference.py"
MERGE_DRAFT = ROOT / "docs" / "proposals" / "plm_0039_merge_0038_branches.py"
RELEASE_REV = "0038_release_reviews"
YOUTUBE_REV = "0038_youtube_candidate_selection"
MERGE_REV = "0039_merge_0038_branches"


@pytest.fixture
def draft_graph(tmp_path: Path) -> ScriptDirectory:
    root = tmp_path / "migration-preview"
    versions = root / "versions"
    for source in ACTIVE.rglob("*.py"):
        target = versions / source.relative_to(ACTIVE)
        target.parent.mkdir(parents=True, exist_ok=True)
        copy2(source, target)
    # Use the installed revisions, not the review copies. A real release
    # must not silently be tested against a different proposal file.
    assert sha256((ACTIVE / "plm/plm_0038_release_reviews.py").read_bytes()).hexdigest() == (
        "58ba6d410af2ff96ad2faf0e9e915e945df12b773677cdae4cd5b89d722b65a6"
    )
    config = Config()
    config.set_main_option("script_location", str(root))
    config.set_main_option("version_locations", str(versions))
    config.set_main_option("recursive_version_locations", "true")
    config.set_main_option("path_separator", "os")
    return ScriptDirectory.from_config(config)


def test_review_reference_preserves_original_revision_source_bytes() -> None:
    # Copied byte-for-byte from the preexisting dashboard worktree before it
    # can disappear; do not silently modify the original applied migration.
    assert sha256(ORIGINAL_REVIEW.read_bytes()).hexdigest() == (
        "58ba6d410af2ff96ad2faf0e9e915e945df12b773677cdae4cd5b89d722b65a6"
    )


def test_draft_merge_has_one_head_and_preserves_both_revisions(
    draft_graph: ScriptDirectory,
) -> None:
    # The original merge remains immutable even when later revisions
    # (currently 0041) extend the same single-head chain.
    assert draft_graph.get_heads() == ["0041_p4_credit_provenance"]
    assert draft_graph.get_bases() == ["0001_baseline"]
    merge = draft_graph.get_revision(MERGE_REV)
    assert merge is not None
    assert set(merge.down_revision) == {RELEASE_REV, YOUTUBE_REV}
    assert draft_graph.get_revision(RELEASE_REV) is not None
    assert draft_graph.get_revision(YOUTUBE_REV) is not None
    assert len(MERGE_REV) <= 32


@pytest.mark.parametrize("start_rev", [RELEASE_REV, YOUTUBE_REV])
def test_draft_merge_has_upgrade_path_from_either_existing_0038(
    draft_graph: ScriptDirectory, start_rev: str,
) -> None:
    # Inspect exact upgrade steps: the already-applied sibling must NOT be
    # executed twice. This is graph resolution, never actual DB migration.
    steps = draft_graph._upgrade_revs(MERGE_REV, start_rev)
    missing = YOUTUBE_REV if start_rev == RELEASE_REV else RELEASE_REV
    assert [step.to_revisions for step in steps] == [(missing,), (MERGE_REV,)]
    assert steps[0].from_revisions == ("0037_dashboard_views",)
    assert set(steps[1].from_revisions) == {RELEASE_REV, YOUTUBE_REV}
    assert all(start_rev not in step.to_revisions for step in steps)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("review", [
            ("create_table", "release_reviews", "ops"),
            ("create_table", "release_review_events", "ops"),
            ("create_index", "ix_release_review_events_package", "ops"),
        ]),
        ("youtube", [("add_column", "publish_manifests", "youtube")]),
    ],
)
def test_missing_sibling_upgrade_uses_only_expected_schema_operations(
    source: str, expected: list[tuple[str, str, str]],
) -> None:
    """Intercept Alembic operations; never execute schema SQL."""
    path = (
        ORIGINAL_REVIEW if source == "review"
        else ACTIVE / "youtube" / "youtube_0038_candidate_selection.py"
    )
    spec = importlib.util.spec_from_file_location(f"offline_revision_{source}", path)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    operations = MagicMock()
    revision.op = operations
    revision.upgrade()
    observed = [
        (call[0], call.args[0], call.kwargs.get("schema"))
        for call in operations.method_calls
    ]
    assert observed == expected
    if source == "youtube":
        column = operations.add_column.call_args.args[1]
        assert isinstance(column, sa.Column)
        assert column.name == "selection_json"
        assert isinstance(column.type, sa.JSON)
        assert column.nullable is True


def test_draft_merge_node_has_no_schema_operations() -> None:
    spec = importlib.util.spec_from_file_location("offline_merge", MERGE_DRAFT)
    assert spec is not None and spec.loader is not None
    merge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(merge)
    # The draft merge functions contain no operation calls or SQL.
    assert merge.upgrade() is None
    assert merge.downgrade() is None


def test_installed_revisions_form_one_head_without_changing_historical_sql() -> None:
    active = Config(str(ROOT / "alembic.ini"))
    current = ScriptDirectory.from_config(active)
    # 0040 and 0041 are code-only P4 extensions. The original merged
    # 0038 branches and 0039 SQL remain installed byte-for-byte.
    assert current.get_heads() == ["0041_p4_credit_provenance"]
    assert current.get_revision("0041_p4_credit_provenance").down_revision == "0040_p4_unpriced_costs"
    assert current.get_revision("0040_p4_unpriced_costs").down_revision == MERGE_REV
    assert current.get_bases() == ["0001_baseline"]
    known = {item.revision for item in current.walk_revisions()}
    assert {RELEASE_REV, YOUTUBE_REV, MERGE_REV} <= known
    installed_review = ACTIVE / "plm/plm_0038_release_reviews.py"
    assert installed_review.read_bytes() == ORIGINAL_REVIEW.read_bytes()
    installed_merge = ACTIVE / "plm/plm_0039_merge_0038_branches.py"
    spec = importlib.util.spec_from_file_location("installed_merge", installed_merge)
    assert spec is not None and spec.loader is not None
    merge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(merge)
    assert merge.revision == MERGE_REV
    assert set(merge.down_revision) == {RELEASE_REV, YOUTUBE_REV}
    assert merge.upgrade() is None
    assert merge.downgrade() is None
