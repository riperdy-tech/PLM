# services/market_intelligence/reports/exports.py
"""§31's human-readable and machine-readable exports under `market/reports/` (R13).

Both forms render the same `MarketSnapshot`: the machine form is the stored,
content-addressed artifact a later task reads; the markdown form is what an
operator reads. Money and quota figures never appear in this report without their
unit (§42's counters name theirs where they are emitted — `observability.logging`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from plm_platform.storage import StoredObject, put_content

from .market_snapshot import MarketSnapshot

__all__ = ["render_machine", "render_markdown", "store_markdown", "store_report"]

_TITLES: dict[str, str] = {
    "market_snapshot": "Market snapshot",
    "fastest_growing_concepts": "Fastest-growing concepts",
    "largest_demand_clusters": "Largest demand clusters",
    "lowest_saturation_clusters": "Lowest-saturation attractive clusters",
    "new_taxonomy_discoveries": "New taxonomy discoveries",
    "data_quality_warnings": "Data-quality warnings",
    "source_freshness": "Source freshness",
    "top_opportunity_arms": "Top opportunity arms",
    "low_confidence_opportunities": "Low-confidence opportunities",
    "changes_vs_previous_snapshot": "Changes vs previous snapshot",
}


def render_markdown(snapshot: MarketSnapshot) -> str:
    """§31: every section states its evidence class; speculative text is never 'observed'."""
    lines = [f"# Market Snapshot — {snapshot.as_of.date().isoformat()}", ""]
    for key, title in _TITLES.items():
        rows = snapshot.sections.get(key, [])
        lines.append(f"## {title} ({snapshot.evidence_classes.get(key, 'DERIVED')})")
        if not rows:
            lines.append("_no rows: this is an empty result, not a zero score_")
        for row in rows:
            lines.append(f"- {json.dumps(row, sort_keys=True, default=str)}")
        lines.append("")
    return "\n".join(lines)


def render_machine(snapshot: MarketSnapshot) -> str:
    return json.dumps({"as_of": snapshot.as_of.isoformat(),
                       "generated_at": snapshot.generated_at.isoformat(),
                       "evidence_classes": snapshot.evidence_classes,
                       "sections": snapshot.sections},
                      sort_keys=True, indent=2, default=str) + "\n"


def _put(
    snapshot: MarketSnapshot, body: bytes, content_type: str,
    put_object: Callable[..., StoredObject] | None,
    settings: Any, client: Any,
) -> StoredObject:
    put = put_content if put_object is None else put_object
    return put("market/", f"reports/{snapshot.as_of.date().isoformat()}", body,
               content_type=content_type, settings=settings, client=client)


def store_report(snapshot: MarketSnapshot, *,
                 put_object: Callable[..., StoredObject] | None = None,
                 settings: Any = None, client: Any = None) -> StoredObject:
    """Content-addressed machine-readable artifact; the `market_snapshots` row carries its URI."""
    return _put(snapshot, render_machine(snapshot).encode("utf-8"), "application/json",
                put_object, settings, client)


def store_markdown(snapshot: MarketSnapshot, *,
                   put_object: Callable[..., StoredObject] | None = None,
                   settings: Any = None, client: Any = None) -> StoredObject:
    """The human-readable companion artifact, same date folder, its own digest key."""
    return _put(snapshot, render_markdown(snapshot).encode("utf-8"), "text/markdown",
                put_object, settings, client)
