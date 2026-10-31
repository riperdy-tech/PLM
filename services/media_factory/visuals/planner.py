"""Brand profile and the visual plan of a render (P6 §12, §13, §25).

The visual plan describes exactly what the renderer draws, nothing more: a canvas at
the render profile's size and frame rate, a background gradient and a procedural waveform
visualiser. Both take the package's visual world (:mod:`.worlds`) when P6 knows it, else
the brand palette; the plan records which. Assets are recorded only
when bytes are produced (the background image, by the renderer), never as planned URIs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..domain.models import VisualAsset, VisualSession
from ..rendering.profiles import RenderProfile
from .worlds import WORLDS_VERSION, visual_world

__all__ = ["VISUAL_RENDERER", "BrandProfile", "VisualPlannerResult", "plan_visual_session"]

VISUAL_RENDERER = "procedural_waveform_v1"


@dataclass(frozen=True)
class BrandProfile:
    """Immutable brand visual and identity guidelines (P6 §12).

    ``palette`` is ``[base, shade, accent, highlight]`` as ``#RRGGBB``.
    """

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
    """Outcome of visual planning."""

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
    render_profile: RenderProfile,
    total_duration_seconds: float,
    visual_profile_version: str = "v1",
    now: datetime,
) -> VisualPlannerResult:
    """The visual session for a playlist: what the renderer will draw, and over how long."""
    world = visual_world(visual_profile)
    background = ([world.sky_top, world.sky_bottom] if world is not None
                  else list(brand_profile.palette[:2]))
    wave_color = (world.glow if world is not None
                  else brand_profile.palette[2] if len(brand_profile.palette) > 2 else None)
    visual_plan: dict[str, Any] = {
        "renderer": VISUAL_RENDERER,
        "visual_profile": visual_profile,
        "brand_id": brand_profile.brand_id,
        "brand_version": brand_profile.version,
        "canvas": {"width": render_profile.width, "height": render_profile.height,
                   "fps": render_profile.fps},
        "duration_seconds": round(total_duration_seconds, 3),
        "visual_world": {"name": visual_profile, "known": world is not None,
                         "version": WORLDS_VERSION},
        "background": {"style": "vertical_gradient", "colors": background},
        "visualizer": {"filter": "showwaves", "mode": "cline", "scale": "sqrt",
                       "color": wave_color,
                       "height_ratio": render_profile.waveform_height_ratio},
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
    return VisualPlannerResult(visual_session=session, assets=[], visual_plan=visual_plan)
