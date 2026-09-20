# services/autonomous_ar/ingestion/result_adapter.py
"""§72's result contract: validate, map to `RewardObservation`, store idempotently.

P9 owns the arithmetic (R11): `normalized_reward` is required and range-checked here, and
this module never derives it from raw metrics. Nulls stay unknown end to end. The economic
terms are C7's reserved names only (ADR-0023 #4) — a result that carries an invented term
is refused, not stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from plm_contracts import ObservationWindow, QualityStatus
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from ..domain.enums import ObservationMaturity
from ..domain.reward import RESERVED_VALUE_TERMS, RewardObservation, tri_from_optional
from ..storage import repositories

__all__ = ["ResultPayload", "ResultValidationError", "StoredResult", "parse_result_payload",
           "store_result"]


class ResultValidationError(ValueError):
    """§72 validation failed; `field_paths` names the offending fields (P3 §81 shape)."""

    def __init__(self, message: str, field_paths: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.field_paths = field_paths


class ResultPayload(BaseModel):
    """The §72 wire object, plus the C7-freeze-resolving optional `quality_status`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str = Field(min_length=1)
    arm_id: str = Field(min_length=1)
    content_id: str = Field(min_length=1)
    observation_window: ObservationWindow
    status: ObservationMaturity
    observed_at: datetime
    raw_metrics: dict[str, float | None]
    normalized_reward: float = Field(ge=0.0, le=1.0)
    reward_version: str = Field(min_length=1)
    attribution_confidence: float = Field(ge=0.0, le=1.0)
    quality_status: QualityStatus | None = None
    economic_terms: dict[str, float | None] | None = None

    @field_validator("economic_terms")
    @classmethod
    def _economic_terms_are_reserved_names_only(
        cls, value: dict[str, float | None] | None,
    ) -> dict[str, float | None] | None:
        """Field-level so a refusal names `economic_terms` (plan ruling: field paths, §81)."""
        if value:
            unknown = set(value) - set(RESERVED_VALUE_TERMS)
            if unknown:
                raise ValueError(
                    f"economic_terms may only carry reserved C7 names (ADR-0023 #4), got "
                    f"{sorted(unknown)}"
                )
        return value

    @field_validator("observed_at")
    @classmethod
    def _observed_at_is_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        return value


@dataclass(frozen=True, slots=True)
class StoredResult:
    observation_id: str
    experiment_result_id: str
    stored: bool


def parse_result_payload(payload: dict[str, Any]) -> RewardObservation:
    """§72 payload → domain object, with `field_paths` on every refusal."""
    try:
        parsed = ResultPayload.model_validate(payload)
    except ValidationError as exc:
        paths = tuple(".".join(str(part) for part in error["loc"]) for error in exc.errors())
        raise ResultValidationError(
            f"result payload failed validation: {paths}", field_paths=paths
        ) from exc
    return RewardObservation(
        experiment_id=parsed.experiment_id, arm_id=parsed.arm_id, content_id=parsed.content_id,
        observation_window=parsed.observation_window, maturity=parsed.status,
        observed_at=parsed.observed_at,
        raw_metrics={name: tri_from_optional(value)
                     for name, value in parsed.raw_metrics.items()},
        normalized_reward=tri_from_optional(parsed.normalized_reward),
        reward_version=parsed.reward_version,
        attribution_confidence=parsed.attribution_confidence,
        quality_status=parsed.quality_status,
        economic_terms={name: tri_from_optional(value)
                        for name, value in (parsed.economic_terms or {}).items()},
    )


def store_result(session: Any, payload: dict[str, Any], *, brand_id: str, now: datetime,
                 require_known_arm: bool = True) -> StoredResult:
    """Validate (§72), store idempotently (§73), and mirror the canonical result row."""
    observation = parse_result_payload(payload)
    if require_known_arm:
        states = {row["arm_id"] for row in repositories.load_arm_states(session)}
        if observation.arm_id not in states:
            raise ResultValidationError(
                f"unknown arm {observation.arm_id!r}: a result may only update an arm P2 "
                "already knows (§72 'arm exists'; R15: P1 mints arms)",
                field_paths=("arm_id",),
            )
    observation_id, stored = repositories.insert_reward_observation(
        session, observation, brand_id=brand_id, received_at=now)
    # `insert_experiment_result` is itself idempotent (deterministic id, exists-check), so
    # calling it on a replay returns the true stored result id instead of guessing one.
    experiment_result_id = repositories.insert_experiment_result(
        session, observation, brand_id=brand_id)
    return StoredResult(observation_id=observation_id,
                        experiment_result_id=experiment_result_id, stored=stored)
