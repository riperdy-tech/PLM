"""0041 code-only credit provenance DDL and guard regression tests."""
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
REVISION = ROOT / "migrations/versions/music/music_0041_credit_provenance.py"


def _revision():
    spec = importlib.util.spec_from_file_location("music_0041_test", REVISION)
    assert spec is not None and spec.loader is not None
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_upgrade_only_adds_nullable_credits_and_immutable_provenance() -> None:
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output},
    )
    script = _revision()
    assert script.revision == "0041_p4_credit_provenance"
    assert script.down_revision == "0040_p4_unpriced_costs"
    script.op = Operations(context)
    script.upgrade()
    sql = output.getvalue()
    assert sql.count("DROP NOT NULL") == 4
    assert sql.count("ADD COLUMN credit_basis") == 2
    assert sql.count("ADD COLUMN credit_receipt_ref") == 2
    assert sql.count("ALTER COLUMN credit_basis DROP DEFAULT") == 2
    assert sql.count("ADD CONSTRAINT") == 2
    assert "UPDATE music." not in sql
    assert "DELETE FROM music." not in sql
    assert "DROP TABLE" not in sql


def test_downgrade_refuses_converting_unknown_credit_to_fake_number() -> None:
    revision = _revision()
    operations = MagicMock()
    operations.get_bind.return_value.execute.return_value = SimpleNamespace(
        scalar_one=lambda: 1,
    )
    revision.op = operations
    with pytest.raises(RuntimeError, match="cannot downgrade"):
        revision.downgrade()
    operations.drop_column.assert_not_called()
    operations.drop_constraint.assert_not_called()


def test_0041_isolation_mode_requires_exact_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.support import migration_p4_costs_isolated_rehearsal as runner

    monkeypatch.setattr(
        runner, "rehearse",
        lambda **_kwargs: pytest.fail("isolation run without exact opt-in"),
    )
    assert runner.main(["--include-0041-credit-provenance"]) == 2
    assert runner.main([runner.OPT_IN, "plm"]) == 2
    assert runner.main([runner.OPT_IN, "--include-0041-credit-provenance", "plm"]) == 2
