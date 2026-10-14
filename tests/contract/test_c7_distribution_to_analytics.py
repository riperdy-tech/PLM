# tests/contract/test_c7_distribution_to_analytics.py
"""Seam C7 — PHASE-P8 → PHASE-P9 → PHASE-P2 (the feedback loop). R9, R10, R11, ADR-0030.

Leg 1: P8's real release submission emits ``DistributionRecordPayload``; P9's real
``ingest_distribution_record`` links each ISRC to the release and lineage, and P9's real
statement ingestion attributes revenue through that link. A statement row without an ISRC
is counted as unattributed revenue; no ISRC is invented.

Leg 2: P9's real outcome evaluation emits ``RewardResultPayload``; P2's real
``store_result`` accepts it. Unmeasured metrics stay ``null``, and the shared window and
maturity vocabulary is the only one either side accepts.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from plm_contracts import ObservationWindow
from plm_contracts.seams.c7 import (
    DistributionRecordPayload,
    ObservationMaturity,
    RewardResultPayload,
)
from plm_platform.clock import FixedClock

from services.analytics.evaluation.feedback import (
    evaluate_experiment_outcome,
    generate_and_post_reward,
)
from services.analytics.ingestion.distributor import ingest_distributor_statement
from services.analytics.ingestion.releases import (
    ReleaseRecordRejected,
    ingest_distribution_record,
    lineage_for_isrc,
)
from services.autonomous_ar.ingestion.result_adapter import ResultValidationError, store_result
from services.autonomous_ar.storage import repositories as ar_repositories
from services.distribution.client.mock import MockDistributorClient
from services.distribution.engine import DistributionEngine
from services.distribution.submission import ReleaseMetadata, submit_release_from_seams
from tests.support.analytics_db import analytics_db_session
from tests.support.ar_db import ar_session, create_ar_engine
from tests.support.contract import SeamRegistry, assert_refused, with_value, without
from tests.support.distribution_db import distribution_db_session
from tests.support.seams import lineage_fields, media_package, release_manifest, sample_lineage

REGISTRY_ENTRY = "C7 · PHASE-P8 → PHASE-P9 → PHASE-P2"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def leg1() -> dict[str, Any]:
    with distribution_db_session() as session:
        result = submit_release_from_seams(
            session, DistributionEngine(MockDistributorClient(clock=FixedClock(NOW))),
            track_manifests=[release_manifest(candidate_id=c).to_wire() for c in ("c_a", "c_b")],
            media_package=media_package("c_a", "c_b").to_wire(),
            publication=None,
            metadata=ReleaseMetadata(
                artist_name="Loop Artist", release_date="2026-10-01", label_name="Neon",
                copyright_line="2026 Neon", publisher_line="2026 Neon Publishing",
                territories="WORLDWIDE",
            ),
            now=NOW,
        )
    assert result.payload is not None
    return json.loads(json.dumps(result.payload.to_wire()))


@pytest.fixture(scope="module")
def leg2() -> dict[str, Any]:
    with analytics_db_session() as session:
        outcome = evaluate_experiment_outcome(
            session, lineage=sample_lineage(), unit_id="vid_loop_01",
            net_contribution_usd=40.0, views=5000, watch_time_minutes=900.0,
            window=ObservationWindow.D7, maturity=ObservationMaturity.MATURE,
            attribution_confidence=0.95, quality_status=None, now=NOW,
        )
        payload = generate_and_post_reward(
            session, outcome, views=5000, watch_time_minutes=900.0,
            average_percentage_viewed=None, subscribers_gained=None, estimated_revenue=12.5,
            now=NOW,
        )
    return json.loads(json.dumps(payload.to_wire()))


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    contract_registry.require_entry(REGISTRY_ENTRY)


# --- leg 1 -----------------------------------------------------------------------------


def test_leg1_producer_emits_exactly_the_shared_wire(leg1: dict[str, Any]) -> None:
    assert tuple(leg1) == tuple(DistributionRecordPayload.model_fields)
    assert leg1["lineage"] == lineage_fields()
    assert len(leg1["isrcs"]) == 2


def test_leg1_p9_links_each_isrc_to_the_lineage_and_attributes_revenue(
    leg1: dict[str, Any],
) -> None:
    isrc_a, isrc_b = leg1["isrcs"]
    with analytics_db_session() as session:
        ingest_distribution_record(session, leg1, now=NOW)
        ingest_distribution_record(session, leg1, now=NOW)  # idempotent replay
        link = lineage_for_isrc(session, isrc_a)
        statement, rows = ingest_distributor_statement(
            session, provider="mock", period="2026-10", source_reference="stmt.csv",
            raw_content="statement-1",
            rows=[
                {"isrc": isrc_a, "streams": 100, "revenue_usd": 1.5},
                {"isrc": isrc_b, "streams": 50, "revenue_usd": 0.5},
                {"isrc": "", "streams": 10, "revenue_usd": 0.25},  # no recording named
            ],
            now=NOW,
        )
    assert link is not None
    assert link[0] == leg1["release_id"]
    assert link[1].model_dump() == leg1["lineage"]
    assert {r.isrc for r in rows} == {isrc_a, isrc_b}
    assert all(r.release_id == leg1["release_id"] for r in rows)
    assert statement.unattributed_row_count == 1
    assert statement.unattributed_revenue_usd == 0.25
    assert statement.total_revenue_usd == 2.25  # unattributed revenue is still counted


def test_leg1_p9_refuses_a_conflicting_lineage(leg1: dict[str, Any]) -> None:
    with analytics_db_session() as session:
        ingest_distribution_record(session, leg1, now=NOW)
        other = with_value(leg1, "lineage.content_arm_id", "arm_other")
        with pytest.raises(ReleaseRecordRejected) as exc:
            ingest_distribution_record(session, other, now=NOW)
    assert exc.value.error.code == "lineage_conflict"


@pytest.mark.parametrize(
    "field", [*DistributionRecordPayload.model_fields, "lineage.content_genome_id"]
)
def test_leg1_p9_refuses_a_missing_fact(leg1: dict[str, Any], field: str) -> None:
    def consume(payload: dict[str, Any]) -> object:
        with analytics_db_session() as session:
            return ingest_distribution_record(session, payload, now=NOW)

    exc = assert_refused(consume, without(leg1, field), ReleaseRecordRejected, because=field)
    assert isinstance(exc, ReleaseRecordRejected)
    assert exc.error.code == "missing_required_field"


@pytest.mark.parametrize(
    ("field", "value"),
    [("isrcs", []), ("isrcs", ["USPLM26"]), ("lineage.experiment_id", "exp_unknown")],
)
def test_leg1_p9_refuses_invalid_values(leg1: dict[str, Any], field: str, value: Any) -> None:
    def consume(payload: dict[str, Any]) -> object:
        with analytics_db_session() as session:
            return ingest_distribution_record(session, payload, now=NOW)

    assert_refused(consume, with_value(leg1, field, value), ReleaseRecordRejected, because=field)


# --- leg 2 -----------------------------------------------------------------------------


def _store(payload: dict[str, Any]) -> object:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        ar_repositories.upsert_arm_state(
            session, brand_id="neon_seoul", arm_id=payload.get("arm_id", "x"), now=NOW
        )
        return store_result(session, payload, brand_id="neon_seoul", now=NOW)


def test_leg2_producer_emits_exactly_the_shared_wire(leg2: dict[str, Any]) -> None:
    assert set(leg2) == set(RewardResultPayload.model_fields)
    assert leg2["arm_id"] == lineage_fields()["content_arm_id"]
    assert leg2["raw_metrics"]["subscriber_gain"] is None  # unmeasured: null, not 0
    assert leg2["status"] == "mature"


def test_leg2_real_reward_is_stored_by_p2(leg2: dict[str, Any]) -> None:
    stored = _store(leg2)
    assert getattr(stored, "stored", False) is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("observation_window", "1h"),  # P9's old private window
        ("observation_window", "14d"),
        ("status", "MATURE"),  # P9's old uppercase default
        ("normalized_reward", 1.5),
        ("raw_metrics", {}),  # registry keys are required (null when unmeasured)
        ("observed_at", "2026-10-01T12:00:00"),
        ("schema_version", "2.0"),
    ],
)
def test_leg2_p2_refuses_invalid_values(leg2: dict[str, Any], field: str, value: Any) -> None:
    assert_refused(_store, with_value(leg2, field, value), ResultValidationError, because=field)


@pytest.mark.parametrize(
    "field",
    [f for f, info in RewardResultPayload.model_fields.items() if info.is_required()],
)
def test_leg2_p2_refuses_a_missing_fact(leg2: dict[str, Any], field: str) -> None:
    assert_refused(_store, without(leg2, field), ResultValidationError, because=field)
