"""Session compilations: long videos re-assembled from released tracks (ADR-0044 revision).

A compilation reuses recordings of several experiments on purpose, so it takes its own
lineage (no P2 arm) and, instead of the cross-album near-copy screen, may only contain
tracks an earlier album of the brand released. P9 sends no reward for it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from media_factory.api.app import create_app
from media_factory.audio.features import DEFAULT_FEATURE_POLICY, measure_features
from media_factory.config import MediaFactorySettings
from media_factory.errors import MediaAssemblyError
from media_factory.playlist.assembler import PlaylistAssemblyError, check_clearance
from media_factory.playlist.catalog import (
    CatalogAlbum,
    check_compilation_sources,
    serialize_album_signatures,
)
from media_factory.playlist.diversity import DEFAULT_ALBUM_TEXTURE_POLICY
from plm_contracts.seams.lineage import SESSION_COMPILATION_ARM_ID
from plm_platform.clock import FixedClock
from plm_platform.config import SERVICE_TOKEN_HEADER, load_settings

from tests.support.media_audio import synth_track
from tests.support.media_backend import (
    InMemoryArtifactStore,
    StandInToolchain,
    backend_for,
    stored_manifest,
)
from tests.support.media_db import create_media_engine, media_session
from tests.support.seams import lineage_fields
from tests.unit.test_media_api import MINIMAL_ENV

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
TOKEN = "test-token"
HEADERS = {SERVICE_TOKEN_HEADER: TOKEN}


def _lineage(name: str) -> dict[str, str]:
    return lineage_fields(experiment_id=f"exp_{name}", experiment_family_id=f"fam_{name}",
                          content_arm_id=f"arm_{name}", content_genome_id=f"gen_{name}",
                          production_directive_id=f"dir_{name}")


def _album(store: InMemoryArtifactStore, name: str, specs: list[dict[str, Any]]) -> list[Any]:
    return [stored_manifest(store, f"song_{name}_{i}", measured_duration_seconds=60.0,
                            synth=spec, lineage=_lineage(name))
            for i, spec in enumerate(specs)]


def _request(*candidate_ids: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "candidate_ids": list(candidate_ids), "brand_id": "brand_neon",
        "duration_range_seconds": {"min_seconds": 170, "max_seconds": 180},
        "visual_profile": "architect_studio_night", "playlist_concept": "Deep Work",
        "render_profile": "preview_360p_v1",
    }
    body.update(overrides)
    return body


def test_mixed_lineages_are_refused_unless_it_is_a_compilation() -> None:
    store = InMemoryArtifactStore()
    mixed = [*_album(store, "a", [{"bpm": 90.0}]), *_album(store, "b", [{"bpm": 95.0}])]
    with pytest.raises(PlaylistAssemblyError, match="mixed_lineage|different experiment"):
        check_clearance(mixed)
    lineage = check_clearance(mixed, session_compilation=True)
    assert lineage.content_arm_id == SESSION_COMPILATION_ARM_ID
    assert lineage.experiment_id.startswith("cmp_")
    assert check_clearance(list(reversed(mixed)), session_compilation=True) == lineage


def _record(evidence: object) -> CatalogAlbum:
    return CatalogAlbum("pkg_old", "rel_old", evidence,
                        policy_version=DEFAULT_ALBUM_TEXTURE_POLICY.version,
                        extractor_version=DEFAULT_FEATURE_POLICY.version)


def test_a_compilation_may_only_reuse_released_tracks() -> None:
    measured = {"a": measure_features("a", synth_track(seconds=4, bpm=90.0)),
                "b": measure_features("b", synth_track(seconds=4, bpm=100.0, tonic=5))}
    sha = {"a": "a" * 64, "b": "b" * 64}
    history = [_record(serialize_album_signatures(["a"], measured, sha)["track_signatures"])]
    report = check_compilation_sources(["a"], measured, sha, history=history)
    assert report["mode"] == "session_compilation"
    with pytest.raises(MediaAssemblyError) as exc:
        check_compilation_sources(["a", "b"], measured, sha, history=history)
    assert exc.value.code == "compilation_track_not_released"
    with pytest.raises(MediaAssemblyError, match="compilation_track_not_released|not"):
        check_compilation_sources(["a"], measured, {"a": "c" * 64}, history=history)


def test_a_session_compilation_reassembles_two_released_albums_over_http() -> None:
    store = InMemoryArtifactStore()
    first = _album(store, "first", [{"bpm": 90.0, "tonic": 0}, {"bpm": 92.0, "tonic": 7},
                                    {"bpm": 88.0, "tonic": 9, "minor": True}])
    second = _album(store, "second", [{"bpm": 100.0, "tonic": 2}, {"bpm": 98.0, "tonic": 4},
                                      {"bpm": 102.0, "tonic": 11, "minor": True}])
    toolchain = StandInToolchain()
    platform = load_settings(env={**MINIMAL_ENV, "PLM_SERVICE_TOKEN": TOKEN})
    engine = create_media_engine(static_pool=True)
    client = TestClient(create_app(
        session_factory=lambda: media_session(engine),
        settings=MediaFactorySettings(platform=platform), clock=FixedClock(NOW),
        backend=backend_for(store, [*first, *second], toolchain=toolchain)))

    # Each upload differs in concept and visual world (P6's upload variation check).
    for album, concept, world in ((first, "Night Desk", "architect_studio_night"),
                                  (second, "Dawn Drafting", "drafting_table_dawn")):
        made = client.post("/media/packages/assemble", headers=HEADERS,
                           json=_request(*(m.candidate_id for m in album),
                                         playlist_concept=concept, visual_profile=world))
        assert made.status_code == 200, made.text

    every = [m.candidate_id for m in (*first, *second)]
    unflagged = client.post("/media/packages/assemble", headers=HEADERS,
                            json=_request(*every, duration_range_seconds={
                                "min_seconds": 340, "max_seconds": 360}))
    assert unflagged.status_code == 422

    compiled = client.post("/media/packages/assemble", headers=HEADERS,
                           json=_request(*every, session_compilation=True,
                                         visual_profile="blueprint_hall",
                                         duration_range_seconds={
                                             "min_seconds": 340, "max_seconds": 360}))
    assert compiled.status_code == 200, compiled.text
    package = compiled.json()
    assert package["content_arm_id"] == SESSION_COMPILATION_ARM_ID
    assert package["experiment_id"].startswith("cmp_")
    assert len(package["tracklist"]) == 6
    assert package["render_report"]["album_texture"]["catalog"]["mode"] == "session_compilation"


def test_p9_sends_no_reward_for_a_session_compilation() -> None:
    from datetime import timedelta

    from analytics.domain.enums import ObservationWindow
    from analytics.evaluation.delivery import reward_delivery_status
    from analytics.evaluation.feedback import (
        evaluate_experiment_outcome,
        record_and_enqueue_reward,
    )
    from plm_contracts.money import FxTable, Money
    from plm_contracts.seams.lineage import session_compilation_lineage

    from tests.support.analytics_db import analytics_db_session

    with analytics_db_session() as session:
        outcome = evaluate_experiment_outcome(
            session, lineage=session_compilation_lineage(["song_a", "song_b"]),
            unit_id="vid_session_01", net_contribution=Money.of("10", "USD"),
            reporting_currency="USD", fx=FxTable(), views=500, watch_time_minutes=900.0,
            window=ObservationWindow.D7, published_at=NOW - timedelta(days=8),
            attribution_confidence=1.0, quality_status=None, observed_at=NOW)
        payload = record_and_enqueue_reward(
            session, outcome, views=500, watch_time_minutes=900.0,
            average_percentage_viewed=None, subscribers_gained=None,
            estimated_revenue=None, now=NOW, enqueued_at=NOW)
        assert payload.arm_id == SESSION_COMPILATION_ARM_ID
        assert reward_delivery_status(session, payload) is None
