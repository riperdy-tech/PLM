"""Songs the owner makes on suno.com, imported from an inbox folder (R6, D10).

Suno's terms bar automated access and grant commercial rights only for output obtained
through a permitted Download. So for this provider PLM does not call Suno: it writes the
prompts of the songs it wants into the inbox, the owner makes each song in Suno's own
interface, downloads it as WAV and saves it under the file name the prompt sheet gives.
Executing the plan then imports those files.

* **Prompt sheet.** ``<inbox>/<track_spec_id>.prompts.txt``: the plan, then one entry per
  song still needed, with the file name to save it as, the accepted length and every
  problem found in a file already saved (``Problem:``).
* **Names.** A song is ``<track_spec_id>-<variant>`` (``trk_1-v2``). The name and the
  prompt text are recorded in ``<track_spec_id>.songs.json`` when the sheet first asks
  for the song and never change after, so a later prompt-code change cannot turn a saved
  file into a missing one. A name of the earlier form ``<track>-<sha256(prompt)[:8]>``
  (a sheet written before 2026-11-27, or a file already saved under it) is kept.
* **All or nothing.** :meth:`SunoManualAdapter.awaiting_audio` checks every song a plan
  still needs before any is imported; while one is missing or unusable nothing is
  imported and the plan waits (``manual_audio_awaited``). Unusable: not a WAV, cut
  short, outside the accepted length, or the same audio as another WAV in the inbox or
  a song already imported.
* **Files.** ``<name>.wav`` (PCM or float WAV, read by libsndfile: the length is the
  frames the file holds, not its header), and for a vocal track ``<name>.lyrics.txt``
  with the lyrics as sung: P5 screens them. A file is read and hashed once per version
  (path, size, modification time).
* **Finished plans leave the inbox.** Once every song of a plan is imported (and
  committed), its sheet, record and song files move to ``<inbox>/imported/<plan_id>/``;
  the Studio moves a cancelled or failed run's to ``<inbox>/cancelled/<plan_id>/``
  (:func:`archive_track`). Nothing is deleted. Moved WAVs still count as the inbox's
  audio (the same audio saved again waits) and a moved record still names the songs (a
  replacement keeps its name); only a file in the inbox itself is ever imported.
* **Identity.** The provider is ``suno`` (P3's allowlist applies) and the model
  :data:`MANUAL_MODEL_ID`. The job id is ``manual:<sha256 of the WAV>``.
* **Rights.** The owner declares the plan tier and account (``PLM_SUNO_PLAN_TIER``,
  ``PLM_SUNO_ACCOUNT_REF``); only a paid tier with an account on file yields terms.
  Nothing is inferred from the file.
* **Import is a write.** It goes through the guard as :data:`IMPORT_CAPABILITY`, so the
  owner authorizes it like any other capability (a ledger rate of 0 prices it). The
  adapter declares ``costs_nothing``, so P4 imports it without a paid-job journal.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import soundfile as sf  # type: ignore[import-untyped]
from plm_contracts import CommercialUseEligibility, ProviderMode
from plm_contracts.seams.c4 import ProviderLicenseTerms
from plm_platform.dotenv import load_env_file

from ..domain.enums import AttemptStatus
from ..domain.models import GenerationRequest, GenerationResponse

__all__ = [
    "ARCHIVE_FOLDERS",
    "IMPORT_CAPABILITY",
    "MANUAL_MODEL_ID",
    "STYLES_LIMIT",
    "SunoManualAdapter",
    "SunoManualSettings",
    "WavFacts",
    "archive_track",
    "load_manual_settings",
    "load_suno_manual_settings",
    "song_problem",
    "wav_facts",
]

IMPORT_CAPABILITY = "music.import"
"""The write-guard capability of importing one owner-made song."""

MANUAL_MODEL_ID = "suno-web-manual"
"""The model id of a song the owner made in Suno's web interface."""

INBOX_VAR = "PLM_SUNO_MANUAL_INBOX"
PLAN_TIER_VAR = "PLM_SUNO_PLAN_TIER"
ACCOUNT_REF_VAR = "PLM_SUNO_ACCOUNT_REF"
MODEL_VERSION_VAR = "PLM_SUNO_MODEL_VERSION"

PAID_TIERS = frozenset({"pro", "premier"})
"""Suno tiers whose terms include commercial use (suno.com/pricing, read 2026-10-30)."""

STYLES_LIMIT: Final = 1000
"""Characters Suno's Styles box takes (v4.5 and later)."""

TERMS_VERSION = "suno.com/terms as read 2026-10-30"
LICENSE_REFERENCE = "https://suno.com/terms (Permitted Commercial Use)"
BASIS = ("owner-declared paid plan; song made by the account owner in Suno's interface "
         "and obtained through a permitted Download")


@dataclass(frozen=True, slots=True)
class SunoManualSettings:
    """What the owner declares for manually made Suno songs."""

    inbox: Path | None = None
    plan_tier: str | None = None
    account_ref: str | None = None
    model_version: str | None = None
    terms_ref: str | None = None
    """Where and when the owner read the tool's own terms (Flow Music); unset: not read."""


def load_manual_settings(
    names: tuple[str, str, str, str],
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
    *,
    terms_var: str | None = None,
) -> SunoManualSettings:
    """Read the inbox, plan tier, account and model version variables ``names`` (and
    ``terms_var``) from env, then ``.env``; unset means unknown."""
    process_env: Mapping[str, str] = os.environ if env is None else env
    dotenv_values = load_env_file(Path(".env") if dotenv_path is None else dotenv_path)

    def value(name: str) -> str | None:
        raw = process_env.get(name, "").strip() or dotenv_values.get(name, "").strip()
        return raw or None

    inbox_var, tier_var, account_var, model_var = names
    inbox = value(inbox_var)
    tier = value(tier_var)
    return SunoManualSettings(
        inbox=None if inbox is None else Path(inbox),
        plan_tier=None if tier is None else tier.lower(),
        account_ref=value(account_var),
        model_version=value(model_var),
        terms_ref=None if terms_var is None else value(terms_var),
    )


def load_suno_manual_settings(
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> SunoManualSettings:
    """Read the ``PLM_SUNO_*`` declarations (env, then ``.env``); unset means unknown."""
    return load_manual_settings(
        (INBOX_VAR, PLAN_TIER_VAR, ACCOUNT_REF_VAR, MODEL_VERSION_VAR), env, dotenv_path)


_STYLE_PHRASES = {
    # prompt label -> how the value reads in Suno's Styles box ({} is the value)
    "mood": "{} mood",
    "harmonic key": "key of {}",
    "production approach": "{} production",
    "instrument palette": "{}",
    "hook character": "{} hook",
    "section structure": "{} structure",
    "energy": "{} energy",
    "foreground lead": "{} lead",
    "groove": "groove: {}",
    "melodic character": "melody: {}",
    "harmonic motion": "harmony: {}",
    "arrangement progression": "arrangement: {}",
    "mix balance": "mix: {}",
    "vocal character": "{} vocals",
    "lyric density": "{} lyric density",
    # a track's own character (P3 v9): its phrases read as written
    "energy shape": "{}",
    "texture": "{}",
    "scene": "{}",
}


def _plain_instruction(sentence: str) -> str | None:
    """A variant's arrangement instruction (P4's planner) as plain style phrases, or
    ``None`` when ``sentence`` is not one. The planner's wording is written for a model
    ("within the declared ..., without replacing the supplied ..."); the owner pastes
    style words, and the constraints it repeats are already in the other phrases."""
    if sentence.startswith("Within the declared"):
        return "space between phrases, short phrases with rests and light accents"
    if sentence.startswith("Develop the declared"):
        lead = re.search(r"contrast around (.+?);", sentence)
        who = lead.group(1).replace("_", " ") if lead else "the parts"
        return (f"{who} moving between foreground and background, "
                "phrase density and dynamics changing across sections")
    if sentence.startswith("For independent interpretation"):
        return "its own placement of phrases and rests"
    return None


def suno_styles(request: GenerationRequest) -> str:
    """Every descriptor of P4's prompt, phrased for Suno's Styles box.

    The owner pastes this one text into Styles (Advanced mode has no description box),
    so nothing in the prompt may be dropped: each ``label: value`` sentence becomes a
    phrase, ``snake_case`` values become words, a variant's arrangement instruction
    becomes plain style phrases, and any other sentence is kept as written.
    """
    phrases: list[str] = []
    for sentence in (s.strip() for s in request.prompt.split(". ")):
        sentence = sentence.rstrip(".").strip()
        if not sentence:
            continue
        label, sep, value = sentence.partition(": ")
        plain = _plain_instruction(sentence)
        if plain is not None:
            phrase = plain
        elif sep and label.lower() in _STYLE_PHRASES:
            words = value.replace("_", " ")
            template = _STYLE_PHRASES[label.lower()]
            # "airy_synth_lead" is already a lead: "airy synth lead", not "... lead lead";
            # so is a bare "lead"
            phrase = (words if template.startswith("{} ")
                      and f" {words}".endswith(template[2:]) else template.format(words))
        elif sentence.lower().endswith(" track") and " " not in sentence[:-6]:
            phrase = sentence[:-6].lower()  # "LOFI track" -> "lofi"
        elif sentence.startswith("for "):
            phrase = sentence.replace("_", " ") + " music"
        else:
            phrase = sentence.replace("_", " ")
        phrases.append(phrase)
    return ", ".join(dict.fromkeys(phrases))


# -- reading a saved WAV -------------------------------------------------------------

_SAMPLE_BYTES: Final = {"PCM_U8": 1, "PCM_16": 2, "PCM_24": 3, "PCM_32": 4,
                        "FLOAT": 4, "DOUBLE": 8}
"""The WAV sample formats an import accepts (Suno's and Flow Music's downloads, and a
32-bit float export), with their bytes per sample."""

_ACCEPTED: Final = re.compile(r"^Accepted length: (\d+):(\d\d) to (\d+):(\d\d)", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class WavFacts:
    """What one saved file is: why it cannot be imported (``None``: usable), its SHA-256
    and the length of the audio it holds."""

    problem: str | None
    sha256: str | None = None
    seconds: float | None = None


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def _declared_data_bytes(path: Path) -> int | None:
    """The ``data`` chunk size the RIFF header declares; ``None`` when it does not say
    (a streamed or RF64 file) or cannot be read."""
    try:
        with path.open("rb") as f:
            if f.read(12)[:4] != b"RIFF":
                return None
            while True:
                head = f.read(8)
                if len(head) < 8:
                    return None
                chunk, size = head[:4], struct.unpack("<I", head[4:])[0]
                if chunk == b"data":
                    return None if size in (0, 0xFFFFFFFF) else int(size)
                f.seek(size + (size & 1), 1)
    except (OSError, struct.error):
        return None


def _read_wav_facts(path: Path) -> WavFacts:
    try:
        with path.open("rb") as f:
            head = f.read(12)
    except OSError as exc:
        return WavFacts(f"cannot be read ({exc})")
    if not (head[:4] in (b"RIFF", b"RF64") and head[8:12] == b"WAVE"):
        return WavFacts("is not a WAV file (download it as WAV)")
    try:
        info = sf.info(str(path))
    except (RuntimeError, OSError, ValueError, TypeError) as exc:
        return WavFacts(f"is not a readable WAV ({exc})")
    if info.subtype not in _SAMPLE_BYTES:
        return WavFacts(f"is a {info.subtype} WAV; download it as a PCM or float WAV")
    if info.frames <= 0 or info.samplerate < 8000:
        return WavFacts("has no audio")
    # libsndfile counts the frames the file holds; a header promising more is a file
    # cut short (an interrupted download), whose header length would be a lie.
    seconds = info.frames / info.samplerate
    declared = _declared_data_bytes(path)
    if declared is not None:
        declared_frames = declared // (info.channels * _SAMPLE_BYTES[info.subtype])
        if declared_frames > info.frames:
            return WavFacts(f"is cut short: its header says "
                            f"{_clock(declared_frames / info.samplerate)} but it holds "
                            f"{_clock(seconds)} (download it again)")
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return WavFacts(None, digest.hexdigest(), seconds)


_FACTS: dict[str, tuple[tuple[int, int], WavFacts]] = {}


def wav_facts(path: Path) -> WavFacts:
    """:func:`_read_wav_facts`, read once per file version (path, size, mtime)."""
    try:
        stat = path.stat()
    except OSError as exc:
        return WavFacts(f"cannot be read ({exc})")
    key, version = str(path.resolve()), (stat.st_size, stat.st_mtime_ns)
    cached = _FACTS.get(key)
    if cached is not None and cached[0] == version:
        return cached[1]
    facts = _read_wav_facts(path)
    _FACTS[key] = (version, facts)
    return facts


def _length_problem(seconds: float, low: int | None, high: int | None) -> str | None:
    if low is None or high is None or low <= seconds <= high:
        return None
    return (f"is {_clock(seconds)} long; the accepted length is "
            f"{_clock(low)} to {_clock(high)}")


def song_problem(path: Path, sheet_block: str | None = None) -> str | None:
    """Why the saved song at ``path`` cannot be imported, or ``None``: the check P4
    makes, for the Studio to run when the owner saves a file. ``sheet_block`` is the
    song's entry on the prompt sheet; its accepted length is checked too."""
    facts = wav_facts(path)
    if facts.problem is not None or facts.seconds is None:
        return facts.problem
    accepted = _ACCEPTED.search(sheet_block or "")
    if accepted is None:
        return None
    low_m, low_s, high_m, high_s = (int(g) for g in accepted.groups())
    return _length_problem(facts.seconds, low_m * 60 + low_s, high_m * 60 + high_s)


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


ARCHIVE_FOLDERS: Final = ("imported", "cancelled")
"""Where a finished plan's files go: all imported, or its run cancelled or failed."""


def archive_track(inbox: Path, track_spec_id: str, plan_id: str | None,
                  kind: str) -> list[Path]:
    """Move one track's sheet, record and song files (``<track>-*``: WAVs, lyrics, M4As)
    from ``inbox`` into ``<inbox>/<kind>/<plan_id>/`` and return where they went.

    Never deletes: when the folder already holds one of the names (a replacement song's
    second sheet), the files go to ``<plan_id>-2`` (``-3``...) instead. Each file is one
    rename on the same volume, the sheet first, so a crash mid-way leaves at most song
    files behind and a second call moves them; with nothing left it does nothing."""
    if kind not in ARCHIVE_FOLDERS:
        raise ValueError(f"unknown archive folder {kind!r}")
    name = _safe(track_spec_id)
    files = [inbox / f"{name}.prompts.txt", inbox / f"{name}.songs.json",
             *sorted(inbox.glob(f"{name}-*"))]
    files = [f for f in files if f.is_file()]
    if not files:
        return []
    base = inbox / kind / _safe(plan_id or name)
    folder, n = base, 1
    while any((folder / f.name).exists() for f in files):
        n += 1
        folder = base.with_name(f"{base.name}-{n}")
    folder.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for source in files:
        os.replace(source, folder / source.name)
        moved.append(folder / source.name)
    return moved


class SunoManualAdapter:
    """Imports owner-made Suno songs from the inbox (no call to Suno)."""

    provider_mode = ProviderMode.REAL
    write_capability = IMPORT_CAPABILITY
    costs_nothing = True
    paid_tiers: frozenset[str] = PAID_TIERS
    terms_version = TERMS_VERSION
    license_reference = LICENSE_REFERENCE
    basis = BASIS
    inbox_var = INBOX_VAR
    model_version_var = MODEL_VERSION_VAR

    def __init__(self, settings: SunoManualSettings, *, model_id: str = MANUAL_MODEL_ID) -> None:
        self._settings = settings
        self._model_id = model_id

    @property
    def provider_id(self) -> str:
        return "suno"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def inbox(self) -> Path | None:
        return self._settings.inbox

    def check_health(self) -> bool:
        return self._settings.inbox is not None and self._settings.inbox.is_dir()

    def get_quota_status(self) -> dict[str, Any]:
        """The owner spends Suno credits in Suno's interface; PLM never sees the balance."""
        return {
            "provider_id": self.provider_id,
            "configured": self.check_health(),
            "credits_remaining": None,
            "quota_exhausted": None,
            "quota_known": False,
            "quota_status": "manual",
        }

    def license_terms(self) -> ProviderLicenseTerms | None:
        """The owner's declared paid tier and account, or ``None`` (commercially unknown)."""
        tier, account = self._settings.plan_tier, self._settings.account_ref
        if tier not in self.paid_tiers or not account:
            return None
        return ProviderLicenseTerms(
            commercial_use_status=CommercialUseEligibility.ELIGIBLE,
            basis=self.basis,
            license_reference=self.license_reference,
            plan_or_tier=tier,
            account_identity_ref=account,
            terms_version=self.terms_version,
        )

    # -- files -----------------------------------------------------------------------

    @staticmethod
    def legacy_stem(request: GenerationRequest) -> str:
        """The name sheets gave a song before 2026-11-27: track id and a prompt digest."""
        digest = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()[:8]
        return f"{_safe(request.track_spec_id)}-{digest}"

    def _inbox(self) -> Path:
        if self._settings.inbox is None:
            raise RuntimeError(f"{self.inbox_var} is not set: there is no inbox to import from")
        return self._settings.inbox

    @staticmethod
    def _record_path(inbox: Path, track_spec_id: str) -> Path:
        return inbox / f"{_safe(track_spec_id)}.songs.json"

    def _record_paths(self, inbox: Path, track_spec_id: str,
                      plan_id: str | None) -> list[Path]:
        """The track's record in the inbox, then where a finished plan's record went."""
        found = [self._record_path(inbox, track_spec_id)]
        if plan_id is not None:
            found += [self._record_path(inbox / kind / _safe(plan_id), track_spec_id)
                      for kind in ARCHIVE_FOLDERS]
        return found

    def _record(self, inbox: Path, track_spec_id: str, plan_id: str | None) -> dict[str, Any]:
        """The track's recorded names and prompt texts (a new record for another plan)."""
        loaded = None
        for path in self._record_paths(inbox, track_spec_id, plan_id):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                break
            except (OSError, ValueError):
                loaded = None
        if (not isinstance(loaded, dict) or not isinstance(loaded.get("songs"), dict)
                or (plan_id is not None and loaded.get("plan_id") != plan_id)):
            return {"plan_id": plan_id, "provider": self.provider_id, "songs": {}}
        return loaded

    def records_plan(self, track_spec_id: str, plan_id: str) -> bool:
        """Whether this inbox already asked the owner for ``plan_id``'s songs."""
        inbox = self._settings.inbox
        if inbox is None:
            return False
        for path in self._record_paths(inbox, track_spec_id, plan_id):
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(loaded, dict) and loaded.get("plan_id") == plan_id:
                return True
        return False

    def archive_plan(self, track_spec_id: str, plan_id: str) -> list[Path]:
        """Move a plan whose songs are all imported out of the inbox (``imported/``)."""
        inbox = self._settings.inbox
        if inbox is None or not inbox.is_dir():
            return []
        return archive_track(inbox, track_spec_id, plan_id, "imported")

    def _stem(self, inbox: Path, request: GenerationRequest,
              record: dict[str, Any]) -> str:
        """The variant's name, with ``-r<n>`` for the n-th song made in place of a
        rejected one (``-r2`` replaces the first)."""
        base = self._base_stem(inbox, request, record)
        return base if not request.replacement else f"{base}-r{request.replacement + 1}"

    def _base_stem(self, inbox: Path, request: GenerationRequest,
                   record: dict[str, Any]) -> str:
        """The recorded name, else an old prompt-hash name in use, else track + variant."""
        code = request.variant_code
        entry = record["songs"].get(code) if code else None
        if isinstance(entry, dict) and isinstance(entry.get("stem"), str):
            return str(entry["stem"])
        legacy = self.legacy_stem(request)
        if code is None or (inbox / f"{legacy}.wav").is_file():
            return legacy
        sheet = inbox / f"{_safe(request.track_spec_id)}.prompts.txt"
        try:
            if f"Save as: {legacy}.wav" in sheet.read_text(encoding="utf-8"):
                return legacy
        except OSError:
            pass
        return f"{_safe(request.track_spec_id)}-{_safe(code)}"

    def file_stem(self, request: GenerationRequest) -> str:
        """The name the owner saves ``request``'s song under."""
        inbox = self._inbox()
        return self._stem(inbox, request,
                          self._record(inbox, request.track_spec_id, request.plan_id))

    def _inbox_index(self, inbox: Path) -> dict[str, list[str]]:
        """SHA-256 -> paths (relative to the inbox) of every usable WAV in the inbox and
        in its folders of finished plans."""
        index: dict[str, list[str]] = {}
        wavs = [*inbox.glob("*.wav"),
                *(w for kind in ARCHIVE_FOLDERS for w in inbox.glob(f"{kind}/*/*.wav"))]
        for wav in sorted(wavs):
            facts = wav_facts(wav)
            if facts.sha256 is not None:
                index.setdefault(facts.sha256, []).append(wav.relative_to(inbox).as_posix())
        return index

    def awaiting_audio(self, requests: Sequence[GenerationRequest], *,
                       imported: Mapping[str, str] | None = None) -> list[str]:
        """What is still missing or unusable for ``requests`` (empty: ready to import).

        ``imported`` maps the SHA-256 of audio already imported to its candidate: a file
        with that audio is not a new song. Writes the prompt sheet of the songs still
        awaited, with each saved file's problem, so the owner sees what to make or fix.
        """
        inbox = self._inbox()
        index = self._inbox_index(inbox)
        records: dict[str, dict[str, Any]] = {}
        problems: list[str] = []
        awaited: list[tuple[GenerationRequest, str, list[str]]] = []
        for request in requests:
            record = records.setdefault(
                request.track_spec_id,
                self._record(inbox, request.track_spec_id, request.plan_id))
            stem = self._stem(inbox, request, record)
            wav = inbox / f"{stem}.wav"
            found: list[str] = []
            if not wav.is_file():
                problems.append(f"{wav.name} is missing")
            else:
                facts = wav_facts(wav)
                problem = facts.problem or (None if facts.seconds is None else _length_problem(
                    facts.seconds, request.duration_min_seconds, request.duration_max_seconds))
                if problem is None and facts.sha256 is not None:
                    others = [n for n in index.get(facts.sha256, []) if n != wav.name]
                    if others:
                        problem = f"is the same audio as {others[0]}"
                    elif imported and facts.sha256 in imported:
                        problem = (f"is the same audio as song {imported[facts.sha256]}, "
                                   "already imported")
                if problem is not None:
                    found.append(problem)
                    problems.append(f"{wav.name} {problem}")
            missing_lyrics = request.vocal_mode != "instrumental" and not (
                inbox / f"{stem}.lyrics.txt").is_file()
            if missing_lyrics:
                problems.append(f"{stem}.lyrics.txt is missing (the lyrics as sung)")
            if found or missing_lyrics or not wav.is_file():
                awaited.append((request, stem, found))
        if awaited:
            self._write_sheet(inbox, awaited, records)
        return problems

    def _sheet_header(self, track_spec_id: str) -> list[str]:
        model = self._settings.model_version or f"(not declared: set {self.model_version_var})"
        return [
            f"PLM songs to make on suno.com for track {track_spec_id}",
            f"Suno Advanced mode, model {model}. Paste the Styles text into Styles and",
            "the Exclude styles into Exclude. Pick the better of Suno's two clips,",
            "download it as WAV and save it in this folder under the name given.",
        ]

    def _prompt_lines(self, request: GenerationRequest) -> list[str]:
        styles = suno_styles(request)
        warning: list[str] = []
        if len(styles) > STYLES_LIMIT:
            cut = styles.rfind(", ", 0, STYLES_LIMIT + 1)
            kept = styles[:cut] if cut > 0 else styles[:STYLES_LIMIT]
            warning = [(f"Warning: Suno takes at most {STYLES_LIMIT} characters of Styles, "
                       f"so this end was left out: {styles[len(kept):].lstrip(', ')}")]
            styles = kept
        return [
            f"Styles ({len(styles)} characters; paste all of it into Styles):",
            styles,
            *([f"Exclude styles: {request.negative_prompt}"] if request.negative_prompt else []),
            *warning,
        ]

    def _write_sheet(self, inbox: Path,
                     awaited: Sequence[tuple[GenerationRequest, str, list[str]]],
                     records: dict[str, dict[str, Any]]) -> None:
        by_track: dict[str, list[tuple[GenerationRequest, str, list[str]]]] = {}
        for item in awaited:
            by_track.setdefault(item[0].track_spec_id, []).append(item)
        for track_spec_id, wanted in by_track.items():
            record = records[track_spec_id]
            plan_id = wanted[0][0].plan_id
            lines = [*self._sheet_header(track_spec_id),
                     *([f"Plan: {plan_id}"] if plan_id else []), ""]
            for n, (request, stem, found) in enumerate(wanted, start=1):
                instrumental = request.vocal_mode == "instrumental"
                code = request.variant_code
                entry = record["songs"].get(code) if code else None
                base = self._base_stem(inbox, request, record)
                if not isinstance(entry, dict):
                    # The text the owner is first asked for is kept as written.
                    entry = {"stem": base, "prompt": request.prompt,
                             "lines": self._prompt_lines(request)}
                    if code:
                        record["songs"][code] = entry
                previous = (base if request.replacement <= 1
                            else f"{base}-r{request.replacement}")
                low, high = request.duration_min_seconds, request.duration_max_seconds
                lines += [
                    f"--- Song {n} ---",
                    f"Save as: {stem}.wav",
                    *([f"Replaces: {previous}.wav (rejected; make a new song)"]
                      if request.replacement else []),
                    *([] if instrumental else [
                        f"Lyrics:  save the lyrics as sung in {stem}.lyrics.txt",
                    ]),
                    f"Instrumental: {'yes' if instrumental else 'no'}",
                    f"Length: about {request.duration_seconds} seconds",
                    *([] if low is None or high is None else [
                        (f"Accepted length: {_clock(low)} to {_clock(high)} "
                        "(a song outside it is not imported)")]),
                    *(f"Problem: {problem}" for problem in found),
                    *[str(line) for line in entry["lines"]],
                    "",
                ]
            name = _safe(track_spec_id)
            (inbox / f"{name}.prompts.txt").write_text("\n".join(lines), encoding="utf-8")
            if record["songs"]:
                self._record_path(inbox, track_spec_id).write_text(
                    json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Import the owner's WAV for ``request``; nothing is sent anywhere."""
        inbox = self._inbox()
        stem = self.file_stem(request)
        wav = inbox / f"{stem}.wav"
        facts = wav_facts(wav)
        try:
            data = wav.read_bytes()
        except OSError as exc:
            return GenerationResponse(
                provider_job_id="", status=AttemptStatus.FAILED,
                error_message=f"cannot read {wav.name}: {exc}", confirmed_unsubmitted=True,
            )
        sha = hashlib.sha256(data).hexdigest()
        if facts.problem is not None or facts.seconds is None or facts.sha256 != sha:
            return GenerationResponse(
                provider_job_id="", status=AttemptStatus.FAILED,
                error_message=f"{wav.name} {facts.problem or 'changed while it was read'}",
                confirmed_unsubmitted=True,
            )
        lyrics_path = inbox / f"{stem}.lyrics.txt"
        lyrics = (lyrics_path.read_text(encoding="utf-8").strip() or None
                  if lyrics_path.is_file() else None)
        return GenerationResponse(
            provider_job_id=f"manual:{sha}",
            status=AttemptStatus.SUCCEEDED,
            audio_data=data,
            mime_type="audio/wav",
            actual_duration_seconds=round(facts.seconds, 4),
            cost_credits=None,  # spent in Suno's interface; PLM never sees the amount
            model_version=self._settings.model_version,
            lyrics_text=None if request.vocal_mode == "instrumental" else lyrics,
        )
