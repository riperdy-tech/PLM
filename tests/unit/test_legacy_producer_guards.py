"""Archived producer must never spend Suno credits or upload without the P5/P7 guards."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import produce_full_playlist


def test_legacy_producer_aborts_before_suno_construction() -> None:
    with patch.object(produce_full_playlist, "SunoClient") as provider:
        with pytest.raises(RuntimeError, match="disabled before paid generation"):
            produce_full_playlist.main()
        provider.assert_not_called()


def test_legacy_direct_upload_is_blocked_for_import_callers() -> None:
    with pytest.raises(RuntimeError, match="Direct album upload blocked"):
        produce_full_playlist.publish_album_to_youtube(Path("unneeded.mp4"), [], 0.0)
