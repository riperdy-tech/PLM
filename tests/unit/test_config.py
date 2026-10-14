"""W0.4: layered configuration, fail-fast validation and secret hygiene.

Hermetic by construction: every test injects the process-level mapping and points the
loader at a dotenv path it controls, so a developer's real ``.env`` can never influence
the suite — and ``.env`` is never required. The one intentional exception reads the
committed ``.env.example``: those tests are the drift guard binding that file to this
package.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

import pytest
from plm_platform.config import (
    DEFAULTS,
    PROVIDER_SECRET_VARS,
    REQUIRED_VARS,
    SERVICE_TOKEN_VAR,
    Env,
    LogLevel,
    Secret,
    Settings,
    SettingsError,
    load_settings,
)

INTERNAL_SECRET_VARS = (SERVICE_TOKEN_VAR,)
"""Operator-configured secrets that are not provider credentials (R15)."""

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
ABSENT_DOTENV = Path(__file__).resolve().parent / "no-such.env"

POSTGRES_SENTINEL = "postgres-secret-sentinel"
MINIO_SENTINEL = "minio-secret-sentinel"
YOUTUBE_SENTINEL = "youtube-secret-sentinel"


def full_env(**overrides: str) -> dict[str, str]:
    """A complete, hand-written process environment — no file, no defaults involved."""
    env = {
        "DATABASE_URL": f"postgresql://plm:{POSTGRES_SENTINEL}@db.internal:5432/plm",
        "POSTGRES_PASSWORD": POSTGRES_SENTINEL,
        "MINIO_ENDPOINT": "objects.internal:9000",
        "MINIO_ROOT_USER": "plm-root",
        "MINIO_ROOT_PASSWORD": MINIO_SENTINEL,
        "MINIO_BUCKET": "plm-media",
        "TEMPORAL_HOST": "temporal.internal:7233",
    }
    env.update(overrides)
    return env


def load(env: Mapping[str, str] | None, dotenv: Path = ABSENT_DOTENV) -> Settings:
    """Load settings from an explicit environment; the dotenv file is opt-in per test."""
    return load_settings(env=env, dotenv_path=dotenv)


def env_example_names() -> tuple[set[str], set[str]]:
    """Names assigned and names only mentioned in comments of ``.env.example``."""
    active: set[str] = set()
    commented: set[str] = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            body = stripped.lstrip("#").strip()
            name = body.split("=", 1)[0].strip()
            if "=" in body and name:
                commented.add(name)
        elif "=" in stripped:
            active.add(stripped.split("=", 1)[0].strip())
    return active, commented


class TestLayeredLoading:
    def test_process_environment_overrides_the_dotenv_file(self, tmp_path: Path) -> None:
        dotenv = tmp_path / ".env"
        dotenv.write_text(
            "POSTGRES_PASSWORD=file-secret\nMINIO_BUCKET=file-bucket\n", encoding="utf-8"
        )
        settings = load(full_env(), dotenv=dotenv)
        assert settings.postgres_password.get_secret_value() == POSTGRES_SENTINEL
        assert settings.minio_bucket == "plm-media"

    def test_dotenv_fills_variables_the_process_environment_omits(self, tmp_path: Path) -> None:
        dotenv = tmp_path / ".env"
        dotenv.write_text("MINIO_BUCKET=file-bucket\n", encoding="utf-8")
        env = full_env()
        del env["MINIO_BUCKET"]
        settings = load(env, dotenv=dotenv)
        assert settings.minio_bucket == "file-bucket"

    def test_dotenv_overrides_documented_defaults(self, tmp_path: Path) -> None:
        dotenv = tmp_path / ".env"
        dotenv.write_text("ENV=staging\nLOG_LEVEL=DEBUG\n", encoding="utf-8")
        settings = load(full_env(), dotenv=dotenv)
        assert settings.env is Env.STAGING
        assert settings.log_level is LogLevel.DEBUG

    def test_documented_defaults_apply_when_there_is_no_dotenv_file(self) -> None:
        settings = load(full_env())
        assert settings.env is Env.LOCAL
        assert settings.log_level is LogLevel.INFO

    def test_empty_values_count_as_unset(self) -> None:
        settings = load(full_env(ENV="", YOUTUBE_API_KEY=""))
        assert settings.env is Env.LOCAL
        assert settings.youtube_api_key is None
        assert settings.is_mock_mode()

    def test_process_environment_is_the_default_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name, value in full_env().items():
            monkeypatch.setenv(name, value)
        settings = load_settings(dotenv_path=ABSENT_DOTENV)
        assert settings.minio_endpoint == "objects.internal:9000"
        assert settings.postgres_password.get_secret_value() == POSTGRES_SENTINEL


class TestTypedSettings:
    def test_enum_values_are_typed_and_case_normalised(self) -> None:
        settings = load(full_env(ENV="staging", LOG_LEVEL="debug"))
        assert settings.env is Env.STAGING
        assert settings.log_level is LogLevel.DEBUG

    def test_deployment_values_are_carried_verbatim(self) -> None:
        settings = load(full_env())
        assert settings.database_url.get_secret_value().startswith("postgresql://")
        assert settings.minio_endpoint == "objects.internal:9000"
        assert settings.minio_root_user == "plm-root"
        assert settings.temporal_host == "temporal.internal:7233"


class TestFailFast:
    @pytest.mark.parametrize("name", REQUIRED_VARS)
    def test_each_required_variable_fails_by_name_when_it_is_missing(self, name: str) -> None:
        env = full_env()
        del env[name]
        with pytest.raises(SettingsError) as excinfo:
            load(env)
        assert name in str(excinfo.value)
        for other in REQUIRED_VARS:
            if other != name:
                assert other not in str(excinfo.value)

    def test_missing_variable_failure_never_carries_a_value(self) -> None:
        env = full_env(YOUTUBE_API_KEY=YOUTUBE_SENTINEL)
        del env["TEMPORAL_HOST"]
        with pytest.raises(SettingsError) as excinfo:
            load(env)
        rendered = f"{excinfo.value!r} {excinfo.value}"
        assert "TEMPORAL_HOST" in str(excinfo.value)
        for sentinel in (POSTGRES_SENTINEL, MINIO_SENTINEL, YOUTUBE_SENTINEL):
            assert sentinel not in rendered

    @pytest.mark.parametrize(("name", "bad_value"), [("ENV", "brisk"), ("LOG_LEVEL", "SHOUTY")])
    def test_an_invalid_value_is_refused_without_echoing_it(
        self, name: str, bad_value: str
    ) -> None:
        with pytest.raises(SettingsError) as excinfo:
            load(full_env(**{name: bad_value}))
        assert name in str(excinfo.value)
        assert bad_value not in str(excinfo.value)


class TestSecret:
    def test_repr_and_str_are_redacted(self) -> None:
        secret = Secret(POSTGRES_SENTINEL)
        assert POSTGRES_SENTINEL not in repr(secret)
        assert POSTGRES_SENTINEL not in str(secret)
        assert POSTGRES_SENTINEL not in f"{secret}"
        assert "**********" in repr(secret)

    def test_the_value_is_reachable_only_through_the_explicit_accessor(self) -> None:
        assert Secret(POSTGRES_SENTINEL).get_secret_value() == POSTGRES_SENTINEL

    def test_a_secret_cannot_be_concatenated_into_a_string(self) -> None:
        secret = Secret(POSTGRES_SENTINEL)
        with pytest.raises(TypeError):
            _ = "token=" + secret
        # str.join refuses a Secret outright; an f-string would silently redact instead
        # of raising, so the join path is pinned separately.
        parts = ["token=", secret]
        with pytest.raises(TypeError):
            _ = "".join(parts)

    def test_equality_compares_secret_values(self) -> None:
        assert Secret("a") == Secret("a")
        assert Secret("a") != Secret("b")
        assert Secret("a") != "a"


class TestSecretHygieneInSettings:
    def test_repr_of_settings_contains_no_secret_value(self) -> None:
        settings = load(full_env(YOUTUBE_API_KEY=YOUTUBE_SENTINEL))
        rendered = f"{settings!r} {settings}"
        for sentinel in (POSTGRES_SENTINEL, MINIO_SENTINEL, YOUTUBE_SENTINEL):
            assert sentinel not in rendered
        assert "**********" in repr(settings)

    def test_logging_a_secret_writes_only_redaction(self, caplog: pytest.LogCaptureFixture) -> None:
        settings = load(full_env())
        with caplog.at_level(logging.INFO):
            logging.getLogger("plm.w0_4.hygiene").info(
                "db=%s password=%s", settings.database_url, settings.postgres_password
            )
        assert POSTGRES_SENTINEL not in caplog.text
        assert "**********" in caplog.text


class TestMockMode:
    def test_mock_mode_is_true_when_no_provider_credential_is_set(self) -> None:
        settings = load(full_env())
        assert settings.is_mock_mode()
        for name in PROVIDER_SECRET_VARS:
            assert getattr(settings, name.lower()) is None

    def test_mock_mode_is_false_as_soon_as_a_credential_is_set(self) -> None:
        settings = load(full_env(YOUTUBE_API_KEY=YOUTUBE_SENTINEL))
        assert not settings.is_mock_mode()
        assert settings.youtube_api_key is not None
        assert settings.youtube_api_key.get_secret_value() == YOUTUBE_SENTINEL
        assert settings.spotify_client_id is None


class TestServiceToken:
    def test_the_service_token_is_optional_and_redacted(self) -> None:
        settings = load(full_env(PLM_SERVICE_TOKEN="shared-secret-sentinel"))
        assert settings.service_token is not None
        assert settings.service_token.get_secret_value() == "shared-secret-sentinel"
        assert "shared-secret-sentinel" not in repr(settings)

    def test_absent_service_token_is_none_and_does_not_break_mock_mode(self) -> None:
        settings = load(full_env())
        assert settings.service_token is None
        assert settings.is_mock_mode()

    def test_the_token_variable_name_is_not_a_provider_credential(self) -> None:
        assert SERVICE_TOKEN_VAR not in PROVIDER_SECRET_VARS


class TestEnvExampleDrift:
    def test_loader_variable_names_match_the_example_exactly(self) -> None:
        active, commented = env_example_names()
        assert active == set(REQUIRED_VARS)
        assert commented == set(PROVIDER_SECRET_VARS) | set(INTERNAL_SECRET_VARS)
        # ENV and LOG_LEVEL are operator knobs with code defaults, not deployment
        # values; .env.example must not grow or lose entries silently.
        assert set(DEFAULTS) & (active | commented) == set()

    def test_example_placeholder_values_become_the_local_configuration(self) -> None:
        settings = load(env={}, dotenv=ENV_EXAMPLE)
        assert settings.env is Env.LOCAL
        assert settings.log_level is LogLevel.INFO
        assert settings.postgres_password.get_secret_value() == "change-me"
        expected_url = "postgresql://plm:change-me@127.0.0.1:5432/plm"
        assert settings.database_url.get_secret_value() == expected_url
        assert settings.minio_endpoint == "127.0.0.1:9000"
        assert settings.minio_root_user == "plm"
        assert settings.minio_root_password.get_secret_value() == "change-me"
        assert settings.minio_bucket == "plm-media"
        assert settings.temporal_host == "127.0.0.1:7233"
        assert settings.is_mock_mode()
