"""Within-song repetition proxies (P5 plan §24): a looped song scores higher than a varied one."""
from __future__ import annotations

import io
import json

import numpy as np
import pytest
import soundfile as sf
from media_factory.audio.repetition_probe import POLICY_VERSION, probe_song_repetition

from scripts import probe_song_repetition as cli
from tests.support.media_audio import synth_track


def _join(*parts: bytes) -> bytes:
    blocks = [sf.read(io.BytesIO(part), always_2d=True)[0] for part in parts]
    buffer = io.BytesIO()
    sf.write(buffer, np.concatenate(blocks), 44100, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def test_a_looped_song_scores_higher_than_a_varied_one() -> None:
    loop = synth_track(seconds=4, bpm=120, tonic=0)
    looped = _join(loop, loop, loop, loop)
    varied = _join(*(synth_track(seconds=4, bpm=bpm, tonic=tonic)
                     for bpm, tonic in ((120, 0), (84, 5), (100, 9), (140, 2))))
    same = probe_song_repetition(looped, window_seconds=4)
    different = probe_song_repetition(varied, window_seconds=4)
    assert same["policy_version"] == POLICY_VERSION
    assert same["windows"] == 4 and len(same["pairs"]) == 6  # type: ignore[arg-type]
    assert same["evaluation"] == "diagnostic_only_no_threshold_no_clearance"
    for metric in ("rhythm_onset_proxy", "pitch_class_motion_proxy"):
        looped_median = same["summary"][metric]["median"]  # type: ignore[index]
        assert looped_median > 0.99
        assert different["summary"][metric]["median"] < looped_median  # type: ignore[index]


def test_too_few_or_too_many_windows_are_refused() -> None:
    song = synth_track(seconds=5, bpm=120, tonic=0)
    with pytest.raises(ValueError, match="windows"):
        probe_song_repetition(song, window_seconds=4)
    with pytest.raises(ValueError, match="at least 2"):
        probe_song_repetition(song, window_seconds=1)


def test_cli_prints_a_report(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "song.wav"
    path.write_bytes(synth_track(seconds=8.5, bpm=120, tonic=0))
    assert cli.main(["--song", str(path), "--window-seconds", "4"]) == 0
    assert json.loads(capsys.readouterr().out)["windows"] == 2
    assert cli.main(["--song", str(tmp_path / "missing.wav")]) == 2
