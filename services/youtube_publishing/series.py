"""Which YouTube playlist each channel series goes to (growth spec rank 4, ADR-0045).

P6 tags every package with its series (``MetadataCandidate.series``). The playlists
themselves are the channel's: an operator creates them once (in YouTube Studio) and
lists them in the JSON file named by :data:`SERIES_PLAYLISTS_VAR`::

    {"night_desk": {"playlist_id": "PL...", "title": "Night Desk Sessions"}, ...}

P7 then adds each upload to its series' playlist (the guarded ``youtube.playlist``
write) and links the playlist in the description. Without the file, or for a series it
does not list, nothing is added and nothing is guessed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = ["SERIES_PLAYLISTS_VAR", "SeriesPlaylist", "load_series_playlists"]

SERIES_PLAYLISTS_VAR: Final[str] = "PLM_YOUTUBE_SERIES_PLAYLISTS_FILE"


@dataclass(frozen=True, slots=True)
class SeriesPlaylist:
    playlist_id: str
    title: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/playlist?list={self.playlist_id}"


def load_series_playlists(env: Mapping[str, str] | None = None) -> dict[str, SeriesPlaylist]:
    """The configured playlists by series id; empty when no file is configured.

    A configured file that is missing or malformed raises: a typo must not silently
    publish videos outside their series."""
    path = (os.environ if env is None else env).get(SERIES_PLAYLISTS_VAR)
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError(f"{SERIES_PLAYLISTS_VAR}: expected an object of series")
    out: dict[str, SeriesPlaylist] = {}
    for series, entry in raw.items():
        playlist_id = entry.get("playlist_id") if isinstance(entry, dict) else None
        title = entry.get("title") if isinstance(entry, dict) else None
        if not isinstance(playlist_id, str) or not playlist_id.strip() or not isinstance(
                title, str) or not title.strip():
            raise ValueError(f"{SERIES_PLAYLISTS_VAR}: series {series!r} needs a playlist_id "
                             "and a title")
        out[str(series)] = SeriesPlaylist(playlist_id.strip(), title.strip())
    return out
