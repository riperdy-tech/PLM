"""The brand registry P6 renders with (P6 §12).

A brand's display name and palette are P6's brand-system data, keyed by the
``brand_id`` P3 assigns. A request names only the brand id; an unknown id is refused
(``brand_profile_unknown``), never rendered with a made-up name or palette.

:data:`DEFAULT_BRANDS` is development configuration: it declares the one brand P3
materializes by default (``neon_seoul``). Operators supply real brands through
:meth:`BrandRegistry.from_json_file` (``PLM_MEDIA_BRANDS_FILE``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from ..errors import MediaAssemblyError
from ..visuals.planner import BrandProfile

__all__ = ["BRANDS_FILE_VAR", "DEFAULT_BRANDS", "BrandRegistry"]

BRANDS_FILE_VAR: Final[str] = "PLM_MEDIA_BRANDS_FILE"


class BrandRegistry:
    def __init__(self, brands: Iterable[BrandProfile]) -> None:
        self._brands: Mapping[str, BrandProfile] = MappingProxyType(
            {brand.brand_id: brand for brand in brands})

    def get(self, brand_id: str) -> BrandProfile:
        brand = self._brands.get(brand_id)
        if brand is None:
            raise MediaAssemblyError(
                "brand_profile_unknown",
                f"no brand profile is registered for {brand_id!r}", ["brand_id"])
        return brand

    @classmethod
    def from_json_file(cls, path: str | Path) -> BrandRegistry:
        """``[{"brand_id", "brand_name", "version", "palette": [4 x #RRGGBB]}, ...]``."""
        raw: Any = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise TypeError(f"{path}: a brand registry is a JSON list")
        return cls(
            BrandProfile(brand_id=str(b["brand_id"]), brand_name=str(b["brand_name"]),
                         version=str(b["version"]), palette=[str(c) for c in b["palette"]])
            for b in raw
        )


DEFAULT_BRANDS: Final = BrandRegistry([
    BrandProfile(brand_id="neon_seoul", brand_name="Neon Seoul", version="v1",
                 palette=["#0B0E14", "#1F2937", "#60A5FA", "#EC4899"]),
])
