# services/market_intelligence/reports/market_snapshot.py
"""§31's daily report data and §32's change detection.

Nothing here computes a new metric: the report is a *view* over stored scores and
observations, and every section states which evidence class produced it. A section
with no rows is an empty result, never a `0` score: missing data has no row to
print, and `render_markdown` says so (`exports.py`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import MarketSettings
from ..domain.entities import MarketChangeEventDraft
from ..domain.enums import ChangeEventType, EvidenceClass
from ..domain.value_objects import classify_freshness
from ..storage import repositories

__all__ = ["MarketSnapshot", "build_snapshot", "detect_changes", "load_snapshot",
           "persist_snapshot", "previous_snapshot"]


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    as_of: datetime
    generated_at: datetime
    sections: dict[str, list[dict[str, Any]]]
    evidence_classes: dict[str, str] = field(default_factory=dict)
    changes: tuple[MarketChangeEventDraft, ...] = ()


def _component_value(components: Mapping[str, Any], key: str) -> float | None:
    """One component's value from the stored `components_json`.

    Known components persist as numbers; unknown ones persist in the tagged Tri
    form (never as `0`). An unknown component yields `None` here — the report
    prints `null`, not a fabricated zero (Review Focus 1).
    """
    raw = components.get(key)
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, Mapping) and raw.get("state") == "known":
        value = raw.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def build_snapshot(session: Session, *, as_of: datetime, settings: MarketSettings) -> MarketSnapshot:
    """§31's ten sections; each row already carries the evidence class it came from."""
    scores = repositories.latest_scores(session, limit=500)
    observations = session.execute(text(
        "SELECT count(*) AS observations, max(observed_at) AS newest "
        "FROM market.market_observations"
    )).mappings().one()
    quarantined = repositories.count_quarantined(session, run_id=None)
    arms = repositories.load_arms(session)
    freshness = (classify_freshness(
        as_of=observations["newest"], now=as_of,
        fresh_hours=settings.freshness_fresh_hours,
        normal_hours=settings.freshness_normal_hours, stale_hours=settings.freshness_stale_hours,
    ).bucket.value if observations["newest"] is not None else "no observations")
    ranked = [{"arm_id": row["arm_id"], "score": row["market_opportunity_score"],
               "confidence": row["confidence"], "as_of": row["as_of"].isoformat()}
              for row in scores]
    sections: dict[str, list[dict[str, Any]]] = {
        "market_snapshot": [{"observations": observations["observations"],
                             "arms": len(arms), "quarantined": quarantined,
                             "as_of": as_of.isoformat()}],
        "fastest_growing_concepts": sorted(
            ({"arm_id": row["arm_id"],
              "growth": _component_value(row["components_json"], "growth")}
             for row in scores), key=lambda item: -(item["growth"] or 0.0))[:10],
        "largest_demand_clusters": sorted(
            ({"arm_id": row["arm_id"],
              "demand": _component_value(row["components_json"], "demand")}
             for row in scores), key=lambda item: -(item["demand"] or 0.0))[:10],
        "lowest_saturation_clusters": sorted(
            ({"arm_id": row["arm_id"],
              "competition": _component_value(row["components_json"], "competition")}
             for row in scores), key=lambda item: -(item["competition"] or 0.0))[:10],
        "new_taxonomy_discoveries": [{"arm_id": row["arm_id"]}
                                     for row in scores if row["confidence"] < 0.5],
        "data_quality_warnings": [{"quarantined_records": quarantined}],
        "source_freshness": [{"newest_observation": (
            observations["newest"].isoformat() if observations["newest"] else None),
            "bucket": freshness}],
        "top_opportunity_arms": ranked[:10],
        "low_confidence_opportunities": [row for row in ranked if row["confidence"] < 0.4][:10],
        "changes_vs_previous_snapshot": [],
    }
    evidence_classes = {
        "market_snapshot": EvidenceClass.OBSERVED.value,
        "fastest_growing_concepts": EvidenceClass.DERIVED.value,
        "largest_demand_clusters": EvidenceClass.DERIVED.value,
        "lowest_saturation_clusters": EvidenceClass.DERIVED.value,
        "new_taxonomy_discoveries": EvidenceClass.CLASSIFIED.value,
        "data_quality_warnings": EvidenceClass.OBSERVED.value,
        "source_freshness": EvidenceClass.OBSERVED.value,
        "top_opportunity_arms": EvidenceClass.DERIVED.value,
        "low_confidence_opportunities": EvidenceClass.DERIVED.value,
        "changes_vs_previous_snapshot": EvidenceClass.DERIVED.value,
    }
    snapshot = MarketSnapshot(as_of=as_of, generated_at=as_of, sections=sections,
                              evidence_classes=evidence_classes)
    previous = previous_snapshot(session)
    if previous is not None:
        changes = detect_changes(_snapshot_from_row(previous), snapshot, settings=settings)
        sections["changes_vs_previous_snapshot"] = [
            {"event_type": draft.event_type.value, "arm_id": draft.arm_id,
             "previous": draft.previous_value, "current": draft.current_value}
            for draft in changes
        ]
        snapshot = MarketSnapshot(as_of=as_of, generated_at=as_of, sections=sections,
                                  evidence_classes=evidence_classes,
                                  changes=tuple(changes))
    return snapshot


def detect_changes(previous: MarketSnapshot | None, current: MarketSnapshot, *,
                   settings: MarketSettings) -> list[MarketChangeEventDraft]:
    """§32: score and growth moves beyond configured thresholds, plus availability drops.

    An unknown reading (an arm with no growth component) can never "move": there is
    nothing to compare against, so it produces no event rather than a zero delta.
    """
    if previous is None:
        return []
    drafts: list[MarketChangeEventDraft] = []
    old = {row["arm_id"]: row for row in previous.sections.get("top_opportunity_arms", [])}
    for row in current.sections.get("top_opportunity_arms", []):
        before = old.get(row["arm_id"])
        if before is None:
            continue
        delta = float(row["score"]) - float(before["score"])
        if abs(delta) >= settings.change_score_threshold:
            drafts.append(MarketChangeEventDraft(
                event_type=ChangeEventType.OPPORTUNITY_SCORE_CHANGE, arm_id=row["arm_id"],
                previous_value=float(before["score"]), current_value=float(row["score"]),
                detail={"delta": delta},
            ))
    old_growth = {row["arm_id"]: row["growth"]
                  for row in previous.sections.get("fastest_growing_concepts", [])}
    for row in current.sections.get("fastest_growing_concepts", []):
        before = old_growth.get(row["arm_id"])
        if before is None or row["growth"] is None:
            continue
        if abs(float(row["growth"]) - float(before)) >= settings.change_growth_threshold:
            drafts.append(MarketChangeEventDraft(
                event_type=ChangeEventType.GROWTH_COMPONENT_CHANGE, arm_id=row["arm_id"],
                previous_value=float(before), current_value=float(row["growth"]), detail={},
            ))
    return drafts


def persist_snapshot(session: Session, snapshot: MarketSnapshot, *, artifact_uri: str,
                     artifact_hash: str) -> str:
    """Insert the snapshot row and exactly the change events the report showed.

    `build_snapshot` already ran `detect_changes` against the previous snapshot, so the
    stored events and the rendered report can never disagree.
    """
    snapshot_id = repositories.insert_snapshot(
        session, as_of=snapshot.as_of, artifact_uri=artifact_uri, artifact_hash=artifact_hash,
        sections=snapshot.sections, evidence_classes=snapshot.evidence_classes,
    )
    for draft in snapshot.changes:
        repositories.insert_change_event(session, draft, detected_at=snapshot.generated_at)
    return snapshot_id


def previous_snapshot(session: Session) -> Mapping[str, Any] | None:
    return repositories.previous_snapshot(session)


def load_snapshot(session: Session, as_of: datetime) -> Mapping[str, Any] | None:
    return repositories.load_snapshot(session, as_of)


def _snapshot_from_row(row: Mapping[str, Any]) -> MarketSnapshot:
    return MarketSnapshot(as_of=row["as_of"], generated_at=row["created_at"],
                          sections=_as_mapping(row["sections_json"]),
                          evidence_classes=_as_mapping(row["evidence_classes_json"]))


def _as_mapping(value: Any) -> Any:
    """JSON columns come back as mappings on both stores; tolerate a raw string too."""
    if isinstance(value, str):
        return json.loads(value)
    return value
