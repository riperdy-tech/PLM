"""`plm migrate --approved-backup` refuses without an intact proof for this head.

No database, Docker or Alembic migration is used: the source check is faked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from plm_platform import migration_rollout as rollout

GOOD_ID = "local_plm_20261127T000000Z_0123456789ab"


@pytest.mark.parametrize("identifier", ["", "../etc", "0043_local_plm_x", GOOD_ID + "/.."])
def test_an_invalid_backup_identifier_is_refused_before_any_database_access(
    monkeypatch: pytest.MonkeyPatch, identifier: str,
) -> None:
    monkeypatch.setattr(rollout, "_source", lambda: pytest.fail("touched the database"))
    with pytest.raises(RuntimeError, match="invalid approved backup identifier"):
        rollout.apply(identifier)


def _proof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **manifest: object) -> None:
    folder = tmp_path / GOOD_ID
    folder.mkdir()
    archive = folder / rollout.BACKUP_NAME
    archive.write_bytes(b"x" * 2048)
    record = {
        "identifier": GOOD_ID, "started_revision": "0042_media_render_evidence",
        "target_revision": rollout._head(), "isolated_restore_passed": True,
        "backup_size": 2048, "backup_sha256": rollout._sha256(archive),
        "source_snapshot": {}, "upgraded_clone_snapshot": {},
    }
    record.update(manifest)
    (folder / rollout.MANIFEST_NAME).write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(rollout, "BACKUP_ROOT", tmp_path)
    monkeypatch.setattr(rollout, "_source", lambda: object())
    monkeypatch.setattr(rollout, "_snapshot", lambda *a: pytest.fail("read the database"))


@pytest.mark.parametrize("manifest", [
    {"target_revision": "0042_media_render_evidence"},  # proved for another head
    {"isolated_restore_passed": False},
    {"backup_sha256": "0" * 64},
    {"backup_size": 1},
    {"started_revision": None},
])
def test_a_proof_for_another_head_or_a_changed_backup_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: dict[str, object],
) -> None:
    _proof(tmp_path, monkeypatch, **manifest)
    with pytest.raises(RuntimeError, match="missing, changed or for another head"):
        rollout.apply(GOOD_ID)


def test_an_applied_backup_cannot_be_applied_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _proof(tmp_path, monkeypatch)
    (tmp_path / GOOD_ID / "applied.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="already applied"):
        rollout.apply(GOOD_ID)
