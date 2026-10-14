"""Visual concept and scene planner (P6 §12-§15, §25).

Generates structured visual scene plans, canvas specifications, and
procedural background assets adhering to brand profiles.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..domain.enums import VisualAssetType
from ..domain.models import VisualAsset, VisualSession

__all__ = ["BrandProfile", "VisualPlannerResult", "plan_visual_session"]


@dataclass(frozen=True)
class BrandProfile:
    """Immutable brand visual and identity guidelines (P6 §12)."""

    brand_id: str
    brand_name: str
    version: str = "v1"
    palette: list[str] = field(default_factory=lambda: ["#0B0E14", "#1F2937", "#60A5FA", "#EC4899"])
    primary_font: str = "Inter"
    secondary_font: str = "Roboto Mono"
    visual_theme: str = "cyberpunk_ambient"
    camera_language: list[str] = field(
        default_factory=lambda: ["cinematic", "slow_pan", "static_medium"]
    )
    motifs: list[str] = field(default_factory=lambda: ["neon", "rain", "cityscape", "analog_glow"])


class VisualPlannerResult:
    """Outcome of visual concept planning."""

    def __init__(
        self,
        visual_session: VisualSession,
        assets: list[VisualAsset],
        visual_plan: dict[str, Any],
    ) -> None:
        self.visual_session = visual_session
        self.assets = assets
        self.visual_plan = visual_plan


def plan_visual_session(
    *,
    playlist_session_id_str: str,
    brand_profile: BrandProfile,
    visual_profile: str,
    total_duration_seconds: float,
    visual_profile_version: str = "v1",
    scene_length_seconds: float = 60.0,
    now: datetime,
) -> VisualPlannerResult:
    """Generate structured visual session and scene timeline for a playlist."""
    # 1. Determine number of scenes
    scene_count = max(
        1,
        int(total_duration_seconds // scene_length_seconds)
        + (1 if total_duration_seconds % scene_length_seconds > 0 else 0),
    )

    scenes: list[dict[str, Any]] = []
    current_time = 0.0

    for idx in range(scene_count):
        duration = min(scene_length_seconds, total_duration_seconds - current_time)
        motif = brand_profile.motifs[idx % len(brand_profile.motifs)]
        cam = brand_profile.camera_language[idx % len(brand_profile.camera_language)]

        scenes.append(
            {
                "scene_index": idx,
                "start_seconds": round(current_time, 2),
                "duration_seconds": round(duration, 2),
                "motif": motif,
                "camera_motion": cam,
                "color_accent": brand_profile.palette[(idx + 2) % len(brand_profile.palette)],
            }
        )
        current_time += duration

    visual_plan = {
        "visual_theme": brand_profile.visual_theme,
        "brand_id": brand_profile.brand_id,
        "brand_version": brand_profile.version,
        "canvas": {"width": 1920, "height": 1080, "fps": 30},
        "palette": brand_profile.palette,
        "typography": {
            "primary": brand_profile.primary_font,
            "secondary": brand_profile.secondary_font,
        },
        "scenes": scenes,
        "loop_policy": {"allow_reuse": True, "max_consecutive_reuse": 1},
    }

    session = VisualSession.create(
        playlist_session_id_str=playlist_session_id_str,
        brand_id=brand_profile.brand_id,
        brand_version=brand_profile.version,
        visual_profile=visual_profile,
        visual_profile_version=visual_profile_version,
        visual_plan_json=visual_plan,
        now=now,
    )

    # 2. Materialize primary visual assets (Tier A/B procedural backgrounds)
    assets: list[VisualAsset] = []
    for idx, scene in enumerate(scenes):
        asset_seed = f"{session.id}:{idx}:{scene['motif']}"
        checksum = hashlib.sha256(asset_seed.encode("utf-8")).hexdigest()
        asset = VisualAsset.create(
            session_id=session.id,
            asset_type=VisualAssetType.BACKGROUND,
            uri=f"media/procedural/{brand_profile.brand_id}/{scene['motif']}_{idx}.svg",
            checksum=checksum,
            width=1920,
            height=1080,
            duration_seconds=scene["duration_seconds"],
            source_type="procedural_shader",
            provider="internal_compositor",
            provider_asset_id=f"scene_{idx}_{scene['motif']}",
            now=now,
        )
        assets.append(asset)

    session.assets = assets
    session.status = "assets_ready"

    return VisualPlannerResult(
        visual_session=session,
        assets=assets,
        visual_plan=visual_plan,
    )
