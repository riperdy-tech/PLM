"""W0.5 layout guards: env.py discipline, revision naming, single chain head.

These run without a database — alembic is used as a library, never a subprocess.
They pin the rules documented in ``migrations/README.md``.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

from alembic.config import Config
from alembic.script import ScriptDirectory
from plm_platform.db import SCHEMAS

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = REPO_ROOT / "migrations"
VERSIONS = MIGRATIONS / "versions"
BASELINE = VERSIONS / "plm" / "plm_0001_baseline_schemas.py"
REVISION_FILE = re.compile(r"^(?P<schema>[a-z]+)_(?P<revision>[0-9a-z]+)_(?P<slug>[a-z0-9_]+)\.py$")
PLATFORM_TOKEN = "plm"


def _config() -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


def _load_baseline() -> ModuleType:
    spec = importlib.util.spec_from_file_location("plm_baseline_revision", BASELINE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestEnvPyDiscipline:
    def test_env_py_builds_the_url_from_plm_platform_config(self) -> None:
        source = (MIGRATIONS / "env.py").read_text(encoding="utf-8")
        assert "load_settings" in source
        assert "plm_platform" in source

    def test_env_py_never_reads_the_process_environment_or_hardcodes_a_url(self) -> None:
        source = (MIGRATIONS / "env.py").read_text(encoding="utf-8")
        assert "os.environ" not in source
        assert "postgresql://" not in source


class TestRevisionConvention:
    def test_revision_files_are_named_schema_revision_slug_under_their_schema_directory(
        self,
    ) -> None:
        files = sorted(VERSIONS.rglob("*.py"))
        assert files, "the baseline revision must exist"
        allowed_schemas = (*SCHEMAS, PLATFORM_TOKEN)
        for path in files:
            match = REVISION_FILE.match(path.name)
            assert match is not None, f"{path.name} must be <schema>_<revision>_<slug>.py"
            assert match["schema"] == path.parent.name, (
                f"{path.name} must live in the {match['schema']}/ directory"
            )
            assert match["schema"] in allowed_schemas, f"unknown schema token in {path.name}"


class TestSingleChain:
    def test_chain_has_exactly_one_head_and_one_base(self) -> None:
        script = ScriptDirectory.from_config(_config())
        assert len(script.get_heads()) == 1, "the alembic chain must not fork"
        assert len(script.get_bases()) == 1, "the alembic chain must have one base"

    def test_every_revision_id_fits_the_alembic_version_column(self) -> None:
        """``alembic_version.version_num`` is ``varchar(32)`` on Postgres. SQLite does not
        enforce the length, so an over-long id passes every unit test and then fails the
        first real upgrade."""
        script = ScriptDirectory.from_config(_config())
        too_long = sorted(r.revision for r in script.walk_revisions() if len(r.revision) > 32)
        assert not too_long, f"revision ids longer than 32 characters: {too_long}"


class TestBaseline:
    def test_baseline_schema_list_matches_the_platform_module(self) -> None:
        assert tuple(_load_baseline().PHASE_SCHEMAS) == SCHEMAS

    def test_baseline_is_the_chain_root(self) -> None:
        script = ScriptDirectory.from_config(_config())
        assert script.get_bases() == ["0001_baseline"]
