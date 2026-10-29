"""Guarded 0038 clone preflight: unit tests never connect to PostgreSQL."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.support import migration_clone_preflight as checker
from tests.support.migration_review_compare import RELEASE, YOUTUBE
from tests.unit.test_migration_review_compare import _cases

CLONE = "plm_migration_disposable_reviewcheck"


@pytest.mark.parametrize("revision", [RELEASE, YOUTUBE])
def test_exact_historical_clone_snapshot_is_only_read_only_preflight(revision: str) -> None:
    report, _, _ = _cases(revision)
    report["database"] = CLONE
    decision = checker.preflight_clone_snapshot(
        report, configured_database=CLONE,
        intended_database=CLONE, starting_revision=revision,
    )
    assert decision == {
        "passed": True, "authorizes_migration": False, "failures": [],
    }


@pytest.mark.parametrize("revision", [RELEASE, YOUTUBE])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database", "plm"),
        ("alembic_revision", "0037_dashboard_views"),
        ("read_only", 1),
        ("transaction_isolation", "read_committed"),
        ("youtube_manifest_storage", None),
        ("youtube_manifest_integrity", None),
        ("youtube_manifest_security", None),
        ("youtube_manifest_schema", None),
        ("youtube_manifest_common", None),
        ("youtube_all_null_selection", None),
    ],
)
def test_wrong_target_or_missing_historical_facts_are_refused(
    revision: str, field: str, value: object,
) -> None:
    report, _, _ = _cases(revision)
    report["database"] = CLONE
    report[field] = value
    result = checker.preflight_clone_snapshot(
        report, configured_database=CLONE,
        intended_database=CLONE, starting_revision=revision,
    )
    assert result["passed"] is False
    assert result["authorizes_migration"] is False


@pytest.mark.parametrize("revision", [RELEASE, YOUTUBE])
def test_refuses_wrong_configured_target_and_unknown_revision(revision: str) -> None:
    report, _, _ = _cases(revision)
    report["database"] = CLONE
    for configured, expected in (("plm", revision), (CLONE, "0039_merge_0038_branches")):
        result = checker.preflight_clone_snapshot(
            report, configured_database=configured,
            intended_database=CLONE, starting_revision=expected,
        )
        assert not result["passed"] and not result["authorizes_migration"]


@pytest.mark.parametrize(
    ("revision", "field", "replacement"),
    [
        (RELEASE, "release_review_storage", None),
        (RELEASE, "release_review_security", None),
        (RELEASE, "release_review_schema", None),
        (RELEASE, "release_review_access", None),
        (RELEASE, "release_reviews", None),
        (RELEASE, "release_review_events", None),
        (YOUTUBE, "release_review_storage", {"unexpected": True}),
        (YOUTUBE, "release_reviews", {"count": 0, "sha256": "a" * 64}),
        (YOUTUBE, "youtube_selection_column_contract", None),
        (YOUTUBE, "youtube_selection", None),
        (YOUTUBE, "youtube_selection_nonnull", True),
    ],
)
def test_branch_specific_missing_or_unexpected_evidence_is_refused(
    revision: str, field: str, replacement: object,
) -> None:
    report, _, _ = _cases(revision)
    report["database"] = CLONE
    report[field] = replacement
    result = checker.preflight_clone_snapshot(
        report, configured_database=CLONE,
        intended_database=CLONE, starting_revision=revision,
    )
    assert result["passed"] is False


@pytest.mark.parametrize("revision", [RELEASE, YOUTUBE])
def test_clone_preflight_rejects_invalid_fingerprint_and_extra_fields(revision: str) -> None:
    report, _, _ = _cases(revision)
    report["database"] = CLONE
    report["youtube_manifest_common"]["count"] = 0
    result = checker.preflight_clone_snapshot(
        report, configured_database=CLONE,
        intended_database=CLONE, starting_revision=revision,
    )
    assert not result["passed"]
    report, _, _ = _cases(revision)
    report["database"] = CLONE
    report["extra_untrusted_metadata"] = True
    assert not checker.preflight_clone_snapshot(
        report, configured_database=CLONE,
        intended_database=CLONE, starting_revision=revision,
    )["passed"]


def test_cli_refuses_shared_database_before_connecting(monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    settings = object()
    monkeypatch.setattr("plm_platform.config.load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(
        "plm_platform.db.database_url",
        lambda configured: SimpleNamespace(database="plm"),
    )

    def unexpected_snapshot(**kwargs: object) -> None:
        raise AssertionError("pre-connection guard was bypassed")

    monkeypatch.setattr(checker, "read_only_review_snapshot", unexpected_snapshot)
    assert checker.main([CLONE, RELEASE]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert payload["authorizes_migration"] is False


def test_cli_pins_configured_settings_for_read_only_snapshot(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    settings = object()
    monkeypatch.setattr("plm_platform.config.load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(
        "plm_platform.db.database_url",
        lambda configured: SimpleNamespace(database=CLONE),
    )
    observed: list[object] = []

    def snapshot(*, settings: object) -> dict:
        observed.append(settings)
        value, _, _ = _cases(RELEASE)
        value["database"] = CLONE
        return value

    monkeypatch.setattr(checker, "read_only_review_snapshot", snapshot)
    assert checker.main([CLONE, RELEASE]) == 0
    assert observed == [settings]
    assert json.loads(capsys.readouterr().out) == {
        "passed": True, "authorizes_migration": False, "failures": [],
    }


def test_cli_invalid_arguments_never_connect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        checker, "read_only_review_snapshot",
        lambda **kwargs: pytest.fail("invalid CLI connected"),
    )
    assert checker.main([]) == 2
    assert checker.main([CLONE, "head"]) == 2
    lines = capsys.readouterr().out.splitlines()
    assert all(json.loads(line)["authorizes_migration"] is False for line in lines)


def test_cli_hides_exception_details_and_refuses_approval(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    settings = object()
    monkeypatch.setattr("plm_platform.config.load_settings", lambda **kwargs: settings)
    monkeypatch.setattr(
        "plm_platform.db.database_url",
        lambda configured: SimpleNamespace(database=CLONE),
    )

    def unavailable(**kwargs: object) -> None:
        raise RuntimeError("PASSWORD=do-not-print")

    monkeypatch.setattr(checker, "read_only_review_snapshot", unavailable)
    assert checker.main([CLONE, YOUTUBE]) == 1
    output = capsys.readouterr().out
    assert "do-not-print" not in output
    assert json.loads(output)["authorizes_migration"] is False
