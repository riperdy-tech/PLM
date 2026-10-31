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


def load_suno_manual_settings(
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> SunoManualSettings:
    """Read the ``PLM_SUNO_*`` declarations (env, then ``.env``); unset means unknown."""
    process_env: Mapping[str, str] = os.environ if env is None else env
    dotenv_values = load_env_file(Path(".env") if dotenv_path is None else dotenv_path)

    def value(name: str) -> str | None:
        raw = process_env.get(name, "").strip() or dotenv_values.get(name, "").strip()
        return raw or None

    inbox = value(INBOX_VAR)
    tier = value(PLAN_TIER_VAR)
    return SunoManualSettings(
        inbox=None if inbox is None else Path(inbox),
        plan_tier=None if tier is None else tier.lower(),
        account_ref=value(ACCOUNT_REF_VAR),
        model_version=value(MODEL_VERSION_VAR),
    )


def _wav_problem(data: bytes) -> str | None:
    """Why ``data`` is not a usable PCM WAV, or ``None``."""
    if not (data.startswith(b"RIFF") and b"WAVE" in data[:16]):
        return "is not a WAV file (download it from Suno as WAV)"
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
            "provider_id": "suno",
            "configured": self.check_health(),
            "credits_remaining": None,
            "quota_exhausted": None,
            "quota_known": False,
            "quota_status": "manual",
        }

    def license_terms(self) -> ProviderLicenseTerms | None:
        """The owner's declared paid tier and account, or ``None`` (commercially unknown)."""
        tier, account = self._settings.plan_tier, self._settings.account_ref
        if tier not in PAID_TIERS or not account:
            return None
        return ProviderLicenseTerms(
            commercial_use_status=CommercialUseEligibility.ELIGIBLE,
            basis=BASIS,
            license_reference=LICENSE_REFERENCE,
            plan_or_tier=tier,
            account_identity_ref=account,
            terms_version=TERMS_VERSION,
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
            raise RuntimeError(f"{INBOX_VAR} is not set: there is no inbox to import from")
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

    def _write_sheet(self, inbox: Path, requests: Sequence[GenerationRequest]) -> None:
        model = self._settings.model_version or f"(not declared: set {MODEL_VERSION_VAR})"
        by_track: dict[str, list[GenerationRequest]] = {}
        for request in requests:
            by_track.setdefault(request.track_spec_id, []).append(request)
        for track_spec_id, wanted in by_track.items():
            lines = [
                f"PLM songs to make on suno.com for track {track_spec_id}",
                f"Use model {model}. Download each song as WAV and save it in this folder",
                "under the name given. Pick the better of Suno's two clips.",
                "",
            ]
            for n, request in enumerate(wanted, start=1):
                instrumental = request.vocal_mode == "instrumental"
                styles = " ".join([request.genre, *request.style_tags]
                                  + ([f"{request.bpm}bpm"] if request.bpm else []))
                lines += [
                    f"--- Song {n} ---",
                    f"Save as: {self.file_stem(request)}.wav",
                    *([] if instrumental else [
                        f"Lyrics:  save the lyrics as sung in {self.file_stem(request)}.lyrics.txt",
                    ]),
                    f"Instrumental: {'yes' if instrumental else 'no'}",
                    f"Length: about {request.duration_seconds} seconds",
                    f"Styles: {styles}",
                    *([f"Exclude styles: {request.negative_prompt}"]
                      if request.negative_prompt else []),
                    "Prompt:",
                    request.prompt,
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
