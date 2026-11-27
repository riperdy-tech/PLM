"""`GET /ar/subgenres`: which sub-styles P2 can pick now and which are cooling down.

Review 2026-11-26 (#37): the Studio's Start menu should mark cooling styles. P2 owns the
cooldown, so P2 serves the data; the Studio only shows it.
"""

from __future__ import annotations

from datetime import timedelta

from autonomous_ar.domain.arm import ArmDimensions, ContentArm
from autonomous_ar.domain.enums import ArmLifecycleStatus
from autonomous_ar.storage import repositories
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.unit.ar.conftest import NOW

COMPONENTS = {name: 60.0 for name in
              ("demand", "growth", "competitor_velocity", "competition", "audience_quality",
               "cross_platform", "production_fit")}


def _arm(arm_id: str, subgenre: str) -> ContentArm:
    return ContentArm(
        arm_id=arm_id, dimensions=ArmDimensions(genre="focus_electronic", subgenre=subgenre,
                                                use_cases=("study_focus",),
                                                vocal_mode="instrumental"),
        p1_score=70.0, p1_confidence=0.7, component_scores=dict(COMPONENTS),
        observation_count=3, source_count=1, as_of=NOW, score_version="p1-v1",
        freshness_seconds=0.0)


def test_cooling_and_selectable_sub_styles_are_served(
        ar_factory: sessionmaker[Session], client: TestClient) -> None:
    with ar_factory() as session:
        for arm in (_arm("mp-1", "minimal_pulse"), _arm("mp-2", "minimal_pulse"),
                    _arm("ch-1", "deep_work_chillstep"), _arm("og-1", "organic_electronic")):
            repositories.upsert_arm_state(session, brand_id="longform_signal",
                                          arm_id=arm.arm_id, now=NOW)
            repositories.insert_feature_snapshot(session, brand_id="longform_signal", arm=arm,
                                                 captured_at=NOW, feature_version="f")
        until = NOW + timedelta(hours=72)
        repositories.record_selection(session, arm_id="ch-1", now=NOW, cooldown_until=until)
        repositories.record_selection(session, arm_id="mp-1", now=NOW, cooldown_until=until)
        repositories.set_arm_status(session, arm_id="og-1", status=ArmLifecycleStatus.BLOCKED,
                                    now=NOW, reason="out_of_identity:v")
        session.commit()
    response = client.get("/ar/subgenres", params={
        "brand_id": "longform_signal", "as_of": (NOW + timedelta(hours=1)).isoformat()})
    assert response.status_code == 200, response.text
    rows = {row["subgenre"]: row for row in response.json()}
    assert set(rows) == {"minimal_pulse", "deep_work_chillstep"}, "a blocked style is absent"
    assert rows["minimal_pulse"]["selectable_arms"] == 1
    assert rows["minimal_pulse"]["cooling_arms"] == 1
    assert rows["minimal_pulse"]["cooling"] is False
    chill = rows["deep_work_chillstep"]
    assert (chill["selectable_arms"], chill["cooling_arms"], chill["cooling"]) == (0, 1, True)
    assert chill["cooling_until"].startswith(until.isoformat()[:19])
    later = client.get("/ar/subgenres", params={
        "brand_id": "longform_signal", "as_of": (NOW + timedelta(hours=80)).isoformat()})
    assert {row["subgenre"]: row["cooling"] for row in later.json()} == {
        "minimal_pulse": False, "deep_work_chillstep": False}
