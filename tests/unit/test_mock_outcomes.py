"""The deterministic mock outcome model behind P7's and P8's mock sources (R5).

Proves: the same identifiers and instants give the same metrics (also in another
interpreter with a different ``PYTHONHASHSEED``); different arms get different true
effects and therefore different numbers; metrics accrue with elapsed time up to the
window's length; a configured arm effect wins over the seeded one; malformed or missing
configuration is refused. It proves nothing about real audiences: the model is a mock.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from plm_contracts import ObservationWindow, ProviderMode
from plm_platform.config import SettingsError
from plm_platform.testing.mock_outcomes import (
    MOCK_OUTCOMES_FILE_VAR,
    load_mock_outcome_model,
    mock_outcome_model_from_env,
    monthly_periods,
    parse_mock_outcome_model,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_CONFIG = REPO_ROOT / "config" / "dev" / "mock_outcomes.json"
PUBLISHED = datetime(2026, 10, 1, tzinfo=UTC)
MATURE = PUBLISHED + timedelta(days=28)


def _raw(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = json.loads(DEV_CONFIG.read_text(encoding="utf-8"))
    raw.update(overrides)
    return raw


def test_the_dev_configuration_loads_and_is_labelled_mock() -> None:
    model = load_mock_outcome_model(DEV_CONFIG)
    assert model.provider_mode is ProviderMode.MOCK
    assert model.currency == "USD" and model.model_version == "mock-outcomes-v1"


def test_metrics_are_deterministic_for_the_same_inputs() -> None:
    model = parse_mock_outcome_model(_raw())
    first = model.video_snapshot(video_id="v1", arm_id="arm-a", published_at=PUBLISHED,
                                 window=ObservationWindow.D28, observed_at=MATURE)
    again = parse_mock_outcome_model(_raw()).video_snapshot(
        video_id="v1", arm_id="arm-a", published_at=PUBLISHED, window=ObservationWindow.D28,
        observed_at=MATURE)
    assert first == again
    lines = model.statement_lines(isrcs=["USPLM2600001", "USPLM2600002"], arm_id="arm-a",
                                  period_start=PUBLISHED, period_end=MATURE)
    assert lines == model.statement_lines(isrcs=["USPLM2600001", "USPLM2600002"],
                                          arm_id="arm-a", period_start=PUBLISHED,
                                          period_end=MATURE)
    assert all(line.revenue.currency == "USD" and line.streams > 0 for line in lines)


def test_arms_have_different_true_effects_and_so_different_outcomes() -> None:
    model = parse_mock_outcome_model(_raw(arm_effects={"arm-good": "1.8", "arm-poor": "0.4"}))
    good = model.video_snapshot(video_id="v1", arm_id="arm-good", published_at=PUBLISHED,
                                window=ObservationWindow.D28, observed_at=MATURE)
    poor = model.video_snapshot(video_id="v1", arm_id="arm-poor", published_at=PUBLISHED,
                                window=ObservationWindow.D28, observed_at=MATURE)
    assert good.views > 3 * poor.views
    assert good.estimated_revenue.amount > poor.estimated_revenue.amount
    good_line, = model.statement_lines(isrcs=["USPLM2600001"], arm_id="arm-good",
                                       period_start=PUBLISHED, period_end=MATURE)
    poor_line, = model.statement_lines(isrcs=["USPLM2600001"], arm_id="arm-poor",
                                       period_start=PUBLISHED, period_end=MATURE)
    assert good_line.revenue.amount > 3 * poor_line.revenue.amount
    # An arm without a configured effect gets a seeded one inside the declared range.
    seeded = {model.effect(f"arm-{n}") for n in range(20)}
    assert len(seeded) > 15
    assert all(Decimal("0.5") <= effect <= Decimal("1.5") for effect in seeded)
    assert model.effect("arm-good") == Decimal("1.8")


def test_metrics_accrue_with_elapsed_time_up_to_the_window() -> None:
    model = parse_mock_outcome_model(_raw(noise="0"))
    day = [model.video_snapshot(video_id="v", arm_id="a", published_at=PUBLISHED,
                                window=ObservationWindow.D28,
                                observed_at=PUBLISHED + timedelta(days=d)).views
           for d in (0, 7, 28, 40)]
    assert day[0] == 0 and day[0] < day[1] < day[2]
    assert day[3] == day[2]  # a 28d snapshot stops accruing at the window's end
    with pytest.raises(ValueError, match="predate"):
        model.video_snapshot(video_id="v", arm_id="a", published_at=PUBLISHED,
                             window=ObservationWindow.D28,
                             observed_at=PUBLISHED - timedelta(seconds=1))


def test_metrics_are_identical_in_another_interpreter() -> None:
    probe = (
        "from datetime import datetime, timedelta, timezone\n"
        "from plm_contracts import ObservationWindow\n"
        "from plm_platform.testing.mock_outcomes import load_mock_outcome_model\n"
        f"m = load_mock_outcome_model(r'{DEV_CONFIG}')\n"
        "p = datetime(2026, 10, 1, tzinfo=timezone.utc)\n"
        "s = m.video_snapshot(video_id='v1', arm_id='arm-x', published_at=p, "
        "window=ObservationWindow.D28, observed_at=p + timedelta(days=28))\n"
        "print(s.views, s.watch_time_minutes, s.estimated_revenue.amount, m.effect('arm-x'))\n"
    )
    outputs = set()
    for seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                              env=env, check=True, cwd=REPO_ROOT)
        outputs.add(done.stdout.strip())
    assert len(outputs) == 1


@pytest.mark.parametrize(("overrides", "match"), [
    ({"currency": "dollars"}, "currency"),
    ({"views_per_day": 1.5}, "views_per_day"),
    ({"views_per_day": "-1"}, "views_per_day"),
    ({"noise": "1"}, "noise"),
    ({"effect_range": ["2", "1"]}, "effect_range"),
    ({"arm_effects": None}, "arm_effects"),
])
def test_malformed_configuration_is_refused(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(SettingsError, match=match):
        parse_mock_outcome_model(_raw(**overrides))


def test_without_configuration_there_is_no_model(tmp_path: Path) -> None:
    absent = tmp_path / "none.env"
    assert mock_outcome_model_from_env(env={}, dotenv_path=absent) is None
    model = mock_outcome_model_from_env(env={MOCK_OUTCOMES_FILE_VAR: str(DEV_CONFIG)},
                                        dotenv_path=absent)
    assert model is not None and model.model_version == "mock-outcomes-v1"


def test_statement_periods_are_calendar_months_clipped_to_the_span() -> None:
    periods = monthly_periods(datetime(2026, 11, 20, 8, tzinfo=UTC),
                              datetime(2027, 1, 3, tzinfo=UTC))
    assert [p[0] for p in periods] == ["2026-11", "2026-12", "2027-01"]
    assert periods[0][1] == datetime(2026, 11, 20, 8, tzinfo=UTC)
    assert periods[1][1:] == (datetime(2026, 12, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    assert periods[-1][2] == datetime(2027, 1, 3, tzinfo=UTC)
    with pytest.raises(ValueError):
        monthly_periods(PUBLISHED, PUBLISHED - timedelta(days=1))
