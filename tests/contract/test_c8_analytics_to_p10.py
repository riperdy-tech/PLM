# tests/contract/test_c8_analytics_to_p10.py
"""Seam C8 — PHASE-P9 → PHASE-P10 (feature view). R12 (P10 deferred), ADR-0025.

Producer: P9's real ``build_p10_feature_view`` and ``GET /analytics/p10/features``.
Consumer: P10 is deferred, so the consumer is the shared
``plm_contracts.seams.c8.P10FeatureViewPayload`` a future P10 will parse with. The test
proves the producer emits that wire, serves unmeasured values as ``null`` rather than
zeros, and that the model refuses incomplete or future views.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from plm_contracts import ObservationWindow
from plm_contracts.seams import SeamRejection, parse_seam
from plm_contracts.seams.c7 import ObservationMaturity
from plm_contracts.seams.c8 import P10FeatureViewPayload

from services.analytics.evaluation.feedback import evaluate_experiment_outcome
from services.analytics.projection.p10_features import build_p10_feature_view
from tests.support.analytics_db import analytics_db_session
from tests.support.contract import SeamRegistry, assert_refused, with_value, without
from tests.support.seams import sample_lineage

REGISTRY_ENTRY = "C8 · PHASE-P9 → PHASE-P10"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
UNMEASURED: dict[str, Any] = {
    "views": None, "watch_time_hours": None, "subscribers_gained": None,
    "spotify_streams": None, "data_completeness": None, "fidelity": None,
    "contamination": None,
}


@pytest.fixture(scope="module")
def view() -> dict[str, Any]:
    with analytics_db_session() as session:
        outcome = evaluate_experiment_outcome(
            session, lineage=sample_lineage(), unit_id="pkg_c8_01",
            net_contribution_usd=120.5, views=9000, watch_time_minutes=600.0,
            window=ObservationWindow.D7, maturity=ObservationMaturity.MATURE,
            attribution_confidence=0.98, quality_status=None, now=NOW,
        )
    return json.loads(json.dumps(build_p10_feature_view(outcome, **UNMEASURED).to_wire()))


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)


def test_producer_emits_the_shared_wire_with_nulls_for_unmeasured(view: dict[str, Any]) -> None:
    assert tuple(view) == tuple(P10FeatureViewPayload.model_fields)
    assert view["arm_id"] == sample_lineage().content_arm_id
    assert view["primary_outcome"] == {
        "metric": "net_contribution", "value": 120.5, "status": "mature"
    }
    assert set(view["secondary_outcomes"].values()) == {None}
    assert view["quality"]["contamination"] is None
    assert view["quality"]["attribution_confidence"] == 0.98


@pytest.mark.parametrize(
    "field",
    [*P10FeatureViewPayload.model_fields, "secondary_outcomes.views", "quality.fidelity"],
)
def test_consumer_model_refuses_a_missing_fact(view: dict[str, Any], field: str) -> None:
    def parse(p: dict[str, Any]) -> object:
        return parse_seam(P10FeatureViewPayload, p)

    assert_refused(parse, without(view, field), SeamRejection, because=field)


@pytest.mark.parametrize(
    ("field", "value"),
    [("maturity", "MATURE"), ("observation_window", "14d"), ("schema_version", "2.0")],
)
def test_consumer_model_refuses_old_vocabulary_or_future_views(
    view: dict[str, Any], field: str, value: Any
) -> None:
    def parse(p: dict[str, Any]) -> object:
        return parse_seam(P10FeatureViewPayload, p)

    assert_refused(parse, with_value(view, field, value), SeamRejection, because=field)
