"""Local master audio sampling: tests do not use remote providers or rights claims."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from scripts.probe_local_album_master import main, probe_album_master

CLIPS = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
)
RATE = 8000


def _album(root: Path, *, second_is_wrong: bool = False) -> None:
    root.mkdir()
    tracks = root / "tracks"
    tracks.mkdir()
    noise = np.random.default_rng(42)
    originals = [noise.uniform(-0.15, 0.15, 20 * RATE) for _ in CLIPS]
    for idx, (clip, waveform) in enumerate(zip(CLIPS, originals), start=1):
        sf.write(
            tracks / f"track_{idx:02d}_{clip[:8]}.wav",
            waveform, RATE, subtype="PCM_16",
        )
    master_parts = [
        np.asarray(sf.read(tracks / f"track_{i:02d}_{clip[:8]}.wav")[0])
        for i, clip in enumerate(CLIPS, start=1)
    ]
    if second_is_wrong:
        master_parts[1] = noise.uniform(-0.15, 0.15, 20 * RATE)
    sf.write(root / "album_master_continuous.wav",
             np.concatenate(master_parts) * 0.7, RATE, subtype="PCM_16")
    rows = [
        {"index": i, "clip_id": clip, "start_seconds": (i - 1) * 20.0,
         "duration_seconds": 20.0}
        for i, clip in enumerate(CLIPS, start=1)
    ]
    (root / "publication_summary.json").write_text(
        json.dumps({"tracks": rows, "track_count": 2}), encoding="utf-8",
    )


def test_real_pcm_window_alignment_and_changed_source(tmp_path: Path) -> None:
    album = tmp_path / "album"
    _album(album, second_is_wrong=True)
    result = probe_album_master(album)
    assert result["evaluation"] == (
        "read_only_local_master_pcm_correlation_not_rights_or_publication"
    )
    assert result["measured_track_count"] == 2
    first, second = result["measurements"]
    assert first["status"] == "measured"
    assert all(w["correlation"] > 0.99 for w in first["windows"])
    assert all(abs(w["time_delta_seconds"]) < 0.01 for w in first["windows"])
    assert second["status"] == "measured"
    assert all(w["correlation"] < 0.1 for w in second["windows"])
    assert result["rights_verified"] is False
    assert result["remote_publication_verified"] is False
    assert result["independent_provenance_verified"] is False
    output = json.dumps(result)
    assert str(tmp_path) not in output
    assert CLIPS[0] not in output


def test_master_probe_uses_displaced_filename_prefix(tmp_path: Path) -> None:
    album = tmp_path / "album"
    _album(album)
    track = album / "tracks" / f"track_02_{CLIPS[1][:8]}.wav"
    track.rename(album / "tracks" / f"track_07_{CLIPS[1][:8]}.wav")
    result = probe_album_master(album)
    assert result["measured_track_count"] == 2
    assert result["measurements"][1]["local_filename_index"] == 7
    assert result["measurements"][1]["windows"][0]["correlation"] > 0.99


def test_ambiguous_prefix_and_missing_master_fail_closed(tmp_path: Path) -> None:
    album = tmp_path / "album"
    _album(album)
    source = album / "tracks" / f"track_01_{CLIPS[0][:8]}.wav"
    duplicate = album / "tracks" / f"track_07_{CLIPS[0][:8]}.wav"
    duplicate.write_bytes(source.read_bytes())
    report = probe_album_master(album)
    assert report["measurements"][0]["status"] == "source_prefix_ambiguous"
    assert report["measurements"][0]["windows"] == []
    (album / "album_master_continuous.wav").unlink()
    with pytest.raises(ValueError, match="album_master_unavailable"):
        probe_album_master(album)


def test_cli_output_contains_only_aggregate_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    album = tmp_path / "private-folder"
    _album(album)
    assert main(["--album-dir", str(album)]) == 0
    emitted = capsys.readouterr().out
    assert json.loads(emitted)["measured_track_count"] == 2
    assert "private-folder" not in emitted
    assert CLIPS[0] not in emitted
