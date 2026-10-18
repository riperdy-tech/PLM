"""P7's operator channel route: ``PUT /youtube/channels/{channel_id}`` (R5).

In-process TestClient over in-memory SQLite. Proves: the route needs the service token;
the first ``PUT`` creates the profile (201) and the channel becomes publishable; a replay
of the same facts changes nothing (200, same ``updated_at``); a changed fact replaces the
profile (200) and keeps its id and creation instant; the audience declaration
(``default_made_for_kids``) and the credential reference are required, never defaulted;
an unknown field or operating mode is refused.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings
from sqlalchemy.orm import Session, sessionmaker
from youtube_publishing.api.app import create_app
from youtube_publishing.client.mock import MockYouTubeClient
from youtube_publishing.domain.enums import OperatingMode, PrivacyStatus
from youtube_publishing.storage.repositories import get_channel_profile_by_channel_id

from tests.support.clearance import p5_holding
from tests.support.seams import media_package, release_manifest
from tests.support.youtube_db import create_youtube_engine
from tests.unit.test_youtube_api import MINIMAL_ENV

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
HEADERS = {SERVICE_TOKEN_HEADER: "test-secret-token"}
FACTS: dict[str, Any] = {"channel_name": "PLM Night", "brand_id": "brand_neon",
                         "oauth_credential_ref": "sec_oauth_night",
                         "default_made_for_kids": False}


def _app(clock: FixedClock) -> tuple[TestClient, sessionmaker[Session]]:
    engine = create_youtube_engine(static_pool=True)
    factory = sessionmaker(bind=engine)
    app = create_app(session_factory=factory, settings=load_settings(env=MINIMAL_ENV),
                     client=MockYouTubeClient(), clock=clock,
                     clearance=p5_holding(release_manifest(candidate_id="cand_ch")),
                     analytics_from_env=False)
    return TestClient(app), factory


def test_the_route_needs_the_service_token() -> None:
    client, factory = _app(FixedClock(T0))
    answer = client.put("/youtube/channels/UC_night", json=FACTS)
    assert answer.status_code in (401, 403)
    with factory() as session:
        assert get_channel_profile_by_channel_id(session, "UC_night") is None


def test_put_creates_then_a_replay_changes_nothing_and_the_channel_publishes() -> None:
    client, _ = _app(FixedClock(T0))
    publish = {"channel_id": "UC_night", "operating_mode": "private_only",
               "media_package": media_package("cand_ch", media_package_id="pkg_ch").to_wire()}
    refused = client.post("/youtube/publish", headers=HEADERS, json=publish)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "channel_not_configured"

    created = client.put("/youtube/channels/UC_night", headers=HEADERS, json=FACTS)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["created"] is True and body["profile_id"] == "prof_UC_night"
    assert (body["default_privacy_mode"], body["operating_mode"]) == ("private", "dry_run")
    assert body["default_contains_synthetic_media"] is True

    replay = client.put("/youtube/channels/UC_night", headers=HEADERS, json=FACTS)
    assert replay.status_code == 200
    assert replay.json() == {**body, "created": False}

    published = client.post("/youtube/publish", headers=HEADERS, json=publish)
    assert published.status_code == 200, published.text
    assert published.json()["publication_event"]["channel_id"] == "UC_night"


def test_a_changed_fact_replaces_the_profile_and_keeps_its_identity() -> None:
    clock = FixedClock(T0)
    client, factory = _app(clock)
    first = client.put("/youtube/channels/UC_night", headers=HEADERS, json=FACTS).json()
    clock.set(T1)  # a later operator change
    changed = client.put("/youtube/channels/UC_night", headers=HEADERS,
                          json={**FACTS, "operating_mode": "private_only",
                                "default_privacy_mode": "unlisted"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["created"] is False
    with factory() as session:
        stored = get_channel_profile_by_channel_id(session, "UC_night")
    assert stored is not None
    assert stored.id == first["profile_id"]
    assert (stored.created_at, stored.updated_at) == (T0, T1)
    assert stored.operating_mode is OperatingMode.PRIVATE_ONLY
    assert stored.default_privacy_mode is PrivacyStatus.UNLISTED


def test_missing_declarations_and_unknown_fields_are_refused() -> None:
    client, factory = _app(FixedClock(T0))
    for missing in ("default_made_for_kids", "oauth_credential_ref", "brand_id"):
        body = {k: v for k, v in FACTS.items() if k != missing}
        answer = client.put("/youtube/channels/UC_night", headers=HEADERS, json=body)
        assert answer.status_code == 422, missing
    assert client.put("/youtube/channels/UC_night", headers=HEADERS,
                      json={**FACTS, "oauth_secret": "x"}).status_code == 422
    assert client.put("/youtube/channels/UC_night", headers=HEADERS,
                      json={**FACTS, "operating_mode": "yolo"}).status_code == 422
    with factory() as session:
        assert get_channel_profile_by_channel_id(session, "UC_night") is None
