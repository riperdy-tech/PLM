"""The unit tier never reads a developer's ``.env`` or service variables (CI parity)."""

from __future__ import annotations

from pathlib import Path

import pytest
from plm_platform.config import SettingsError, load_settings


def test_a_local_dotenv_is_invisible_to_unit_tests(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://leak/leak\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SettingsError, match="DATABASE_URL"):
        load_settings()


def test_platform_env_opt_in_sets_the_documented_values(platform_env: dict[str, str]) -> None:
    settings = load_settings()
    assert settings.minio_bucket == platform_env["MINIO_BUCKET"]
