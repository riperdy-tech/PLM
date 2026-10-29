"""Credits used by providers are measured facts, not inferred from a token."""
from __future__ import annotations

from services.music_factory.observability.metrics import MusicMetricsCollector


def test_real_provider_unknown_credit_usage_is_not_reported_as_zero_spend() -> None:
    metrics = MusicMetricsCollector()
    metrics.record_attempt(provider_id="suno", success=True, credits=None)
    result = metrics.snapshot()
    assert result["generations_success"] == 1
    assert result["credits_unknown_attempts"] == 1
    assert result["total_credits_spent"] == 0.0
    assert result["mock_credits_simulated"] == 0.0


def test_simulated_credit_usage_is_not_counted_as_real_spend() -> None:
    metrics = MusicMetricsCollector()
    metrics.record_attempt(
        provider_id="mock", success=True, credits=1.0, credit_basis="mock_simulated",
    )
    result = metrics.snapshot()
    assert result["total_credits_spent"] == 0.0
    assert result["mock_credits_simulated"] == 1.0
    assert result["credits_unknown_attempts"] == 0


def test_provider_reported_credit_usage_is_counted_as_observed() -> None:
    metrics = MusicMetricsCollector()
    metrics.record_attempt(
        provider_id="suno", success=True, credits=12.0, credit_basis="provider_reported",
    )
    result = metrics.snapshot()
    assert result["total_credits_spent"] == 12.0
    assert result["credits_unknown_attempts"] == 0
