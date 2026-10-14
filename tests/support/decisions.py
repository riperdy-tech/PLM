"""Log an allocation decision the way P2's cycle does, for tests that start downstream."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from plm_platform.decision_log import DecisionRecord, allocation_decision_key, record_decision
from sqlalchemy.orm import sessionmaker

__all__ = ["log_allocation_decision"]


def log_allocation_decision(engine: sa.Engine, *, directive_id: str, arm_id: str,
                            decided_at: datetime) -> None:
    with sessionmaker(bind=engine)() as session:
        record_decision(session, DecisionRecord(
            decision_key=allocation_decision_key(directive_id), decision_kind="allocation",
            origin_phase="ar", subject_ref=arm_id, decided_at=decided_at,
            decision=f"exploit allocation to {arm_id}",
            action={"directive_id": directive_id}, input_data={"fixture": True},
            model_version="p2-nn-v1", policy_version="p2-v1", confidence=None,
        ))
        session.commit()
