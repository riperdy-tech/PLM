"""P6 refusal and infrastructure errors.

Two kinds, kept apart so a caller can tell them apart:

* :class:`MediaAssemblyError`: P6 refuses the request itself (a missing or uncleared
  track, a tampered source, an unreachable duration, a chapter-rule violation, an
  unknown brand or render profile). The request must change; retrying cannot help.
* :class:`MediaInfrastructureError`: something P6 depends on is unavailable (the artifact
  store, P5, the exception queue, FFmpeg). Nothing was decided; a retry may succeed.

Both carry an :class:`~plm_contracts.ErrorShape` with a stable reason code.
"""

from __future__ import annotations

from plm_contracts import ErrorShape

__all__ = ["MediaAssemblyError", "MediaInfrastructureError"]


class MediaAssemblyError(ValueError):
    """P6 refused; ``error`` carries the reason code and offending field paths."""

    def __init__(self, code: str, message: str, field_paths: list[str] | None = None) -> None:
        self.error = ErrorShape(code=code, field_paths=field_paths or [], message=message)
        super().__init__(f"{code}: {message}")

    @property
    def code(self) -> str:
        return self.error.code


class MediaInfrastructureError(RuntimeError):
    """A dependency of P6 is unavailable; the request may succeed later."""

    def __init__(self, code: str, message: str) -> None:
        self.error = ErrorShape(code=code, field_paths=[], message=message)
        super().__init__(f"{code}: {message}")

    @property
    def code(self) -> str:
        return self.error.code
