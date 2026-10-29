"""Safe command and retained-backup guards for approved local 0039 rollout.

No actual database, docker, or backup is accessed by these unit tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.support import migration_local_0039_rollout as rollout


@pytest.mark.parametrize(
    "identifier",
    [
        "", "../migrations", "0039_local_plm_20261027T230000Z_bad",
        "0039_local_plm_20261027T230000Z_abc0123!", "plm",
        "0039_local_plm_20261027T230000Z_abcdefabcdef/../unsafe",
    ],
)
def test_backup_identifier_blocks_path_traversal(identifier: str) -> None:
    with pytest.raises(RuntimeError, match="invalid approved backup identifier"):
        rollout._destination(identifier)


def test_rollout_requires_exact_stage_and_never_calls_apply_implicitly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        rollout, "_apply",
        lambda _identifier: pytest.fail("unapproved apply executed"),
    )
    assert rollout.main([]) == 2
    assert rollout.main(["--apply"]) == 2
    assert rollout.main([rollout.APPLY, "../plm"]) == 1
    output = capsys.readouterr().out
    assert "refused" in output
    assert "invalid approved backup" not in output


def test_backup_validation_rejects_tampering_or_missing_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    identifier = "0039_local_plm_20261027T230000Z_abcdefabcdef"
    monkeypatch.setattr(rollout, "BACKUP_ROOT", tmp_path)
    directory = tmp_path / identifier
    directory.mkdir()
    archive = directory / rollout.BACKUP_NAME
    archive.write_bytes(b"sample backup test data")
    sha = rollout._sha256(archive)
    manifest: dict[str, Any] = {
        "identifier": identifier,
        "started_revision": rollout.RELEASE,
        "target_revision": rollout.MERGE,
        "isolated_restore_passed": True,
        "local_db_applied": False,
        "backup_size": archive.stat().st_size,
        "backup_sha256": sha,
    }
    (directory / rollout.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    assert rollout._validated_backup(identifier) == manifest
    archive.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="missing or changed"):
        rollout._validated_backup(identifier)


def test_apply_without_verified_backup_never_opens_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rollout, "_source", lambda: object())
    monkeypatch.setattr(
        rollout, "_validated_backup",
        lambda _identifier: (_ for _ in ()).throw(RuntimeError("not verified")),
    )
    monkeypatch.setattr(
        rollout, "build_engine", lambda _: pytest.fail("opened DB without backup"),
    )
    with pytest.raises(RuntimeError, match="not verified"):
        rollout._apply("0039_local_plm_20261027T230000Z_abcdefabcdef")


def test_prepare_refuses_revision_change_before_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rollout, "_source", lambda: SimpleNamespace())
    monkeypatch.setattr(
        rollout, "read_only_review_snapshot",
        lambda **kwargs: {"alembic_revision": "0040_unknown"},
    )
    monkeypatch.setattr(
        rollout, "_full_backup",
        lambda *_args: pytest.fail("backup started from unknown revision"),
    )
    with pytest.raises(RuntimeError, match="source revision changed"):
        rollout._prepare()
