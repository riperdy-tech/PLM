"""The shared seam base: closed payloads, versioned schemas, fail-closed parsing (ADR-0025)."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from plm_contracts.seams import (
    ExperimentLineage,
    SeamModel,
    SeamRejection,
    parse_seam,
)


class Sample(SeamModel):
    SEAM: ClassVar[str] = "CX"
    SCHEMA_VERSION: ClassVar[str] = "2.1"

    item_id: str
    count: int


def _wire(**overrides: Any) -> dict[str, Any]:
    return {"schema_version": "2.1", "item_id": "a", "count": 1, **overrides}


def test_build_stamps_the_producing_version() -> None:
    assert Sample.build(item_id="a", count=1).to_wire() == _wire()


def test_parse_accepts_current_and_previous_major() -> None:
    assert parse_seam(Sample, _wire()).count == 1
    assert parse_seam(Sample, _wire(schema_version="1.7")).count == 1


@pytest.mark.parametrize("version", ["3.0", "0.9", "2", "v2.1", ""])
def test_parse_rejects_other_majors_and_malformed_versions(version: str) -> None:
    with pytest.raises(SeamRejection) as exc:
        parse_seam(Sample, _wire(schema_version=version))
    assert exc.value.field_paths == ["schema_version"]


def test_unsupported_major_has_its_own_code() -> None:
    with pytest.raises(SeamRejection) as exc:
        parse_seam(Sample, _wire(schema_version="3.0"))
    assert exc.value.error.code == "unsupported_schema_version"


def test_missing_required_field_is_named() -> None:
    payload = _wire()
    del payload["count"]
    with pytest.raises(SeamRejection) as exc:
        parse_seam(Sample, payload)
    assert exc.value.error.code == "missing_required_field"
    assert exc.value.field_paths == ["count"]


def test_missing_schema_version_is_refused() -> None:
    payload = _wire()
    del payload["schema_version"]
    with pytest.raises(SeamRejection) as exc:
        parse_seam(Sample, payload)
    assert exc.value.field_paths == ["schema_version"]


def test_unknown_field_is_refused() -> None:
    with pytest.raises(SeamRejection) as exc:
        parse_seam(Sample, _wire(surprise=True))
    assert exc.value.error.code == "unknown_field"


def test_invalid_value_is_refused() -> None:
    with pytest.raises(SeamRejection) as exc:
        parse_seam(Sample, _wire(count="many"))
    assert exc.value.error.code == "invalid_field_value"


LINEAGE = {
    "experiment_id": "exp_1",
    "experiment_family_id": "fam_1",
    "content_arm_id": "arm_1",
    "content_genome_id": "gen_1",
    "production_directive_id": "dir_1",
}


def test_lineage_requires_every_identifier() -> None:
    assert ExperimentLineage.model_validate(LINEAGE).content_arm_id == "arm_1"
    for name in LINEAGE:
        partial = {k: v for k, v in LINEAGE.items() if k != name}
        with pytest.raises(ValueError, match=name):
            ExperimentLineage.model_validate(partial)


@pytest.mark.parametrize("value", ["", "   ", "arm_unknown", "ARM_PLACEHOLDER", "unknown"])
def test_lineage_refuses_empty_and_placeholder_ids(value: str) -> None:
    with pytest.raises(ValueError):
        ExperimentLineage.model_validate({**LINEAGE, "content_arm_id": value})
