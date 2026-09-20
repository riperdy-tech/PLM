"""Machine-readable API errors — P3 §81.

"Do not return only free-form error strings."
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ErrorShape"]


class ErrorShape(BaseModel):
    """Exactly ``code``, ``field_paths``, ``message`` (P3 §81).

    ``field_paths`` points at the offending fields (e.g. ``track_specs[0].vocals_required``)
    and defaults to empty for errors that are not field-scoped.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1)
    field_paths: list[str] = Field(default_factory=list)
    message: str = Field(min_length=1)
