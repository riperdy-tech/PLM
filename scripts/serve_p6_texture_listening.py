"""Explicit opt-in, loopback-only, in-memory listening interface for P6 sources.

Do not expose this server through a tunnel, proxy, or public interface. Audio
is fetched and SHA256-verified on demand; this tool never writes audio/reviews.
The reviewer explicitly downloads their own CSV, then submits it separately.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import secrets
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import sqlalchemy as sa
from media_factory.artifacts import MinioArtifactStore
from media_factory.audio.texture_listening_review import (
    Pair,
    export_review_csv,
    render_listening_page,
    verified_review_audio,
)
from media_factory.audio.texture_review_integrity import bind_review_queue_sources
from media_factory.texture_sample_audit import load_sample_sources
from plm_platform.config import load_settings
from plm_platform.db import build_engine
from sqlalchemy.orm import Session

MAX_REPORT_BYTES = 300_000
MAX_FORM_BYTES = 24_000


def _unique_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, val in items:
        if key in out:
            raise ValueError("duplicate_queue_json_field")
        out[key] = val
    return out


def _refuse_constant(_value: str) -> None:
    raise ValueError("nonfinite_queue_json_number")


def read_queue(path: Path, *, expected_sha256: str | None = None) -> object:
    with path.open("rb") as handle:
        contents = handle.read(MAX_REPORT_BYTES + 1)
    if len(contents) > MAX_REPORT_BYTES:
        raise ValueError("queue_json_exceeds_limit")
    if expected_sha256 is not None and (
        not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        or hashlib.sha256(contents).hexdigest() != expected_sha256
    ):
        raise ValueError("queue_file_hash_mismatch")
    return json.loads(
        contents.decode("utf-8-sig"),
        object_pairs_hook=_unique_keys,
        parse_constant=_refuse_constant,
    )


def bounded_wav_range(value: str, size: int) -> tuple[int, int]:
    """Accept one bounded RFC 7233 byte range; reject multipart/invalid ranges."""
    match = re.fullmatch(r"bytes=(\d{1,12})-(\d{0,12})", value)
    suffix = re.fullmatch(r"bytes=-(\d{1,12})", value)
    if size <= 0:
        raise ValueError("invalid_wav_size")
    if suffix:
        length = int(suffix.group(1))
        if length <= 0:
            raise ValueError("invalid_suffix_range")
        return max(size - length, 0), size - 1
    if not match:
        raise ValueError("unsupported_wav_range")
    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) else size - 1
    if start >= size or end < start:
        raise ValueError("unsatisfiable_wav_range")
    return start, min(size - 1, end)


def make_handler(
    pairs: tuple[Pair, ...], store: MinioArtifactStore, *,
    session_token: str,
) -> type[BaseHTTPRequestHandler]:
    """Create a server-only handler; token grants access strictly on loopback."""
    if not session_token or not pairs or len(pairs) > 12:
        raise ValueError("invalid_loopback_listening_session")
    prefix = f"/s/{session_token}/"

    class Handler(BaseHTTPRequestHandler):
        server_version = "P6LocalListening"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            # Do not persist URL token or original source identities in logs.
            return

        def _safe_headers(self, *, content_type: str, length: int) -> None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; media-src 'self'; style-src 'unsafe-inline'; "
                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
            )

        def _send(
            self, status: HTTPStatus, body: bytes,
            *, content_type: str = "text/plain; charset=utf-8",
            download: bool = False,
        ) -> None:
            self.send_response(status)
            self._safe_headers(content_type=content_type, length=len(body))
            if download:
                self.send_header(
                    "Content-Disposition",
                    'attachment; filename="p6-listening-review.csv"',
                )
            self.end_headers()
            self.wfile.write(body)

        def _authorized_path(self) -> str | None:
            # Reject hostile Host headers (including DNS rebinding) and paths.
            if (
                self.client_address[0] != "127.0.0.1"
                or self.headers.get("Host") != (
                    f"127.0.0.1:{self.server.server_port}"
                )
            ):
                return None
            candidate = urlsplit(self.path)
            if candidate.query or candidate.fragment or not candidate.path.startswith(prefix):
                return None
            return candidate.path[len(prefix):]

        def do_GET(self) -> None:
            route = self._authorized_path()
            if route is None:
                self._send(HTTPStatus.FORBIDDEN, b"Forbidden")
                return
            if route == "":
                page = render_listening_page(pairs, token=session_token)
                self._send(HTTPStatus.OK, page, content_type="text/html; charset=utf-8")
                return
            parts = route.split("/")
            if len(parts) == 3 and parts[0] == "audio":
                try:
                    if not parts[1].isascii() or not parts[1].isdecimal():
                        raise ValueError("bad_selector")
                    if not 1 <= len(parts[1]) <= 2:
                        raise ValueError("invalid_selector_length")
                    data = verified_review_audio(
                        pairs, store, index=int(parts[1]), side=parts[2],
                    )
                except Exception:  # noqa: BLE001 - redact object-store failures
                    self._send(HTTPStatus.NOT_FOUND, b"Audio unavailable")
                    return
                range_value = self.headers.get("Range")
                if range_value is None:
                    self._send(HTTPStatus.OK, data, content_type="audio/wav")
                    return
                try:
                    start, end = bounded_wav_range(range_value, len(data))
                except ValueError:
                    self._send(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE, b"Bad range")
                    return
                chunk = data[start:end + 1]
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self._safe_headers(content_type="audio/wav", length=len(chunk))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.end_headers()
                self.wfile.write(chunk)
                return
            self._send(HTTPStatus.NOT_FOUND, b"Not found")

        def do_POST(self) -> None:
            if self._authorized_path() != "export":
                self._send(HTTPStatus.FORBIDDEN, b"Forbidden")
                return
            try:
                content_type = self.headers.get("Content-Type", "")
                if content_type.split(";", 1)[0].strip().lower() != (
                    "application/x-www-form-urlencoded"
                ):
                    raise ValueError("unsupported_form_encoding")
                content_length = int(self.headers.get("Content-Length", ""))
                if not 1 <= content_length <= MAX_FORM_BYTES:
                    raise ValueError("form_exceeds_size_limit")
                self.connection.settimeout(10.0)
                data = self.rfile.read(content_length).decode("utf-8")
                fields = parse_qs(
                    data, keep_blank_values=True, strict_parsing=True,
                    max_num_fields=2 * len(pairs) + 1,
                )
                result = export_review_csv(pairs, fields)
            except (ValueError, UnicodeError, OSError):
                self._send(HTTPStatus.BAD_REQUEST, b"Invalid or empty human review")
                return
            self._send(
                HTTPStatus.OK, result, content_type="text/csv; charset=utf-8",
                download=True,
            )

    return Handler


def smoke_listening_session(
    server: HTTPServer, *, token: str, pair_count: int,
) -> dict[str, object]:
    """Exercise local audio routes once, then always stop the temporary server.

    A successful HTTP byte-range request requires a verified original fetch by
    the handler. No human choices are submitted and no WAV bytes are saved.
    """
    if not 1 <= pair_count <= 12 or not token:
        raise ValueError("invalid_listening_smoke_scope")
    listener = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    )
    listener.start()
    verified_responses = 0
    try:
        for index in range(-1, pair_count):
            sides = ("",) if index == -1 else ("a", "b")
            for side in sides:
                path = (
                    f"/s/{token}/" if index == -1
                    else f"/s/{token}/audio/{index}/{side}"
                )
                connection = http.client.HTTPConnection(
                    "127.0.0.1", server.server_port, timeout=90
                )
                try:
                    headers = {"Host": f"127.0.0.1:{server.server_port}"}
                    if index >= 0:
                        headers["Range"] = "bytes=0-15"
                    connection.request("GET", path, headers=headers)
                    response = connection.getresponse()
                    body = response.read()
                    if (
                        response.getheader("Cache-Control") != "no-store, max-age=0"
                    ):
                        raise ValueError("missing_noncacheable_response")
                    if index == -1:
                        if response.status != 200 or (
                            body.count(b"<audio") != pair_count * 2
                        ):
                            raise ValueError("invalid_local_review_page")
                    else:
                        if (
                            response.status != 206
                            or len(body) != 16
                            or body[:4] not in (b"RIFF", b"RF64")
                            or response.getheader("Content-Range") is None
                        ):
                            raise ValueError("verified_audio_range_not_served")
                        verified_responses += 1
                finally:
                    connection.close()
        return {
            "status": "local_audio_smoke_passed",
            "selected_pair_count": pair_count,
            "sha256_verified_audio_http_responses": verified_responses,
            "reviews_submitted": 0,
            "media_files_saved": 0,
            "server_persisted": False,
            "human_rights_or_accuracy_verified": False,
        }
    finally:
        server.shutdown()
        listener.join(timeout=5)
        if listener.is_alive():
            raise RuntimeError("listening_server_did_not_stop")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local-only original-audio review screen")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--expected-report-sha256", required=True,
        help="SHA256 of the previously verified report file bytes",
    )
    parser.add_argument("-p", "--package-id", action="append", required=True)
    parser.add_argument("--samples-per-album", type=int, choices=(2, 3), default=2)
    parser.add_argument(
        "--smoke-only", action="store_true",
        help="Read-only short-lived localhost HTTP WAV check; never collect reviews",
    )
    args = parser.parse_args(argv)
    if not 2 <= len(args.package_id) <= 8 or len(set(args.package_id)) != len(
        args.package_id
    ):
        parser.error("require 2..8 unique original P6 package IDs")
    try:
        report = read_queue(args.report, expected_sha256=args.expected_report_sha256)
        settings = load_settings()
        engine = build_engine(settings)
        try:
            with Session(engine) as session:
                session.execute(sa.text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                ))
                try:
                    sources, total = load_sample_sources(
                        session, args.package_id,
                        samples_per_album=args.samples_per_album,
                    )
                finally:
                    session.rollback()
        finally:
            engine.dispose()
        bound = bind_review_queue_sources(
            report, sources, selected_package_ids=args.package_id,
            samples_per_album=args.samples_per_album,
            archived_track_count=total,
        )
        pair_sources = tuple((left, right) for left, right, _scores in bound)
        store = MinioArtifactStore(settings)
        secret = secrets.token_urlsafe(32)
        handler = make_handler(pair_sources, store, session_token=secret)
        with HTTPServer(("127.0.0.1", 0), handler) as server:
            if args.smoke_only:
                print(json.dumps(smoke_listening_session(
                    server, token=secret, pair_count=len(pair_sources)
                ), sort_keys=True))
                return 0
            print(
                f"Local P6 listening only: http://127.0.0.1:{server.server_port}/s/{secret}/",
                flush=True,
            )
            print(
                "The above private link is local-only. Stop the server with Ctrl+C. "
                "No human reviews are stored on the server.",
                flush=True,
            )
            try:
                server.serve_forever(poll_interval=0.2)
            except KeyboardInterrupt:
                return 0
        return 0
    except Exception as exc:  # noqa: BLE001 - no original source URI in logs
        print(
            f"Local listening unavailable: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
