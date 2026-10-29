"""Checks for explicit local P6 listening and review CSV preparation."""
from __future__ import annotations

import hashlib
import http.client
import json
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import pytest
from media_factory.audio.texture_label_evaluation import EVALUATION, POLICY_VERSION
from media_factory.audio.texture_listening_review import (
    export_review_csv,
    render_listening_page,
    verified_review_audio,
)
from media_factory.texture_sample_audit import SampleSource

from scripts.evaluate_p6_texture_labels import main as evaluate_main
from scripts.serve_p6_texture_listening import (
    bounded_wav_range,
    make_handler,
    read_queue,
    smoke_listening_session,
)
from tests.support.media_audio import synth_track


@pytest.fixture
def sample() -> tuple[tuple[SampleSource, SampleSource], dict[str, bytes]]:
    a = synth_track(seconds=3, tonic=1)
    b = synth_track(seconds=3, tonic=9)
    return (
        (
            SampleSource("pkg_a", "release_a", "song_a", "sample://a",
                         hashlib.sha256(a).hexdigest()),
            SampleSource("pkg_b", "release_b", "song_b", "sample://b",
                         hashlib.sha256(b).hexdigest()),
        ),
        {"sample://a": a, "sample://b": b},
    )


def test_page_hides_spectral_scores_and_source_identifiers(
    sample: tuple[tuple[SampleSource, SampleSource], dict[str, bytes]],
) -> None:
    pair, _objects = sample
    page = render_listening_page((pair,), token="example-session").decode()
    assert "Pair 1" in page
    assert "Recording A" in page and "Recording B" in page
    assert 'name="r0" required' not in page
    assert pair[0].sha256 not in page and pair[1].sha256 not in page
    assert "release_a" not in page and "0.998" not in page


def test_csv_exports_only_explicit_answers(
    sample: tuple[tuple[SampleSource, SampleSource], dict[str, bytes]],
) -> None:
    pair, _objects = sample
    fields = {
        "reviewer_id": ["listener_1"], "r0": ["different_music"],
        "e0": ["evidence-123"], "r1": [""], "e1": [""],
    }
    encoded = export_review_csv((pair, pair), fields).decode()
    assert "listener_1,different_music,evidence-123" in encoded
    assert encoded.count("listener_1") == 1
    assert encoded.startswith("left_sha256,right_sha256")
    assert "\r\n" in encoded


@pytest.mark.parametrize("fields", [
    {"reviewer_id": ["reviewer"], "r0": [""], "e0": [""]},
    {"reviewer_id": ["reviewer"], "r0": ["same_recording"], "e0": [""]},
    {"reviewer_id": ["reviewer"], "r0": ["different_music"], "e0": ["formula,unsafe"]},
    {"reviewer_id": ["reviewer"], "r0": ["unknown"], "e0": ["ref"]},
    {"reviewer_id": ["reviewer", "other"], "r0": ["uncertain"], "e0": [""]},
])
def test_invalid_or_unreviewed_forms_cannot_produce_decisive_labels(
    sample: tuple[tuple[SampleSource, SampleSource], dict[str, bytes]],
    fields: dict[str, list[str]],
) -> None:
    pair, _objects = sample
    with pytest.raises(ValueError):
        export_review_csv((pair,), fields)


def test_original_audio_fetched_only_for_requested_source(
    sample: tuple[tuple[SampleSource, SampleSource], dict[str, bytes]],
) -> None:
    pair, objects = sample
    calls: list[str] = []

    class ReadOnlyStore:
        def fetch_verified(self, uri: str, digest: str) -> bytes:
            calls.append(uri)
            data = objects[uri]
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("checksum mismatch")
            return data

        def put(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("never write")

    store = ReadOnlyStore()
    assert verified_review_audio((pair,), store, index=0, side="a") == objects["sample://a"]
    assert calls == ["sample://a"]
    with pytest.raises(ValueError):
        verified_review_audio((pair,), store, index=1, side="b")
    assert calls == ["sample://a"]


def test_bounded_queue_file_reader_rejects_duplicate_keys(tmp_path: Path) -> None:
    file = tmp_path / "queue.json"
    file.write_text('{"same":1,"same":2}', encoding="utf-8")
    with pytest.raises(ValueError):
        read_queue(file)
    file.write_text(json.dumps({"ok": 2}), encoding="utf-8")
    expected = hashlib.sha256(file.read_bytes()).hexdigest()
    assert read_queue(file, expected_sha256=expected) == {"ok": 2}
    with pytest.raises(ValueError, match="hash_mismatch"):
        read_queue(file, expected_sha256="0" * 64)


def test_form_parse_can_preserve_empty_unreviewed_cases() -> None:
    assert parse_qs("reviewer_id=r1&r0=&e0=", keep_blank_values=True) == {
        "reviewer_id": ["r1"], "r0": [""], "e0": [""],
    }


def test_two_separate_reviewer_exports_feed_existing_evaluator(
    sample: tuple[tuple[SampleSource, SampleSource], dict[str, bytes]],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pair, _objects = sample
    report = tmp_path / "scores.json"
    report.write_text(json.dumps({
        "policy_version": POLICY_VERSION, "evaluation": EVALUATION,
        "pairs": [{
            "left_sha256": pair[0].sha256, "right_sha256": pair[1].sha256,
            "fixed_score": 0.21, "best_offset_score": 0.25,
            "best_rate_offset_score": 0.27,
        }],
    }), encoding="utf-8")
    files: list[Path] = []
    for i in range(2):
        review = tmp_path / f"review-{i}.csv"
        review.write_bytes(export_review_csv((pair,), {
            "reviewer_id": [f"reviewer_{i}"], "r0": ["different_music"],
            "e0": [f"independent-ref-{i}"],
        }))
        files.append(review)
    args = [
        "--report", str(report), "--reviews", str(files[0]),
        "--reviews", str(files[1]),
    ]
    assert evaluate_main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["adjudicated_different_music_pairs"] == 1
    assert result["distinct_reviewer_pseudonyms_count"] == 2
    assert result["sample_confusion"]["best_rate_offset_score"]["tn"] == 1
    assert result["operational_threshold_ready"] is False


def test_bounded_wav_range_supports_browser_seeking() -> None:
    assert bounded_wav_range("bytes=0-15", 100) == (0, 15)
    assert bounded_wav_range("bytes=80-", 100) == (80, 99)
    assert bounded_wav_range("bytes=-4", 100) == (96, 99)
    assert bounded_wav_range("bytes=0-300", 100) == (0, 99)
    for value in ("bytes=101-", "bytes=2-1", "bytes=0-1,5-8", "garbage"):
        with pytest.raises(ValueError):
            bounded_wav_range(value, 100)


def test_smoke_only_checks_verified_sources_then_stops_loopback(
    sample: tuple[tuple[SampleSource, SampleSource], dict[str, bytes]],
) -> None:
    pair, objects = sample
    reads: list[str] = []

    class Store:
        def fetch_verified(self, uri: str, digest: str) -> bytes:
            reads.append(uri)
            data = objects[uri]
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("invalid_checksum")
            return data

    handler = make_handler((pair,), Store(), session_token="private")
    with HTTPServer(("127.0.0.1", 0), handler) as server:
        result = smoke_listening_session(server, token="private", pair_count=1)
        assert result["status"] == "local_audio_smoke_passed"
        assert result["sha256_verified_audio_http_responses"] == 2
        assert result["reviews_submitted"] == 0
        assert reads == ["sample://a", "sample://b"]
        assert not result["server_persisted"]


def test_loopback_routes_stream_audio_and_export_only_explicit_csv(
    sample: tuple[tuple[SampleSource, SampleSource], dict[str, bytes]],
) -> None:
    pair, objects = sample
    requests: list[str] = []

    class Store:
        def fetch_verified(self, uri: str, sha256: str) -> bytes:
            requests.append(uri)
            data = objects[uri]
            assert hashlib.sha256(data).hexdigest() == sha256
            return data

        def put(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("audio writes forbidden")

    handler = make_handler((pair,), Store(), session_token="private")
    with HTTPServer(("127.0.0.1", 0), handler) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

        def call(method: str, path: str, body: bytes | None = None,
                 host: str | None = None) -> tuple[int, bytes, dict[str, str]]:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            headers = {"Host": host or f"127.0.0.1:{server.server_port}"}
            if body is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            try:
                conn.request(method, path, body=body, headers=headers)
                response = conn.getresponse()
                return response.status, response.read(), dict(response.getheaders())
            finally:
                conn.close()

        try:
            status, data, headers = call("GET", "/s/private/")
            assert status == 200 and b"Pair 1" in data
            assert headers["Referrer-Policy"] == "no-referrer"
            assert headers["Cache-Control"].startswith("no-store")
            assert requests == []
            status, data, headers = call("GET", "/s/private/audio/0/a")
            assert status == 200 and data == objects["sample://a"]
            assert headers["Content-Type"] == "audio/wav"
            assert requests == ["sample://a"]
            connection = http.client.HTTPConnection(
                "127.0.0.1", server.server_port, timeout=5
            )
            try:
                connection.request(
                    "GET", "/s/private/audio/0/a",
                    headers={"Host": f"127.0.0.1:{server.server_port}",
                             "Range": "bytes=0-15"},
                )
                response = connection.getresponse()
                assert response.status == 206
                assert response.getheader("Content-Range") == (
                    f"bytes 0-15/{len(objects['sample://a'])}"
                )
                assert response.read() == objects["sample://a"][:16]
            finally:
                connection.close()
            assert call("GET", "/s/wrong/audio/0/a")[0] == 403
            assert call("GET", "/s/private/audio/0/a", host="other.host")[0] == 403
            assert call("GET", "/s/private/audio/99/a")[0] == 404
            assert requests == ["sample://a", "sample://a"]
            review = urlencode({"reviewer_id": "r1", "r0": "uncertain",
                                "e0": ""}).encode()
            status, data, headers = call("POST", "/s/private/export", review)
            assert status == 200 and b"r1,uncertain," in data
            assert headers["Content-Disposition"].startswith("attachment;")
            empty = urlencode({"reviewer_id": "r1", "r0": "",
                               "e0": ""}).encode()
            assert call("POST", "/s/private/export", empty)[0] == 400
        finally:
            server.shutdown()
            worker.join(timeout=5)

