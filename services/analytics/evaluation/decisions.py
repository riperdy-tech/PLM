"""Attach a measured outcome to the allocation decision that caused it (PART XXI).

P2 logs every allocation in the platform decision log (``ops.decision_log``); P9 is the
only phase holding the lineage back to it (``production_directive_id``). A mature or
closed outcome is written as that decision's result for its window. A provisional
outcome is not a realized result and is not attached.

A missing decision is an error, not a skip: every directive is logged when P2 allocates
it, so an outcome whose directive has no decision means the audit trail is broken.
"""

from __future__ import annotations

from datetime import datetime

from plm_contracts.seams import ExperimentLineage
from plm_contracts.seams.c7 import ObservationMaturity
from plm_platform.decision_log import DecisionRecord, allocation_decision_key, record_result
from sqlalchemy.orm import Session

from ..domain.models import ExperimentOutcomeRecord

__all__ = ["attach_outcome_to_decision"]

_REALIZED = (ObservationMaturity.MATURE, ObservationMaturity.CLOSED)


def attach_outcome_to_decision(
    session: Session,
    outcome: ExperimentOutcomeRecord,
    *,
    lineage: ExperimentLineage,
    recorded_at: datetime,
) -> DecisionRecord | None:
    """Record ``outcome`` on its decision; ``None`` when the outcome is still provisional.

    Raises ``DecisionNotFoundError`` when the directive was never logged and
    ``DecisionConflictError`` when the window already holds a different result.
    """
    if outcome.maturity not in _REALIZED:
        return None
    return record_result(
        session,
        allocation_decision_key(lineage.production_directive_id),
        window=outcome.observation_window.value,
        unit_id=outcome.unit_id,
        result={
            "outcome_id": outcome.id,
            "unit_id": outcome.unit_id,
            "experiment_id": outcome.experiment_id,
            "maturity": outcome.maturity.value,
            "normalized_reward": str(outcome.normalized_reward),
            "reward_version": outcome.reward_version,
            "net_contribution": {
                "amount": str(outcome.primary_value),
                "currency": outcome.primary_value_currency,
            },
            "attribution_confidence": str(outcome.attribution_confidence),
        },
        recorded_at=recorded_at,
    )
