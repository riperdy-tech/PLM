"""The owner's song inbox: file names, the sheet's wording and what blocks an import.

Review 2026-11-26 #44-#46, #49, #52 and the P4 bullet: a song is named after its track
and creative variant (old prompt-hash names stay valid); the prompt text is kept as it
was first written; the sheet states the plan, the accepted length and every problem of
a saved file; WAVs are read with libsndfile (float WAVs, real frame counts), once per
file version; the same audio under another name, in any plan or already imported, waits.
"""

from __future__ import annotations

import hashlib
import io
import json
import wave
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf  # type: ignore[import-untyped]
from music_factory.domain.models import GenerationRequest
from music_factory.providers import suno_manual
from music_factory.providers.flow_music_manual import FlowMusicManualAdapter
from music_factory.providers.suno_manual import (
    STYLES_LIMIT,
    SunoManualAdapter,
    SunoManualSettings,
    song_problem,
    suno_styles,
)
from music_factory.storage.artifacts import probe_audio_bytes

TRACK = "trk_sheet_01"


def _request(code: str = "v1", *, prompt: str = "energy: low. groove: slow pulse",
             vocal_mode: str = "instrumental", low: int | None = 2,
             high: int | None = 30) -> GenerationRequest:
    return GenerationRequest(
        track_spec_id=TRACK, experiment_id="exp", prompt=prompt, vocal_mode=vocal_mode,
        duration_seconds=20, plan_id="gen_plan_x", variant_code=code,
        duration_min_seconds=low, duration_max_seconds=high)


def _adapter(inbox: Path) -> SunoManualAdapter:
    return SunoManualAdapter(SunoManualSettings(
        inbox=inbox, plan_tier="pro", account_ref="acct", model_version="v5"))


def _wav(seconds: float, *, tone: int = 1, rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes([tone % 256, 0]) * int(rate * seconds))
    return buffer.getvalue()


def _sheet(inbox: Path) -> str:
    return (inbox / f"{TRACK}.prompts.txt").read_text(encoding="utf-8")


def test_songs_are_named_by_track_and_variant(tmp_path: Path) -> None:
    missing = _adapter(tmp_path).awaiting_audio([_request("v1"), _request("v2")])
    assert missing == [f"{TRACK}-v1.wav is missing", f"{TRACK}-v2.wav is missing"]
    assert "Save as: trk_sheet_01-v1.wav" in _sheet(tmp_path)
    assert "Plan: gen_plan_x" in _sheet(tmp_path)


def test_a_prompt_change_keeps_the_name_and_the_text_first_written(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    adapter.awaiting_audio([_request(prompt="energy: low. groove: slow pulse")])
    (tmp_path / f"{TRACK}-v1.wav").write_bytes(_wav(3))
    adapter.awaiting_audio([_request("v1", prompt="energy: low. groove: other"),
                            _request("v2", prompt="energy: low. groove: other")])
    sheet = _sheet(tmp_path)
    assert "trk_sheet_01-v1.wav" not in sheet  # saved and usable: no longer asked for
    adapter.awaiting_audio([_request("v1", prompt="energy: high"),
                            _request("v2", prompt="energy: high")])
    record = json.loads((tmp_path / f"{TRACK}.songs.json").read_text(encoding="utf-8"))
    assert record["songs"]["v1"]["prompt"] == "energy: low. groove: slow pulse"
    assert record["songs"]["v2"]["prompt"] == "energy: low. groove: other"
    assert "groove: other" in _sheet(tmp_path) and "high energy" not in _sheet(tmp_path)
    response = adapter.generate(_request("v1", prompt="energy: high"))
    assert response.audio_data == (tmp_path / f"{TRACK}-v1.wav").read_bytes()


def test_a_song_saved_under_an_old_prompt_hash_name_stays_valid(tmp_path: Path) -> None:
    request = _request()
    digest = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()[:8]
    legacy = f"{TRACK}-{digest}"
    (tmp_path / f"{TRACK}.prompts.txt").write_text(
        f"old sheet\n--- Song 1 ---\nSave as: {legacy}.wav\n", encoding="utf-8")
    adapter = _adapter(tmp_path)
    assert adapter.awaiting_audio([request]) == [f"{legacy}.wav is missing"]
    (tmp_path / f"{legacy}.wav").write_bytes(_wav(3))
    assert adapter.awaiting_audio([request]) == []
    assert adapter.generate(request).audio_data == (tmp_path / f"{legacy}.wav").read_bytes()


def test_the_sheet_states_the_accepted_length_and_a_long_song_waits(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    adapter.awaiting_audio([_request(low=90, high=300)])
    assert "Accepted length: 1:30 to 5:00" in _sheet(tmp_path)
    (tmp_path / f"{TRACK}-v1.wav").write_bytes(_wav(301, rate=8000))
    missing = adapter.awaiting_audio([_request(low=90, high=300)])
    assert missing == [f"{TRACK}-v1.wav is 5:01 long; the accepted length is 1:30 to 5:00"]
    assert "Problem: is 5:01 long; the accepted length is 1:30 to 5:00" in _sheet(tmp_path)


def test_a_float_wav_is_usable_and_probed_with_its_real_length(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    sf.write(buffer, np.full((8000 * 3, 2), 0.1, dtype="float32"), 8000, format="WAV",
             subtype="FLOAT")
    data = buffer.getvalue()
    (tmp_path / f"{TRACK}-v1.wav").write_bytes(data)
    assert _adapter(tmp_path).awaiting_audio([_request()]) == []
    measured = probe_audio_bytes(data)
    assert measured.duration_seconds == 3.0 and measured.codec == "pcm_f32le"
    assert measured.channels == 2 and measured.sample_rate == 8000


def test_a_cut_short_wav_waits_with_its_real_length(tmp_path: Path) -> None:
    data = _wav(4)
    (tmp_path / f"{TRACK}-v1.wav").write_bytes(data[: 44 + 8000 * 2 * 3])  # 3 of 4 s
    missing = _adapter(tmp_path).awaiting_audio([_request()])
    assert missing == [(f"{TRACK}-v1.wav is cut short: its header says 0:04 but it holds "
                       "0:03 (download it again)")]
    assert "Problem: is cut short" in _sheet(tmp_path)


def test_the_same_audio_anywhere_in_the_inbox_or_already_imported_waits(
    tmp_path: Path,
) -> None:
    data = _wav(3, tone=7)
    (tmp_path / f"{TRACK}-v1.wav").write_bytes(data)
    (tmp_path / "trk_other-v1.wav").write_bytes(data)  # another plan's song
    adapter = _adapter(tmp_path)
    assert adapter.awaiting_audio([_request()]) == [
        f"{TRACK}-v1.wav is the same audio as trk_other-v1.wav"]
    (tmp_path / "trk_other-v1.wav").unlink()
    sha = hashlib.sha256(data).hexdigest()
    assert adapter.awaiting_audio([_request()], imported={sha: "cand_old"}) == [
        f"{TRACK}-v1.wav is the same audio as song cand_old, already imported"]
    assert adapter.awaiting_audio([_request()], imported={}) == []


def test_each_file_is_read_once_per_version(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[str] = []
    real = suno_manual._read_wav_facts

    def counting(path: Path) -> suno_manual.WavFacts:
        reads.append(path.name)
        return real(path)

    monkeypatch.setattr(suno_manual, "_read_wav_facts", counting)
    wav = tmp_path / f"{TRACK}-v1.wav"
    wav.write_bytes(_wav(3))
    adapter = _adapter(tmp_path)
    for _ in range(3):
        assert adapter.awaiting_audio([_request()]) == []
    assert reads == [wav.name]
    wav.write_bytes(_wav(4))
    assert adapter.awaiting_audio([_request()]) == []
    assert reads == [wav.name, wav.name]


def test_song_problem_is_the_check_the_studio_runs_on_save(tmp_path: Path) -> None:
    wav = tmp_path / "x.wav"
    wav.write_bytes(b"ID3 an mp3")
    assert song_problem(wav) == "is not a WAV file (download it as WAV)"
    wav.write_bytes(_wav(3))
    assert song_problem(wav) is None
    block = "--- Song 1 ---\nSave as: x.wav\nAccepted length: 0:05 to 0:30\n"
    assert song_problem(wav, block) == "is 0:03 long; the accepted length is 0:05 to 0:30"


def test_a_styles_text_over_suno_s_limit_is_cut_with_a_warning(tmp_path: Path) -> None:
    phrases = [f"groove: phrase number {n} with some extra words" for n in range(40)]
    request = _request(prompt=". ".join(phrases))
    assert len(suno_styles(request)) > STYLES_LIMIT
    _adapter(tmp_path).awaiting_audio([request])
    lines = _sheet(tmp_path).splitlines()
    head = next(i for i, line in enumerate(lines) if line.startswith("Styles ("))
    assert len(lines[head + 1]) <= STYLES_LIMIT
    assert lines[head + 1].endswith("extra words")
    assert any(line.startswith(f"Warning: Suno takes at most {STYLES_LIMIT} characters")
               for line in lines)


def test_the_variant_instructions_read_as_plain_style_phrases() -> None:
    prompt = (
        "energy: low. Within the declared groove (slow pulse), leave space around the "
        "declared melodic motion (rising line); contrast short phrases with rests and "
        "responsive accents, without replacing the supplied hook, instrumentation, "
        "structure, harmonic key or vocal mode. Develop the declared arrangement arc "
        "(slow build) with foreground/background contrast around soft_piano; vary phrase "
        "density and dynamics across sections, preserving the supplied groove, hook, "
        "structure, BPM, harmonic key and vocal mode. For independent interpretation 4, "
        "use a distinct phrase and rest placement within these constraints, without "
        "changing the supplied structure."
    )
    styles = suno_styles(GenerationRequest(track_spec_id="t", experiment_id="e",
                                           prompt=prompt, vocal_mode="instrumental"))
    assert styles == (
        "low energy, space between phrases, short phrases with rests and light accents, "
        "soft piano moving between foreground and background, phrase density and "
        "dynamics changing across sections, its own placement of phrases and rests")
    for word in ("declared", "supplied", "constraints", "interpretation"):
        assert word not in styles


def test_flow_music_sheet_names_and_checks_songs_the_same_way(tmp_path: Path) -> None:
    adapter = FlowMusicManualAdapter(SunoManualSettings(inbox=tmp_path))
    assert adapter.awaiting_audio([_request()]) == [f"{TRACK}-v1.wav is missing"]
    assert "Accepted length: 0:02 to 0:30" in _sheet(tmp_path)
