"""The Studio: the owner's production steps of a run, in the dashboard.

A run waits for the owner three times. The Studio shows each wait and takes the owner's
answer, so nothing needs a folder, a command or a hand-typed evidence reference:

* **Songs** (``MANUAL_AUDIO_AWAITED``): the prompt sheets P4 wrote into the music inbox,
  with each song's file name; the owner uploads each WAV, then continues.
* **Thumbnails** (``THUMBNAIL_ART_AWAITED``): the three prompts P6 wrote into the art
  inbox; the owner uploads A, B and C, then imports. The import checks every image,
  renders the video as image A held still and releases the package (P6's own importer);
  the Studio then resumes the run.
* **Review** (``PUBLISH_APPROVAL_AWAITED``): the finished package (video, thumbnail,
  title, description). Approve has the exception queue decide the resume, then writes
  the owner's YouTube grants for this upload, expiring in :data:`APPROVAL_GRANT_HOURS`
  hours, then signals the run, which uploads it; the grants are removed once the run has
  published. Reject ends the run.
* **Request changes** (at the review): a new title, description or tags, or another
  thumbnail first, apply at once and the package stays in review; new art sends the
  package back to the thumbnail step (the next import renders the video again); a song
  taken out sends the run back to assembly, which asks for a new song in the songs step.
  The owner's note is kept (:attr:`StudioPaths.notes_file`) and shown in the step the
  change went back to.

Every resume goes through :class:`~.actions.ExceptionActions` as :data:`OWNER` (who must
hold the hold's reviewer role in the dashboard's reviewer file), with the evidence the
hold class names, so the queue records who decided and on what.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import secrets
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from ..clock import Clock, SystemClock
from ..dotenv import load_env_file
from .actions import ActionRefused, ExceptionActions
from .director import (
    RECENT_VIDEOS,
    Brief,
    Director,
    director_from_env,
    recorded_prompts,
    waiting_songs,
)
from .projects import FINISHED, explain_hold, number_runs, plain_log, state_label

__all__ = ["APPROVAL_GRANT_HOURS", "OWNER", "Studio", "StudioPaths", "build_studio"]

OWNER: Final[str] = "owner"
APPROVAL_GRANT_HOURS: Final[int] = 24
"""Long enough for the upload, YouTube's processing (a long video took over 2 hours on
2026-11-24 and its thumbnail write was refused ``authorization_expired``) and the
thumbnail and playlist writes after it; the Studio removes the grants once the run is
published or has ended (review 2026-11-26 #18), so the window is not left open."""
APPROVAL_CAPABILITIES: Final[tuple[str, ...]] = (
    "youtube.upload", "youtube.thumbnail", "youtube.playlist")
SONGS_REASON: Final[str] = "MANUAL_AUDIO_AWAITED"
ART_REASON: Final[str] = "THUMBNAIL_ART_AWAITED"
APPROVAL_REASON: Final[str] = "PUBLISH_APPROVAL_AWAITED"
_FINISHED_STATES: Final = frozenset({
    "PUBLISHED", "ANALYZING", "LEARNING", "FAILED", "CANCELLED", "REJECTED"})
_STEPS: Final[dict[str, str]] = {
    "IDEA": "Planning the video", "RESEARCHED": "Planning the video",
    "POLICY_CHECKED": "Planning the video", "GENERATING": "Preparing the song prompts",
    "GENERATED": "Checking the songs", "QA": "Checking the songs", "PASSED": "Checking the songs",
    "ASSEMBLING": "Building the playlist and rendering the video (this takes a while)",
    "PACKAGED": "Video rendered", "UPLOADING": "Uploading to YouTube",
    "PROCESSING": "YouTube is processing the upload",
}
_WAITING: Final[dict[str, str]] = {
    SONGS_REASON: "Waiting for you: songs (step 1)",
    ART_REASON: "Waiting for you: thumbnails (step 2)",
    APPROVAL_REASON: "Waiting for you: review and approve (step 3)",
}
MUSIC_PROVIDERS: Final[dict[str, str]] = {"suno": "suno_manual",
                                          "flow_music": "flow_music_manual"}
"""The owner's choice at Start -> the P4 provider that writes the song prompts."""
_STEM: Final = re.compile(r"\A[A-Za-z0-9_-]{1,120}\Z")
_PACKAGE: Final = re.compile(r"\Ampkg_[0-9a-f]{8,64}\Z")
_SAVE_AS: Final = re.compile(r"^Save as: ([A-Za-z0-9_-]+)\.wav$", re.MULTILINE)
_PLAN: Final = re.compile(r"^Plan: (\S+)$", re.MULTILINE)
_ARCHIVES: Final = ("imported", "cancelled")
"""Where P4 and the Studio move a finished run's songs (``<inbox>/<folder>/<plan>/``)."""
_IMPORTED_STATES: Final = frozenset({"PUBLISHED", "ANALYZING", "LEARNING"})
_ART_SUFFIXES: Final = (".png", ".jpg", ".jpeg", ".webp")
_STUDIO_GRANT: Final = "Studio approval "
"""The reference prefix of the grants an approval writes (and later revokes)."""
_GRANT_RUN: Final = re.compile(r" for run (\S+) at ")
_LETTERS: Final = ("A", "B", "C")


def _env(name: str, env: Mapping[str, str], dotenv: Mapping[str, str]) -> str | None:
    value = (env.get(name) or dotenv.get(name) or "").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class StudioPaths:
    music_inbox: Path | None
    """The Flow Music inbox (``PLM_FLOW_MUSIC_MANUAL_INBOX``)."""
    art_inbox: Path | None
    grants_file: Path | None
    notes_file: Path | None = None
    """The owner's review notes (``PLM_STUDIO_NOTES_FILE``, default
    ``data/studio-notes.json``)."""
    suno_inbox: Path | None = None
    """The Suno inbox (``PLM_SUNO_MANUAL_INBOX``)."""

    def music_inboxes(self) -> list[tuple[str, Path]]:
        """Each configured song inbox with the tool its sheets are for."""
        found: list[tuple[str, Path]] = []
        for source, inbox in (("Flow Music", self.music_inbox), ("Suno", self.suno_inbox)):
            if inbox is not None and inbox.is_dir() and all(
                    inbox.resolve() != seen.resolve() for _, seen in found):
                found.append((source, inbox))
        return found

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None,
                 dotenv_path: str | Path = ".env") -> StudioPaths:
        """The inboxes and grant file the services use (process env, then ``.env``). Both
        song inboxes: the owner picks Suno or Flow Music when starting a run."""
        process = os.environ if env is None else env
        dotenv = load_env_file(dotenv_path)
        music = _env("PLM_FLOW_MUSIC_MANUAL_INBOX", process, dotenv)
        suno = _env("PLM_SUNO_MANUAL_INBOX", process, dotenv)
        art = _env("PLM_THUMBNAIL_ART_INBOX", process, dotenv)
        grants = _env("PLM_WRITE_AUTHORIZATIONS_FILE", process, dotenv)
        notes = _env("PLM_STUDIO_NOTES_FILE", process, dotenv) or "data/studio-notes.json"
        return cls(music_inbox=None if music is None else Path(music),
                   art_inbox=None if art is None else Path(art),
                   grants_file=None if grants is None else Path(grants),
                   notes_file=Path(notes),
                   suno_inbox=None if suno is None else Path(suno))


def _iso(value: Any) -> str:
    return value.isoformat() if isinstance(value, datetime) else ("" if value is None
                                                                  else str(value))


def _refuse(status: int, code: str, message: str) -> ActionRefused:
    return ActionRefused(status, code, message)


def _expires_after(grant: Mapping[str, Any], moment: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(grant.get("expires_at"))) > moment
    except (TypeError, ValueError):
        return False


def m4a_to_wav(data: bytes) -> bytes:
    """An M4A song decoded to a 16-bit PCM WAV at its own sample rate and channels, with
    nothing trimmed or changed; refused when ffmpeg is missing or cannot read it."""
    import shutil
    import subprocess
    import tempfile

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise _refuse(503, "ffmpeg_missing", "ffmpeg is not installed, so M4A cannot be read")
    with tempfile.TemporaryDirectory() as tmp:
        # A file, not a pipe: ffmpeg fills in the WAV header's sizes only on a seekable output.
        source, target = Path(tmp) / "song.m4a", Path(tmp) / "song.wav"
        source.write_bytes(data)
        done = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source), "-vn",
             "-map_metadata", "-1", "-c:a", "pcm_s16le", str(target)],
            capture_output=True, check=False, timeout=300)
        if done.returncode != 0 or not target.is_file():
            raise _refuse(422, "m4a_unreadable", "ffmpeg could not read the M4A: "
                          + done.stderr.decode(errors="replace")[:200])
        return target.read_bytes()


def _write_atomic(path: Path, data: bytes) -> None:
    # A temp name of its own: two writers never share (or replace) one part file
    # (review 2026-11-26, P2).
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.part")
    try:
        tmp.write_bytes(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _image_suffix(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


@functools.cache
def song_counts(genre: str = "focus_electronic") -> dict[str, dict[str, int]]:
    """What each Start length asks of the owner, from P3's own plan (its template's song
    length, the bucket's range and the supply policy's spares): ``{bucket: {songs,
    song_seconds}}``; empty when P3's code is not importable here. Worked out once per
    process: it depends only on P3's code, and the Start card asks every 5 s (review
    2026-11-26, P2). Callers must not change the answer."""
    try:
        from experiments.genome.engine import default_template_library
        from experiments.materialization.builder import derived_duration_seconds
        from experiments.materialization.supply import default_supply_policy, plan_supply
    except ImportError:
        return {}
    library = default_template_library()
    seconds = derived_duration_seconds(library.for_genre(genre).genome.track_duration)
    buckets = [*LENGTH_CHOICES, *(f"{m}m" for m in range(MINUTES_RANGE[0], MINUTES_RANGE[1] + 1))]
    return {bucket: {"songs": plan_supply(library.playlist_duration_for_bucket(bucket), seconds,
                                          default_supply_policy()).track_count,
                     "song_seconds": seconds}
            for bucket in buckets}


def wav_seconds(path: Path) -> float | None:
    """The length of a PCM WAV from its header (``fmt`` byte rate, ``data`` size);
    ``None`` when the file is not a readable WAV."""
    import struct

    try:
        with path.open("rb") as f:
            if f.read(12)[8:12] != b"WAVE":
                return None
            byte_rate = None
            while True:
                head = f.read(8)
                if len(head) < 8:
                    return None
                chunk, size = head[:4], struct.unpack("<I", head[4:])[0]
                if chunk == b"fmt ":
                    fmt = f.read(size + (size & 1))
                    byte_rate = struct.unpack("<I", fmt[8:12])[0]
                    continue
                if chunk == b"data":
                    return None if not byte_rate else round(size / byte_rate, 1)
                f.seek(size + (size & 1), 1)
    except (OSError, struct.error):
        return None


_EXPECTED: Final = re.compile(r"^Length: about (\d+) seconds", re.MULTILINE)
# A sheet's paste-in texts: "Styles (665 characters; ...):" or "Prompt (...):" with the
# text on the next line, and "Exclude styles: ..." on one line.
_FIELD_HEAD: Final = re.compile(r"^(Styles|Prompt) \(\d+ characters;[^)]*\):$")
_EXCLUDE: Final = re.compile(r"^Exclude styles: (.+)$")


def song_fields(block: str) -> tuple[list[str], list[dict[str, str]]]:
    """A song block split into its information lines and the texts the owner pastes into
    the tool's separate boxes (Suno's Styles and Exclude styles, Flow Music's Prompt)."""
    info: list[str] = []
    fields: list[dict[str, str]] = []
    lines = block.splitlines()
    i = 0
    while i < len(lines):
        head, exclude = _FIELD_HEAD.match(lines[i]), _EXCLUDE.match(lines[i])
        save_as = _SAVE_AS.match(lines[i])
        if save_as:  # the title is the file name without .wav; the line stays for saving
            fields.insert(0, {"label": "Title", "text": save_as.group(1)})
        if head and i + 1 < len(lines):
            fields.append({"label": head.group(1), "text": lines[i + 1]})
            i += 2
            continue
        if exclude:
            fields.append({"label": "Exclude styles", "text": exclude.group(1)})
        elif lines[i].strip():
            info.append(lines[i])
        i += 1
    return info, fields


LENGTH_CHOICES: Final[dict[str, str]] = {
    "20_30m": "20-30 minutes", "45_75m": "45-75 minutes", "90_120m": "90-120 minutes"}
"""The video lengths the owner picks at Start (P3 buckets, owner 2026-11-10)."""
MINUTES_RANGE: Final[tuple[int, int]] = (20, 120)
"""Owner 2026-11-23: the length is typed in whole minutes (sent as ``"95m"``); P3 plans 5
minutes either side. The limits are P3's allowed playlist range."""


def minutes_length(length: str) -> int | None:
    """The minutes of a typed length (``"95m"`` -> 95) inside :data:`MINUTES_RANGE`."""
    found = re.fullmatch(r"(\d{1,3})m", length)
    if found is None:
        return None
    value = int(found.group(1))
    return value if MINUTES_RANGE[0] <= value <= MINUTES_RANGE[1] else None
RENDER_SECONDS_PER_VIDEO_SECOND: Final[float] = 0.25
"""The still video's render time per second of video, measured 2026-11-09 at 1440p (a
97-minute video took about 23 minutes on this host); the import's time estimate."""


STYLE_LABELS: Final[dict[str, str]] = {
    "future_garage_focus": "Future garage",
    "soft_cyber_electronica": "Soft cyber electronica",
    "minimal_pulse": "Minimal pulse",
    "ambient_breakbeat": "Ambient breakbeat",
    "downtempo_synth": "Downtempo synth",
    "deep_work_chillstep": "Deep-work chillstep",
    "restrained_retro_future": "Restrained retro-future",
    "organic_electronic": "Organic electronic",
    "piano_synth_concentration": "Piano & synth concentration",
}
"""Display names of the focus sub-styles (P1 ontology / P3 palettes)."""
STYLE_MEMORY: Final[int] = 2
"""How many of the latest videos' sub-styles "let P2 pick" avoids (P2's SUBGENRE_MEMORY)."""


def _songs_record(sheet: Path) -> dict[str, Any]:
    """P4's ``<track>.songs.json`` beside a prompt sheet (``{}`` when unreadable)."""
    try:
        loaded = json.loads(sheet.with_name(
            sheet.name.replace(".prompts.txt", ".songs.json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _sounds_like(pkg: Mapping[str, Any]) -> list[dict[str, Any]]:
    """P6's sound-variance warning (``render_report.album_texture.sound_variance``): the
    recent videos this one sounds like, for the owner's review."""
    report = pkg.get("render_report")
    texture = report.get("album_texture") if isinstance(report, dict) else None
    variance = texture.get("sound_variance") if isinstance(texture, dict) else None
    found = variance.get("too_similar") if isinstance(variance, dict) else None
    return [dict(f) for f in found if isinstance(f, dict)] if isinstance(found, list) else []

@dataclass
class Studio:
    """The owner's steps (see module docstring). Collaborators are injected so the
    Studio is tested without the services; :func:`build_studio` wires the real ones."""

    paths: StudioPaths
    actions: ExceptionActions
    open_holds: Callable[[], list[dict[str, Any]]]
    """The open exception cases, as the exceptions view describes them."""
    package: Callable[[str], dict[str, Any]]
    """P6's package (``GET /media/packages/{id}``)."""
    media: Callable[[str, str], bytes]
    """Stored bytes by ``(uri, checksum)``, verified."""
    import_art: Callable[[str, Path], dict[str, Any]]
    """P6's importer for one package's folder; raises on refusal."""
    start: Callable[..., Any] | None = None
    """Starts a new run (async) whose songs come from the named P4 provider
    (:data:`MUSIC_PROVIDERS`), of the owner's sub-style or P2's pick (``None``);
    ``None`` here: the Start button is refused."""
    styles: Callable[[], dict[str, Any]] | None = None
    """``{"available": [sub-style], "recent": [{"subgenre", "at"}]}``: the sub-styles P2
    has a usable arm for, and the sub-styles P3 used for the latest videos (newest
    first)."""
    edit: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None
    """P6's review edit of one package (``media_factory.review_edits.edit_presentation``
    keyword arguments); raises on refusal."""
    reopen: Callable[[str], dict[str, Any]] | None = None
    """P6's reopen of one package for new art (``review_edits.reopen_art``)."""
    runs: Callable[[], list[dict[str, Any]]] | None = None
    """The pipeline's active runs (the production-queue view); ``None``: not shown."""
    store: Any = None
    """:class:`~.projects.ProjectStore`: every run and its record (``None``: no projects)."""
    cancel: Callable[[str, str], Any] | None = None
    """Sends a run the pipeline's cancel signal (async): ``(workflow_id, reason)``."""
    video_stats: Callable[[list[str]], dict[str, dict[str, int]]] | None = None
    """YouTube's public counters per video id (read-only); ``None``: not shown."""
    clock: Clock = field(default_factory=SystemClock)
    director: Director | None = None
    """Claude's song prompts and Start suggestion (:mod:`.director`); ``None``: none."""
    video_dir: Path | None = None
    """Where the review's video is kept as a file, one per checksum (``None``: a
    ``plm-studio-video`` folder in the system temp folder)."""
    _video_lock: Any = field(default_factory=threading.Lock)
    _grants_lock: Any = field(default_factory=threading.Lock)
    _notes_lock: Any = field(default_factory=threading.Lock)
    _remakes: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Remakes running or ended in this dashboard: package -> since, state, error."""
    _tasks: set[Any] = field(default_factory=set)
    _stats_cache: tuple[float, dict[str, dict[str, int]]] | None = None
    """The last YouTube counters and when (monotonic): refreshed every ten minutes."""
    _styles_cache: tuple[float, dict[str, Any]] | None = None
    """The last style lookup and when (monotonic): it changes only when a run starts."""
    _imports: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Picture imports running or ended in this dashboard: package -> since, state,
    expected seconds, error (the render takes minutes, so it runs on its own)."""

    # -- holds ------------------------------------------------------------------------------

    def _live_holds(self) -> list[dict[str, Any]]:
        """Open holds of runs that are HELD now (a case left open by a run that was
        cancelled or ended is not a wait)."""
        return [c for c in self.open_holds()
                if (c.get("pipeline") or {}).get("run_state") == "HELD"]

    def _cases(self, reason: str) -> list[dict[str, Any]]:
        return [c for c in self._live_holds() if c.get("reason_code") == reason]

    def videos_now(self) -> list[dict[str, Any]]:
        """Every run still being made (not yet published), with its step in plain words,
        so the Studio shows work in progress, not only the steps that wait for the owner."""
        if self.runs is None:
            return []
        holds = {(c.get("pipeline") or {}).get("workflow_id"): c for c in self._live_holds()}
        videos = []
        for run in self.runs():
            state = str(run.get("state"))
            if state in _FINISHED_STATES:
                continue
            hold = holds.get(run.get("workflow_id"))
            if state == "HELD":
                reason = None if hold is None else hold.get("reason_code")
                label = _WAITING.get(str(reason), "Stopped: needs attention "
                                     f"({reason or run.get('last_error_code') or 'unknown'})")
                yours = reason in _WAITING
            else:
                label, yours = _STEPS.get(state, f"Working ({state})"), False
            videos.append({"workflow_id": run.get("workflow_id"), "state": state,
                           "label": label, "your_turn": yours,
                           "since": run.get("updated_at"), "started": run.get("created_at")})
        return videos

    def overview(self) -> dict[str, Any]:
        holds = self._live_holds()
        return {
            "videos": self.videos_now(),
            "waiting": {reason: [c["case_id"] for c in holds if c.get("reason_code") == reason]
                        for reason in (SONGS_REASON, ART_REASON, APPROVAL_REASON)},
            "songs": self.songs(),
            "thumbnails": self.art_jobs(),
            "reviews": [self.review(c) for c in holds if c.get("reason_code") == APPROVAL_REASON],
            "remakes": self.remakes(),
            "notes": self.notes()[-20:],
            "start_available": self.start is not None,
            "styles": self.style_choices(),
            "lengths": LENGTH_CHOICES,
        }

    def start_options(self) -> dict[str, Any]:
        """What the Start card needs, without the inbox scans of :meth:`overview`."""
        return {"start_available": self.start is not None, "styles": self.style_choices(),
                "lengths": LENGTH_CHOICES, "minutes_range": list(MINUTES_RANGE),
                "song_counts": song_counts(),
                "remakes": self.remakes()}

    def style_choices(self) -> dict[str, Any]:
        """The Start button's sub-style picker: what can be chosen, what the last videos
        used (owner 2026-11-09: videos must not sound alike one to the next) and Claude's
        suggestion (owner 2026-11-27: Claude advises, the owner chooses)."""
        found = self._style_lookup()
        suggestion = self._style_suggestion(found)
        return found if suggestion is None else {**found, "suggestion": suggestion}

    def _style_suggestion(self, found: Mapping[str, Any]) -> dict[str, Any] | None:
        available = [str(s) for s in found.get("available") or []]
        if self.director is None or not available:
            return None
        fingerprint = json.dumps([available, found.get("recent") or [],
                                  sorted(found.get("cooling") or {})], default=str)
        return self.director.suggestion(available, fingerprint,
                                        lambda: self._suggestion_context(found))

    def _suggestion_context(self, found: Mapping[str, Any]) -> dict[str, Any]:
        """What Claude reads for the suggestion (in the background, never on a request)."""
        cooling = found.get("cooling") or {}
        available = [{"id": s, "label": STYLE_LABELS.get(s, s),
                      **({"cooling_down": True} if s in cooling else {})}
                     for s in found.get("available") or []]
        if self.store is None:
            return {"available": available, "videos": [
                {"style": STYLE_LABELS.get(str(r.get("subgenre")), r.get("subgenre")),
                 "started": r.get("at")} for r in found.get("recent") or []]}
        dirs = [inbox for _, inbox in self.paths.music_inboxes()]
        runs = [r for r in self.store.runs() if r.get("experiment_id")][-6:][::-1]
        stats = self._stats([r["youtube"]["video_id"] for r in runs if r.get("youtube")])
        return {"available": available, "videos": [
            {"style": STYLE_LABELS.get(str(r.get("style")), r.get("style")),
             "title": r.get("title"), "state": r.get("state"),
             "started": _iso(r.get("created_at")),
             "youtube": stats.get((r.get("youtube") or {}).get("video_id") or ""),
             "prompts": recorded_prompts(dirs, self.store.track_ids(str(r["experiment_id"])),
                                         limit=3)} for r in runs]}

    def _style_lookup(self) -> dict[str, Any]:
        if self.styles is None:
            return {"available": [], "recent": [], "labels": STYLE_LABELS}
        import time

        if self._styles_cache and time.monotonic() - self._styles_cache[0] < 120:
            return self._styles_cache[1]
        try:
            found = self.styles()
        except Exception as exc:  # noqa: BLE001 - the picker degrades to "P2 picks"
            return {"available": [], "recent": [], "labels": STYLE_LABELS,
                    "error": str(exc)}
        self._styles_cache = (time.monotonic(), {**found, "labels": STYLE_LABELS})
        return self._styles_cache[1]

    async def _resume(self, case_id: str, role: str, evidence: dict[str, str],
                      notes: str, **signal: Any) -> dict[str, Any]:
        return await self.actions.resume(case_id, actor=OWNER, role=role, evidence=evidence,
                                         notes=notes, **signal)

    # -- notes ------------------------------------------------------------------------------

    def notes(self) -> list[dict[str, Any]]:
        path = self.paths.notes_file
        if path is None or not path.is_file():
            return []
        loaded: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def _add_note(self, kind: str, note: str, **about: Any) -> dict[str, Any]:
        entry = {"at": self.clock.now().isoformat(), "kind": kind,
                 "note": note.strip(), **about}
        path = self.paths.notes_file
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._notes_lock:  # read-modify-write: one writer at a time (review P2)
                _write_atomic(path, (json.dumps([*self.notes(), entry][-200:], indent=2)
                                     + "\n").encode("utf-8"))
        return entry

    # -- songs ------------------------------------------------------------------------------

    def _music_inboxes(self) -> list[tuple[str, Path]]:
        inboxes = self.paths.music_inboxes()
        if not inboxes:
            raise _refuse(503, "music_inbox_not_configured",
                          "neither PLM_FLOW_MUSIC_MANUAL_INBOX nor PLM_SUNO_MANUAL_INBOX is a folder")
        return inboxes

    def songs(self) -> dict[str, Any]:
        """Every prompt sheet in the song inboxes (Flow Music and Suno) with a song still
        missing, each naming the tool it is for."""
        inboxes = self.paths.music_inboxes()
        if not inboxes:
            return {"configured": False, "sheets": []}
        sheets = []
        # Only running runs' sheets; a finished run's songs leave the inbox
        # (no project store: every sheet).
        tracks = None if self.store is None else self._archive_finished(inboxes)
        for source, inbox in inboxes:
            sheets += self._sheets(source, inbox, tracks=tracks)
        return {"configured": True, "inbox": str(inboxes[0][1]),
                "inboxes": {source: str(inbox) for source, inbox in inboxes},
                "sheets": sheets}

    def _archive_finished(self, inboxes: list[tuple[str, Path]]) -> set[str]:
        """Move the songs of runs that are finished (published) or ended (cancelled,
        failed, rejected) to ``imported/<plan>/`` or ``cancelled/<plan>/`` with P4's own
        move (idempotent, never deletes), and return the running runs' tracks."""
        running: set[str] = set()
        finished: dict[str, str] = {}
        for run in self.store.runs():
            experiment, state = run.get("experiment_id"), str(run.get("state"))
            if not experiment:
                continue
            for track in self.store.track_ids(str(experiment)):
                if state not in _FINISHED_STATES:
                    running.add(track)
                else:
                    finished[track] = ("imported" if state in _IMPORTED_STATES
                                       else "cancelled")
        try:
            from music_factory.providers.suno_manual import archive_track
        except ImportError:
            return running
        for _, inbox in inboxes:
            for sheet in sorted(inbox.glob("*.prompts.txt")):
                track = sheet.name.split(".")[0]
                if track in running or track not in finished:
                    continue
                try:
                    plan = _PLAN.search(sheet.read_text(encoding="utf-8"))
                    archive_track(inbox, track, plan.group(1) if plan else None,
                                  finished[track])
                except OSError:
                    continue  # left in place; the next listing tries again
        return running

    @staticmethod
    def _sheets(source: str, inbox: Path, *, tracks: set[str] | None = None,
                include_done: bool = False) -> list[dict[str, Any]]:
        """The inbox's prompt sheets with a song still missing; ``include_done`` keeps the
        finished ones too and ``tracks`` limits them to those tracks' sheets (a project
        page shows its own songs, made or not, moved out of the inbox too)."""
        sheets = []
        found = sorted(inbox.glob("*.prompts.txt"))
        if include_done:
            found += sorted(s for kind in _ARCHIVES for s in inbox.glob(f"{kind}/*/*.prompts.txt"))
        for sheet in found:
            if tracks is not None and sheet.name.split(".")[0] not in tracks:
                continue
            text = sheet.read_text(encoding="utf-8")
            blocks = text.split("--- Song ")
            header, songs = blocks[0].strip(), []
            record = _songs_record(sheet)  # who wrote the texts (the director, P4)
            entries = {str(e["stem"]): e for e in (record.get("songs") or {}).values()
                       if isinstance(e, dict) and e.get("stem")}
            for block in blocks[1:]:
                match = _SAVE_AS.search(block)
                if match is None:
                    continue
                stem = match.group(1)
                wav = sheet.parent / f"{stem}.wav"
                expected = _EXPECTED.search(block)
                info, fields = song_fields(block.split("\n", 1)[1] if "\n" in block else "")
                entry = entries.get(stem) or entries.get(re.sub(r"-r\d+\Z", "", stem)) or {}
                songs.append({"stem": stem, "text": f"--- Song {block}".strip(),
                              "written_by": entry.get("written_by"),
                              "director_refused": entry.get("director_refused"),
                              "info": info, "fields": fields,
                              "saved": wav.is_file(),
                              "seconds": wav_seconds(wav) if wav.is_file() else None,
                              "expected_seconds": int(expected.group(1)) if expected else None,
                              "lyrics_needed": "Instrumental: no" in block,
                              "lyrics_saved": (sheet.parent / f"{stem}.lyrics.txt").is_file()})
            if songs and (include_done or not all(s["saved"] for s in songs)):
                sheets.append({"sheet": sheet.name, "source": source, "inbox": str(inbox),
                               "header": header, "songs": songs,
                               "director": ({"written_by": record["written_by"],
                                             **(record.get("director") or {})}
                                            if record.get("written_by") else None)})
        return sheets

    # -- the director (Claude's song prompts) -----------------------------------------------

    @staticmethod
    def _director_key(run: Mapping[str, Any], hold: Mapping[str, Any]) -> str:
        return f"{run['workflow_id']}:{hold.get('case_id')}"

    def _direct(self, run: Mapping[str, Any], hold: Mapping[str, Any] | None) -> None:
        """Start Claude's one request for a run that waits for its songs (in the
        background; nothing waits on it)."""
        if (self.director is None or self.store is None or hold is None
                or hold.get("reason_code") != SONGS_REASON or not run.get("experiment_id")):
            return
        self.director.write_prompts(self._director_key(run, hold), lambda: self._brief(run))

    def _brief(self, run: Mapping[str, Any]) -> Brief | None:
        """The video's direction, its songs still to be made (never one already written
        or saved) and the last videos' prompts."""
        inboxes = self.paths.music_inboxes()
        dirs = [inbox for _, inbox in inboxes]
        songs = waiting_songs(dirs, self.store.track_ids(str(run["experiment_id"])))
        if not songs:
            return None
        runs = [r for r in self.store.runs() if r.get("experiment_id")]
        ids = [r["workflow_id"] for r in runs]
        earlier = runs[:ids.index(run["workflow_id"])] if run["workflow_id"] in ids else runs
        recent: list[dict[str, Any]] = []
        for other in earlier[::-1]:
            texts = recorded_prompts(dirs, self.store.track_ids(str(other["experiment_id"])))
            if texts:
                recent.append({"style": STYLE_LABELS.get(str(other.get("style")),
                                                         other.get("style")),
                               "title": other.get("title"), "prompts": texts})
            if len(recent) >= RECENT_VIDEOS:
                break
        length = run.get("length")
        return Brief(direction={
            "sub_style": STYLE_LABELS.get(str(run.get("style")), run.get("style")),
            "title": run.get("title"),
            "length_minutes": [round(s / 60) for s in length] if length else None,
            "songs": len(songs),
            "tool": next((source for source, inbox in inboxes
                          if inbox == songs[0].sheet.parent), None)},
            songs=songs, recent=recent)

    def save_song(self, stem: str, data: bytes) -> dict[str, Any]:
        self._music_inboxes()
        wanted = {s["stem"]: Path(sheet["inbox"]) for sheet in self.songs()["sheets"]
                  for s in sheet["songs"]}
        if _STEM.match(stem) is None or stem not in wanted:
            raise _refuse(404, "song_not_awaited", f"no waiting prompt sheet names {stem}.wav")
        target = wanted[stem] / f"{stem}.wav"
        if data[4:8] == b"ftyp":  # an M4A: kept as downloaded, imported as its decoded WAV
            _write_atomic(target.with_suffix(".m4a"), data)
            data = m4a_to_wav(data)
        if not (data[:4] == b"RIFF" and data[8:12] == b"WAVE"):
            raise _refuse(422, "not_a_wav", "the file is not a WAV or M4A (download it as one)")
        _write_atomic(target, data)
        try:  # P4's own check (format, cut short, accepted length), so a saved song imports
            from music_factory.providers.suno_manual import song_problem
        except ImportError:
            problem = None
        else:
            problem = song_problem(target, next(
                (s["text"] for sheet in self.songs()["sheets"] for s in sheet["songs"]
                 if s["stem"] == stem), None))
        if problem is not None:
            target.unlink()
            raise _refuse(422, "song_unusable", f"{stem}.wav {problem}")
        return {"saved": f"{stem}.wav", "bytes": len(data), "seconds": wav_seconds(target)}

    def song_file(self, stem: str) -> Path:
        """A saved song, to play in the Studio."""
        if _STEM.match(stem) is None:
            raise _refuse(404, "song_unknown", f"{stem!r} is not a song name")
        for _, inbox in self.paths.music_inboxes():
            path = inbox / f"{stem}.wav"
            if path.is_file():
                return path
            for kind in _ARCHIVES:  # a finished run's song, moved out of the inbox
                moved = sorted(inbox.glob(f"{kind}/*/{stem}.wav"))
                if moved:
                    return moved[0]
        raise _refuse(404, "song_not_saved", f"{stem}.wav is not saved")

    async def songs_done(self, case_id: str) -> list[dict[str, Any]]:
        """Resume the one run whose songs hold is ``case_id``; P4 then checks every file
        again. Only that project: another one waiting for songs is not touched (review
        2026-11-26 #17)."""
        def waiting() -> tuple[str, list[dict[str, Any]]]:
            inbox = ", ".join(str(path) for _, path in self._music_inboxes())
            return inbox, [c for c in self._cases(SONGS_REASON) if c["case_id"] == case_id]

        inbox, cases = await asyncio.to_thread(waiting)
        if not cases:
            raise _refuse(409, "no_run_waiting_for_songs",
                          f"{case_id} is not a run waiting for songs")
        return [await self._resume(c["case_id"], "production_owner",
                                   {"manual_audio_inbox_ref": f"{inbox} (uploaded in the Studio)"},
                                   "songs uploaded in the Studio") for c in cases]

    # -- thumbnails -------------------------------------------------------------------------

    def _art_folder(self, package_id: str) -> Path:
        inbox = self.paths.art_inbox
        if inbox is None:
            raise _refuse(503, "art_inbox_not_configured", "PLM_THUMBNAIL_ART_INBOX is not set")
        folder = inbox / package_id
        if _PACKAGE.match(package_id) is None or not (folder / "job.json").is_file():
            raise _refuse(404, "thumbnail_job_unknown", f"no thumbnail job for {package_id}")
        return folder

    def art_jobs(self) -> list[dict[str, Any]]:
        """Thumbnail jobs whose package still waits for the owner's art."""
        inbox = self.paths.art_inbox
        if inbox is None or not inbox.is_dir():
            return []
        jobs = []
        for job_file in sorted(inbox.glob("mpkg_*/job.json")):
            package_id = job_file.parent.name
            try:
                status = self.package(package_id).get("status")
            except Exception:  # noqa: BLE001 - an unreadable package is shown as unknown
                status = None
            if status not in ("awaiting_thumbnail_art", None):
                continue
            if (self._remakes.get(package_id) or {}).get("state") == "running":
                continue
            job = json.loads(job_file.read_text(encoding="utf-8"))
            variants = []
            for variant, letter in zip(job["variants"], _LETTERS, strict=False):
                saved = [p.name for p in job_file.parent.iterdir()
                         if p.stem.upper() == letter and p.suffix.lower() in _ART_SUFFIXES]
                variants.append({"letter": letter, "style_id": variant.get("style_id"),
                                 "text": variant.get("text"), "prompt": variant.get("prompt"),
                                 "saved": sorted(saved)})
            jobs.append({"media_package_id": package_id, "title": job.get("title"),
                         "status": status, "variants": variants,
                         "notes": [n for n in self.notes()
                                   if n.get("media_package_id") == package_id
                                   and n.get("kind") == "new_art"],
                         "reuse_from": self._reuse_candidates(package_id)})
        return jobs

    def _reuse_candidates(self, package_id: str) -> list[str]:
        """Earlier packages of the same run whose art can be copied here (a song swap
        renders a new package, which asks for art again)."""
        mine = [n for n in self.notes() if n.get("kind") == "replace_song"]
        return [str(n["media_package_id"]) for n in mine
                if n.get("media_package_id") not in (None, package_id)
                and self.paths.art_inbox is not None
                and (self.paths.art_inbox / str(n["media_package_id"])).is_dir()][-1:]

    def reuse_art(self, package_id: str, from_package_id: str) -> dict[str, Any]:
        """Copy A, B and C from an earlier package's job folder into this one."""
        folder = self._art_folder(package_id)
        source = self._art_folder(from_package_id)
        copied = []
        for letter in _LETTERS:
            try:
                data, _ = self.art_file(from_package_id, letter)
            except ActionRefused:
                continue
            copied.append(self.save_art(package_id, letter, data)["saved"])
        if not copied:
            raise _refuse(404, "art_not_saved", f"{source} holds no A, B or C to reuse")
        return {"copied": copied, "into": str(folder)}

    def art_file(self, package_id: str, letter: str) -> tuple[bytes, str]:
        folder = self._art_folder(package_id)
        for path in sorted(folder.iterdir()):
            if path.stem.upper() == letter.upper() and path.suffix.lower() in _ART_SUFFIXES:
                kind = {".png": "image/png", ".webp": "image/webp"}.get(path.suffix.lower(),
                                                                         "image/jpeg")
                return path.read_bytes(), kind
        raise _refuse(404, "art_not_saved", f"{letter} is not saved for {package_id}")

    def save_art(self, package_id: str, letter: str, data: bytes) -> dict[str, Any]:
        folder = self._art_folder(package_id)
        letter = letter.upper()
        if letter not in _LETTERS:
            raise _refuse(422, "art_letter_invalid", "the image must be A, B or C")
        suffix = _image_suffix(data)
        if suffix is None:
            raise _refuse(422, "not_an_image", "the file is not a PNG, JPEG or WebP image")
        for old in folder.iterdir():  # one file per letter: P6 reads A before a or art_a
            if old.is_file() and old.stem.upper() in (letter, f"ART_{letter}") and \
                    old.suffix.lower() in _ART_SUFFIXES:
                if old.read_bytes() == data:
                    return {"saved": old.name, "bytes": len(data)}
                kept = folder / "replaced"
                kept.mkdir(exist_ok=True)
                old.replace(kept / f"{letter}-{self.clock.now():%Y%m%dT%H%M%S}{old.suffix.lower()}")
        _write_atomic(folder / f"{letter}{suffix}", data)
        return {"saved": f"{letter}{suffix}", "bytes": len(data)}

    # -- the art library: every image ever saved for a package ------------------------------

    def art_library(self, package_id: str) -> list[dict[str, Any]]:
        """Every image in the package's job folder and its subfolders (earlier versions,
        replaced images, uploads), newest first; nothing in it is ever deleted."""
        folder = self._art_folder(package_id)
        items = []
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix.lower() in _ART_SUFFIXES:
                rel = path.relative_to(folder).as_posix()
                letter = path.stem.upper()[:1] if path.stem.upper()[:1] in _LETTERS and (
                    len(path.stem) == 1 or path.stem[1] in "-_") else None
                items.append({"path": rel, "letter": letter,
                              "label": rel if "/" in rel else f"current {rel}",
                              "modified": path.stat().st_mtime})
        return sorted(items, key=lambda i: -float(i["modified"]))

    def _library_path(self, package_id: str, rel: str) -> Path:
        folder = self._art_folder(package_id).resolve()
        path = (folder / rel).resolve()
        if folder not in path.parents or path.suffix.lower() not in _ART_SUFFIXES \
                or not path.is_file():
            raise _refuse(404, "art_not_in_library", f"{rel!r} is not an image of {package_id}")
        return path

    def library_file(self, package_id: str, rel: str) -> tuple[bytes, str]:
        path = self._library_path(package_id, rel)
        kind = {".png": "image/png", ".webp": "image/webp"}.get(path.suffix.lower(), "image/jpeg")
        return path.read_bytes(), kind

    def add_to_library(self, package_id: str, data: bytes) -> dict[str, Any]:
        """A new image for the package, kept in ``uploads/``; choosing it is a separate step."""
        folder = self._art_folder(package_id)
        suffix = _image_suffix(data)
        if suffix is None:
            raise _refuse(422, "not_an_image", "the file is not a PNG, JPEG or WebP image")
        uploads = folder / "uploads"
        uploads.mkdir(exist_ok=True)
        name = f"{self.clock.now():%Y%m%dT%H%M%S%f}{suffix}"
        _write_atomic(uploads / name, data)
        return {"path": f"uploads/{name}", "bytes": len(data)}

    async def swap_art(self, case_id: str, package_id: str, choices: dict[str, str],
                       note: str = "") -> dict[str, Any]:
        """The owner's pictures for A, B and C (each from the package's art library) replace
        the package's art: P6 re-renders the video from A and the package comes back to
        review. The run stays held for approval throughout."""
        if self.reopen is None:
            raise _refuse(503, "edit_not_configured", "P6 edits are not configured")
        reopen = self.reopen
        missing = [letter for letter in _LETTERS if not choices.get(letter)]
        if missing:
            raise _refuse(422, "art_choice_missing", f"choose a picture for {', '.join(missing)}")
        if (self._remakes.get(package_id) or {}).get("state") == "running":
            raise _refuse(409, "remake_running", f"{package_id} is already being made again")

        def swap() -> dict[str, Any]:  # off the event loop (review 2026-11-26 #20)
            case = self._review_package(case_id, package_id)
            picked = {letter: self._library_path(package_id, choices[letter]).read_bytes()
                      for letter in _LETTERS}
            for letter, data in picked.items():
                self.save_art(package_id, letter, data)
            reopen(package_id)
            return self._add_note("swap_art", note or "pictures changed in the review",
                                  case_id=case_id, media_package_id=package_id,
                                  workflow_id=(case.get("pipeline") or {}).get("workflow_id"),
                                  choices={k: choices[k] for k in _LETTERS})

        entry = await asyncio.to_thread(swap)
        # The render takes minutes: it runs on its own, so closing or refreshing the page
        # cannot lose it, and the Studio shows it until it is done.
        self._remakes[package_id] = {"since": self.clock.now().isoformat(),
                                     "state": "running", "error": None}
        task = asyncio.create_task(self._remake(package_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"remaking": package_id, "note": entry}

    async def _remake(self, package_id: str) -> None:
        try:
            await asyncio.to_thread(self.import_art, package_id, self._art_folder(package_id))
        except Exception as exc:  # noqa: BLE001 - shown in the Studio, never swallowed
            message = exc.error.message if isinstance(exc, ActionRefused) else str(exc)
            self._remakes[package_id].update(state="failed", error=message)
        else:
            self._remakes[package_id].update(state="done")

    def remakes(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._remakes.items() if v["state"] != "done"}

    async def import_thumbnails(self, package_id: str, *, pick: str | None = None,
                                wait: bool = False) -> dict[str, Any]:
        """P6's import (checks, still video, release), then resume the run waiting on it.
        ``pick`` is the letter the owner chose as the video's picture and first thumbnail;
        it is written to the art folder's ``pick.txt``, which P6's import reads.

        The still video takes minutes (about a quarter of the video's length), so the
        import runs on its own: closing or refreshing the page cannot lose it, and the
        project page shows it with its time estimate until it is done or failed.
        ``wait``: return when it is done (tests, scripts).

        A package already ``ready_for_publish`` under a thumbnail hold was imported
        before and only its resume failed (a restart, a P6 hiccup): P6 refuses a second
        import, so it is only resumed (review 2026-11-26 #15)."""
        if (self._imports.get(package_id) or {}).get("state") == "running":
            raise _refuse(409, "import_running", f"{package_id} is already being imported")

        def prepare() -> tuple[Path, list[dict[str, Any]], dict[str, Any] | None]:
            folder = self._art_folder(package_id)
            if pick is not None:
                job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
                letters = _LETTERS[:len(job.get("variants") or [])]
                if pick.upper() not in letters:
                    raise _refuse(422, "pick_unknown", f"pick one of {', '.join(letters)}")
            cases = [c for c in self._cases(ART_REASON) if package_id in self._package_ids(c)]
            in_review = [c for c in self._cases(APPROVAL_REASON)
                         if package_id in self._package_ids(c)]
            if not cases and not in_review:
                raise _refuse(409, "thumbnail_hold_not_found",
                              f"no active thumbnail hold names {package_id}")
            try:
                pkg: dict[str, Any] | None = self.package(package_id)
            except Exception:  # noqa: BLE001 - the estimate is only shown, never relied on
                pkg = None
            return folder, cases, pkg

        folder, cases, pkg = await asyncio.to_thread(prepare)
        seconds = float((pkg or {}).get("video_duration_seconds") or 0)
        imported_before = bool(cases) and (pkg or {}).get("status") == "ready_for_publish"
        if pick is not None and not imported_before:
            _write_atomic(folder / "pick.txt", pick.upper().encode())
        self._imports[package_id] = {
            "since": self.clock.now().isoformat(), "state": "running", "error": None,
            "pick": pick.upper() if pick else "A",
            "expected_seconds": 0 if imported_before
            else round(seconds * RENDER_SECONDS_PER_VIDEO_SECOND) + 60}
        work = self._import_and_resume(package_id, folder, cases,
                                       imported_before=imported_before)
        if wait:
            return await work
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return {"importing": package_id, **self._imports[package_id]}

    async def _import_and_resume(self, package_id: str, folder: Path,
                                 cases: list[dict[str, Any]], *,
                                 imported_before: bool = False) -> dict[str, Any]:
        try:
            if imported_before:
                released = {"media_package_id": package_id, "status": "ready_for_publish",
                            "imported_before": True}
            else:
                released = await asyncio.to_thread(self.import_art, package_id, folder)
            resumed: list[dict[str, Any]] = []
            waiting: set[str] = set()

            def not_ready_of(case: Mapping[str, Any]) -> list[str]:
                return [p for p in self._package_ids(case)
                        if self.package(p).get("status") != "ready_for_publish"]

            for case in cases:  # none: reopened at the review, still held for approval
                not_ready = await asyncio.to_thread(not_ready_of, case)
                if not_ready:
                    waiting.update(not_ready)
                    continue
                resumed.append(await self._resume(
                    case["case_id"], "production_owner",
                    {"thumbnail_art_inbox_ref": f"{folder} (imported in the Studio)"},
                    f"thumbnail art for {package_id} imported in the Studio"))
        except Exception as exc:
            message = exc.error.message if isinstance(exc, ActionRefused) else str(exc)
            self._imports[package_id].update(state="failed", error=message)
            raise
        self._imports[package_id].update(state="done")
        return {"package": released, "resumed": resumed, "waiting_for": sorted(waiting)}

    # -- review and approval ----------------------------------------------------------------

    @staticmethod
    def _package_ids(case: Mapping[str, Any]) -> list[str]:
        details = case.get("details") or {}
        return [str(p) for p in details.get("media_package_ids") or []]

    def review(self, case: Mapping[str, Any]) -> dict[str, Any]:
        packages: list[dict[str, Any]] = []
        for package_id in self._package_ids(case):
            try:
                pkg = self.package(package_id)
            except Exception as exc:  # noqa: BLE001 - shown, not hidden
                packages.append({"media_package_id": package_id, "error": str(exc)})
                continue
            meta = (pkg.get("metadata_candidates") or [{}])[0]
            packages.append({
                "media_package_id": package_id, "status": pkg.get("status"),
                "title": meta.get("title"), "description": meta.get("description"),
                "tags": meta.get("tags"),
                "duration_seconds": pkg.get("video_duration_seconds"),
                "video_checksum": pkg.get("video_checksum"),
                "thumbnails": [t.get("id") for t in pkg.get("thumbnails") or []],
                "art_library": self._safe_library(package_id),
                "art_prompts": self._job_prompts(package_id),
                "chapters": len(pkg.get("chapters") or []),
                "tracks": [{"song_id": c.get("song_id"), "title": c.get("title"),
                            "start": c.get("timestamp_str")}
                           for c in pkg.get("chapters") or []],
                "sounds_like": _sounds_like(pkg),
            })
        workflow_id = (case.get("pipeline") or {}).get("workflow_id")
        return {"case_id": case["case_id"], "packages": packages, "workflow_id": workflow_id,
                "notes": [n for n in self.notes() if n.get("case_id") == case["case_id"]
                          or (workflow_id and n.get("workflow_id") == workflow_id)]}

    def video_file(self, package_id: str) -> Path:
        """The package's current video as a file (after a remake, the new one, never the
        old): named by its checksum, written once from :attr:`media` (which verifies the
        bytes against the checksum) under a lock, so concurrent range requests share one
        download and the file is served from disk (review 2026-11-26 #21)."""
        import tempfile

        pkg = self.package(package_id)
        uri, checksum = str(pkg["video_master_uri"]), str(pkg["video_checksum"])
        if re.fullmatch(r"[0-9A-Za-z]{8,128}", checksum) is None:
            raise _refuse(409, "checksum_invalid", f"{package_id} has no usable video checksum")
        folder = self.video_dir or Path(tempfile.gettempdir()) / "plm-studio-video"
        target = folder / f"{checksum}.mp4"
        with self._video_lock:
            if target.is_file():
                return target
            folder.mkdir(parents=True, exist_ok=True)
            _write_atomic(target, self.media(uri, checksum))
            for old in folder.glob("*.mp4"):  # one video on disk at a time
                if old != target:
                    try:
                        old.unlink()
                    except OSError:
                        pass  # still being served (Windows); removed on a later download
            return target

    def thumbnail(self, package_id: str, variant: str) -> bytes:
        pkg = self.package(package_id)
        for thumb in pkg.get("thumbnails") or []:
            if thumb.get("id") == variant:
                return self.media(str(thumb["uri"]), str(thumb["checksum"]))
        raise _refuse(404, "thumbnail_unknown", f"{package_id} has no thumbnail {variant}")

    def _safe_library(self, package_id: str) -> list[dict[str, Any]]:
        try:
            return self.art_library(package_id)
        except ActionRefused:
            return []

    def _job_prompts(self, package_id: str) -> dict[str, Any]:
        try:
            job = json.loads((self._art_folder(package_id) / "job.json").read_text(
                encoding="utf-8"))
        except (ActionRefused, OSError, ValueError):
            return {}
        return {letter: {"style_id": v.get("style_id"), "text": v.get("text"),
                         "prompt": v.get("prompt")}
                for v, letter in zip(job.get("variants") or [], _LETTERS, strict=False)}

    def _approval_case(self, case_id: str) -> dict[str, Any]:
        for case in self._cases(APPROVAL_REASON):
            if case["case_id"] == case_id:
                return case
        raise _refuse(404, "approval_not_awaited", f"{case_id} is not an open approval")

    def _grants_path(self) -> Path:
        path = self.paths.grants_file
        if path is None:
            raise _refuse(503, "grants_file_not_configured",
                          "PLM_WRITE_AUTHORIZATIONS_FILE is not set: an approval cannot grant "
                          "the upload")
        return path

    @staticmethod
    def _read_grants(path: Path) -> list[dict[str, Any]]:
        try:
            grants = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, ValueError) as exc:
            raise _refuse(503, "grants_file_unreadable", f"{path}: {exc}") from exc
        if not isinstance(grants, list):
            raise _refuse(503, "grants_file_unreadable", f"{path}: not a JSON list")
        return grants

    def _write_grants(self, case_id: str, package_ids: list[str], workflow_id: str | None,
                      now: datetime, expires: datetime) -> list[str]:
        """This approval's YouTube grants, until ``expires``. The grant file has one grant
        per capability and no package field (``execution.load_write_authorizations``), so
        a grant cannot name the package: it is kept short, names the run in its
        reference and is removed once that run has published (:meth:`_revoke_spent_grants`).
        A manual grant that outlasts this one is kept, not replaced; the capabilities kept
        that way are returned (review 2026-11-26 #18)."""
        path = self._grants_path()
        with self._grants_lock:
            grants = self._read_grants(path)
            kept_manual = sorted(
                str(g["capability"]) for g in grants
                if g.get("capability") in APPROVAL_CAPABILITIES
                and not str(g.get("reference") or "").startswith(_STUDIO_GRANT)
                and _expires_after(g, expires))
            out = [g for g in grants if g.get("capability") not in APPROVAL_CAPABILITIES
                   or g.get("capability") in kept_manual]
            reference = (f"{_STUDIO_GRANT}{case_id} of {', '.join(package_ids)} "
                         f"for run {workflow_id} at {now.isoformat()}")
            out += [{"capability": cap, "granted_by": OWNER, "granted_at": now.isoformat(),
                     "expires_at": expires.isoformat(), "reference": reference}
                    for cap in APPROVAL_CAPABILITIES if cap not in kept_manual]
            _write_atomic(path, (json.dumps(out, indent=2) + "\n").encode("utf-8"))
        return kept_manual

    def _revoke_spent_grants(self, states: Mapping[str, str]) -> None:
        """Remove the approval grants whose run has published or ended (``states``:
        workflow id -> run state). Other grants, manual ones included, stay."""
        path = self.paths.grants_file
        if path is None or not path.is_file():
            return
        with self._grants_lock:
            try:
                grants = self._read_grants(path)
            except ActionRefused:
                return  # the page must still load; the grant still expires on its own

            def spent(grant: Mapping[str, Any]) -> bool:
                found = _GRANT_RUN.search(str(grant.get("reference") or ""))
                return (grant.get("capability") in APPROVAL_CAPABILITIES
                        and str(grant.get("reference") or "").startswith(_STUDIO_GRANT)
                        and found is not None and states.get(found.group(1)) in FINISHED)

            left = [g for g in grants if not spent(g)]
            if len(left) != len(grants):
                _write_atomic(path, (json.dumps(left, indent=2) + "\n").encode("utf-8"))

    async def approve(self, case_id: str,
                      shown: Mapping[str, str] | None = None) -> dict[str, Any]:
        """The owner's approval: the exception queue decides the resume first; only then
        are this upload's grants written, and only then is the run signalled. A refused
        resume grants nothing (review 2026-11-26 #18).

        ``shown`` is ``{package_id: video_checksum}`` of the videos the page played. If a
        package's video changed since (a remake finished), the approval is refused: the
        owner approves only what they watched."""
        def check() -> tuple[dict[str, Any], list[str]]:
            case = self._approval_case(case_id)
            package_ids = self._package_ids(case)
            current = {p: self.package(p) for p in package_ids}
            waiting = [p for p, pkg in current.items()
                       if pkg.get("status") != "ready_for_publish"]
            if waiting:
                raise _refuse(409, "package_not_ready",
                              f"{', '.join(waiting)} is not ready (new art still to import)")
            if shown is not None:
                changed = [p for p, pkg in current.items()
                           if shown.get(p) != pkg.get("video_checksum")]
                if changed:
                    raise _refuse(409, "video_changed",
                                  "the video changed since this page showed it (a remake "
                                  "finished): reload, watch the new video, then approve")
            self._read_grants(self._grants_path())  # refused before the decision, not after
            return case, package_ids

        case, package_ids = await asyncio.to_thread(check)
        workflow_id = (case.get("pipeline") or {}).get("workflow_id")
        now = self.clock.now()
        expires = now + timedelta(hours=APPROVAL_GRANT_HOURS)
        kept: list[str] = []

        def grant() -> None:
            kept.extend(self._write_grants(case_id, package_ids, workflow_id, now, expires))

        answer = await self._resume(
            case_id, "production_owner",
            {"publish_approval_ref": f"Studio approval of {', '.join(package_ids)}; "
                                     f"YouTube grants until {expires.isoformat()}"},
            "approved for upload in the Studio", before_signal=grant)
        return {**answer, "grants_expire_at": expires.isoformat(), "grants_kept": kept}

    # -- change requests (at the review) -----------------------------------------------------

    def _review_package(self, case_id: str, package_id: str) -> dict[str, Any]:
        case = self._approval_case(case_id)
        if package_id not in self._package_ids(case):
            raise _refuse(404, "package_not_in_review",
                          f"{package_id} is not part of approval {case_id}")
        return case

    def edit_text(self, case_id: str, package_id: str, *, title: str | None,
                  description: str | None, tags: list[str] | None,
                  note: str = "") -> dict[str, Any]:
        """A new title, description or tags: applied at once; the package stays in review."""
        case = self._review_package(case_id, package_id)
        if self.edit is None:
            raise _refuse(503, "edit_not_configured", "P6 edits are not configured")
        changed = self.edit(package_id, {"title": title, "description": description,
                                         "tags": tags})
        self._add_note("edit_text", note or "title/description/tags edited",
                       case_id=case_id, media_package_id=package_id,
                       workflow_id=(case.get("pipeline") or {}).get("workflow_id"))
        return changed

    def choose_thumbnail(self, case_id: str, package_id: str, thumbnail_id: str,
                         note: str = "") -> dict[str, Any]:
        """Offer another thumbnail first (the video picture stays image A)."""
        case = self._review_package(case_id, package_id)
        if self.edit is None:
            raise _refuse(503, "edit_not_configured", "P6 edits are not configured")
        changed = self.edit(package_id, {"thumbnail_id": thumbnail_id})
        self._add_note("choose_thumbnail", note or f"thumbnail {thumbnail_id} first",
                       case_id=case_id, media_package_id=package_id,
                       workflow_id=(case.get("pipeline") or {}).get("workflow_id"))
        return changed

    def request_new_art(self, case_id: str, package_id: str, note: str) -> dict[str, Any]:
        """Back to the thumbnail step for this package; the run stays held at approval."""
        case = self._review_package(case_id, package_id)
        if self.reopen is None:
            raise _refuse(503, "edit_not_configured", "P6 edits are not configured")
        note = note.strip() or "new art (no note)"
        reopened = self.reopen(package_id)
        entry = self._add_note("new_art", note, case_id=case_id, media_package_id=package_id,
                               workflow_id=(case.get("pipeline") or {}).get("workflow_id"))
        return {"package": reopened, "note": entry}

    async def replace_songs(self, case_id: str, song_ids: list[str],
                            note: str) -> dict[str, Any]:
        """Take songs out: the run goes back to assembly, which asks P3 for as many new
        songs (the songs step), then renders a new package."""
        case = await asyncio.to_thread(self._approval_case, case_id)  # review 2026-11-26 #20
        note = note.strip() or "replace these songs (no note)"
        members = {str(c) for p in (case.get("details") or {}).get("packages") or []
                   for c in p.get("candidate_ids") or []}
        chosen = sorted({str(s) for s in song_ids})
        unknown = [s for s in chosen if s not in members]
        if not chosen or unknown:
            raise _refuse(422, "song_not_in_package",
                          f"choose songs of this package (unknown: {', '.join(unknown)})")
        workflow_id = (case.get("pipeline") or {}).get("workflow_id")
        entry = await asyncio.to_thread(
            self._add_note, "replace_song", note, case_id=case_id, workflow_id=workflow_id,
            song_ids=chosen, media_package_id=next(iter(self._package_ids(case)), None))
        answer = await self._resume(
            case_id, "production_owner",
            {"publish_approval_ref": f"changes requested in the Studio: replace "
                                     f"{', '.join(chosen)}"},
            f"replace song(s) {', '.join(chosen)}: {note.strip()}",
            rewind_to="assemble", exclude_candidates=tuple(chosen))
        return {**answer, "note": entry}

    async def reject(self, case_id: str) -> dict[str, Any]:
        await asyncio.to_thread(self._approval_case, case_id)
        return await self.actions.reject(case_id, actor=OWNER, role="production_owner",
                                         notes="rejected in the Studio")

    # -- projects: every run, numbered, with every step kept ----------------------------------

    def _owner_roles(self) -> set[str]:
        reviewers = getattr(self.actions, "reviewers", None)
        return set(reviewers.roles_of(OWNER)) if reviewers is not None else set()

    def _holds_by_run(self) -> dict[str, dict[str, Any]]:
        return {str((c.get("pipeline") or {}).get("workflow_id")): c
                for c in self._live_holds()}

    def projects(self) -> list[dict[str, Any]]:
        """Every project, newest first, with what its list row shows."""
        if self.store is None:
            return []
        holds, roles = self._holds_by_run(), self._owner_roles()
        listed = []
        runs = number_runs(self.store.runs())
        self._revoke_spent_grants({r["workflow_id"]: str(r["state"]) for r in runs})
        stats = self._stats([r["youtube"]["video_id"] for r in runs if r.get("youtube")])
        for run in runs:
            if run.get("youtube"):
                run["youtube"] = {**run["youtube"],
                                  "stats": stats.get(run["youtube"]["video_id"])}
            hold = holds.get(run["workflow_id"]) if run["state"] == "HELD" else None
            self._direct(run, hold)
            explained = None if hold is None else explain_hold(hold, owner_roles=roles)
            yours = bool(explained and explained["your_turn"])
            listed.append({
                "number": run["number"], "workflow_id": run["workflow_id"],
                "title": run.get("title"), "style": run.get("style"),
                "style_label": STYLE_LABELS.get(str(run.get("style")), run.get("style")),
                "length": run.get("length"), "duration_seconds": run.get("duration_seconds"),
                "state": run["state"],
                "label": state_label(run["state"], None if hold is None
                                     else hold.get("reason_code")),
                "your_turn": yours,
                "problem": run["state"] == "HELD" and not yours,
                "finished": run["state"] in FINISHED,
                "started": _iso(run["created_at"]), "updated": _iso(run["updated_at"]),
                "youtube": run.get("youtube"),
                "working": self._busy(run.get("media_package_id")),
            })
        return listed[::-1]

    def _stats(self, video_ids: list[str]) -> dict[str, dict[str, int]]:
        """Views, likes and comments per published video, at most ten minutes old; empty
        when YouTube cannot be read (the list still shows, without numbers)."""
        import time

        if self.video_stats is None or not video_ids:
            return {}
        cached = self._stats_cache
        if cached and time.monotonic() - cached[0] < 600 and set(video_ids) <= set(cached[1]):
            return cached[1]
        try:
            found = self.video_stats(sorted(set(video_ids)))
        except Exception:  # noqa: BLE001 - counters are optional; the page must still load
            return cached[1] if cached else {}
        self._stats_cache = (time.monotonic(), found)
        return found

    def _busy(self, package: Any) -> dict[str, Any] | None:
        """Work this dashboard runs for the package now (a picture import or remake)."""
        if not package:
            return None
        for kind, table in (("import", self._imports), ("remake", self._remakes)):
            job = table.get(str(package))
            if job and job.get("state") in ("running", "failed"):
                return {"kind": kind, "media_package_id": package, **job}
        return None

    def _run_of(self, number: int) -> dict[str, Any]:
        if self.store is None:
            raise _refuse(503, "projects_unavailable", "the project store is not configured")
        for run in number_runs(self.store.runs()):
            if run["number"] == number:
                return run
        raise _refuse(404, "project_unknown", f"there is no project #{number}")

    def project(self, number: int) -> dict[str, Any]:
        """One project's page: its status and stop, its log, and every step's content
        (songs, pictures, video, upload), whether done or still to do."""
        run = self._run_of(number)
        self._revoke_spent_grants({run["workflow_id"]: str(run["state"])})
        if run.get("youtube"):
            video_id = run["youtube"]["video_id"]
            run["youtube"] = {**run["youtube"], "stats": self._stats([video_id]).get(video_id)}
        hold = self._holds_by_run().get(run["workflow_id"]) if run["state"] == "HELD" else None
        explained = None if hold is None else explain_hold(hold, owner_roles=self._owner_roles())
        reason = None if hold is None else hold.get("reason_code")
        self._direct(run, hold)
        experiment = run.get("experiment_id")
        tracks = set(self.store.track_ids(experiment)) if experiment else set()
        sheets: list[dict[str, Any]] = []
        for source, inbox in self.paths.music_inboxes():
            sheets += self._sheets(source, inbox, tracks=tracks, include_done=True)
        packages = self.store.packages(experiment) if experiment else []
        # Only versions that asked for pictures (an art job): a failed render never did.
        art = [job for job in (self._project_art(str(p["media_package_id"]), str(p["status"]))
                               for p in packages) if job["variants"]]
        latest = packages[0]["media_package_id"] if packages else None
        video = self._project_video(str(latest)) if latest else None
        # Step 2's controls whenever the package waits for art and nothing runs for it,
        # also at the review: a failed or lost remake (in memory only) must still leave
        # an Import button (review 2026-11-26 #16).
        reopened = (reason == APPROVAL_REASON and bool(packages)
                    and packages[0].get("status") == "awaiting_thumbnail_art"
                    and all((table.get(str(latest)) or {}).get("state") != "running"
                            for table in (self._imports, self._remakes)))
        return {
            "number": number, "workflow_id": run["workflow_id"], "title": run.get("title"),
            "style": run.get("style"),
            "style_label": STYLE_LABELS.get(str(run.get("style")), run.get("style")),
            "length": run.get("length"), "visual_world": run.get("visual_world"),
            "state": run["state"], "label": state_label(run["state"], reason),
            "started": _iso(run["created_at"]), "updated": _iso(run["updated_at"]),
            "finished": run["state"] in FINISHED,
            "your_turn": bool(explained and explained["your_turn"]),
            "problem": run["state"] == "HELD" and not (explained and explained["your_turn"]),
            "hold": explained,
            "log": plain_log(self.store.transitions(run["workflow_id"])),
            "songs": {"sheets": sheets, "waiting": reason == SONGS_REASON,
                      "director": None if self.director is None else {
                          "enabled": self.director.enabled,
                          "writing": hold is not None and self.director.writing(
                              self._director_key(run, hold))}},
            "art": art, "art_waiting": reason == ART_REASON or reopened,
            "video": video,
            "review": self.review(hold) if reason == APPROVAL_REASON and hold else None,
            "can_replace_songs": reason in (ART_REASON, APPROVAL_REASON) and bool(video),
            "youtube": run.get("youtube"),
            "working": self._busy(latest),
            "notes": [n for n in self.notes() if n.get("workflow_id") == run["workflow_id"]],
        }

    def _project_art(self, package_id: str, status: str) -> dict[str, Any]:
        inbox = self.paths.art_inbox
        folder = None if inbox is None else inbox / package_id
        variants: list[dict[str, Any]] = []
        if folder is not None and (folder / "job.json").is_file():
            job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
            for variant, letter in zip(job.get("variants") or [], _LETTERS, strict=False):
                saved = sorted(p.name for p in folder.iterdir()
                               if p.stem.upper() == letter and p.suffix.lower() in _ART_SUFFIXES)
                variants.append({"letter": letter, "style_id": variant.get("style_id"),
                                 "text": variant.get("text"), "prompt": variant.get("prompt"),
                                 "saved": saved})
        return {"media_package_id": package_id, "status": status, "variants": variants,
                "import": self._imports.get(package_id), "remake": self._remakes.get(package_id),
                "reuse_from": self._reuse_candidates(package_id) if variants else []}

    def _project_video(self, package_id: str) -> dict[str, Any] | None:
        try:
            pkg = self.package(package_id)
        except Exception as exc:  # noqa: BLE001 - shown, not hidden
            return {"media_package_id": package_id, "error": str(exc)}
        meta = (pkg.get("metadata_candidates") or [{}])[0]
        return {"media_package_id": package_id, "status": pkg.get("status"),
                "title": meta.get("title"), "description": meta.get("description"),
                "duration_seconds": pkg.get("video_duration_seconds"),
                "has_video": pkg.get("status") in ("ready_for_publish", "published"),
                "video_checksum": pkg.get("video_checksum"),
                "thumbnails": [t.get("id") for t in pkg.get("thumbnails") or []],
                "tracks": [{"song_id": c.get("song_id"), "title": c.get("title"),
                            "start": c.get("timestamp_str")}
                           for c in pkg.get("chapters") or []],
                "sounds_like": _sounds_like(pkg)}

    async def cancel_project(self, number: int, reason: str = "") -> dict[str, Any]:
        """Stop a project for good (the run is cancelled; nothing more is made or sent)."""
        run = await asyncio.to_thread(self._run_of, number)  # review 2026-11-26 #20
        if run["state"] in FINISHED:
            raise _refuse(409, "project_finished", f"project #{number} has already ended")
        if self.cancel is None:
            raise _refuse(503, "cancel_not_configured", "cancelling runs is not configured")
        why = reason.strip() or "cancelled in the Studio"
        await self.cancel(run["workflow_id"], why)
        await asyncio.to_thread(self._add_note, "cancel", why, workflow_id=run["workflow_id"])
        return {"cancelled": number, "workflow_id": run["workflow_id"]}

    async def retry_hold(self, case_id: str) -> dict[str, Any]:
        """Continue past a stop the owner has read (a refused step, short supply, the
        spending guard): the evidence the hold names is the Studio's record that the owner
        saw the reason and chose to try again. The exception queue still decides."""
        def read() -> tuple[dict[str, Any] | None, set[str]]:
            found = next((c for c in self._live_holds() if c.get("case_id") == case_id), None)
            return found, self._owner_roles()

        case, roles = await asyncio.to_thread(read)
        if case is None:
            raise _refuse(404, "hold_not_open", f"{case_id} is not an open stop")
        explained = explain_hold(case, owner_roles=roles)
        if explained["action"] != "retry":
            raise _refuse(409, "not_retryable",
                          f"{case_id} is answered on its own step, or the Studio cannot "
                          "continue it (ask Claude)")
        if not explained["owner_may_act"]:
            raise _refuse(403, "owner_lacks_role", str(explained["blocked_because"]))
        said = (f"Studio: the owner read the stop ({explained['detail'][:300]}) and chose to "
                f"try again at {self.clock.now().isoformat()}")
        evidence = {str(key): said for key in case.get("required_evidence") or []}
        return await self._resume(case_id, str(case.get("reviewer_role")), evidence,
                                  "try again from the Studio")

    async def replace_project_songs(self, number: int, song_ids: list[str],
                                    note: str) -> dict[str, Any]:
        """Take songs out at the thumbnail step or the review: the run goes back to make as
        many new ones (the songs step), then builds the video again."""
        def read() -> tuple[dict[str, Any], dict[str, Any] | None]:
            run = self._run_of(number)
            return run, self._holds_by_run().get(run["workflow_id"])

        run, hold = await asyncio.to_thread(read)  # review 2026-11-26 #20
        reason = None if hold is None else hold.get("reason_code")
        if hold is None or reason not in (ART_REASON, APPROVAL_REASON):
            raise _refuse(409, "songs_not_replaceable",
                          "songs can be replaced while the project waits for its pictures "
                          "or for your approval")
        if reason == APPROVAL_REASON:
            return await self.replace_songs(str(hold["case_id"]), song_ids, note)
        package_id = next(iter(self._package_ids(hold)), None)
        members: set[str] = set()
        if package_id:
            chapters = (await asyncio.to_thread(self.package, package_id)).get("chapters")
            members = {str(c.get("song_id")) for c in chapters or []}
        chosen = sorted({str(x) for x in song_ids})
        unknown = [x for x in chosen if x not in members]
        if not chosen or unknown:
            raise _refuse(422, "song_not_in_package",
                          f"choose songs of this video (unknown: {', '.join(unknown)})")
        note = note.strip() or "replace these songs (no note)"
        entry = await asyncio.to_thread(
            self._add_note, "replace_song", note, case_id=hold["case_id"],
            workflow_id=run["workflow_id"], song_ids=chosen, media_package_id=package_id)
        answer = await self._resume(
            str(hold["case_id"]), str(hold.get("reviewer_role") or "production_owner"),
            {str(key): f"Studio: the owner replaced {', '.join(chosen)} at the thumbnail step"
             for key in hold.get("required_evidence") or []},
            f"replace song(s) {', '.join(chosen)}: {note}",
            rewind_to="assemble", exclude_candidates=tuple(chosen))
        return {**answer, "note": entry}

    async def start_run(self, music: str, subgenre: str | None = None,
                        length: str = "90_120m", request_id: str | None = None) -> Any:
        """Start a run whose song prompts are for ``music`` (``suno`` or ``flow_music``);
        its inbox must be configured, or nobody could hand the songs in. ``subgenre`` is
        the owner's sub-style, one P2 has an arm for; ``None`` lets P2 pick.
        ``request_id`` is the page's id of this Start (one per form): a repeated click
        sends the same id, so P2 answers with the first cycle (review 2026-11-26 #14)."""
        if self.start is None:
            raise _refuse(503, "start_not_configured", "starting runs is not configured")
        if length not in LENGTH_CHOICES and minutes_length(length) is None:
            raise _refuse(422, "length_unknown",
                          f"type whole minutes from {MINUTES_RANGE[0]} to {MINUTES_RANGE[1]} "
                          f"(e.g. 95m) or one of {', '.join(LENGTH_CHOICES)}, not {length!r}")
        if music not in MUSIC_PROVIDERS:
            raise _refuse(422, "music_tool_unknown",
                          f"choose one of {sorted(MUSIC_PROVIDERS)}, not {music!r}")
        source = {"suno": "Suno", "flow_music": "Flow Music"}[music]
        if source not in dict(self.paths.music_inboxes()):
            raise _refuse(503, "music_inbox_not_configured",
                          f"the {source} inbox is not configured: set "
                          f"{'PLM_SUNO_MANUAL_INBOX' if music == 'suno' else 'PLM_FLOW_MUSIC_MANUAL_INBOX'}")
        styles = await asyncio.to_thread(self.style_choices)  # review 2026-11-26 #20
        if subgenre is not None:
            available = styles.get("available") or []
            if subgenre not in available:
                raise _refuse(422, "style_unavailable",
                              f"P2 has no usable arm for {subgenre!r}; choose one of "
                              f"{', '.join(available) or 'none'}, or let P2 pick")
        avoid: tuple[str, ...] = ()
        if subgenre is None:  # P2 picks: away from the last videos' sub-styles
            seen: list[str] = []
            for item in styles.get("recent") or []:
                name = item.get("subgenre") if isinstance(item, dict) else None
                if name and name not in seen:
                    seen.append(name)
            avoid = tuple(seen[:STYLE_MEMORY])
        extra = {} if request_id is None else {"request_id": request_id}
        started = await self.start(MUSIC_PROVIDERS[music], subgenre, avoid, length, **extra)
        self._styles_cache = None  # the new run is now one of the last videos
        return started


def build_studio(platform: Any, live: Any, actions: ExceptionActions, apis: Any,
                 urls: Mapping[Any, str], *, clock: Clock | None = None,
                 dotenv_path: str | Path = ".env") -> Studio:
    """The Studio over the deployment: P6 through the phase API, media from the object
    store, P6's importer in process (FFmpeg on this host) and runs started through P2
    and Temporal. ``platform=None``: every action is refused by its collaborator."""
    from ..storage import ObjectNotFoundError, digest_from_key, from_uri, get_verified
    from ..temporal import Phase

    # One engine (one pool) for every Studio read and P6 change, not one per call
    # (review 2026-11-26, P2).
    engine: Any = None
    if platform is not None:
        from ..db import build_engine

        engine = build_engine(platform)

    def runs() -> list[dict[str, Any]]:
        panel = live.view("production-queue")["panels"]["runs"]
        if panel.get("status") != "ok":
            raise _refuse(503, "runs_unavailable", str(panel.get("reason")))
        return list(panel["data"]["active"])

    def open_holds() -> list[dict[str, Any]]:
        panel = live.exceptions()["panels"]["open"]
        if panel.get("status") != "ok":
            raise _refuse(503, "holds_unavailable", str(panel.get("reason")))
        return list(panel["data"]["items"])

    def package(package_id: str) -> dict[str, Any]:
        if _PACKAGE.match(package_id) is None:
            raise _refuse(404, "package_unknown", f"{package_id!r} is not a package id")
        answer: dict[str, Any] = apis.get_json(Phase.MEDIA, f"/media/packages/{package_id}")
        return answer

    def media(uri: str, checksum: str) -> bytes:
        key = from_uri(uri)
        if digest_from_key(key) != checksum:
            raise _refuse(409, "checksum_mismatch", f"{uri} does not pin {checksum}")
        try:
            return get_verified(key)
        except ObjectNotFoundError as exc:
            raise _refuse(410, "media_archived",
                          "this file was moved to the backup drive after the upload "
                          "(E:\\PLM-backups\\videos)") from exc

    def import_art(package_id: str, folder: Path) -> dict[str, Any]:
        if platform is None:
            raise _refuse(503, "settings_unavailable", "the platform settings did not load")
        from media_factory.artifacts import MinioArtifactStore
        from media_factory.errors import MediaAssemblyError
        from media_factory.rendering.ffmpeg import FFmpegToolchain
        from media_factory.thumbnail_art.importer import import_owner_art

        from ..db import session_factory

        with session_factory(engine)() as session:
            try:
                pkg = import_owner_art(session, MinioArtifactStore(platform),
                                       FFmpegToolchain(), package_id, folder,
                                       now=(clock or SystemClock()).now())
            except MediaAssemblyError as exc:
                session.rollback()
                raise _refuse(422, exc.code, str(exc)) from exc
            session.commit()
        return {"media_package_id": pkg.media_package_id, "status": pkg.status.value,
                "video_master_uri": pkg.video_master_uri}

    def _p6(change: Callable[..., Any], package_id: str, **kwargs: Any) -> dict[str, Any]:
        if platform is None:
            raise _refuse(503, "settings_unavailable", "the platform settings did not load")
        from media_factory.errors import MediaAssemblyError

        from ..db import session_factory

        with session_factory(engine)() as session:
            try:
                pkg = change(session, package_id, now=(clock or SystemClock()).now(),
                             **kwargs)
            except MediaAssemblyError as exc:
                session.rollback()
                raise _refuse(422, exc.code, str(exc)) from exc
            session.commit()
        return {"media_package_id": pkg.media_package_id, "status": pkg.status.value}

    def edit(package_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        from media_factory.review_edits import edit_presentation

        return _p6(edit_presentation, package_id, **changes)

    def reopen(package_id: str) -> dict[str, Any]:
        from media_factory.review_edits import reopen_art

        return _p6(reopen_art, package_id)

    start = cancel = None
    store = None

    def video_stats(video_ids: list[str]) -> dict[str, dict[str, int]]:
        from youtube_publishing.client.real import RealYouTubeClient

        client = RealYouTubeClient()
        if not client.is_configured:
            return {}
        return client.video_statistics(video_ids)

    if platform is not None:
        from .projects import ProjectStore

        store = ProjectStore(engine)
    if platform is not None and platform.service_token is not None:
        token = platform.service_token.get_secret_value()

        start_times: dict[str, Any] = {}
        """Review 2026-11-26 #14: a repeated Start (same request id) asks P2 at the first
        click's instant, so P2 replays that cycle instead of refusing a changed as_of."""

        async def start(music_provider: str, subgenre: str | None,
                        avoid: tuple[str, ...], length: str,
                        request_id: str | None = None) -> dict[str, Any]:
            from dataclasses import asdict

            from ..pipeline.config import load_pipeline_config
            from ..pipeline.start import RunNotStarted, start_new_run
            from ..pipeline.types import PipelineOptions
            from ..temporal import connect_client

            config = load_pipeline_config(dotenv_path=dotenv_path)
            policy = config.learning.policy_version if config.learning else "p2-v1"
            env = load_env_file(dotenv_path)
            brand = (os.environ.get("PLM_STUDIO_BRAND_ID") or env.get("PLM_STUDIO_BRAND_ID")
                     or "longform_signal")
            try:
                started = await start_new_run(
                    await connect_client(platform), ar_url=urls[Phase.AR], token=token,
                    brand_id=brand, policy_version=policy,
                    now=(start_times.setdefault(request_id, (clock or SystemClock()).now())
                         if request_id else (clock or SystemClock()).now()),
                    options=PipelineOptions(music_provider=music_provider),
                    subgenre=subgenre, avoid_subgenres=avoid, duration_bucket=length,
                    request_id=request_id)
            except RunNotStarted as exc:
                raise _refuse(409, exc.code, str(exc)) from exc
            return {**asdict(started), "music_provider": music_provider,
                    "subgenre": subgenre, "avoided": list(avoid), "length": length}

        async def cancel(workflow_id: str, reason: str) -> None:
            from ..pipeline.types import CancelSignal
            from ..pipeline.workflow import PipelineWorkflow
            from ..temporal import connect_client

            client = await connect_client(platform)
            await client.get_workflow_handle(workflow_id).signal(
                PipelineWorkflow.cancel, CancelSignal(actor=OWNER, reason=reason))

    def styles() -> dict[str, Any]:
        if platform is None:
            raise _refuse(503, "settings_unavailable", "the platform settings did not load")
        import sqlalchemy as sa

        brand = (os.environ.get("PLM_STUDIO_BRAND_ID")
                 or load_env_file(dotenv_path).get("PLM_STUDIO_BRAND_ID") or "longform_signal")
        with engine.connect() as conn:  # the shared engine (review 2026-11-26, P2)
            available = conn.execute(sa.text(
                "select distinct m.taxonomy_json::json->>'subgenre' "
                "from market.market_arms m "
                "left join ar.ar_content_arm_state s on s.arm_id = m.arm_id "
                "where m.brand_id = :brand "
                "and m.taxonomy_json::json->>'subgenre' is not null "
                "and coalesce(s.status, 'candidate') in ('candidate', 'active') "
                "order by 1"), {"brand": brand}).scalars().all()
            # P3's record: it knows the sub-style it chose for an arm naming none.
            recent = conn.execute(sa.text(
                "select g.subgenre, e.created_at from experiments.experiments e "
                "join experiments.content_genomes g "
                "on g.fingerprint = e.base_genome_fingerprint "
                "where g.genre = 'focus_electronic' and g.subgenre is not null "
                "and e.created_at <= now() "
                "order by e.created_at desc limit 5")).all()
        # Review 2026-11-26 #37: P2 says which sub-styles are only cooling down, so the
        # Start menu can say so instead of a pick that P2 then relaxes or refuses.
        cooling: dict[str, str | None] = {}
        try:
            answer = apis.get_json(Phase.AR, "/ar/subgenres", params={
                "brand_id": brand, "as_of": (clock or SystemClock()).now().isoformat()})
            for entry in answer if isinstance(answer, list) else []:
                if isinstance(entry, dict) and entry.get("cooling") and entry.get("subgenre"):
                    cooling[str(entry["subgenre"])] = entry.get("cooling_until")
        except Exception:  # noqa: BLE001 - the menu still works without the marks
            cooling = {}
        return {"available": [s for s in available if s and s != "none"],
                "recent": [{"subgenre": r[0], "at": r[1].isoformat()} for r in recent],
                "cooling": cooling}

    return Studio(paths=StudioPaths.from_env(dotenv_path=dotenv_path), actions=actions,
                  open_holds=open_holds, package=package, media=media, runs=runs,
                  import_art=import_art, start=start, styles=styles, edit=edit,
                  reopen=reopen, store=store, cancel=cancel, video_stats=video_stats,
                  clock=clock or SystemClock(),
                  director=director_from_env(dotenv_path=dotenv_path, clock=clock))
