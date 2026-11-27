"""`GET /market/v1/opportunities` pages through every arm, in the ranked order.

Review 2026-11-26: the endpoint loaded the top `limit x 3` rows and then applied the
cursor, so paging stopped after about three pages (about 600 arms at P2's page size of 200).
The cursor also held only (score, arm), while the order puts under-covered arms after
every ranked one, so a cursor crossing that boundary skipped or repeated arms. The cursor
is now a keyset over the full order: (coverage group, score desc, arm asc).
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from services.market_intelligence.api.schemas import decode_cursor, encode_cursor
from services.market_intelligence.config import load_market_settings
from services.market_intelligence.domain.value_objects import TaxonomyProfile
from services.market_intelligence.storage import repositories
from services.market_intelligence.storage.models import scores
from services.market_intelligence.taxonomy.arms import ArmDraft
from tests.support.c1_api import ARM_ID, open_c1_client, seed_market

SETTINGS = load_market_settings()
RANKED = 47
THIN = 13


def _seed_many(session: Session) -> None:
    """The standard fixture plus 60 arms: 47 fully covered, 13 under the coverage floor,
    with repeated scores so ties fall on both sides of page boundaries."""
    seed_market(session)
    template = dict(session.execute(sa.select(scores)).mappings().one())
    for index in range(RANKED + THIN):
        arm_id = f"arm_{index:03d}"
        repositories.upsert_arm(
            session, ArmDraft(arm_id=arm_id, fingerprint=arm_id, profile=TaxonomyProfile(),
                              support_count=2), brand_id="longform_signal")
        thin = index >= RANKED
        session.execute(sa.insert(scores).values({
            **template, "id": repositories.deterministic_id("score", arm_id),
            "arm_id": arm_id, "market_opportunity_score": float(90 - (index % 7) * 10),
            "basis_weight": 0.1 if thin else 1.0,
        }))


def _walk(client: Any, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(100):
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        body = client.get("/market/v1/opportunities", params=params).json()
        items.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("paging did not end")


def test_paging_returns_every_arm_once_in_the_ranked_order() -> None:
    with open_c1_client(seed=_seed_many) as client:
        everything = client.get("/market/v1/opportunities", params={"limit": 200}).json()
        assert everything["next_cursor"] is None
        expected = [item["arm_id"] for item in everything["items"]]
        assert len(expected) == RANKED + THIN + 1, "nothing left out"
        for limit in (1, 4, 7, 20):
            walked = [item["arm_id"] for item in _walk(client, limit=limit)]
            assert walked == expected, limit
        # under-covered arms come after every ranked one, whatever their number
        thin = {f"arm_{index:03d}" for index in range(RANKED, RANKED + THIN)}
        assert set(expected[-THIN:]) == thin
        assert ARM_ID in expected[:-THIN]


def test_more_than_three_pages_are_served() -> None:
    """The old cut-off: limit x 3 rows were loaded, so page four never existed."""
    with open_c1_client(seed=_seed_many) as client:
        pages = 0
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 5}
            if cursor is not None:
                params["cursor"] = cursor
            body = client.get("/market/v1/opportunities", params=params).json()
            pages += 1
            cursor = body["next_cursor"]
            if cursor is None:
                break
        assert pages == 13  # 61 arms, 5 per page


def test_a_filtered_walk_is_complete_too() -> None:
    def seed(session: Session) -> None:
        _seed_many(session)
        for index in range(30):
            arm_id = f"rnb_{index:03d}"
            repositories.upsert_arm(
                session, ArmDraft(arm_id=arm_id, fingerprint=arm_id,
                                  profile=TaxonomyProfile(genre="rnb"), support_count=2),
                brand_id="longform_signal")
            template = dict(session.execute(
                sa.select(scores).where(scores.c.arm_id == ARM_ID)).mappings().one())
            session.execute(sa.insert(scores).values({
                **template, "id": repositories.deterministic_id("score", arm_id),
                "arm_id": arm_id, "market_opportunity_score": float(index)}))

    with open_c1_client(seed=seed) as client:
        walked = _walk_filtered(client, genre="rnb", limit=3)
        assert len(walked) == 31 and len(set(walked)) == 31


def _walk_filtered(client: Any, *, genre: str, limit: int) -> list[str]:
    ids: list[str] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": limit, "genre": genre}
        if cursor is not None:
            params["cursor"] = cursor
        body = client.get("/market/v1/opportunities", params=params).json()
        ids.extend(item["arm_id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return ids


def test_the_detail_finds_an_arm_beyond_the_first_two_hundred() -> None:
    def seed(session: Session) -> None:
        seed_market(session)
        template = dict(session.execute(sa.select(scores)).mappings().one())
        for index in range(210):
            arm_id = f"arm_{index:03d}"
            repositories.upsert_arm(
                session, ArmDraft(arm_id=arm_id, fingerprint=arm_id,
                                  profile=TaxonomyProfile(), support_count=2),
                brand_id="longform_signal")
            session.execute(sa.insert(scores).values({
                **template, "id": repositories.deterministic_id("score", arm_id),
                "arm_id": arm_id, "market_opportunity_score": 99.0}))

    with open_c1_client(seed=seed) as client:
        assert client.get(f"/market/v1/opportunities/{ARM_ID}").status_code == 200


def test_the_cursor_carries_the_coverage_group_and_reads_old_cursors() -> None:
    assert decode_cursor(encode_cursor(82.4, "a", group=1)) == (1, 82.4, "a")
    old = "eyJzIjo4Mi40LCJhIjoiYSJ9"  # {"s":82.4,"a":"a"}, written before the group
    assert decode_cursor(old) == (0, 82.4, "a")
    with open_c1_client() as client:
        refused = client.get("/market/v1/opportunities", params={"cursor": "not-a-cursor"})
        assert refused.status_code == 422
        assert refused.json()["detail"]["code"] == "invalid_cursor"
