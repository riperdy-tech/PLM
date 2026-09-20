"""Unit tests for the seam-contract helpers (``tests/support/contract.py``).

The drift path is the important one: a renamed producer field must produce a
message that names both halves of the rename.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from tests.support.contract import SeamRegistry, assert_seam_payload


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    observation_count: int


def test_accepts_a_matching_payload() -> None:
    payload = {"experiment_id": "exp_01", "observation_count": 3}
    validated = assert_seam_payload(payload, Handoff, seam="C2-test")
    assert validated.model_dump() == payload


def test_diagnoses_a_renamed_field() -> None:
    payload = {"experimentId": "exp_01", "observation_count": 3}
    with pytest.raises(AssertionError) as info:
        assert_seam_payload(payload, Handoff, seam="C2-test", producer="producer_stub")
    message = str(info.value)
    assert "C2-test" in message
    assert "missing" in message
    assert "'experiment_id'" in message
    assert "unknown" in message
    assert "'experimentId'" in message
    assert "likely a rename" in message


def test_reports_value_failures_after_names_match() -> None:
    payload = {"experiment_id": "exp_01", "observation_count": "forty-two"}
    with pytest.raises(AssertionError) as info:
        assert_seam_payload(payload, Handoff, seam="C2-test")
    message = str(info.value)
    assert "values do not validate" in message
    assert "observation_count" in message


def test_extra_field_is_unknown_not_silently_dropped() -> None:
    payload = {"experiment_id": "exp_01", "observation_count": 3, "surprise": True}
    with pytest.raises(AssertionError) as info:
        assert_seam_payload(payload, Handoff, seam="C2-test")
    assert "'surprise'" in str(info.value)


def test_registry_skips_only_while_the_file_is_absent(tmp_path: Path) -> None:
    registry = SeamRegistry(path=tmp_path / "missing.md")
    with pytest.raises(pytest.skip.Exception) as info:
        registry.require_entry("C2")
    assert "not present yet" in str(info.value)


def test_registry_requires_the_entry_once_the_file_exists(tmp_path: Path) -> None:
    registry_file = tmp_path / "CONTRACT_REGISTRY.md"
    registry_file.write_text("# Registry\n\n## C2 - AR to experiments\n", encoding="utf-8")
    registry = SeamRegistry(path=registry_file)
    registry.require_entry("C2")
    with pytest.raises(AssertionError) as info:
        registry.require_entry("C9")
    assert "C9" in str(info.value)
