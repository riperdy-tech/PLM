"""Provider mode x execution policy, the external-write guard, and boot validation (R0).

The P7/P8 tests use spy clients that declare ``ProviderMode.REAL`` and record every
call, standing in for a real adapter: they prove the provider is unreachable unless the
guard grants a live, authorized, budgeted write.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from distribution.client.mock import MockDistributorClient
from distribution.engine import DistributionEngine
from plm_contracts import ExecutionPolicy, ProviderMode
from plm_platform.config import SettingsError
from plm_platform.execution import (
    BudgetVerdict,
    CapabilityUnavailableError,
    ExecutionSettings,
    ExternalWriteGuard,
    ExternalWriteRefusedError,
    IntegrationRequirement,
    WriteAuthorization,
    WriteDecision,
    load_execution_settings,
    provider_mode_of,
    validate_integrations,
)
from youtube_publishing.client.mock import MockYouTubeClient
from youtube_publishing.domain.enums import FailureReasonCode, OperatingMode, PublishState
from youtube_publishing.engine import YouTubePublishingEngine
from youtube_publishing.storage.repositories import insert_publish_manifest

from tests.support.distribution_db import distribution_db_session
from tests.support.youtube_db import youtube_db_session
from tests.unit.test_distribution_engine import _create_sample_package
from tests.unit.test_youtube_engine import _sample_manifest

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
NO_DOTENV = "does-not-exist.env"


class AllowAll:
    """Budget gate that funds every write, standing in for the R3 ledger."""

    def __init__(self) -> None:
        self.reservations: list[tuple[str, str, Decimal]] = []

    def reserve(self, capability: str, idempotency_key: str, cost_estimate: Decimal) -> BudgetVerdict:
        self.reservations.append((capability, idempotency_key, cost_estimate))
        return BudgetVerdict(allowed=True, reason="funded")


def _grant(capability: str, *, expires_at: datetime | None = None) -> WriteAuthorization:
    return WriteAuthorization(
        capability=capability,
        granted_by="operator@test",
        granted_at=NOW - timedelta(hours=1),
        expires_at=expires_at,
    )


def _guard(
    policies: dict[str, ExecutionPolicy],
    *,
    grants: tuple[WriteAuthorization, ...] = (),
    budget: Any = None,
) -> ExternalWriteGuard:
    return ExternalWriteGuard(
        ExecutionSettings(policies=policies),
        authorizations=grants,
        budget=budget,
        clock=lambda: NOW,
    )


def _decide(guard: ExternalWriteGuard, mode: ProviderMode = ProviderMode.REAL, **kw: Any) -> WriteDecision:
    return guard.authorize_external_write(
        "youtube.upload", provider_mode=mode, idempotency_key=kw.pop("key", "k1"), **kw
    ).decision


# --- the decision table --------------------------------------------------------------


def test_defaults_are_mock_provider_and_dry_run_policy() -> None:
    settings = ExecutionSettings()
    assert settings.provider_mode("youtube") is ProviderMode.MOCK
    assert settings.policy("youtube.upload") is ExecutionPolicy.DRY_RUN


def test_mock_provider_simulates_under_every_policy() -> None:
    for policy in ExecutionPolicy:
        assert _decide(_guard({"youtube.upload": policy}), ProviderMode.MOCK) is WriteDecision.SIMULATE


def test_real_provider_with_dry_run_or_shadow_records_intent_only() -> None:
    assert _decide(_guard({"youtube.upload": ExecutionPolicy.DRY_RUN})) is WriteDecision.RECORD_INTENT
    assert _decide(_guard({"youtube.upload": ExecutionPolicy.SHADOW})) is WriteDecision.RECORD_INTENT


def test_unconfigured_capability_never_writes() -> None:
    assert _decide(_guard({})) is WriteDecision.RECORD_INTENT


def test_read_only_denies_writes() -> None:
    assert _decide(_guard({"youtube.upload": ExecutionPolicy.READ_ONLY})) is WriteDecision.DENY


def test_live_without_authorization_is_refused() -> None:
    guard = _guard({"youtube.upload": ExecutionPolicy.LIVE}, budget=AllowAll())
    decision = guard.authorize_external_write(
        "youtube.upload", provider_mode=ProviderMode.REAL, idempotency_key="k1"
    )
    assert decision.decision is WriteDecision.DENY
    assert decision.reason == "no_authorization"


def test_authorization_is_per_capability() -> None:
    guard = _guard(
        {"youtube.upload": ExecutionPolicy.LIVE},
        grants=(_grant("youtube.thumbnail"),),
        budget=AllowAll(),
    )
    assert _decide(guard) is WriteDecision.DENY


def test_expired_authorization_is_refused() -> None:
    guard = _guard(
        {"youtube.upload": ExecutionPolicy.LIVE},
        grants=(_grant("youtube.upload", expires_at=NOW - timedelta(minutes=1)),),
        budget=AllowAll(),
    )
    decision = guard.authorize_external_write(
        "youtube.upload", provider_mode=ProviderMode.REAL, idempotency_key="k1"
    )
    assert decision.reason == "authorization_expired"


def test_live_authorized_without_budget_ledger_fails_closed() -> None:
    guard = _guard({"youtube.upload": ExecutionPolicy.LIVE}, grants=(_grant("youtube.upload"),))
    decision = guard.authorize_external_write(
        "youtube.upload", provider_mode=ProviderMode.REAL, idempotency_key="k1"
    )
    assert decision.decision is WriteDecision.DENY
    assert decision.reason == "budget_ledger_unavailable"


def test_live_authorized_and_funded_executes() -> None:
    budget = AllowAll()
    guard = _guard(
        {"youtube.upload": ExecutionPolicy.LIVE}, grants=(_grant("youtube.upload"),), budget=budget
    )
    assert _decide(guard, cost_estimate=Decimal("1.50")) is WriteDecision.EXECUTE
    assert budget.reservations == [("youtube.upload", "k1", Decimal("1.50"))]


def test_missing_idempotency_key_is_refused_even_for_mocks() -> None:
    guard = _guard({"youtube.upload": ExecutionPolicy.LIVE})
    assert _decide(guard, ProviderMode.MOCK, key="  ") is WriteDecision.DENY


def test_policy_ceiling_tightens_but_never_loosens() -> None:
    budget = AllowAll()
    live = _guard(
        {"youtube.upload": ExecutionPolicy.LIVE}, grants=(_grant("youtube.upload"),), budget=budget
    )
    assert _decide(live, policy_ceiling=ExecutionPolicy.DRY_RUN) is WriteDecision.RECORD_INTENT
    dry = _guard({"youtube.upload": ExecutionPolicy.DRY_RUN})
    assert _decide(dry, policy_ceiling=ExecutionPolicy.LIVE) is WriteDecision.RECORD_INTENT


def test_every_decision_is_recorded() -> None:
    guard = _guard({})
    _decide(guard)
    _decide(guard, ProviderMode.MOCK)
    assert [d.decision for d in guard.decisions] == [WriteDecision.RECORD_INTENT, WriteDecision.SIMULATE]


def test_undeclared_clients_count_as_real() -> None:
    assert provider_mode_of(object()) is ProviderMode.REAL
    assert provider_mode_of(MockYouTubeClient()) is ProviderMode.MOCK
    assert provider_mode_of(MockDistributorClient()) is ProviderMode.MOCK


# --- configuration ---------------------------------------------------------------------


def test_settings_load_from_env() -> None:
    settings = load_execution_settings(
        env={
            "PLM_PROVIDER_MODE__YOUTUBE": "REAL",
            "PLM_EXECUTION_POLICY__YOUTUBE_UPLOAD": "live",
            "PLM_EXECUTION_POLICY__DISTRIBUTION_SUBMIT": "shadow",
        },
        dotenv_path=NO_DOTENV,
    )
    assert settings.provider_mode("youtube") is ProviderMode.REAL
    assert settings.policy("youtube.upload") is ExecutionPolicy.LIVE
    assert settings.policy("distribution.submit") is ExecutionPolicy.SHADOW


def test_invalid_setting_names_the_variable() -> None:
    with pytest.raises(SettingsError, match="PLM_EXECUTION_POLICY__YOUTUBE_UPLOAD"):
        load_execution_settings(
            env={"PLM_EXECUTION_POLICY__YOUTUBE_UPLOAD": "yolo"}, dotenv_path=NO_DOTENV
        )


# --- boot validation --------------------------------------------------------------------


def test_required_integration_without_implementation_fails_boot() -> None:
    settings = ExecutionSettings(provider_modes={"youtube": ProviderMode.REAL})
    with pytest.raises(CapabilityUnavailableError, match="youtube"):
        validate_integrations(
            [IntegrationRequirement("youtube", frozenset({ProviderMode.MOCK}), required=True)],
            settings,
        )


def test_missing_optional_integration_only_warns() -> None:
    settings = ExecutionSettings(provider_modes={"spotify": ProviderMode.REAL})
    warnings = validate_integrations(
        [IntegrationRequirement("spotify", frozenset({ProviderMode.MOCK}), required=False)],
        settings,
    )
    assert len(warnings) == 1
    assert "spotify" in warnings[0]


def test_youtube_app_refuses_to_boot_in_real_mode_without_a_real_client() -> None:
    from youtube_publishing.api.app import create_app

    with pytest.raises(CapabilityUnavailableError):
        create_app(
            session_factory=object(),
            settings=_platform(),
            execution=ExecutionSettings(provider_modes={"youtube": ProviderMode.REAL}),
        )


def test_distribution_app_refuses_to_boot_in_real_mode_without_a_real_client() -> None:
    from distribution.api.app import create_app

    with pytest.raises(CapabilityUnavailableError):
        create_app(
            session_factory=object(),
            settings=_platform(),
            execution=ExecutionSettings(provider_modes={"distribution": ProviderMode.REAL}),
        )


def _platform() -> Any:
    from plm_platform.config import load_settings

    return load_settings(
        env={
            "DATABASE_URL": "postgresql://plm:x@127.0.0.1:9/plm", "POSTGRES_PASSWORD": "x",
            "MINIO_ENDPOINT": "127.0.0.1:9", "MINIO_ROOT_USER": "plm", "MINIO_ROOT_PASSWORD": "x",
            "MINIO_BUCKET": "plm-media", "TEMPORAL_HOST": "127.0.0.1:9",
        },
        dotenv_path=NO_DOTENV,
    )


# --- P7: the guard is the only path to YouTube writes ------------------------------------


class SpyRealYouTubeClient(MockYouTubeClient):
    """Behaves like the mock but declares itself real, so the guard treats it as external."""

    provider_mode = ProviderMode.REAL


_YT_WRITES = {"insert_resumable_session", "upload_video_chunk", "set_thumbnail", "insert_playlist_item"}


def _publish(guard: ExternalWriteGuard, mode: OperatingMode = OperatingMode.AUTONOMOUS) -> tuple[Any, SpyRealYouTubeClient]:
    client = SpyRealYouTubeClient()
    engine = YouTubePublishingEngine(client, chunk_size=512, write_guard=guard)
    manifest = _sample_manifest()
    with youtube_db_session() as session:
        insert_publish_manifest(session, manifest)
        result = engine.execute_publish(
            session, manifest, video_data=b"0" * 1024, thumbnail_data=b"IMG", operating_mode=mode
        )
    return result, client


def _yt_writes(client: SpyRealYouTubeClient) -> list[str]:
    return [c["method"] for c in client.recorded_calls if c["method"] in _YT_WRITES]


def test_p7_real_client_under_dry_run_policy_is_never_called() -> None:
    result, client = _publish(_guard({}))
    assert _yt_writes(client) == []
    assert result.attempt.state is PublishState.INTENT_RECORDED
    assert result.publication_record is None
    assert not result.is_success


def test_p7_live_without_authorization_is_refused_before_any_write() -> None:
    policies = {c: ExecutionPolicy.LIVE for c in ("youtube.upload", "youtube.thumbnail", "youtube.playlist")}
    result, client = _publish(_guard(policies, budget=AllowAll()))
    assert _yt_writes(client) == []
    assert result.attempt.state is PublishState.FAILED_RETRYABLE
    assert result.attempt.error_code == FailureReasonCode.EXTERNAL_WRITE_NOT_AUTHORIZED.value


def test_p7_operating_mode_dry_run_caps_a_live_authorized_policy() -> None:
    caps = ("youtube.upload", "youtube.thumbnail", "youtube.playlist")
    guard = _guard(
        {c: ExecutionPolicy.LIVE for c in caps}, grants=tuple(_grant(c) for c in caps), budget=AllowAll()
    )
    result, client = _publish(guard, OperatingMode.DRY_RUN)
    assert _yt_writes(client) == []
    assert result.attempt.state is PublishState.INTENT_RECORDED


def test_p7_live_authorized_and_funded_publishes() -> None:
    caps = ("youtube.upload", "youtube.thumbnail", "youtube.playlist")
    guard = _guard(
        {c: ExecutionPolicy.LIVE for c in caps}, grants=tuple(_grant(c) for c in caps), budget=AllowAll()
    )
    result, client = _publish(guard)
    assert result.is_success
    assert set(_yt_writes(client)) == _YT_WRITES


# --- P8: the guard is the only path to distributor writes --------------------------------


class SpyRealDistributor(MockDistributorClient):
    provider_mode = ProviderMode.REAL

    def __init__(self) -> None:
        super().__init__()
        self.writes: list[str] = []

    def submit_release(self, package: Any) -> Any:
        self.writes.append("submit_release")
        return super().submit_release(package)

    def request_takedown(self, provider_release_id: str) -> Any:
        self.writes.append("request_takedown")
        return super().request_takedown(provider_release_id)


def test_p8_real_client_under_dry_run_is_never_called_and_nothing_persists() -> None:
    client = SpyRealDistributor()
    engine = DistributionEngine(client, write_guard=_guard({}))
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)
        with pytest.raises(ExternalWriteRefusedError) as exc:
            engine.submit_release(session, pkg, artist=artist)
    assert exc.value.decision.decision is WriteDecision.RECORD_INTENT
    assert client.writes == []


def test_p8_live_without_authorization_is_refused() -> None:
    client = SpyRealDistributor()
    guard = _guard({"distribution.submit": ExecutionPolicy.LIVE}, budget=AllowAll())
    engine = DistributionEngine(client, write_guard=guard)
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)
        with pytest.raises(ExternalWriteRefusedError) as exc:
            engine.submit_release(session, pkg, artist=artist)
    assert exc.value.decision.reason == "no_authorization"
    assert client.writes == []


def test_p8_takedown_needs_its_own_grant() -> None:
    client = SpyRealDistributor()
    guard = _guard(
        {"distribution.submit": ExecutionPolicy.LIVE, "distribution.takedown": ExecutionPolicy.LIVE},
        grants=(_grant("distribution.submit"),),
        budget=AllowAll(),
    )
    engine = DistributionEngine(client, write_guard=guard)
    with distribution_db_session() as session:
        pkg, artist = _create_sample_package(session)
        submitted = engine.submit_release(session, pkg, artist=artist)
        with pytest.raises(ExternalWriteRefusedError):
            engine.request_takedown(session, submitted.release.release_id)
    assert client.writes == ["submit_release"]
