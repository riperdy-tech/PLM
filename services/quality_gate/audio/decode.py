"""Decode an audio artifact to floating-point PCM for P5 measurement (R4).

* **WAV** (RIFF/RIFX ``WAVE``, RF64, BW64; detected from the header bytes, never from the
  file extension) is read by libsndfile through ``soundfile``. The original bytes are
  measured as they are; nothing is transcoded first (P5 plan section 11).
* **Anything else** (MP3, FLAC, M4A, ...) is decoded by ``ffmpeg`` to 32-bit float PCM in
  a private temporary WAV, then read by libsndfile. The subprocess gets an explicit
  argument list (no shell), reads the input through the ``file:`` protocol so a path is
  never parsed as an option or URL, keeps the native sample rate and channel layout
  (no resampling or downmix), decodes the first audio stream only, runs single-threaded,
  and stops on the first decode error (``-xerror``).

A decode that cannot be completed returns :class:`DecodeFailure` with a reason code. It
is never a pass: the technical evaluator turns it into a blocking finding.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import numpy.typing as npt
import soundfile as sf  # type: ignore[import-untyped]

__all__ = [
    "DECODER_UNAVAILABLE",
    "DECODE_EMPTY",
    "DECODE_FAILED",
    "DecodeFailure",
    "DecodedAudio",
    "decode_audio",
    "sniff_container",
]

# Reason codes of a decode that could not be completed.
DECODE_FAILED = "DECODE_FAILED"
DECODER_UNAVAILABLE = "DECODER_UNAVAILABLE"
DECODE_EMPTY = "DECODE_EMPTY"

_HEADER_BYTES = 12


@dataclass(frozen=True)
class DecodedAudio:
    """PCM samples as float64 in [-1, 1) full scale, shaped ``(frames, channels)``."""

    samples: npt.NDArray[np.float64]
    sample_rate: int
    container: str
    codec: str
    decoder: str

    @property
    def frames(self) -> int:
        return int(self.samples.shape[0])

    @property
    def channels(self) -> int:
        return int(self.samples.shape[1])

    @property
    def duration_seconds(self) -> float:
        return self.frames / float(self.sample_rate)


@dataclass(frozen=True)
class DecodeFailure:
    """Why the audio could not be decoded; ``reason_code`` is one of the module codes."""

    reason_code: str
    detail: str
    container: str
    decoder: str


def sniff_container(header: bytes) -> str:
    """``"wav"`` for a RIFF/RIFX/RF64/BW64 WAVE header, else ``"other"``."""
    is_wave = len(header) >= _HEADER_BYTES and header[8:12] == b"WAVE"
    if is_wave and header[:4] in (b"RIFF", b"RIFX", b"RF64", b"BW64"):
        return "wav"
    return "other"


def _libsndfile_label() -> str:
    return f"libsndfile {sf.__libsndfile_version__}"


def _read_with_libsndfile(
    path: Path, *, container: str, decoder: str
) -> DecodedAudio | DecodeFailure:
    try:
        info = sf.info(str(path))
        data, rate = sf.read(str(path), dtype="float64", always_2d=True)
    except (RuntimeError, OSError, ValueError, TypeError) as exc:
        # soundfile.LibsndfileError subclasses RuntimeError.
        return DecodeFailure(DECODE_FAILED, f"{type(exc).__name__}: {exc}", container, decoder)
    samples = np.ascontiguousarray(data, dtype=np.float64)
    if samples.shape[0] == 0 or samples.shape[1] == 0 or int(rate) <= 0:
        return DecodeFailure(DECODE_EMPTY, "decoded no audio frames", container, decoder)
    return DecodedAudio(
        samples=samples,
        sample_rate=int(rate),
        container=container,
        codec=str(info.subtype),
        decoder=decoder,
    )


@lru_cache(maxsize=8)
def _ffmpeg_version(ffmpeg: str) -> str:
    """``"ffmpeg <version>"`` from ``ffmpeg -version`` (the decoder that made the PCM)."""
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-version"],
            capture_output=True,
            check=False,
            timeout=30,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return "ffmpeg unknown-version"
    first = out.splitlines()[0].split() if out else []
    return f"ffmpeg {first[2]}" if len(first) >= 3 else "ffmpeg unknown-version"


def _decode_with_ffmpeg(
    path: Path, *, ffmpeg_path: str | None, timeout_seconds: float
) -> DecodedAudio | DecodeFailure:
    ffmpeg = ffmpeg_path or shutil.which("ffmpeg")
    if ffmpeg is None:
        return DecodeFailure(
            DECODER_UNAVAILABLE,
            "non-WAV input needs ffmpeg, which is not on PATH",
            "other",
            "ffmpeg",
        )
    decoder = _ffmpeg_version(ffmpeg)
    with tempfile.TemporaryDirectory(prefix="plm_p5_decode_") as tmp:
        out_path = Path(tmp) / "decoded.wav"
        args = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel", "error",
            "-xerror",
            "-threads", "1",
            "-i", f"file:{path.resolve()}",
            "-map", "0:a:0",
            "-vn", "-sn", "-dn",
            "-c:a", "pcm_f32le",
            "-rf64", "auto",
            "-f", "wav",
            f"file:{out_path}",
        ]
        try:
            proc = subprocess.run(args, capture_output=True, check=False, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            return DecodeFailure(
                DECODE_FAILED, f"ffmpeg exceeded {timeout_seconds}s", "other", decoder
            )
        except OSError as exc:
            return DecodeFailure(DECODER_UNAVAILABLE, f"ffmpeg did not start: {exc}", "other", decoder)
        if proc.returncode != 0 or not out_path.is_file():
            stderr = proc.stderr.decode("utf-8", "replace").strip()
            detail = stderr.splitlines()[-1] if stderr else f"exit status {proc.returncode}"
            return DecodeFailure(DECODE_FAILED, f"ffmpeg: {detail}", "other", decoder)
        decoded = _read_with_libsndfile(out_path, container="other", decoder=decoder)
    if isinstance(decoded, DecodeFailure):
        return decoded
    return DecodedAudio(
        samples=decoded.samples,
        sample_rate=decoded.sample_rate,
        container=_container_label(path),
        codec="ffmpeg_decoded_pcm_f32",
        decoder=decoder,
    )


def _container_label(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return suffix or "unknown"


def decode_audio(
    path: Path, *, timeout_seconds: float, ffmpeg_path: str | None = None
) -> DecodedAudio | DecodeFailure:
    """Decode ``path`` to float PCM, or say why it could not be decoded."""
    try:
        with path.open("rb") as handle:
            header = handle.read(_HEADER_BYTES)
    except OSError as exc:
        return DecodeFailure(DECODE_FAILED, f"unreadable: {exc}", "unknown", "none")
    if not header:
        return DecodeFailure(DECODE_EMPTY, "the file is empty", "unknown", "none")
    if sniff_container(header) == "wav":
        return _read_with_libsndfile(path, container="wav", decoder=_libsndfile_label())
    return _decode_with_ffmpeg(path, ffmpeg_path=ffmpeg_path, timeout_seconds=timeout_seconds)
