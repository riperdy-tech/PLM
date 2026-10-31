"""Songs the owner makes in Google Flow Music (Lyria 3.5), imported from an inbox (D10).

Owner decision 2026-10-31: Suno's 20 downloads a month cannot supply one 23-song album,
so the owner makes the songs by hand in Flow Music instead (Google AI Pro includes Flow
Music's Plus plan: about 2,000 songs a month and "Commercial use rights"). Flow Music
has no API, and PLM does not drive the site; it works exactly like
:mod:`.suno_manual` (prompt sheet, all-or-nothing inbox, guarded ``music.import``), with
its own identity, terms and sheet wording.

* **Identity.** Provider ``lyria`` (P3's allowlist applies), model
  :data:`MANUAL_MODEL_ID`; the owner declares the model version (``lyria-3.5``).
* **Rights.** The owner declares the plan (``PLM_FLOW_MUSIC_PLAN_TIER``) and account
  (``PLM_FLOW_MUSIC_ACCOUNT_REF``). Only ``google_ai_pro`` yields terms: Google's AI Pro
  benefits page lists Flow Music's Plus plan with "Commercial use rights", and Google's
  Terms of Service say Google won't claim ownership of generated content. Flow Music's
  own terms page was not found (read 2026-10-31); other plans stay commercially unknown.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ..domain.models import GenerationRequest
from .suno_manual import (
    SunoManualAdapter,
    SunoManualSettings,
    load_manual_settings,
    suno_styles,
)

__all__ = ["MANUAL_MODEL_ID", "FlowMusicManualAdapter", "load_flow_music_manual_settings"]

MANUAL_MODEL_ID = "flow-music-web-manual"
"""The model id of a song the owner made in Flow Music's web interface."""

INBOX_VAR = "PLM_FLOW_MUSIC_MANUAL_INBOX"
PLAN_TIER_VAR = "PLM_FLOW_MUSIC_PLAN_TIER"
ACCOUNT_REF_VAR = "PLM_FLOW_MUSIC_ACCOUNT_REF"
MODEL_VERSION_VAR = "PLM_FLOW_MUSIC_MODEL_VERSION"


def load_flow_music_manual_settings(
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
) -> SunoManualSettings:
    """Read the ``PLM_FLOW_MUSIC_*`` declarations (env, then ``.env``)."""
    return load_manual_settings(
        (INBOX_VAR, PLAN_TIER_VAR, ACCOUNT_REF_VAR, MODEL_VERSION_VAR), env, dotenv_path)


class FlowMusicManualAdapter(SunoManualAdapter):
    """Imports owner-made Flow Music songs from the inbox (no call to Google)."""

    paid_tiers = frozenset({"google_ai_pro"})
    terms_version = ("Google Terms of Service effective 2026-07-30; Google AI Pro "
                     "benefits as read 2026-10-31")
    license_reference = ("https://support.google.com/googleone/answer/14534406 "
                         "(Google AI Pro: Flow Music Plus plan, Commercial use rights)")
    basis = ("owner-declared Google AI Pro plan (Flow Music Plus, commercial use rights); "
             "song made by the account owner in Flow Music's interface and downloaded; "
             "Google does not claim ownership of generated content")
    inbox_var = INBOX_VAR
    model_version_var = MODEL_VERSION_VAR

    def __init__(self, settings: SunoManualSettings, *, model_id: str = MANUAL_MODEL_ID) -> None:
        super().__init__(settings, model_id=model_id)

    @property
    def provider_id(self) -> str:
        return "lyria"

    def _sheet_header(self, track_spec_id: str) -> list[str]:
        model = self._settings.model_version or f"(not declared: set {self.model_version_var})"
        return [
            f"PLM songs to make in Google Flow Music for track {track_spec_id}",
            f"Instrument: {model}. Start a new session, paste the Prompt text into the",
            "prompt box and send it. If Flow Music makes more than one song, pick the",
            "better one, download it as WAV and save it in this folder under the name given.",
            "",
        ]

    def _prompt_lines(self, request: GenerationRequest) -> list[str]:
        opening = ("Instrumental only, no vocals" if request.vocal_mode == "instrumental"
                   else "With vocals")
        text = f"{opening}, about {request.duration_seconds} seconds long. {suno_styles(request)}."
        if request.negative_prompt:
            text += f" Avoid: {request.negative_prompt}."
        return [f"Prompt ({len(text)} characters; paste all of it):", text]
