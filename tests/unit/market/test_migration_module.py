import importlib.util

from services.market_intelligence.storage.models import MARKET_TABLES
from tests.support.paths import REPO_ROOT

MIGRATION = REPO_ROOT / "migrations" / "versions" / "market" / "market_0002_initial_tables.py"
MIGRATION_0003 = (
    REPO_ROOT / "migrations" / "versions" / "market"
    / "market_0003_observation_normalization_version.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("market_0002", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_0003():
    spec = importlib.util.spec_from_file_location("market_0003", MIGRATION_0003)
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


def test_revision_0003_follows_the_naming_rule_and_chains_to_0002() -> None:
    module = _load_0003()
    assert MIGRATION_0003.name == "market_0003_observation_normalization_version.py"
    # alembic stores revision ids in `alembic_version.version_num varchar(32)`
    assert module.revision == "0003_market_norm_version"
    assert len(module.revision) <= 32
    assert module.down_revision == "0002_market_initial"


def test_revision_0003_upgrade_and_downgrade_are_callable() -> None:
    module = _load_0003()
    assert callable(module.upgrade) and callable(module.downgrade)
