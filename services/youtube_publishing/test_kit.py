"""The Test & Compare kit for a package (growth spec 2026-11-03, ADR-0045).

YouTube picks a Test & Compare winner by watch time, and the test has no public API: it is
started by hand in desktop YouTube Studio. So P7 does not test anything itself. For every
package it lays out what to start: the three titles and three thumbnails P6 made, the
test to run (thumbnails first while a series' look is new, as the spec advises), when to
start it, and the one rule that keeps it valid (changing the title or thumbnail during a
test stops it; P7 has no metadata-update path, so it never does).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final

from plm_contracts.seams.c6 import MediaPackagePayload

__all__ = ["LAUNCH_WITHIN", "build_test_kit"]

LAUNCH_WITHIN: Final[timedelta] = timedelta(hours=24)
"""Start the test within a day of publishing, while the video is still being shown."""


def build_test_kit(pkg: MediaPackagePayload, *, published_at: datetime | None = None,
                   ) -> dict[str, Any]:
    """The kit for ``pkg``; ``launch_by`` only once it is published."""
    return {
        "media_package_id": pkg.media_package_id,
        "test": "thumbnail",
        "thumbnails": [{"id": t.id, "uri": t.uri} for t in pkg.thumbnails[:3]],
        "titles": [{"variant_id": m.variant_id, "title": m.title}
                   for m in pkg.metadata_candidates[:3]],
        "launch_by": None if published_at is None
        else (published_at + LAUNCH_WITHIN).isoformat(),
        "where": "YouTube Studio on a desktop: Content, the video, Test & Compare",
        "rule": "do not change the title or thumbnail while the test runs (it stops the "
                "test); run the title test once the thumbnail test has a winner",
    }
