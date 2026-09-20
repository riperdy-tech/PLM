import random

import pytest

from services.market_intelligence.collection.retries import (
    RetryPolicy,
    classify_http_status,
    delay_for_attempt,
    execute_with_retries,
    is_retryable,
)
from services.market_intelligence.connectors.base import ConnectorError
from services.market_intelligence.domain.enums import ConnectorErrorClass


def test_status_mapping_covers_every_class_the_connector_can_see() -> None:
    assert classify_http_status(400) is ConnectorErrorClass.INVALID_REQUEST
    assert classify_http_status(401) is ConnectorErrorClass.AUTH_FAILURE
    assert classify_http_status(404) is ConnectorErrorClass.NOT_FOUND
    assert classify_http_status(429) is ConnectorErrorClass.RATE_LIMIT
    assert classify_http_status(500) is ConnectorErrorClass.PROVIDER_5XX
    assert classify_http_status(503) is ConnectorErrorClass.PROVIDER_5XX


def test_only_transient_classes_are_retryable() -> None:
    assert is_retryable(ConnectorErrorClass.RATE_LIMIT)
    assert is_retryable(ConnectorErrorClass.TRANSIENT_NETWORK)
    assert is_retryable(ConnectorErrorClass.PROVIDER_5XX)
    for permanent in (
        ConnectorErrorClass.INVALID_REQUEST, ConnectorErrorClass.AUTH_FAILURE,
        ConnectorErrorClass.NOT_FOUND, ConnectorErrorClass.SCHEMA_CHANGE,
        ConnectorErrorClass.QUOTA_EXCEEDED, ConnectorErrorClass.PARSER_FAILURE,
        ConnectorErrorClass.UNSUPPORTED_OPERATION, ConnectorErrorClass.DATA_QUALITY,
    ):
        assert not is_retryable(permanent), permanent


def test_provider_retry_after_wins_over_the_backoff_curve() -> None:
    policy = RetryPolicy()
    assert delay_for_attempt(policy, 1, rng=random.Random(0), retry_after_seconds=30.0) == 30.0
    assert delay_for_attempt(policy, 1, rng=random.Random(0), retry_after_seconds=600.0) == 60.0


def test_retries_stop_after_max_attempts_and_raise_exhausted() -> None:
    calls = {"n": 0}
    sleeps: list[float] = []

    def attempt() -> str:
        calls["n"] += 1
        raise ConnectorError(ConnectorErrorClass.PROVIDER_5XX, "boom")

    with pytest.raises(ConnectorError) as excinfo:
        execute_with_retries(
            attempt, policy=RetryPolicy(max_attempts=3), sleep=sleeps.append,
            rng=random.Random(7),
        )
    assert calls["n"] == 3
    assert excinfo.value.error_class is ConnectorErrorClass.PROVIDER_5XX
    assert len(sleeps) == 2  # no sleep after the final attempt
    assert all(0 < delay <= RetryPolicy().max_delay_seconds + RetryPolicy().jitter_seconds
               for delay in sleeps)


def test_permanent_errors_are_not_retried() -> None:
    calls = {"n": 0}

    def attempt() -> str:
        calls["n"] += 1
        raise ConnectorError(ConnectorErrorClass.INVALID_REQUEST, "bad request")

    with pytest.raises(ConnectorError):
        execute_with_retries(
            attempt, policy=RetryPolicy(max_attempts=3), sleep=lambda _d: None,
            rng=random.Random(0),
        )
    assert calls["n"] == 1
