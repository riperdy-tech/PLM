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
from datetime import datetime
from typing import Any

from plm_contracts import QualityStatus
from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c7 import ObservationMaturity
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


REWARD_VERSION = "engagement-contribution-proxy-v1"
"""The version of :func:`compute_normalized_reward`. It is a bounded proxy of net
contribution and engagement, not expected economic value (economics gate)."""


def evaluate_experiment_outcome(
    session: Session,
    *,
    lineage: ExperimentLineage,
    unit_id: str,
    net_contribution_usd: float,
    views: int,
    watch_time_minutes: float,
    window: ObservationWindow,
    maturity: ObservationMaturity,
    attribution_confidence: float,
    quality_status: QualityStatus | None,
    now: datetime,
) -> ExperimentOutcomeRecord:
    """Evaluate one outcome and record it (ADR-0030).

    Every measurement fact is the caller's: window, maturity, attribution confidence and
    quality are never defaulted. Deriving maturity from publish time is stage R2.
    """
    norm_reward = compute_normalized_reward(net_contribution_usd, views, watch_time_minutes)

    outc_id = build_outcome_id(lineage.experiment_id, lineage.content_arm_id, window.value)
    outc = ExperimentOutcomeRecord(
        id=outc_id,
        experiment_id=lineage.experiment_id,
        experiment_family_id=lineage.experiment_family_id,
        content_arm_id=lineage.content_arm_id,
        content_genome_id=lineage.content_genome_id,
        unit_id=unit_id,
        observation_window=window,
        primary_metric="net_contribution",
        primary_value=round(net_contribution_usd, 4),
        normalized_reward=norm_reward,
        reward_version=REWARD_VERSION,
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
    views: float | None,
    watch_time_minutes: float | None,
    average_percentage_viewed: float | None,
    subscribers_gained: float | None,
    estimated_revenue: float | None,
    now: datetime,
    http_client: Any = None,
    ar_results_url: str | None = None,
) -> NormalizedRewardPayload:
    """Build the Seam C7 leg-2 payload through the shared model (R11, ADR-0030).

    A metric that was not measured is passed as ``None`` and travels as ``null``; it is
    never sent as ``0``. ``now`` stamps ``observed_at``: P2 only ingests observations at
    or before its own cycle clock, so the stamp must come from the caller's clock.
    Delivery to P2 is not guaranteed here; the transactional outbox is stage R3.
    """
    payload = NormalizedRewardPayload.build(
        experiment_id=outcome.experiment_id,
        arm_id=outcome.content_arm_id,
        content_id=outcome.unit_id,
        observation_window=outcome.observation_window,
        status=outcome.maturity,
        observed_at=now,
        raw_metrics={
            "views": views,
            "watch_time_minutes": watch_time_minutes,
            "average_percentage_viewed": average_percentage_viewed,
            "subscriber_gain": subscribers_gained,
            "estimated_revenue": estimated_revenue,
        },
        normalized_reward=outcome.normalized_reward,
        reward_version=outcome.reward_version,
        attribution_confidence=outcome.attribution_confidence,
        quality_status=outcome.quality_status,
    )

    # Optional HTTP dispatch to P2 if client provided
    if http_client is not None and ar_results_url:
        try:
            http_client.post(ar_results_url, json=payload.to_wire())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to post reward to P2: %s", exc)

    # Record to audit table
    insert_posted_reward(session, payload)
    return payload
