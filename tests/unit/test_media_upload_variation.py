"""P6 refuses a package whose packaging repeats the brand's recent uploads (growth spec
2026-11-03, rank 1: YouTube does not monetize generic or repetitive content)."""

from __future__ import annotations

from typing import Any

import pytest
from media_factory.errors import MediaAssemblyError
from media_factory.playlist.catalog import CatalogAlbum
from media_factory.playlist.variation import (
    check_upload_variation,
    variation_fingerprint,
)

SUFFIX = " — 25 min of Instrumental Electronic Music for Coding & Focus"


def _album(n: int, concept: str, world: str, *, release: str | None = None) -> CatalogAlbum:
    return CatalogAlbum(f"pkg_{n}", release or f"rel_{n}", None, variation=variation_fingerprint(
        concept=concept, visual_world=world, titles=[concept + SUFFIX]))


def _current(concept: str = "Blueprint Hours", world: str = "blueprint_hall",
             titles: list[str] | None = None) -> dict[str, Any]:
    return variation_fingerprint(concept=concept, visual_world=world,
                                 titles=titles or [concept + SUFFIX])


HISTORY = [_album(1, "Night Desk Session", "observatory_workspace"),
           _album(2, "Server Room Calm", "server_hall_glow"),
           _album(3, "Dawn Drafting", "drafting_table_dawn"),
           _album(4, "Monolith Focus", "minimal_desk_monolith")]


def test_a_package_unlike_the_recent_ones_passes_and_reports_its_scope() -> None:
    report = check_upload_variation(
        _current(), current_release_manifest_id="rel_new",
        history=[*HISTORY, CatalogAlbum("pkg_old", "rel_old", None)])
    assert report == {"policy_version": "upload-variation-v1", "compared_packages": 4,
                      "unfingerprinted_packages": 1}


@pytest.mark.parametrize(("current", "why"), [
    (_current(concept="night desk  session!"), "concept"),
    (_current(world="server_hall_glow"), "visual world"),
    (_current(titles=["Night Desk Hours" + SUFFIX]), "template fill"),
], ids=["concept", "visual_world", "title"])
def test_a_repeated_concept_world_or_template_title_is_refused(
        current: dict[str, Any], why: str) -> None:
    with pytest.raises(MediaAssemblyError, match=why) as refused:
        check_upload_variation(current, current_release_manifest_id="rel_new",
                               history=HISTORY)
    assert refused.value.code == "upload_variation_insufficient"


def test_windows_bound_what_is_compared() -> None:
    """A world comes back after three other uploads; a concept after ten; a re-render
    of the same release is not an earlier upload."""
    world = _current(world="minimal_desk_monolith")  # fourth newest
    check_upload_variation(world, current_release_manifest_id="rel_new", history=HISTORY)
    eleven = [*(_album(n, f"Concept {chr(65 + n)}", f"world_{n}") for n in range(10)),
              _album(99, "Blueprint Hours", "world_99")]
    check_upload_variation(_current(), current_release_manifest_id="rel_new", history=eleven)
    same = [_album(1, "Blueprint Hours", "blueprint_hall", release="rel_new")]
    check_upload_variation(_current(), current_release_manifest_id="rel_new", history=same)


def test_p6_stores_the_fingerprint_and_refuses_a_repeated_concept_over_http() -> None:
    from fastapi.testclient import TestClient
    from media_factory.api.app import create_app
    from media_factory.config import MediaFactorySettings
    from plm_platform.clock import FixedClock
    from plm_platform.config import load_settings

    from tests.support.media_backend import InMemoryArtifactStore, StandInToolchain, backend_for
    from tests.support.media_db import create_media_engine, media_session
    from tests.unit.test_media_session_compilation import (
        HEADERS,
        MINIMAL_ENV,
        NOW,
        TOKEN,
        _album,
        _request,
    )

    store = InMemoryArtifactStore()
    first = _album(store, "first", [{"bpm": 90.0, "tonic": 0}, {"bpm": 92.0, "tonic": 7},
                                    {"bpm": 88.0, "tonic": 9, "minor": True}])
    second = _album(store, "second", [{"bpm": 100.0, "tonic": 2}, {"bpm": 98.0, "tonic": 4},
                                      {"bpm": 102.0, "tonic": 11, "minor": True}])
    engine = create_media_engine(static_pool=True)
    client = TestClient(create_app(
        session_factory=lambda: media_session(engine),
        settings=MediaFactorySettings(platform=load_settings(
            env={**MINIMAL_ENV, "PLM_SERVICE_TOKEN": TOKEN})),
        clock=FixedClock(NOW),
        backend=backend_for(store, [*first, *second], toolchain=StandInToolchain())))

    made = client.post("/media/packages/assemble", headers=HEADERS, json=_request(
        *(m.candidate_id for m in first), playlist_concept="Night Desk"))
    assert made.status_code == 200, made.text
    stored = made.json()["render_report"]["variation"]
    assert stored["visual_world"] == "architect_studio_night"
    assert stored["concept"] == stored["hooks"][0]  # the scene hook, not P3's raw pattern
    assert stored["compared_packages"] == 0 and stored["coverage"] == "complete"

    # The same P3 concept again: P6 still gives the next upload hooks of its own, so
    # nothing repeats; a world one of the last three used is refused.
    reused = client.post("/media/packages/assemble", headers=HEADERS, json=_request(
        *(m.candidate_id for m in second), playlist_concept="Night Desk"))
    assert reused.status_code == 422
    assert reused.json()["detail"]["code"] == "upload_variation_insufficient"
    again = client.post("/media/packages/assemble", headers=HEADERS, json=_request(
        *(m.candidate_id for m in second), playlist_concept="Night Desk",
        visual_profile="drafting_table_dawn"))
    assert again.status_code == 200, again.text
    later = again.json()["render_report"]["variation"]
    assert later["compared_packages"] == 1
    assert not set(later["hooks"]) & set(stored["hooks"])


def test_the_test_and_compare_kit_lists_three_titles_and_thumbnails() -> None:
    from datetime import UTC, datetime

    from plm_contracts.seams.c6 import MediaPackagePayload
    from youtube_publishing.test_kit import build_test_kit

    from tests.support.seams import media_package_fields

    fields = media_package_fields()
    fields["metadata_candidates"] = [
        {**fields["metadata_candidates"][0], "variant_id": f"meta_{n}", "title": f"Title {n}"}
        for n in "abc"]
    fields["thumbnails"] = [{**fields["thumbnails"][0], "id": f"thumb_{n}"} for n in "abc"]
    pkg = MediaPackagePayload.build(**fields)
    kit = build_test_kit(pkg, published_at=datetime(2026, 11, 3, 12, tzinfo=UTC))
    assert [t["title"] for t in kit["titles"]] == ["Title a", "Title b", "Title c"]
    assert [t["id"] for t in kit["thumbnails"]] == ["thumb_a", "thumb_b", "thumb_c"]
    assert kit["test"] == "thumbnail"
    assert kit["launch_by"] == "2026-11-04T12:00:00+00:00"
    assert build_test_kit(pkg)["launch_by"] is None
