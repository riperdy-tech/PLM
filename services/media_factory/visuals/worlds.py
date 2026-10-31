"""What each P3 visual world looks like when P6 draws it (owner decision 2026-10-31).

P3 sends every package's ``visual_world`` (the pipeline passes it as ``visual_profile``),
but P6 drew every video and thumbnail from the brand palette alone, and thumbnail A
always drew rain. A world here sets the video background (sky gradient), the waveform
colour, and thumbnail A's scene: sky, light, weather and skyline. The brand still owns
the text, the accent and variants B and C. A world without an entry keeps the brand
look, and the visual plan records ``visual_world_known: false`` instead of guessing.

The worlds from ``architect_studio_night`` on are the focus-music identity's
(ADR-0044): stylized architectural workspaces and abstract technical environments in
charcoal and graphite, warm amber, cool white and muted electric accents. The older
worlds stay because recorded packages name them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

__all__ = ["WORLDS", "WORLDS_VERSION", "VisualWorld", "visual_world"]

WORLDS_VERSION: Final[str] = "p6-worlds-v2"

Weather = Literal["rain", "snow", "stars", "mist", "none"]
Skyline = Literal["city", "hills", "waves", "trees", "room", "road",
                  "workspace", "grid", "racks"]


@dataclass(frozen=True, slots=True)
class VisualWorld:
    sky_top: str      # #RRGGBB, top of the background gradient
    sky_bottom: str   # #RRGGBB, bottom of the background gradient
    glow: str         # #RRGGBB, the waveform and the scene's highlights (>= 3:1 on the sky)
    light: str        # #RRGGBB, the scene's moon, sun or lamp
    weather: Weather
    skyline: Skyline


WORLDS: Final[dict[str, VisualWorld]] = {
    "rainy_asian_city": VisualWorld("#0E1626", "#1E2A44", "#5FA8D3", "#FFD896", "rain", "city"),
    "rainy_window_desk": VisualWorld("#141B2B", "#2A3550", "#8FB3D9", "#FFCF8A", "rain", "room"),
    "rainy_jazz_bar": VisualWorld("#1A1018", "#3A2230", "#D9A066", "#FFC27A", "rain", "room"),
    "lofi_study_bedroom": VisualWorld("#2B2440", "#4A3B5E", "#F2B5A0", "#FFE2A8", "none", "room"),
    "sunrise_cafe": VisualWorld("#F2A65A", "#F7D9A8", "#5A2414", "#FFF1C1", "none", "room"),
    "bookstore_corner": VisualWorld("#2E2219", "#5A4430", "#E0B07A", "#FFD99A", "none", "room"),
    "night_train": VisualWorld("#0B1020", "#22304F", "#9AB8E6", "#FFE6B0", "stars", "hills"),
    "misty_forest": VisualWorld("#2F3D38", "#7A8C82", "#FFFFFF", "#F4F1E1", "mist", "trees"),
    "snowy_mountains": VisualWorld("#5B6E8C", "#C9D6E8", "#0B1730", "#FFF7E0", "snow", "hills"),
    "starfield": VisualWorld("#05060F", "#141A33", "#B9C6FF", "#E8ECFF", "stars", "hills"),
    "night_ocean": VisualWorld("#06121F", "#123049", "#6FC3DF", "#E9F2FF", "stars", "waves"),
    "coastal_drive": VisualWorld("#FF8A5B", "#FFC59E", "#123150", "#FFF0B3", "none", "road"),
    "seaside_resort": VisualWorld("#58B4D1", "#BDE6F1", "#0A2A40", "#FFF6C9", "none", "waves"),
    "night_highway": VisualWorld("#0A0F24", "#2A1B4A", "#FF4FA3", "#FFD27F", "stars", "road"),
    "misty_highways": VisualWorld("#3A4150", "#8A93A6", "#FFFFFF", "#FFF2CC", "mist", "road"),
    "neon_tokyo": VisualWorld("#0D0221", "#2B0B4F", "#FF2E97", "#00E5FF", "none", "city"),
    "tokyo_cyberpunk": VisualWorld("#05010F", "#1F0A3A", "#00F0FF", "#FF3EA5", "rain", "city"),
    "neon_shopping_street": VisualWorld("#140A2E", "#3D1460", "#FFB000", "#FF4FD8", "none", "city"),
    "neon_club": VisualWorld("#0A0014", "#2A0040", "#B026FF", "#00FFC8", "none", "city"),
    "night_rooftop": VisualWorld("#0C1328", "#2C2350", "#F7B267", "#FFE7B3", "stars", "city"),
    "midnight_diner": VisualWorld("#130F1F", "#33233A", "#FF6B6B", "#FFE3A3", "none", "room"),
    "luxury_hotel": VisualWorld("#1B1410", "#4A3726", "#D4AF37", "#FFF1CC", "none", "room"),
    # The focus-music identity (ADR-0044).
    "architect_studio_night": VisualWorld("#14171D", "#2A2F38", "#F2A23C", "#FFE3B0", "none",
                                          "workspace"),
    "drafting_table_dawn": VisualWorld("#2E3440", "#5E636E", "#F6F8FA", "#FFC98A", "none",
                                       "workspace"),
    "minimal_desk_monolith": VisualWorld("#101114", "#22252B", "#E9EEF2", "#F2A23C", "none",
                                         "workspace"),
    "observatory_workspace": VisualWorld("#0B0D12", "#1E222A", "#C9D6E3", "#F5E6C8", "stars",
                                         "workspace"),
    "server_hall_glow": VisualWorld("#0E1116", "#1C2129", "#7FD1E0", "#F2A23C", "none",
                                    "racks"),
    "circuit_grid_abstract": VisualWorld("#0F1115", "#23262D", "#8FB8C9", "#F2A23C", "none",
                                         "grid"),
    "blueprint_hall": VisualWorld("#121821", "#26303C", "#E9EEF2", "#F2A23C", "none", "grid"),
}
"""Every visual world the P3 templates name (default and alternatives)."""


def visual_world(name: str | None) -> VisualWorld | None:
    """The world's look, or ``None`` when P6 has no entry for it (the brand look stays)."""
    return None if name is None else WORLDS.get(name)
