"""The compose containers never read the bind-mounted .env; compose passes every key.

Finding 54 (review 2026-11-26): the repo is mounted at /app, so /app/.env silently set
provider modes, policies and grants inside the containers. They now set
PLM_DOTENV_DISABLED and receive each key explicitly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from plm_platform.config import PROVIDER_SECRET_VARS, REQUIRED_VARS, SERVICE_TOKEN_VAR
from plm_platform.dotenv import DISABLED_VAR, load_env_file

from tests.support import REPO_ROOT

APP_SERVICES = ("worker", "pipeline-worker", "api-market", "api-ar", "api-experiments",
                "api-music", "api-qa", "api-media", "api-youtube", "api-distribution",
                "api-analytics")


@pytest.fixture(scope="module")
def services() -> dict[str, Any]:
    parsed = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return dict(parsed["services"])


@pytest.mark.parametrize("name", APP_SERVICES)
def test_every_app_container_disables_dotenv_and_gets_each_platform_key(
    services: dict[str, Any], name: str,
) -> None:
    env = services[name]["environment"]
    assert isinstance(env, dict)
    assert env[DISABLED_VAR] == "1"
    expected = {*REQUIRED_VARS, *PROVIDER_SECRET_VARS, SERVICE_TOKEN_VAR,
                "YOUTUBE_OAUTH_REFRESH_TOKEN", "PLM_WRITE_AUTHORIZATIONS_FILE",
                "PLM_PROVIDER_MODE__YOUTUBE", "PLM_PROVIDER_MODE__MUSIC", "CI"}
    assert expected <= set(env), sorted(expected - set(env))


def test_the_flag_stops_every_dotenv_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("PLM_PROVIDER_MODE__YOUTUBE=real\n", encoding="utf-8")
    assert load_env_file(dotenv) == {"PLM_PROVIDER_MODE__YOUTUBE": "real"}
    monkeypatch.setenv(DISABLED_VAR, "1")
    assert load_env_file(dotenv) == {}
