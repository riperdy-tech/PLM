# tests/unit/market/test_api_endpoints.py
"""P1.10: the read-only `/market/v1` endpoints against the fixture-backed app."""


def test_list_supports_filters_and_cursors(c1_api_client) -> None:
    first = c1_api_client.get("/market/v1/opportunities", params={"limit": 1}).json()
    assert first["score_version"] == "p1-v1"
    assert first["next_cursor"] is None or isinstance(first["next_cursor"], str)
    filtered = c1_api_client.get("/market/v1/opportunities",
                                 params={"genre": "rnb", "use_case": "late_night_drive"}).json()
    assert filtered["items"] and filtered["items"][0]["arm_id"].startswith("rnb_")
    empty = c1_api_client.get("/market/v1/opportunities", params={"genre": "polka"}).json()
    assert empty["items"] == []


def test_detail_returns_the_arm_with_all_thirteen_dimensions(c1_api_client) -> None:
    body = c1_api_client.get(
        "/market/v1/opportunities/rnb_late_night_drive_english_vocal_45_75m"
    ).json()
    assert set(body) == {"arm", "score", "component_scores", "confidence", "evidence_summary",
                         "observation_counts", "source_counts", "last_updated"}
    assert body["arm"]["vocal_gender"] is None and body["arm"]["bpm_range"] is None
    assert body["arm"]["vocal_mode"] == "vocal"


def test_unknown_identifiers_are_404_with_the_platform_error_shape(c1_api_client) -> None:
    response = c1_api_client.get("/market/v1/runs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"

    missing = c1_api_client.get("/market/v1/observations/does-not-exist")
    assert missing.status_code == 404
