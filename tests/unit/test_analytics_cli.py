"""The P9 CLI refuses missing facts instead of filling them in (ADR-0031).

Every refusal happens before a database engine is built, so these run without one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer
from analytics.cli import MISSING_FACT_EXIT_CODE, cli_ingest

YOUTUBE: dict[str, Any] = {
    "video_id": "vid_cli",
    "channel_id": "chan_cli",
    "date_str": "2026-09-26",
    "window": "7d",
    "observed_at": "2026-09-26T00:00:00+00:00",
    "views": 10,
}
COST: dict[str, Any] = {
    "entity_id": "vid_cli",
    "entity_type": "video",
    "category": "compute",
    "amount": "1.25",
    "currency": "USD",
    "period": "2026-09",
    "incurred_at": "2026-09-26T00:00:00+00:00",
}


def _refused(capsys: pytest.CaptureFixture[str], **kwargs: Any) -> str:
    with pytest.raises(typer.Exit) as exc:
        cli_ingest(**kwargs)
    assert exc.value.exit_code == MISSING_FACT_EXIT_CODE == 2
    return capsys.readouterr().err


def _write(tmp_path: Path, data: dict[str, Any]) -> str:
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("missing", ["video_id", "channel_id", "date_str", "window", "observed_at"])
def test_youtube_without_an_identifying_fact_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
    data = {k: v for k, v in YOUTUBE.items() if k != missing}
    err = _refused(capsys, source="youtube", file_path=_write(tmp_path, data))
    assert missing in err


def test_youtube_revenue_without_currency_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    err = _refused(capsys, source="youtube",
                   file_path=_write(tmp_path, {**YOUTUBE, "estimated_revenue": "1.50"}))
    assert "revenue_currency" in err


@pytest.mark.parametrize("missing", ["entity_id", "entity_type", "category", "amount", "currency",
                                     "period", "incurred_at"])
def test_cost_without_a_fact_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
    data = {k: v for k, v in COST.items() if k != missing}
    err = _refused(capsys, source="cost", file_path=_write(tmp_path, data))
    assert missing in err


@pytest.mark.parametrize("missing", ["provider", "period", "currency"])
def test_distributor_without_declared_facts_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
    path = tmp_path / "statement.csv"
    path.write_text("isrc,streams,revenue\nUSPLM2600001,1,1.00\n", encoding="utf-8")
    facts = {"provider": "spotify", "period": "2026-09", "currency": "USD"}
    facts[missing] = None  # type: ignore[assignment]
    err = _refused(capsys, source="distributor", file_path=str(path), **facts)
    assert missing in err
