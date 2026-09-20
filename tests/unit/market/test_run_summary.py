# tests/unit/market/test_run_summary.py
"""§AC-14: one object answers what ran, on which source, over which queries, with how
much quota and LLM spend, and what changed.

Money and quota figures state their unit: `quota_units` are YouTube API quota units
(`search.list` = 100) and `llm_cost_estimate` is USD — an unlabelled number cannot be
priced by P9 (§42). The structured line is asserted through the platform's own
`JSONFormatter`, which is what a deployed worker writes.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest
from plm_platform.observability import JSONFormatter

from services.market_intelligence.domain.enums import ConnectorType, ProviderName
from services.market_intelligence.observability.logging import log_run_summary
from services.market_intelligence.observability.metrics import (
    QUARANTINED_TOTAL,
    QUOTA_UNITS_TOTAL,
    RUNS_TOTAL,
    record_run_metrics,
)
from services.market_intelligence.observability.run_summary import summarize_run
from services.market_intelligence.storage import repositories

FIXED_NOW = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)


def _running_run(market_session) -> str:
    source_id = repositories.upsert_source(
        market_session, provider_name=ProviderName.YOUTUBE_DATA_API,
        connector_type=ConnectorType.DATA_API, base_identifier="x",
        configuration_version="p1-sources-v1",
    )
    return repositories.start_collection_run(
        market_session, source_id=source_id, query_id=None, software_version="p1-v1",
        started_at=FIXED_NOW,
    )


def test_run_summary_answers_every_ac14_question(market_session, caplog) -> None:
    run_id = _running_run(market_session)
    repositories.add_run_cost(market_session, run_id, api_calls=2, api_quota_units=200,
                              llm_calls=1, llm_tokens=900, llm_cost_estimate=0.0135,
                              storage_bytes=4096)
    summary = summarize_run(market_session, run_id)
    assert summary.run_id == run_id
    assert summary.source == ProviderName.YOUTUBE_DATA_API.value
    assert summary.status == "running"
    assert summary.api_calls == 2
    assert summary.quota_units == 200
    assert summary.quota_units_unit == "youtube_api_quota_units"
    assert summary.llm_calls == 1
    assert summary.llm_tokens == 900
    assert summary.llm_cost_estimate == 0.0135
    assert summary.cost_currency == "USD"
    assert summary.storage_bytes == 4096
    assert summary.score_changes == 0  # a returned query: a real zero, not missing data

    with caplog.at_level(logging.INFO, logger="plm.market.run"):
        log_run_summary(summary)
    assert caplog.records, "the run summary must emit one structured line"
    record = caplog.records[-1]
    assert record.getMessage() == "market run summary"
    payload = json.loads(JSONFormatter().format(record))
    assert payload["run_id"] == run_id and payload["status"] == summary.status
    for counter in ("api_calls", "api_quota_units", "llm_calls", "llm_tokens",
                    "llm_cost_estimate", "storage_bytes"):
        assert counter in payload, counter
    assert payload["api_quota_units_unit"] == "youtube_api_quota_units"
    assert payload["llm_cost_estimate_currency"] == "USD"
    assert payload["llm_tokens"] == 900 and payload["api_calls"] == 2


def test_metrics_increment_the_platform_registry(market_session) -> None:
    run_id = _running_run(market_session)
    repositories.add_run_cost(market_session, run_id, api_quota_units=100)
    summary = summarize_run(market_session, run_id)
    before_runs = RUNS_TOTAL.labels(status="running")._value.get()
    before_quota = QUOTA_UNITS_TOTAL.labels(provider="youtube_data_api")._value.get()
    before_quarantined = QUARANTINED_TOTAL.labels(provider="youtube_data_api")._value.get()
    record_run_metrics(summary)
    assert RUNS_TOTAL.labels(status="running")._value.get() == before_runs + 1
    assert QUOTA_UNITS_TOTAL.labels(provider="youtube_data_api")._value.get() == \
        before_quota + 100
    assert QUARANTINED_TOTAL.labels(provider="youtube_data_api")._value.get() == \
        before_quarantined  # zero quarantined records is a real zero


def test_an_unknown_run_id_is_a_key_error(market_session) -> None:
    with pytest.raises(KeyError):
        summarize_run(market_session, "00000000-0000-0000-0000-000000000000")


def test_a_collection_result_emits_its_six_counters(market_session, caplog) -> None:
    """Every collection entry point routes through `_emit_run_costs` (P1.14 proves it live)."""
    from services.market_intelligence.cli import _emit_run_costs

    run_id = _running_run(market_session)
    repositories.add_run_cost(market_session, run_id, api_calls=3, api_quota_units=102,
                              llm_calls=1, llm_tokens=120, llm_cost_estimate=0.0018,
                              storage_bytes=2048)
    with caplog.at_level(logging.INFO, logger="plm.market.run"):
        costs = _emit_run_costs(market_session, {"run_id": run_id})
    assert costs is not None
    assert costs["api_calls"] == 3 and costs["api_quota_units"] == 102
    assert costs["api_quota_units_unit"] == "youtube_api_quota_units"
    assert costs["llm_calls"] == 1 and costs["llm_tokens"] == 120
    assert costs["llm_cost_estimate"] == 0.0018
    assert costs["llm_cost_estimate_currency"] == "USD"
    assert costs["storage_bytes"] == 2048
    assert caplog.records[-1].getMessage() == "market run summary"


def test_a_collection_with_no_run_emits_nothing(market_session) -> None:
    from services.market_intelligence.cli import _emit_run_costs

    assert _emit_run_costs(market_session, {"queries": 0}) is None
