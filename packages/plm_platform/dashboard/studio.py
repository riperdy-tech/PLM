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
  title, description). Approve writes the owner's YouTube grants for this upload,
  expiring in :data:`APPROVAL_GRANT_HOURS` hours, and resumes the run, which uploads it.
  Reject ends the run.

Every resume goes through :class:`~.actions.ExceptionActions` as :data:`OWNER` (who must
hold the hold's reviewer role in the dashboard's reviewer file), with the evidence the
hold class names, so the queue records who decided and on what.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

from ..clock import Clock, SystemClock
from ..dotenv import load_env_file
from .actions import ActionRefused, ExceptionActions

__all__ = ["APPROVAL_GRANT_HOURS", "OWNER", "Studio", "StudioPaths", "build_studio"]

OWNER: Final[str] = "owner"
APPROVAL_GRANT_HOURS: Final[int] = 2
APPROVAL_CAPABILITIES: Final[tuple[str, ...]] = (
    "youtube.upload", "youtube.thumbnail", "youtube.playlist")
SONGS_REASON: Final[str] = "MANUAL_AUDIO_AWAITED"
ART_REASON: Final[str] = "THUMBNAIL_ART_AWAITED"
APPROVAL_REASON: Final[str] = "PUBLISH_APPROVAL_AWAITED"
_STEM: Final = re.compile(r"\A[A-Za-z0-9_-]{1,120}\Z")
_PACKAGE: Final = re.compile(r"\Ampkg_[0-9a-f]{8,64}\Z")
_SAVE_AS: Final = re.compile(r"^Save as: ([A-Za-z0-9_-]+)\.wav$", re.MULTILINE)
_ART_SUFFIXES: Final = (".png", ".jpg", ".jpeg", ".webp")
_LETTERS: Final = ("A", "B", "C")


def _env(name: str, env: Mapping[str, str], dotenv: Mapping[str, str]) -> str | None:
    value = (env.get(name) or dotenv.get(name) or "").strip()
    return value or None


@dataclass(frozen=True, slots=True)
class StudioPaths:
    music_inbox: Path | None
    art_inbox: Path | None
    grants_file: Path | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None,
                 dotenv_path: str | Path = ".env") -> StudioPaths:
        """The inboxes and grant file the services use (process env, then ``.env``). The
        Flow Music inbox wins over the Suno one, as in P4."""
        process = os.environ if env is None else env
        dotenv = load_env_file(dotenv_path)
        music = (_env("PLM_FLOW_MUSIC_MANUAL_INBOX", process, dotenv)
                 or _env("PLM_SUNO_MANUAL_INBOX", process, dotenv))
        art = _env("PLM_THUMBNAIL_ART_INBOX", process, dotenv)
        grants = _env("PLM_WRITE_AUTHORIZATIONS_FILE", process, dotenv)
        return cls(music_inbox=None if music is None else Path(music),
                   art_inbox=None if art is None else Path(art),
                   grants_file=None if grants is None else Path(grants))


def _refuse(status: int, code: str, message: str) -> ActionRefused:
    return ActionRefused(status, code, message)


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_bytes(data)
    tmp.replace(path)


def _image_suffix(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


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
    start: Callable[[], Any] | None = None
    """Starts a new run (async); ``None``: the Start button is refused."""
    clock: Clock = field(default_factory=SystemClock)
    _video_cache: dict[str, bytes] = field(default_factory=dict)

    # -- holds ------------------------------------------------------------------------------

    def _cases(self, reason: str) -> list[dict[str, Any]]:
        return [c for c in self.open_holds() if c.get("reason_code") == reason]

    def overview(self) -> dict[str, Any]:
        holds = self.open_holds()
        return {
            "waiting": {reason: [c["case_id"] for c in holds if c.get("reason_code") == reason]
                        for reason in (SONGS_REASON, ART_REASON, APPROVAL_REASON)},
            "songs": self.songs(),
            "thumbnails": self.art_jobs(),
            "reviews": [self.review(c) for c in holds if c.get("reason_code") == APPROVAL_REASON],
            "start_available": self.start is not None,
        }

    async def _resume(self, case_id: str, role: str, evidence: dict[str, str],
                      notes: str) -> dict[str, Any]:
        return await self.actions.resume(case_id, actor=OWNER, role=role, evidence=evidence,
                                         notes=notes)

    # -- songs ------------------------------------------------------------------------------

    def _music_inbox(self) -> Path:
        if self.paths.music_inbox is None or not self.paths.music_inbox.is_dir():
            raise _refuse(503, "music_inbox_not_configured",
                          "PLM_FLOW_MUSIC_MANUAL_INBOX is not set or not a folder")
        return self.paths.music_inbox

    def songs(self) -> dict[str, Any]:
        """Every prompt sheet in the music inbox with a song still missing."""
        if self.paths.music_inbox is None or not self.paths.music_inbox.is_dir():
            return {"configured": False, "sheets": []}
        inbox = self.paths.music_inbox
        sheets = []
        for sheet in sorted(inbox.glob("*.prompts.txt")):
            text = sheet.read_text(encoding="utf-8")
            blocks = text.split("--- Song ")
            header, songs = blocks[0].strip(), []
            for block in blocks[1:]:
                match = _SAVE_AS.search(block)
                if match is None:
                    continue
                stem = match.group(1)
                songs.append({"stem": stem, "text": f"--- Song {block}".strip(),
                              "saved": (inbox / f"{stem}.wav").is_file(),
                              "lyrics_needed": "Instrumental: no" in block,
                              "lyrics_saved": (inbox / f"{stem}.lyrics.txt").is_file()})
            if songs and not all(s["saved"] for s in songs):
                sheets.append({"sheet": sheet.name, "header": header, "songs": songs})
        return {"configured": True, "inbox": str(inbox), "sheets": sheets}

    def save_song(self, stem: str, data: bytes) -> dict[str, Any]:
        inbox = self._music_inbox()
        wanted = {s["stem"] for sheet in self.songs()["sheets"] for s in sheet["songs"]}
        if _STEM.match(stem) is None or stem not in wanted:
            raise _refuse(404, "song_not_awaited", f"no waiting prompt sheet names {stem}.wav")
        if not (data[:4] == b"RIFF" and data[8:12] == b"WAVE"):
            raise _refuse(422, "not_a_wav", "the file is not a WAV (download it as WAV)")
        _write_atomic(inbox / f"{stem}.wav", data)
        return {"saved": f"{stem}.wav", "bytes": len(data)}

    async def songs_done(self) -> list[dict[str, Any]]:
        """Resume every run waiting for songs; P4 then checks every file again."""
        inbox = self._music_inbox()
        cases = self._cases(SONGS_REASON)
        if not cases:
            raise _refuse(409, "no_run_waiting_for_songs", "no run is waiting for songs")
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
            job = json.loads(job_file.read_text(encoding="utf-8"))
            variants = []
            for variant, letter in zip(job["variants"], _LETTERS, strict=False):
                saved = [p.name for p in job_file.parent.iterdir()
                         if p.stem.upper() == letter and p.suffix.lower() in _ART_SUFFIXES]
                variants.append({"letter": letter, "style_id": variant.get("style_id"),
                                 "text": variant.get("text"), "prompt": variant.get("prompt"),
                                 "saved": sorted(saved)})
            jobs.append({"media_package_id": package_id, "title": job.get("title"),
                         "status": status, "variants": variants})
        return jobs

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
            if old.stem.upper() in (letter, f"ART_{letter}") and \
                    old.suffix.lower() in _ART_SUFFIXES:
                old.unlink()
        _write_atomic(folder / f"{letter}{suffix}", data)
        return {"saved": f"{letter}{suffix}", "bytes": len(data)}

    async def import_thumbnails(self, package_id: str) -> dict[str, Any]:
        """P6's import (checks, still video, release), then resume the run waiting on it."""
        folder = self._art_folder(package_id)
        released = await asyncio.to_thread(self.import_art, package_id, folder)
        cases = [c for c in self._cases(ART_REASON)
                 if package_id in json.dumps(c.get("details") or {})] or self._cases(ART_REASON)
        resumed = [await self._resume(c["case_id"], "production_owner",
                                      {"thumbnail_art_inbox_ref": f"{folder} (imported in the Studio)"},
                                      f"thumbnail art for {package_id} imported in the Studio")
                   for c in cases[:1]]
        return {"package": released, "resumed": resumed}

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
                "thumbnails": [t.get("id") for t in pkg.get("thumbnails") or []],
                "chapters": len(pkg.get("chapters") or []),
            })
        return {"case_id": case["case_id"], "packages": packages,
                "workflow_id": (case.get("pipeline") or {}).get("workflow_id")}

    def video(self, package_id: str) -> bytes:
        if package_id not in self._video_cache:
            pkg = self.package(package_id)
            self._video_cache.clear()  # one video in memory at a time
            self._video_cache[package_id] = self.media(str(pkg["video_master_uri"]),
                                                       str(pkg["video_checksum"]))
        return self._video_cache[package_id]

    def thumbnail(self, package_id: str, variant: str) -> bytes:
        pkg = self.package(package_id)
        for thumb in pkg.get("thumbnails") or []:
            if thumb.get("id") == variant:
                return self.media(str(thumb["uri"]), str(thumb["checksum"]))
        raise _refuse(404, "thumbnail_unknown", f"{package_id} has no thumbnail {variant}")

    def _approval_case(self, case_id: str) -> dict[str, Any]:
        for case in self._cases(APPROVAL_REASON):
            if case["case_id"] == case_id:
                return case
        raise _refuse(404, "approval_not_awaited", f"{case_id} is not an open approval")

    def _write_grants(self, case_id: str, package_ids: list[str]) -> datetime:
        path = self.paths.grants_file
        if path is None:
            raise _refuse(503, "grants_file_not_configured",
                          "PLM_WRITE_AUTHORIZATIONS_FILE is not set: an approval cannot grant "
                          "the upload")
        now = self.clock.now()
        expires = now + timedelta(hours=APPROVAL_GRANT_HOURS)
        grants = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        kept = [g for g in grants if g.get("capability") not in APPROVAL_CAPABILITIES]
        reference = (f"Studio approval {case_id} of {', '.join(package_ids)} "
                     f"at {now.isoformat()}")
        kept += [{"capability": cap, "granted_by": OWNER, "granted_at": now.isoformat(),
                  "expires_at": expires.isoformat(), "reference": reference}
                 for cap in APPROVAL_CAPABILITIES]
        _write_atomic(path, (json.dumps(kept, indent=2) + "\n").encode("utf-8"))
        return expires

    async def approve(self, case_id: str) -> dict[str, Any]:
        """The owner's approval: grant this upload, then resume the run."""
        case = self._approval_case(case_id)
        package_ids = self._package_ids(case)
        expires = self._write_grants(case_id, package_ids)
        answer = await self._resume(
            case_id, "production_owner",
            {"publish_approval_ref": f"Studio approval of {', '.join(package_ids)}; "
                                     f"YouTube grants until {expires.isoformat()}"},
            "approved for upload in the Studio")
        return {**answer, "grants_expire_at": expires.isoformat()}

    async def reject(self, case_id: str) -> dict[str, Any]:
        self._approval_case(case_id)
        return await self.actions.reject(case_id, actor=OWNER, role="production_owner",
                                         notes="rejected in the Studio")

    async def start_run(self) -> Any:
        if self.start is None:
            raise _refuse(503, "start_not_configured", "starting runs is not configured")
        return await self.start()


def build_studio(platform: Any, live: Any, actions: ExceptionActions, apis: Any,
                 urls: Mapping[Any, str], *, clock: Clock | None = None,
                 dotenv_path: str | Path = ".env") -> Studio:
    """The Studio over the deployment: P6 through the phase API, media from the object
    store, P6's importer in process (FFmpeg on this host) and runs started through P2
    and Temporal. ``platform=None``: every action is refused by its collaborator."""
    from ..storage import digest_from_key, from_uri, get_verified
    from ..temporal import Phase

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
        return get_verified(key)

    def import_art(package_id: str, folder: Path) -> dict[str, Any]:
        if platform is None:
            raise _refuse(503, "settings_unavailable", "the platform settings did not load")
        from media_factory.artifacts import MinioArtifactStore
        from media_factory.errors import MediaAssemblyError
        from media_factory.rendering.ffmpeg import FFmpegToolchain
        from media_factory.thumbnail_art.importer import import_owner_art

        from ..db import build_engine, session_factory

        engine = build_engine(platform)
        try:
            with session_factory(engine)() as session:
                try:
                    pkg = import_owner_art(session, MinioArtifactStore(platform),
                                           FFmpegToolchain(), package_id, folder,
                                           now=(clock or SystemClock()).now())
                except MediaAssemblyError as exc:
                    session.rollback()
                    raise _refuse(422, exc.code, str(exc)) from exc
                session.commit()
        finally:
            engine.dispose()
        return {"media_package_id": pkg.media_package_id, "status": pkg.status.value,
                "video_master_uri": pkg.video_master_uri}

    start = None
    if platform is not None and platform.service_token is not None:
        token = platform.service_token.get_secret_value()

        async def start() -> dict[str, Any]:
            from dataclasses import asdict

            from ..pipeline.config import load_pipeline_config
            from ..pipeline.start import RunNotStarted, start_new_run
            from ..temporal import connect_client

            config = load_pipeline_config(dotenv_path=dotenv_path)
            policy = config.learning.policy_version if config.learning else "p2-v1"
            env = load_env_file(dotenv_path)
            brand = (os.environ.get("PLM_STUDIO_BRAND_ID") or env.get("PLM_STUDIO_BRAND_ID")
                     or "longform_signal")
            try:
                started = await start_new_run(
                    await connect_client(platform), ar_url=urls[Phase.AR], token=token,
                    brand_id=brand, policy_version=policy, now=(clock or SystemClock()).now())
            except RunNotStarted as exc:
                raise _refuse(409, exc.code, str(exc)) from exc
            return asdict(started)

    return Studio(paths=StudioPaths.from_env(dotenv_path=dotenv_path), actions=actions,
                  open_holds=open_holds, package=package, media=media,
                  import_art=import_art, start=start, clock=clock or SystemClock())
