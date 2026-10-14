"""Experiment evaluation and feedback reward posting for Phase P9 (Ruling R11, Seam C7 Leg 2).

Implements Ruling R11:
P9 posts normalized rewards to P2's `POST /ar/results`.
P9 owns the reward arithmetic: `normalized_reward`, `reward_version`, window,
maturity, attribution confidence, quality status.
P2 owns posterior updates (P2 §74/§75).

The reward is ``engagement-contribution-proxy-v1`` (ADR-0031): a bounded proxy built
from net contribution and engagement. It is **not** expected economic value and must
never be read or labelled as such. Maturity is derived from the publication time and the
window (`plm_contracts.measurement.maturity_at`), never asserted by a caller.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from decimal import Decimal
from typing import Any

from plm_contracts import QualityStatus
from plm_contracts.measurement import maturity_at
from plm_contracts.money import CurrencyMismatchError, FxTable, Money
from plm_contracts.seams import ExperimentLineage
from plm_platform.clock import require_aware
from sqlalchemy.orm import Session

from ..domain.enums import ObservationWindow
from ..domain.ids import build_outcome_id
from ..domain.models import ExperimentOutcomeRecord, NormalizedRewardPayload
from ..storage.repositories import (
    insert_experiment_outcome,
    insert_posted_reward,
)

__all__ = [
    "REWARD_CONTRIBUTION_SCALE",
    "REWARD_VERSION",
    "compute_normalized_reward",
    "evaluate_experiment_outcome",
    "generate_and_post_reward",
    "reward_contribution_scale",
]

logger = logging.getLogger(__name__)

REWARD_VERSION = "engagement-contribution-proxy-v1"
"""The version of :func:`compute_normalized_reward`. It is a bounded proxy of net
contribution and engagement, not expected economic value (economics gate, ADR-0031)."""

REWARD_CONTRIBUTION_SCALE = Money.of(50, "USD")
"""Part of the v1 proxy's definition: a net contribution of one scale unit adds ``1`` to
the logistic's argument. It has always been USD 50; in another reporting currency it is
converted through the FX table at the observation instant, so the proxy keeps its shape
and stays reproducible from the recorded rates."""


def _sigmoid(x: float) -> float:
    """Logistic function that cannot overflow for large ``|x|``."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def compute_normalized_reward(
    net_contribution: Money,
    views: int,
    watch_time_minutes: float,
    *,
    contribution_scale: Money,
) -> float:
    """The ``engagement-contribution-proxy-v1`` score in ``[0.0, 1.0]``. A proxy only.

    ``sigmoid(net / scale + log1p(views) / 20 + log1p(watch_time_minutes) / 20)``,
    rounded to 4 places. Non-decreasing in each input with the others fixed; break-even
    with no engagement is 0.5. ``net_contribution`` and ``contribution_scale`` must be in
    the same currency (the reporting currency). Views and watch time are required
    measurements: there is no default, and negatives are refused.
    """
    if views < 0 or watch_time_minutes < 0:
        raise ValueError("views and watch time cannot be negative")
    if contribution_scale.amount <= 0:
        raise ValueError("the contribution scale must be positive")
    ratio = net_contribution.ratio_to(contribution_scale)
    assert ratio is not None  # scale is positive
    economic_signal = float(ratio)
    engagement_signal = math.log1p(views) / 20.0 + math.log1p(watch_time_minutes) / 20.0
    reward = _sigmoid(economic_signal + engagement_signal)
    return max(0.0, min(1.0, round(reward, 4)))


def reward_contribution_scale(reporting_currency: str, *, fx: FxTable, as_of: datetime) -> Money:
    """`REWARD_CONTRIBUTION_SCALE` in the reporting currency; a missing rate raises."""
    return fx.convert(REWARD_CONTRIBUTION_SCALE, reporting_currency, as_of=as_of).converted


def evaluate_experiment_outcome(
    session: Session,
    *,
    lineage: ExperimentLineage,
    unit_id: str,
    net_contribution: Money,
    reporting_currency: str,
    fx: FxTable,
    views: int,
    watch_time_minutes: float,
    window: ObservationWindow,
    published_at: datetime,
    closed_by_source: bool = False,
    attribution_confidence: float,
    quality_status: QualityStatus | None,
    observed_at: datetime,
) -> ExperimentOutcomeRecord:
    """Evaluate one outcome and record it (ADR-0030, ADR-0031).

    * ``net_contribution`` must already be in ``reporting_currency`` (convert first);
      another currency raises `CurrencyMismatchError`.
    * Maturity is derived: ``maturity_at(published_at, window, as_of=observed_at)``. An
      observation that precedes the publication raises ``ValueError``. Provisional
      outcomes are still produced; P2 records them without applying them.
    * ``attribution_confidence`` is passed through unchanged; it does not change the
      reward (P2 quarantines anything below its policy floor).
    """
    observed = require_aware(observed_at)
    published = require_aware(published_at)
    if net_contribution.currency != reporting_currency:
        raise CurrencyMismatchError(
            f"net contribution is in {net_contribution.currency}; convert it to the "
            f"reporting currency {reporting_currency} first"
        )
    maturity = maturity_at(
        published_at=published, window=window, as_of=observed, closed_by_source=closed_by_source
    )
    scale = reward_contribution_scale(reporting_currency, fx=fx, as_of=observed)
    norm_reward = compute_normalized_reward(
        net_contribution, views, watch_time_minutes, contribution_scale=scale
    )

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
        primary_value=float(net_contribution.amount.quantize(Decimal("0.0001"))),
        primary_value_currency=net_contribution.currency,
        normalized_reward=norm_reward,
        reward_version=REWARD_VERSION,
        attribution_confidence=round(attribution_confidence, 4),
        maturity=maturity,
        published_at=published,
        quality_status=quality_status,
        created_at=observed,
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
    estimated_revenue: Money | None,
    now: datetime,
    http_client: Any = None,
    ar_results_url: str | None = None,
) -> NormalizedRewardPayload:
    """Build the Seam C7 leg-2 payload through the shared model (R11, ADR-0030, ADR-0031).

    A metric that was not measured is passed as ``None`` and travels as ``null``; it is
    never sent as ``0``. ``estimated_revenue`` is a platform estimate in the outcome's
    reporting currency; the C7 wire carries it as an unlabelled float in that currency
    (ADR-0031 defers moving it to `Money`). ``now`` stamps ``observed_at``: P2 only
    ingests observations at or before its own cycle clock, so the stamp must come from
    the caller's clock. Delivery to P2 is not guaranteed here; the transactional outbox
    is stage R3.
    """
    if estimated_revenue is not None and estimated_revenue.currency != outcome.primary_value_currency:
        raise CurrencyMismatchError(
            f"estimated revenue is in {estimated_revenue.currency}; the outcome is reported "
            f"in {outcome.primary_value_currency}"
        )
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
            "estimated_revenue": (
                float(estimated_revenue.amount) if estimated_revenue is not None else None
            ),
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
