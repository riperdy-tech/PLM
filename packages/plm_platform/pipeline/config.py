"""Deployment configuration the publishing and learning steps need (R5).

Read from the JSON file named by ``PLM_PIPELINE_CONFIG_FILE`` (process environment, then
``.env``). Every section is optional at boot, so a worker without it still runs the
steps that need nothing; a step whose section is missing refuses with
``pipeline_config_missing`` instead of inventing a value. A file that is present but
malformed fails the worker at boot (:class:`~plm_platform.config.SettingsError`).

The file::

    {
      "publish": {"channel_id": "UC...", "operating_mode": "autonomous"},
      "release": {"artist_name": "...", "label_name": "...", "copyright_line": "...",
                  "publisher_line": "...", "territories": "WORLD",
                  "release_date_offset_days": 0},
      "fx_rates": [{"base": "USD", "quote": "KRW", "rate": "1380.00",
                    "source": "...", "as_of": "2026-01-01T00:00:00+00:00"}],
      "learning": {"slot_count": 3, "budget": 30.0, "policy_version": "...",
                   "activation_state": "AUTONOMOUS"}
    }

* ``publish``: the operator-configured P7 channel and P7's operating mode. P7 refuses a
  channel it has no profile for (``channel_not_configured``).
* ``release``: P8's release-level facts (P8 §11), which P8 never defaults. The release
  date is the submission instant's date plus ``release_date_offset_days``.
* ``fx_rates``: rates P9 records before it measures, so amounts convert into its
  reporting currency from recorded rates (ADR-0031); a missing rate stays a refusal.
* ``learning``: the second allocation cycle P2 runs after the posterior update.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from ..config import SettingsError
from ..dotenv import load_env_file
from ..temporal import ValidationFailure

__all__ = [
    "PIPELINE_CONFIG_FILE_VAR",
    "FxRateConfig",
    "LearningCycleConfig",
    "PipelineConfig",
    "PipelineConfigMissing",
    "PublishConfig",
    "ReleaseConfig",
    "load_pipeline_config",
    "parse_pipeline_config",
]

PIPELINE_CONFIG_FILE_VAR: Final[str] = "PLM_PIPELINE_CONFIG_FILE"


class PipelineConfigMissing(ValidationFailure):
    """A step needs a configuration section the worker was not given."""

    code = "pipeline_config_missing"

    def __init__(self, section: str, step: str) -> None:
        self.section = section
        super().__init__(
            f"{step} needs the {section!r} section of {PIPELINE_CONFIG_FILE_VAR}; "
            "nothing is defaulted"
        )


@dataclass(frozen=True, slots=True)
class PublishConfig:
    channel_id: str
    operating_mode: str


@dataclass(frozen=True, slots=True)
class ReleaseConfig:
    artist_name: str
    label_name: str
    copyright_line: str
    publisher_line: str
    territories: str
    release_date_offset_days: int


@dataclass(frozen=True, slots=True)
class FxRateConfig:
    base: str
    quote: str
    rate: str
    source: str
    as_of: str

    def wire(self) -> dict[str, str]:
        return {"base": self.base, "quote": self.quote, "rate": self.rate,
                "source": self.source, "as_of": self.as_of}


@dataclass(frozen=True, slots=True)
class LearningCycleConfig:
    slot_count: int
    budget: float
    policy_version: str
    activation_state: str


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    publish: PublishConfig | None = None
    release: ReleaseConfig | None = None
    fx_rates: tuple[FxRateConfig, ...] = ()
    learning: LearningCycleConfig | None = None

    def require_publish(self, step: str) -> PublishConfig:
        if self.publish is None:
            raise PipelineConfigMissing("publish", step)
        return self.publish

    def require_release(self, step: str) -> ReleaseConfig:
        if self.release is None:
            raise PipelineConfigMissing("release", step)
        return self.release

    def require_learning(self, step: str) -> LearningCycleConfig:
        if self.learning is None:
            raise PipelineConfigMissing("learning", step)
        return self.learning


def _text(section: Mapping[str, Any], key: str, where: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{where}.{key} must be a non-empty string")
    return value.strip()


def _int(section: Mapping[str, Any], key: str, where: str, *, minimum: int) -> int:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SettingsError(f"{where}.{key} must be an integer >= {minimum}")
    return value


def _section(raw: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SettingsError(f"{key} must be an object")
    return value


def _fx_rate(item: Any, index: int) -> FxRateConfig:
    where = f"fx_rates[{index}]"
    if not isinstance(item, dict):
        raise SettingsError(f"{where} must be an object")
    rate = _text(item, "rate", where)
    try:
        if Decimal(rate) <= 0:
            raise SettingsError(f"{where}.rate must be positive")
    except InvalidOperation:
        raise SettingsError(f"{where}.rate must be a decimal string") from None
    as_of = _text(item, "as_of", where)
    try:
        moment = datetime.fromisoformat(as_of)
    except ValueError:
        raise SettingsError(f"{where}.as_of must be an ISO 8601 instant") from None
    if moment.tzinfo is None:
        raise SettingsError(f"{where}.as_of must carry a timezone")
    return FxRateConfig(base=_text(item, "base", where).upper(),
                        quote=_text(item, "quote", where).upper(), rate=rate,
                        source=_text(item, "source", where), as_of=as_of)


def parse_pipeline_config(raw: Mapping[str, Any]) -> PipelineConfig:
    """Validate a parsed configuration document; anything malformed raises."""
    publish = _section(raw, "publish")
    release = _section(raw, "release")
    learning = _section(raw, "learning")
    rates = raw.get("fx_rates", [])
    if not isinstance(rates, list):
        raise SettingsError("fx_rates must be a list")
    budget: float | None = None
    if learning is not None:
        value = learning.get("budget")
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            raise SettingsError("learning.budget must be a positive number")
        budget = float(value)
    return PipelineConfig(
        publish=None if publish is None else PublishConfig(
            channel_id=_text(publish, "channel_id", "publish"),
            operating_mode=_text(publish, "operating_mode", "publish")),
        release=None if release is None else ReleaseConfig(
            artist_name=_text(release, "artist_name", "release"),
            label_name=_text(release, "label_name", "release"),
            copyright_line=_text(release, "copyright_line", "release"),
            publisher_line=_text(release, "publisher_line", "release"),
            territories=_text(release, "territories", "release"),
            release_date_offset_days=_int(release, "release_date_offset_days", "release",
                                          minimum=0)),
        fx_rates=tuple(_fx_rate(item, n) for n, item in enumerate(rates)),
        learning=None if learning is None or budget is None else LearningCycleConfig(
            slot_count=_int(learning, "slot_count", "learning", minimum=1), budget=budget,
            policy_version=_text(learning, "policy_version", "learning"),
            activation_state=_text(learning, "activation_state", "learning")),
    )


def load_pipeline_config(env: Mapping[str, str] | None = None,
                         dotenv_path: str | Path | None = None) -> PipelineConfig:
    """The configuration named by ``PLM_PIPELINE_CONFIG_FILE``; unset: every section absent."""
    process_env: Mapping[str, str] = os.environ if env is None else env
    dotenv_values = load_env_file(Path(".env") if dotenv_path is None else dotenv_path)
    path = (process_env.get(PIPELINE_CONFIG_FILE_VAR)
            or dotenv_values.get(PIPELINE_CONFIG_FILE_VAR) or "").strip()
    if not path:
        return PipelineConfig()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SettingsError(f"{PIPELINE_CONFIG_FILE_VAR}={path!r} is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise SettingsError(f"{PIPELINE_CONFIG_FILE_VAR}={path!r} must hold a JSON object")
    return parse_pipeline_config(raw)
