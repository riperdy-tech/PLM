import importlib.util

from services.market_intelligence.storage.models import MARKET_TABLES
from tests.support.paths import REPO_ROOT

MIGRATION = REPO_ROOT / "migrations" / "versions" / "market" / "market_0002_initial_tables.py"


def _load():
    spec = importlib.util.spec_from_file_location("market_0002", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_revision_file_follows_the_naming_rule_and_chains_to_the_baseline() -> None:
    module = _load()
    assert MIGRATION.name == "market_0002_initial_tables.py"
    assert module.revision == "0002_market_initial"
    assert module.down_revision == "0001_baseline"


def test_migration_covers_every_model_table() -> None:
    module = _load()
    assert tuple(module.MARKET_TABLES) == MARKET_TABLES


def test_upgrade_and_downgrade_are_callable() -> None:
    module = _load()
    assert callable(module.upgrade) and callable(module.downgrade)
