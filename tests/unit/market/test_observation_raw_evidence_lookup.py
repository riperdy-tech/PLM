"""P1 evidence IDs are raw IDs; resolve them read-only without changing frozen C1."""
from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from services.market_intelligence.storage.models import observations
from tests.support.c1_api import open_c1_client

RAW_ID = "00000000-0000-0000-0000-000000000099"
FIRST_ID = "00000000-0000-0000-0000-000000000011"
SECOND_ID = "00000000-0000-0000-0000-000000000022"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000088"


def _seed_observations(session, *, ambiguous: bool = False) -> None:
    seed = [
        {
            "id": FIRST_ID,
            "entity_id": "00000000-0000-0000-0000-000000000051",
            "source_id": "00000000-0000-0000-0000-000000000061",
            "raw_observation_id": RAW_ID,
            "observed_at": datetime(2026, 10, 1, tzinfo=UTC),
            "views": 25,
            "normalization_version": "p1-test-normalization-v1",
        }
    ]
    if ambiguous:
        seed.append({
            "id": SECOND_ID,
            "entity_id": "00000000-0000-0000-0000-000000000052",
            "source_id": "00000000-0000-0000-0000-000000000061",
            "raw_observation_id": RAW_ID,
            "observed_at": datetime(2026, 10, 2, tzinfo=UTC),
            "views": 100,
            "normalization_version": "p1-test-normalization-v1",
        })
    session.execute(sa.insert(observations), seed)


def test_unique_raw_evidence_id_resolves_same_observation_as_normalized_id() -> None:
    with open_c1_client(seed=_seed_observations) as client:
        by_normalized = client.get(f"/market/v1/observations/{FIRST_ID}")
        by_raw = client.get(f"/market/v1/observations/{RAW_ID}")
    assert by_raw.status_code == by_normalized.status_code == 200
    assert by_raw.json() == by_normalized.json()
    result = by_raw.json()
    assert result["id"] == FIRST_ID
    assert result["provenance"]["raw_observation_id"] == RAW_ID
    assert result["metrics"]["views"] == 25


def test_ambiguous_raw_evidence_never_selects_an_arbitrary_normalization() -> None:
    with open_c1_client(seed=lambda s: _seed_observations(s, ambiguous=True)) as client:
        raw = client.get(f"/market/v1/observations/{RAW_ID}")
        normalized = client.get(f"/market/v1/observations/{FIRST_ID}")
    assert raw.status_code == 409
    assert raw.json()["detail"]["code"] == "observation_id_ambiguous"
    assert normalized.status_code == 200
    assert normalized.json()["id"] == FIRST_ID


def test_unknown_and_malformed_observation_ids_are_not_fabricated() -> None:
    with open_c1_client(seed=_seed_observations) as client:
        unknown = client.get(f"/market/v1/observations/{UNKNOWN_ID}")
        malformed = client.get("/market/v1/observations/does-not-exist")
    assert unknown.status_code == malformed.status_code == 404
    assert unknown.json()["detail"]["code"] == "not_found"
    assert malformed.json()["detail"]["code"] == "not_found"


def test_raw_evidence_lookup_still_requires_service_token() -> None:
    with open_c1_client(seed=_seed_observations) as client:
        response = client.get(
            f"/market/v1/observations/{RAW_ID}",
            headers={"X-PLM-Service-Token": "invalid"},
        )
    assert response.status_code in (401, 403)
