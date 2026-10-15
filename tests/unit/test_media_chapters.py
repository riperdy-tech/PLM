"""YouTube chapter rules (R4).

Proves each rule refuses with its own reason code (first chapter at 0:00, at least
three chapters, each at least 10 s, ascending, titles non-empty and bounded), that a
valid set passes, and that a playlist whose chapters break a rule is blocked before
anything is rendered.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from media_factory.domain.models import Chapter
from media_factory.errors import MediaAssemblyError
from media_factory.metadata.chapters import chapter_violations, require_valid_chapters
from media_factory.rendering.assembler import assemble_media_package
from media_factory.visuals.planner import BrandProfile
from plm_contracts.seams.c3 import DurationRangeSeconds
from plm_platform.clock import FixedClock

from tests.support.media_backend import InMemoryArtifactStore, StandInToolchain, stored_manifest

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _chapters(*starts: float, titles: tuple[str, ...] | None = None) -> list[Chapter]:
    names = titles or tuple(f"Track {n + 1}" for n in range(len(starts)))
    return [Chapter(title=t, start_seconds=s, end_seconds=s + 1, song_id=f"s{n}")
            for n, (s, t) in enumerate(zip(starts, names, strict=True))]


def test_a_valid_chapter_set_passes() -> None:
    assert chapter_violations(_chapters(0.0, 10.0, 25.5), 40.0) == []
    require_valid_chapters(_chapters(0.0, 10.0, 25.5), 40.0)


@pytest.mark.parametrize(("chapters", "total", "code"), [
    (_chapters(0.0, 30.0), 60.0, "chapters_too_few"),
    (_chapters(1.0, 30.0, 60.0), 90.0, "chapters_first_not_at_zero"),
    (_chapters(0.0, 9.5, 30.0), 60.0, "chapter_too_short"),
    (_chapters(0.0, 30.0, 55.0), 60.0, "chapter_too_short"),  # the last one runs to the end
    (_chapters(0.0, 30.0, 20.0, 50.0), 80.0, "chapters_not_ascending"),
    (_chapters(0.0, 30.0, 60.0, titles=("A", " ", "C")), 90.0, "chapter_title_empty"),
    (_chapters(0.0, 30.0, 60.0, titles=("A", "B" * 101, "C")), 90.0, "chapter_title_too_long"),
])
def test_each_rule_violation_blocks_with_its_code(chapters: list[Chapter], total: float,
                                                  code: str) -> None:
    assert code in {v.code for v in chapter_violations(chapters, total)}
    with pytest.raises(MediaAssemblyError) as raised:
        require_valid_chapters(chapters, total)
    assert code in raised.value.error.message


def test_a_playlist_that_breaks_the_chapter_rules_is_blocked_before_rendering() -> None:
    store = InMemoryArtifactStore()
    toolchain = StandInToolchain()
    manifests = [stored_manifest(store, f"cand_{n}", measured_duration_seconds=90.0)
                 for n in range(2)]  # two tracks: two chapters
    with pytest.raises(MediaAssemblyError) as raised:
        assemble_media_package(
            track_manifests=manifests,
            brand_profile=BrandProfile(brand_id="brand_neon", brand_name="Neon"),
            duration_range=DurationRangeSeconds(min_seconds=172, max_seconds=184), visual_profile="v", playlist_concept="Focus",
            store=store, toolchain=toolchain, clock=FixedClock(NOW),
        )
    assert raised.value.code == "chapters_too_few"
    assert toolchain.calls == []
    assert not any(key.startswith("media/") for key in store.objects)
