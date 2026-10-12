"""Rendering and packaging module for Media Factory (Phase P6)."""

from .assembler import AssemblyPipelineResult, assemble_media_package
from .renderer import (
    DeterministicRenderer,
    PostRenderCheckResult,
    PreflightCheckResult,
    RenderExecutionResult,
)

__all__ = [
    "AssemblyPipelineResult",
    "DeterministicRenderer",
    "PostRenderCheckResult",
    "PreflightCheckResult",
    "RenderExecutionResult",
    "assemble_media_package",
]
