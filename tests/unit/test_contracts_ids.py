"""Brand-scoped IDs (ruling R19) and the ``<name>-vN`` versioned identifier shape."""

from __future__ import annotations

import pytest
from plm_contracts import (
    BrandId,
    BrandScoped,
    format_versioned_id,
    is_versioned_id,
    parse_versioned_id,
)
from pydantic import BaseModel, ValidationError


class _Holder(BaseModel):
    brand_id: BrandId


class _Arm(BrandScoped):
    arm_id: str


class TestBrandId:
    def test_seeded_brand_style_slug_is_accepted(self) -> None:
        # P6 example: "brand_id": "neon_seoul"
        holder = _Holder(brand_id="neon_seoul")
        assert holder.brand_id == "neon_seoul"
        assert _Holder(brand_id="brand1").brand_id == "brand1"

    def test_invalid_slugs_are_rejected(self) -> None:
        for bad in ("", "Neon_Seoul", "neon seoul", "neon-seoul", "_neon", "neon__seoul"):
            with pytest.raises(ValidationError):
                _Holder(brand_id=bad)

    def test_brand_id_is_first_class_and_required(self) -> None:
        with pytest.raises(ValidationError):
            _Arm(arm_id="arm_1")

    def test_brand_scoped_models_round_trip(self) -> None:
        arm = _Arm(brand_id="neon_seoul", arm_id="arm_1")
        assert _Arm.model_validate_json(arm.model_dump_json()) == arm


class TestVersionedIdentifier:
    def test_formats_the_canonical_shape(self) -> None:
        assert format_versioned_id("p1", 1) == "p1-v1"
        assert format_versioned_id("genome", 2) == "genome-v2"
        assert format_versioned_id("youtube-policy", 12) == "youtube-policy-v12"

    def test_format_validates_its_inputs(self) -> None:
        for bad_name in ("", "P1", "-bad", "bad-", "bad--name", "bad_name", "bad name"):
            with pytest.raises(ValueError):
                format_versioned_id(bad_name, 1)
        for bad_version in (0, -3):
            with pytest.raises(ValueError):
                format_versioned_id("p1", bad_version)

    def test_parsing_round_trips(self) -> None:
        for name, version in (("p1", 1), ("genome", 2), ("youtube-policy", 3)):
            value = format_versioned_id(name, version)
            assert parse_versioned_id(value) == (name, version)

    def test_malformed_values_are_rejected(self) -> None:
        for bad in (
            "",
            "p1",
            "p1-v",
            "p1-v0",
            "p1-v01",
            "P1-v1",
            "p1_v1",
            "-v1",
            "p1-v1-extra",
            "p1-v1.5",
        ):
            assert not is_versioned_id(bad)
            with pytest.raises(ValueError):
                parse_versioned_id(bad)

    def test_valid_values_are_recognised(self) -> None:
        for good in ("p1-v1", "p2-v1", "genome-v1", "reward-v1", "youtube-policy-v1"):
            assert is_versioned_id(good)
