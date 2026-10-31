"""Songs the owner makes on suno.com, imported from an inbox folder (R6, D10).

Suno's terms bar automated access and grant commercial rights only for output obtained
through a permitted Download. So for this provider PLM does not call Suno: it writes the
prompts of the songs it wants into the inbox, the owner makes each song in Suno's own
interface, downloads it as WAV and saves it under the file name the prompt sheet gives.
Executing the plan then imports those files.

* **Prompt sheet.** ``<inbox>/<track_spec_id>.prompts.txt``: one entry per song still
  needed, with the file name to save it as.
* **All or nothing.** :meth:`SunoManualAdapter.awaiting_audio` checks every song a plan
  still needs before any is imported; while one is missing or unusable nothing is
  imported and the plan waits (``manual_audio_awaited``).
* **Files.** ``<name>.wav`` (PCM WAV, as Suno's WAV download), and for a vocal track
  ``<name>.lyrics.txt`` with the lyrics as sung: P5 screens them.
* **Identity.** The provider is ``suno`` (P3's allowlist applies) and the model
  :data:`MANUAL_MODEL_ID`. The job id is ``manual:<sha256 of the WAV>``.
* **Rights.** The owner declares the plan tier and account (``PLM_SUNO_PLAN_TIER``,
  ``PLM_SUNO_ACCOUNT_REF``); only a paid tier with an account on file yields terms.
  Nothing is inferred from the file.
* **Import is a write.** It goes through the guard as :data:`IMPORT_CAPABILITY`, so the
  owner authorizes it like any other capability (a ledger rate of 0 prices it).
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plm_contracts import CommercialUseEligibility, ProviderMode
from plm_contracts.seams.c4 import ProviderLicenseTerms
from plm_platform.dotenv import load_env_file

from ..domain.enums import AttemptStatus
from ..domain.models import GenerationRequest, GenerationResponse

__all__ = [
    "IMPORT_CAPABILITY",
    "MANUAL_MODEL_ID",
    "SunoManualAdapter",
    "SunoManualSettings",
    "load_manual_settings",
    "load_suno_manual_settings",
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


def load_manual_settings(
    names: tuple[str, str, str, str],
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> SunoManualSettings:
    """Read the inbox, plan tier, account and model version variables ``names`` (env,
    then ``.env``); unset means unknown."""
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
    "vocal character": "{} vocals",
    "lyric density": "{} lyric density",
}


def suno_styles(request: GenerationRequest) -> str:
    """Every descriptor of P4's prompt, phrased for Suno's Styles box.

    The owner pastes this one text into Styles (Advanced mode has no description box),
    so nothing in the prompt may be dropped: each ``label: value`` sentence becomes a
    phrase, ``snake_case`` values become words, and any sentence without a known label
    (a candidate's variation instruction) is kept as written.
    """
    phrases: list[str] = []
    for sentence in (s.strip() for s in request.prompt.split(". ")):
        sentence = sentence.rstrip(".").strip()
        if not sentence:
            continue
        label, sep, value = sentence.partition(": ")
        if sep and label.lower() in _STYLE_PHRASES:
            phrase = _STYLE_PHRASES[label.lower()].format(value.replace("_", " "))
        elif sentence.lower().endswith(" track") and " " not in sentence[:-6]:
            phrase = sentence[:-6].lower()  # "LOFI track" -> "lofi"
        elif sentence.startswith("for "):
            phrase = sentence.replace("_", " ") + " music"
        else:
            phrase = sentence.replace("_", " ")
        phrases.append(phrase)
    return ", ".join(dict.fromkeys(phrases))


def _wav_problem(data: bytes) -> str | None:
    """Why ``data`` is not a usable PCM WAV, or ``None``."""
    if not (data.startswith(b"RIFF") and b"WAVE" in data[:16]):
        return "is not a WAV file (download it as WAV)"
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            if wf.getnframes() <= 0 or wf.getframerate() < 8000:
                return "has no audio"
    except (wave.Error, EOFError) as exc:
        return f"is not a readable PCM WAV ({exc})"
    return None


class SunoManualAdapter:
    """Imports owner-made Suno songs from the inbox (no call to Suno)."""

    provider_mode = ProviderMode.REAL
    write_capability = IMPORT_CAPABILITY
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
    def file_stem(request: GenerationRequest) -> str:
        """The name the owner saves a song under: track spec id and a prompt digest."""
        track = re.sub(r"[^A-Za-z0-9_-]", "_", request.track_spec_id)
        digest = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()[:8]
        return f"{track}-{digest}"

    def _inbox(self) -> Path:
        if self._settings.inbox is None:
            raise RuntimeError(f"{self.inbox_var} is not set: there is no inbox to import from")
        return self._settings.inbox

    def awaiting_audio(self, requests: Sequence[GenerationRequest]) -> list[str]:
        """What is still missing or unusable for ``requests`` (empty: ready to import).

        Writes the prompt sheet of the songs still awaited, so the owner sees what to make.
        """
        inbox = self._inbox()
        problems: list[str] = []
        awaited: list[GenerationRequest] = []
        seen: dict[str, str] = {}
        for request in requests:
            stem = self.file_stem(request)
            wav = inbox / f"{stem}.wav"
            before = len(problems)
            if not wav.is_file():
                problems.append(f"{wav.name} is missing")
            else:
                data = wav.read_bytes()
                problem = _wav_problem(data)
                if problem is not None:
                    problems.append(f"{wav.name} {problem}")
                else:
                    sha = hashlib.sha256(data).hexdigest()
                    if sha in seen:
                        problems.append(f"{wav.name} is the same audio as {seen[sha]}")
                    seen[sha] = wav.name
            if request.vocal_mode != "instrumental" and not (
                inbox / f"{stem}.lyrics.txt"
            ).is_file():
                problems.append(f"{stem}.lyrics.txt is missing (the lyrics as sung)")
            if len(problems) > before:
                awaited.append(request)
        if awaited:
            self._write_sheet(inbox, awaited)
        return problems

    def _sheet_header(self, track_spec_id: str) -> list[str]:
        model = self._settings.model_version or f"(not declared: set {self.model_version_var})"
        return [
            f"PLM songs to make on suno.com for track {track_spec_id}",
            f"Suno Advanced mode, model {model}. Paste the Styles text into Styles and",
            "the Exclude styles into Exclude. Pick the better of Suno's two clips,",
            "download it as WAV and save it in this folder under the name given.",
            "",
        ]

    def _prompt_lines(self, request: GenerationRequest) -> list[str]:
        styles = suno_styles(request)
        return [
            f"Styles ({len(styles)} characters; paste all of it into Styles):",
            styles,
            *([f"Exclude styles: {request.negative_prompt}"] if request.negative_prompt else []),
        ]

    def _write_sheet(self, inbox: Path, requests: Sequence[GenerationRequest]) -> None:
        by_track: dict[str, list[GenerationRequest]] = {}
        for request in requests:
            by_track.setdefault(request.track_spec_id, []).append(request)
        for track_spec_id, wanted in by_track.items():
            lines = self._sheet_header(track_spec_id)
            for n, request in enumerate(wanted, start=1):
                instrumental = request.vocal_mode == "instrumental"
                lines += [
                    f"--- Song {n} ---",
                    f"Save as: {self.file_stem(request)}.wav",
                    *([] if instrumental else [
                        f"Lyrics:  save the lyrics as sung in {self.file_stem(request)}.lyrics.txt",
                    ]),
                    f"Instrumental: {'yes' if instrumental else 'no'}",
                    (f"Length: about {request.duration_seconds} seconds "
                     "(any length the track accepts is fine)"),
                    *self._prompt_lines(request),
                    "",
                ]
            name = re.sub(r"[^A-Za-z0-9_-]", "_", track_spec_id)
            (inbox / f"{name}.prompts.txt").write_text("\n".join(lines), encoding="utf-8")

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Import the owner's WAV for ``request``; nothing is sent anywhere."""
        inbox = self._inbox()
        stem = self.file_stem(request)
        wav = inbox / f"{stem}.wav"
        try:
            data = wav.read_bytes()
        except OSError as exc:
            return GenerationResponse(
                provider_job_id="", status=AttemptStatus.FAILED,
                error_message=f"cannot read {wav.name}: {exc}", confirmed_unsubmitted=True,
            )
        problem = _wav_problem(data)
        if problem is not None:
            return GenerationResponse(
                provider_job_id="", status=AttemptStatus.FAILED,
                error_message=f"{wav.name} {problem}", confirmed_unsubmitted=True,
            )
        lyrics_path = inbox / f"{stem}.lyrics.txt"
        lyrics = (lyrics_path.read_text(encoding="utf-8").strip() or None
                  if lyrics_path.is_file() else None)
        with wave.open(io.BytesIO(data), "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()
        return GenerationResponse(
            provider_job_id=f"manual:{hashlib.sha256(data).hexdigest()}",
            status=AttemptStatus.SUCCEEDED,
            audio_data=data,
            mime_type="audio/wav",
            actual_duration_seconds=round(duration, 4),
            cost_credits=None,  # spent in Suno's interface; PLM never sees the amount
            model_version=self._settings.model_version,
            lyrics_text=None if request.vocal_mode == "instrumental" else lyrics,
        )
