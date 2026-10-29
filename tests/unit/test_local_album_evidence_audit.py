"""Read-only local album reconciliation tests: no external music or rights claims."""
from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from scripts import produce_full_playlist
from scripts.audit_local_album_evidence import audit_local_albums, main

IDS = (
    "11111111-1111-4111-8111-111111111111",
    "22222222-2222-4222-8222-222222222222",
    "33333333-3333-4333-8333-333333333333",
)


def _wav(album: Path, index: int, clip: str, *, alternate: bool = False) -> Path:
    folder = album / "tracks"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"track_{index:02d}_{clip[:8]}.wav"
    with wave.open(str(target), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(1000)
        writer.writeframes((b"\x01\x00" if alternate else b"\x00\x00") * 2000)
    return target


def _summary(album: Path, clips: list[str], *, duration: float = 2.0) -> None:
    rows = [
        {
            "index": i,
            "clip_id": clip,
            "title": "Repeated Title",
            "start_seconds": (i - 1) * 2.0,
            "duration_seconds": duration,
        }
        for i, clip in enumerate(clips, start=1)
    ]
    (album / "publication_summary.json").write_text(
        json.dumps({"track_count": len(rows), "tracks": rows,
                    "duration_seconds": len(rows) * 2.0}),
        encoding="utf-8",
    )


def test_manifest_inventory_and_cross_album_byte_reuse(tmp_path: Path) -> None:
    first, second = tmp_path / "album-a", tmp_path / "album-b"
    first.mkdir()
    second.mkdir()
    _summary(first, list(IDS[:2]))
    _summary(second, [IDS[2]])
    _wav(first, 1, IDS[0])
    _wav(first, 2, IDS[1], alternate=True)
    _wav(second, 1, IDS[2], alternate=True)

    report = audit_local_albums([first, second])
    a, b = report["albums"]
    assert a["manifest_status"] == "present"
    assert a["matched_wav_count"] == 2
    assert a["missing_source_track_indices"] == []
    assert a["duration_mismatch_track_indices"] == []
    assert a["reused_title_group_count"] == 1
    assert a["track_rows_with_reused_title_count"] == 2
    assert a["unlisted_wav_count"] == 0
    assert a["final_duration_mismatch"] is False
    assert b["matched_wav_count"] == 1
    assert report["exact_duplicate_wav_group_count"] == 1
    assert report["cross_album_exact_duplicate_wav_group_count"] == 1
    assert report["cross_album_exact_duplicate_wav_file_count"] == 2
    assert report["rights_verified"] is False
    assert report["provenance_independently_verified"] is False
    assert report["human_listening_verified"] is False
    raw = json.dumps(report)
    assert str(tmp_path) not in raw
    assert "Repeated Title" not in raw
    assert IDS[0] not in raw


def test_manifest_missing_files_duration_variance_and_unlisted_wav(tmp_path: Path) -> None:
    album = tmp_path / "album"
    album.mkdir()
    _summary(album, list(IDS[:2]), duration=8.0)
    _wav(album, 1, IDS[0])
    _wav(album, 7, IDS[2])
    report = audit_local_albums([album])["albums"][0]
    assert report["matched_wav_count"] == 1
    assert report["missing_source_track_indices"] == [2]
    assert report["duration_mismatch_track_indices"] == [1]
    assert report["unlisted_wav_count"] == 1
    assert report["final_duration_mismatch"] is True


def test_displaced_clip_prefix_filenames_are_not_called_absent_audio(
    tmp_path: Path,
) -> None:
    album = tmp_path / "displaced"
    album.mkdir()
    _summary(album, list(IDS[:2]))
    _wav(album, 1, IDS[0])
    _wav(album, 7, IDS[1])
    result = audit_local_albums([album])["albums"][0]
    assert result["matched_wav_count"] == 1
    assert result["missing_source_track_indices"] == [2]
    assert result["clip_prefix_absent_track_indices"] == []
    assert result["ambiguous_clip_prefix_track_indices"] == []
    assert result["displaced_clip_prefix_matches"] == [
        {"manifest_index": 2, "local_file_index": 7}
    ]
    assert result["displaced_clip_prefix_duration_mismatch_indices"] == []
    assert result["unlisted_wav_count"] == 1


def test_displaced_clip_prefix_duration_and_ambiguity_remain_visible(
    tmp_path: Path,
) -> None:
    album = tmp_path / "displaced"
    album.mkdir()
    _summary(album, list(IDS[:2]))
    _wav(album, 8, IDS[1])
    summary = json.loads((album / "publication_summary.json").read_text(encoding="utf-8"))
    summary["tracks"][1]["duration_seconds"] = 8.0
    (album / "publication_summary.json").write_text(
        json.dumps(summary), encoding="utf-8",
    )
    result = audit_local_albums([album])["albums"][0]
    assert result["clip_prefix_absent_track_indices"] == [1]
    assert result["displaced_clip_prefix_matches"] == [
        {"manifest_index": 2, "local_file_index": 8}
    ]
    assert result["displaced_clip_prefix_duration_mismatch_indices"] == [2]

    _wav(album, 9, IDS[1], alternate=True)
    ambiguous = audit_local_albums([album])["albums"][0]
    assert ambiguous["displaced_clip_prefix_matches"] == []
    assert ambiguous["ambiguous_clip_prefix_track_indices"] == [2]


def test_same_clip_prefix_in_multiple_manifest_rows_is_ambiguous(
    tmp_path: Path,
) -> None:
    album = tmp_path / "duplicated"
    album.mkdir()
    _summary(album, [IDS[0], IDS[0]])
    _wav(album, 7, IDS[0])
    result = audit_local_albums([album])["albums"][0]
    assert result["displaced_clip_prefix_matches"] == []
    assert result["ambiguous_clip_prefix_track_indices"] == [1, 2]


def test_legacy_harvest_refuses_displaced_cached_clip_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = tmp_path / "legacy"
    _wav(folder, 4, IDS[0])
    monkeypatch.setattr(produce_full_playlist, "TRACKS_DIR", folder / "tracks")
    monkeypatch.setattr(produce_full_playlist, "KNOWN_CLIPS", [IDS[0]])

    class NoProvider:
        def __getattr__(self, name: str) -> object:
            raise AssertionError("provider_not_authorized")

    with pytest.raises(
        RuntimeError, match="legacy_harvest_displaced_cached_clip_refused",
    ):
        produce_full_playlist.generate_and_download_tracks(
            NoProvider(), target_track_count=1,
        )
    assert not (folder / "tracks" / f"track_01_{IDS[0][:8]}.wav").exists()


def test_legacy_harvest_keeps_exact_position_cached_clip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder = tmp_path / "legacy"
    file = _wav(folder, 1, IDS[0])
    monkeypatch.setattr(produce_full_playlist, "TRACKS_DIR", folder / "tracks")
    monkeypatch.setattr(produce_full_playlist, "KNOWN_CLIPS", [IDS[0]])
    monkeypatch.setattr(produce_full_playlist, "get_audio_duration", lambda _: 2.0)
    rows = produce_full_playlist.generate_and_download_tracks(
        object(), target_track_count=1,
    )
    assert len(rows) == 1
    assert rows[0].index == 1
    assert rows[0].wav_path == file
    assert rows[0].clip_id == IDS[0]


def test_missing_or_invalid_manifest_is_unknown_not_clean(tmp_path: Path) -> None:
    album = tmp_path / "unmanifested"
    album.mkdir()
    _wav(album, 1, IDS[0])
    missing = audit_local_albums([album])["albums"][0]
    assert missing["manifest_status"] == "missing"
    assert missing["matched_wav_count"] == 0
    assert missing["unlisted_wav_count"] == 1
    (album / "publication_summary.json").write_text("{broken-secret", encoding="utf-8")
    invalid = audit_local_albums([album])["albums"][0]
    assert invalid["manifest_status"] == "invalid"
    assert "broken-secret" not in json.dumps(invalid)


def test_local_audit_cli_emits_only_aggregates(tmp_path: Path, capsys) -> None:
    album = tmp_path / "not-private-path"
    album.mkdir()
    _summary(album, [IDS[0]])
    _wav(album, 1, IDS[0])
    assert main(["--album-dir", str(album)]) == 0
    result = capsys.readouterr()
    assert json.loads(result.out)["albums"][0]["matched_wav_count"] == 1
    assert str(album) not in result.out


@pytest.mark.parametrize("count", [0, 9])
def test_album_count_bound(count: int, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="provide_1_to_8_local_albums"):
        audit_local_albums([tmp_path] * count)
