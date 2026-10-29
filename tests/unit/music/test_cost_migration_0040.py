"""Read-only review of the code-only 0040 monetary provenance migration."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[3]
REVISION = ROOT / "migrations/versions/music/music_0040_unpriced_costs.py"


def _revision():
    spec = importlib.util.spec_from_file_location("music_0040_unpriced_test", REVISION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_emits_only_nullable_columns_and_provenance_ddl() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output},
    )
    revision = _revision()
    assert revision.revision == "0040_p4_unpriced_costs"
    assert revision.down_revision == "0039_merge_0038_branches"
    revision.op = Operations(context)
    revision.upgrade()
    sql = output.getvalue()
    assert "ALTER TABLE music.generation_attempts ALTER COLUMN actual_cost DROP NOT NULL" in sql
    assert "ALTER TABLE music.provider_cost_records ALTER COLUMN actual_amount DROP NOT NULL" in sql
    assert "ALTER TABLE music.provider_cost_records ALTER COLUMN currency DROP NOT NULL" in sql
    assert sql.count("DEFAULT 'legacy_unverified' NOT NULL") == 2
    assert sql.count("ALTER COLUMN money_basis DROP DEFAULT") == 2
    assert sql.count("ADD COLUMN cost_receipt_ref VARCHAR(256)") == 2
    assert "UPDATE music." not in sql
    assert "DELETE FROM music." not in sql
    assert "DROP TABLE" not in sql
    assert "TRUNCATE " not in sql


def test_downgrade_refuses_discarding_unknown_money() -> None:
    revision = _revision()
    fake = MagicMock()
    fake.get_bind.return_value.execute.return_value = SimpleNamespace(
        scalar_one=lambda: 1
    )
    revision.op = fake
    with pytest.raises(RuntimeError, match="cannot downgrade"):
        revision.downgrade()
    fake.drop_column.assert_not_called()
    fake.alter_column.assert_not_called()


def test_isolated_rehearsal_requires_exact_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.support import migration_p4_costs_isolated_rehearsal as runner

    monkeypatch.setattr(
        runner, "rehearse",
        lambda: pytest.fail("real backup/test called without explicit opt-in"),
    )
    assert runner.main([]) == 2
    assert runner.main(["--apply"]) == 2
    assert runner.main(["--approved-isolated-test-only", "plm"]) == 2
