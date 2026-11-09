"""P6 warns when a new album sounds like one of the brand's last ones (owner 2026-11-09).

Pure function over stored signature evidence; the threshold's basis is the brand's own
catalog (see ``media_factory.playlist.sound_variance``).
"""

from __future__ import annotations

from media_factory.playlist.catalog import CatalogAlbum
from media_factory.playlist.sound_variance import (
    DEFAULT_SOUND_VARIANCE_POLICY,
    check_sound_variance,
)


def _tracks(*signatures: list[float]) -> list[dict[str, object]]:
    return [{"candidate_id": f"c{n}", "artifact_sha256": "a" * 64, "signature": s}
            for n, s in enumerate(signatures)]


WARM = [0.8, 0.5, 0.2, 0.1]
WARM_ALIKE = [0.78, 0.52, 0.22, 0.1]
BRIGHT = [0.1, 0.2, 0.5, 0.8]


def _album(package: str, release: str, *signatures: list[float],
           title: str | None = None) -> CatalogAlbum:
    return CatalogAlbum(package, release, _tracks(*signatures),
                        variation=None if title is None else {"titles": [title]})


def test_an_album_that_sounds_like_a_recent_one_is_named_with_its_similarity() -> None:
    history = [_album("mpkg_bright", "rel_2", BRIGHT, BRIGHT),
               _album("mpkg_warm", "rel_1", WARM, WARM_ALIKE, title="Late Desk")]
    report = check_sound_variance(_tracks(WARM_ALIKE, WARM), current_release_manifest_id="rel_9",
                                  history=history)
    assert [c["media_package_id"] for c in report["compared"]] == ["mpkg_bright", "mpkg_warm"]
    (close,) = report["too_similar"]
    assert close["media_package_id"] == "mpkg_warm" and close["title"] == "Late Desk"
    assert close["similarity"] >= DEFAULT_SOUND_VARIANCE_POLICY.too_similar
    assert report["compared"][0]["similarity"] < DEFAULT_SOUND_VARIANCE_POLICY.too_similar


def test_a_re_render_of_the_same_release_and_old_albums_beyond_the_window_are_skipped(
        ) -> None:
    window = DEFAULT_SOUND_VARIANCE_POLICY.window
    history = ([_album("mpkg_same", "rel_9", WARM)]
               + [_album(f"mpkg_{n}", f"rel_{n}", BRIGHT) for n in range(window)]
               + [_album("mpkg_old", "rel_old", WARM)])
    report = check_sound_variance(_tracks(WARM), current_release_manifest_id="rel_9",
                                  history=history)
    assert len(report["compared"]) == window
    assert report["too_similar"] == []


def test_unmeasured_albums_are_counted_not_compared() -> None:
    history = [CatalogAlbum("mpkg_none", "rel_1", None),
               _album("mpkg_bad", "rel_2", [float("nan"), 1.0, 0.0, 0.0])]
    report = check_sound_variance(_tracks(WARM), current_release_manifest_id="rel_9",
                                  history=history)
    assert report["unmeasured_packages"] == 2 and report["compared"] == []
    empty = check_sound_variance(None, current_release_manifest_id="rel_9", history=history)
    assert empty["unmeasured_current"] is True
