"""Unit tests for the golden-file and clock/seeding helpers.

``tests/support/golden.py`` and ``tests/support/clock.py`` are exercised
including the failure and update paths, because those are the paths a phase's
test author will actually hit.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.support.clock import DEFAULT_SEED, DEFAULT_START, frozen_time, seed
from tests.support.golden import UPDATE_ENV_VAR, assert_golden, update_enabled


def test_assert_golden_passes_on_match(tmp_path: Path) -> None:
    golden = tmp_path / "artifact.txt"
    golden.write_text("hello\n", encoding="utf-8", newline="")
    assert_golden("hello\n", golden)


def test_assert_golden_reports_a_diff(tmp_path: Path) -> None:
    golden = tmp_path / "artifact.txt"
    golden.write_text("line one\nline two\n", encoding="utf-8", newline="")
    with pytest.raises(AssertionError) as info:
        assert_golden("line one\nline CHANGED\n", golden)
    message = str(info.value)
    assert str(golden) in message
    assert "-line two" in message
    assert "+line CHANGED" in message
    assert UPDATE_ENV_VAR in message


def test_assert_golden_missing_file_points_at_the_update_path(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception) as info:
        assert_golden("x", tmp_path / "not-there.txt")
    assert UPDATE_ENV_VAR in str(info.value)


def test_update_path_rewrites_and_skips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(UPDATE_ENV_VAR, "1")
    golden = tmp_path / "nested" / "artifact.txt"
    with pytest.raises(pytest.skip.Exception) as info:
        assert_golden("fresh content\n", golden)
    assert golden.read_text(encoding="utf-8") == "fresh content\n"
    assert UPDATE_ENV_VAR in str(info.value)


def test_update_enabled_truthiness(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(UPDATE_ENV_VAR, raising=False)
    assert not update_enabled()
    for falsy in ("0", "false", "no", "  "):
        monkeypatch.setenv(UPDATE_ENV_VAR, falsy)
        assert not update_enabled(), falsy
    monkeypatch.setenv(UPDATE_ENV_VAR, "1")
    assert update_enabled()


def test_binary_mismatch_reports_the_first_difference(tmp_path: Path) -> None:
    golden = tmp_path / "render.bin"
    golden.write_bytes(b"\x00\x01\x02\x03")
    with pytest.raises(AssertionError) as info:
        assert_golden(b"\x00\x01\xff\x03", golden)
    message = str(info.value)
    assert "binary" in message
    assert "first difference at byte 2" in message


def test_frozen_clock_stops_and_advances_time() -> None:
    with frozen_time("2026-03-01T00:00:00+00:00") as clock:
        assert datetime.now(UTC) == datetime(2026, 3, 1, tzinfo=UTC)
        later = clock.advance(timedelta(hours=24))
        assert later == datetime(2026, 3, 2, tzinfo=UTC)
        assert datetime.now(UTC) == later


def test_frozen_clock_can_jump_absolutely() -> None:
    with frozen_time("2026-03-01T00:00:00+00:00") as clock:
        moved = clock.set("2026-03-05T12:00:00+00:00")
        assert moved == datetime(2026, 3, 5, 12, tzinfo=UTC)


def test_frozen_clock_fixture_starts_at_the_documented_default(frozen_clock: object) -> None:
    assert datetime.now(UTC) == datetime.fromisoformat(DEFAULT_START)


def test_seed_is_reproducible() -> None:
    seed(7)
    first = [random.random() for _ in range(3)]
    seed(7)
    again = [random.random() for _ in range(3)]
    assert first == again
    seed(8)
    assert random.random() != first[0]


def test_seed_returns_its_value_for_one_liner_fixtures() -> None:
    assert seed(41) == 41


def test_seeded_rng_fixture_is_deterministic(seeded_rng: random.Random) -> None:
    assert seeded_rng.random() == random.Random(DEFAULT_SEED).random()
