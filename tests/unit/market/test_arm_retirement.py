# tests/unit/market/test_arm_retirement.py
"""An older taxonomy's arms stop being served once the current taxonomy recomputes."""

from sqlalchemy.orm import Session

from services.market_intelligence.domain.enums import ArmStatus
from services.market_intelligence.storage import repositories
from tests.support.c1_api import ARM_ID, seed_market


def test_arms_outside_the_current_namespace_are_retired_and_not_served(
        market_session: Session) -> None:
    seed_market(market_session)
    assert ARM_ID in {row["arm_id"] for row in repositories.latest_scores(market_session)}

    retired = repositories.retire_arms_outside_namespace(market_session,
                                                         prefix="p1_taxonomy_v3_")
    assert retired == 1
    assert repositories.retire_arms_outside_namespace(
        market_session, prefix="p1_taxonomy_v3_") == 0, "idempotent"
    assert [row["arm_id"] for row in repositories.load_arms(
        market_session, status=ArmStatus.RETIRED)] == [ARM_ID]
    assert ARM_ID not in {row["arm_id"] for row in repositories.latest_scores(market_session)}


def test_arms_inside_the_namespace_are_kept(market_session: Session) -> None:
    seed_market(market_session)
    assert repositories.retire_arms_outside_namespace(
        market_session, prefix=ARM_ID[:4]) == 0
    assert ARM_ID in {row["arm_id"] for row in repositories.latest_scores(market_session)}
