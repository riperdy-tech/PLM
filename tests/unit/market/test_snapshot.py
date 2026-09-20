# tests/unit/market/test_snapshot.py
"""§31's ten sections, in both forms: machine-readable JSON and the markdown report.

The golden file pins the empty-database report: every count in `market_snapshot` is a
real `0` because the query returned, and every section with no rows says so instead of
printing a score that was never observed.
"""

import json
from datetime import UTC, datetime

from services.market_intelligence.config import load_market_settings
from services.market_intelligence.reports.exports import render_machine, render_markdown
from services.market_intelligence.reports.market_snapshot import build_snapshot
from tests.support.golden import assert_golden, golden_path

AS_OF = datetime(2026, 9, 20, 6, 0, tzinfo=UTC)

TEN_SECTIONS = {
    "market_snapshot", "fastest_growing_concepts", "largest_demand_clusters",
    "lowest_saturation_clusters", "new_taxonomy_discoveries", "data_quality_warnings",
    "source_freshness", "top_opportunity_arms", "low_confidence_opportunities",
    "changes_vs_previous_snapshot",
}


def test_the_ten_spec_sections_exist_and_are_labelled(c1_api_client, market_session) -> None:
    snapshot = build_snapshot(market_session, as_of=AS_OF, settings=load_market_settings())
    assert set(snapshot.sections) == TEN_SECTIONS
    assert set(snapshot.evidence_classes) == TEN_SECTIONS
    assert set(snapshot.evidence_classes.values()) <= {"OBSERVED", "DERIVED", "CLASSIFIED",
                                                       "ASSUMED"}


def test_the_machine_readable_form_carries_the_same_ten_sections(market_session) -> None:
    snapshot = build_snapshot(market_session, as_of=AS_OF, settings=load_market_settings())
    payload = json.loads(render_machine(snapshot))
    assert set(payload["sections"]) == TEN_SECTIONS
    assert payload["as_of"] == AS_OF.isoformat()
    assert payload["evidence_classes"] == {key: str(value) for key, value in
                                           snapshot.evidence_classes.items()}


def test_an_empty_section_says_so_rather_than_printing_a_zero(market_session) -> None:
    snapshot = build_snapshot(market_session, as_of=AS_OF, settings=load_market_settings())
    rendered = render_markdown(snapshot)
    # `top_opportunity_arms` has no rows in an empty database: the report must say the
    # result is empty, never fabricate a `0` score for an arm that was never scored.
    section = rendered.split("## Top opportunity arms", 1)[1].split("## ", 1)[0]
    assert "no rows" in section
    assert "0" not in section.replace("(DERIVED)", "")


def test_the_rendered_report_is_stable(market_session) -> None:
    snapshot = build_snapshot(market_session, as_of=AS_OF, settings=load_market_settings())
    rendered = render_markdown(snapshot)
    assert rendered.startswith("# Market Snapshot — 2026-09-20")
    assert "OBSERVED" in rendered and "DERIVED" in rendered
    assert_golden(rendered, golden_path("market", "market_snapshot.md"))
