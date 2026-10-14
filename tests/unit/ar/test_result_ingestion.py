"""§72/§73: the result wire, its refusals, and idempotent storage (P2.11)."""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from services.autonomous_ar.ingestion.result_adapter import (
    ResultValidationError,
    parse_result_payload,
    store_result,
)
from services.autonomous_ar.storage import repositories
from services.autonomous_ar.storage.models import ar_reward_observation
from tests.support.ar_db import ar_session, create_ar_engine

NOW = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "schema_version": "1.0", "experiment_id": "exp-1", "arm_id": "rnb-1", "content_id": "content-1",
        "observation_window": "7d", "status": "mature", "observed_at": NOW.isoformat(),
        "raw_metrics": {"views": 42000.0, "watch_time_minutes": 185000.0,
                        "average_percentage_viewed": 41.2, "subscriber_gain": 613.0,
                        "estimated_revenue": None},
        "normalized_reward": 0.74, "reward_version": "p9-reward-v1",
        "attribution_confidence": 0.99,
    }
    base.update(overrides)
    return base


def test_payload_parses_with_null_metrics_staying_unknown() -> None:
    observation = parse_result_payload(_payload())
    assert observation.normalized_reward.unwrap() == 0.74
    assert observation.raw_metrics["views"].unwrap() == 42000.0
    assert observation.raw_metrics["estimated_revenue"].state.value == "unknown", (
        "a null metric on the wire is unknown, never 0.0"
    )
    assert observation.key == "exp-1|7d"


def test_unmeasured_reward_is_refused_with_the_field_named() -> None:
    with pytest.raises(ResultValidationError) as error:
        parse_result_payload(_payload(normalized_reward=None))
    assert "normalized_reward" in error.value.field_paths, (
        "R11/R2: P9 owns normalized_reward; P2 refuses a result that carries none"
    )


def test_reward_out_of_range_and_unknown_economic_terms_are_refused() -> None:
    with pytest.raises(ResultValidationError):
        parse_result_payload(_payload(normalized_reward=1.4))
    with pytest.raises(ResultValidationError) as error:
        parse_result_payload(_payload(economic_terms={"made_up_revenue": 3.0}))
    assert "economic_terms" in error.value.field_paths[0]


def test_storing_twice_is_idempotent_and_an_unknown_arm_is_refused() -> None:
    engine = create_ar_engine()
    with ar_session(engine) as session:
        repositories.upsert_arm_state(session, brand_id="neon_seoul", arm_id="rnb-1", now=NOW)
        first = store_result(session, _payload(), brand_id="neon_seoul", now=NOW)
        second = store_result(session, _payload(), brand_id="neon_seoul", now=NOW)
        assert first.stored is True and second.stored is False
        assert first.observation_id == second.observation_id
        count = session.execute(sa.select(sa.func.count())
                                .select_from(ar_reward_observation)).scalar_one()
        assert count == 1, "§73: the same (experiment, window) is stored once"
        with pytest.raises(ResultValidationError, match="unknown arm"):
            store_result(session, _payload(arm_id="nope"), brand_id="neon_seoul", now=NOW)
