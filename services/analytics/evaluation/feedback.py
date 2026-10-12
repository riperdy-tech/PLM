"""Experiment evaluation and feedback reward posting for Phase P9 (Ruling R11, Seam C7 Leg 2).

Implements Ruling R11:
P9 posts normalized rewards to P2's `POST /ar/results`.
P9 owns the reward arithmetic: `normalized_reward`, `reward_version`, window,
maturity, attribution confidence, quality status.
P2 owns posterior updates (P2 §74/§75).
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..domain.enums import ObservationWindow
from ..domain.ids import build_outcome_id
from ..domain.models import ExperimentOutcomeRecord, NormalizedRewardPayload
from ..storage.repositories import (
    insert_experiment_outcome,
    insert_posted_reward,
)

logger = logging.getLogger(__name__)


def compute_normalized_reward(
    net_contribution_usd: float,
    views: int = 0,
    watch_time_minutes: float = 0.0,
) -> float:
    """Compute normalized scalar reward in [0.0, 1.0] from economic and engagement outcomes.

    Uses a bounded smooth logistic function centered at break-even ($0.0 net contribution).
    Guaranteed strictly in range [0.0, 1.0].
    """
    # Scale factor for net contribution: $50 net contribution maps near ~0.73
    economic_signal = net_contribution_usd / 50.0

    # Mild positive bias for engagement
    engagement_signal = math.log1p(views) / 20.0 + math.log1p(watch_time_minutes) / 20.0

    raw_score = economic_signal + engagement_signal

    # Standard logistic sigmoid: 1 / (1 + e^(-raw_score))
    reward = 1.0 / (1.0 + math.exp(-raw_score))

    # Clamp to [0.0, 1.0]
    return max(0.0, min(1.0, round(reward, 4)))


def evaluate_experiment_outcome(
    session: Session,
    *,
    experiment_id: str,
    experiment_family_id: str,
    content_arm_id: str,
    content_genome_id: str,
    unit_id: str,
    net_contribution_usd: float,
    views: int = 0,
    watch_time_minutes: float = 0.0,
    window: ObservationWindow = ObservationWindow.W_7D,
    reward_version: str = "v1",
    attribution_confidence: float = 1.0,
    maturity: str = "mature",
    quality_status: str = "eligible",
) -> ExperimentOutcomeRecord:
    """Evaluate experiment performance and record outcome."""
    now = datetime.now(UTC)
    norm_reward = compute_normalized_reward(net_contribution_usd, views, watch_time_minutes)

    outc_id = build_outcome_id(experiment_id, content_arm_id, window.value)
    outc = ExperimentOutcomeRecord(
        id=outc_id,
        experiment_id=experiment_id,
        experiment_family_id=experiment_family_id,
        content_arm_id=content_arm_id,
        content_genome_id=content_genome_id,
        unit_id=unit_id,
        observation_window=window,
        primary_metric="net_contribution",
        primary_value=round(net_contribution_usd, 4),
        normalized_reward=norm_reward,
        reward_version=reward_version,
        attribution_confidence=round(attribution_confidence, 4),
        maturity=maturity,
        quality_status=quality_status,
        created_at=now,
    )

    insert_experiment_outcome(session, outc)
    return outc


def generate_and_post_reward(
    session: Session,
    outcome: ExperimentOutcomeRecord,
    *,
    views: int = 0,
    watch_time_minutes: float = 0.0,
    average_percentage_viewed: float = 0.0,
    subscribers_gained: int = 0,
    estimated_revenue: float = 0.0,
    http_client: Any = None,
    ar_results_url: str | None = None,
) -> NormalizedRewardPayload:
    """Construct Seam C7 Leg 2 payload and post to P2 (Ruling R11)."""
    now = datetime.now(UTC)

    raw_metrics: dict[str, Any] = {
        "views": float(views),
        "watch_time_minutes": float(watch_time_minutes),
        "average_percentage_viewed": float(average_percentage_viewed),
        "subscriber_gain": float(subscribers_gained),
        "estimated_revenue": float(estimated_revenue),
    }

    payload = NormalizedRewardPayload(
        experiment_id=outcome.experiment_id,
        arm_id=outcome.content_arm_id,
        content_id=outcome.unit_id,
        observation_window=outcome.observation_window.value,
        status=outcome.maturity,
        observed_at=now.isoformat(),
        raw_metrics=raw_metrics,
        normalized_reward=outcome.normalized_reward,
        reward_version=outcome.reward_version,
        attribution_confidence=outcome.attribution_confidence,
    )

    # Optional HTTP dispatch to P2 if client provided
    if http_client is not None and ar_results_url:
        try:
            http_client.post(ar_results_url, json=payload.model_dump())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to post reward to P2: %s", exc)

    # Record to audit table
    insert_posted_reward(session, payload)
    return payload
