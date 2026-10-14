"""Base type for every cross-phase seam payload, and the one way consumers parse them.

A seam model is the single definition of a wire payload: the producer serialises it and
the consumer parses it with the same class, so the two cannot drift apart silently. The
rules every seam model follows (ADR-0025):

* ``extra="forbid"``: an unknown field is a contract change, not noise.
* Required facts have **no defaults**. A missing fact fails parsing with a reason code;
  it never becomes ``""``, ``0``, ``PASSED`` or an invented identifier. Where the
  registry allows a fact to be legitimately unknown, the field is typed so unknown is an
  explicit value (``None`` with a documented meaning, ``Tri``, or an ``UNKNOWN`` member).
* Every payload carries ``schema_version`` (``"<major>.<minor>"``). A consumer accepts
  its own major version and the one before it, so producer and consumer can deploy in
  either order across one major bump. Any other major is rejected with
  ``unsupported_schema_version``. Minor versions only add optional fields.

Consumers call :func:`parse_seam`. It turns every validation failure into a
:class:`SeamRejection` carrying an :class:`~plm_contracts.errors.ErrorShape`, so an
HTTP consumer can return it unchanged and a library consumer can block the handoff
with a reason code.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, ClassVar, Final, Self

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from ..errors import ErrorShape

__all__ = [
    "SCHEMA_VERSION_PATTERN",
    "SeamModel",
    "SeamRejection",
    "parse_seam",
]

SCHEMA_VERSION_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(\d+)\.(\d+)$")


def _major(version: str) -> int:
    match = SCHEMA_VERSION_PATTERN.match(version)
    if match is None:
        raise ValueError(f"schema_version {version!r} is not '<major>.<minor>'")
    return int(match.group(1))


class SeamModel(BaseModel):
    """A frozen, closed wire payload with a versioned schema.

    Subclasses set ``SEAM`` (registry entry id, e.g. ``"C5"``) and ``SCHEMA_VERSION``
    (the version this code produces). Producers construct the model with
    ``schema_version=Model.SCHEMA_VERSION`` via :meth:`build`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    SEAM: ClassVar[str] = ""
    SCHEMA_VERSION: ClassVar[str] = "1.0"

    schema_version: str

    @field_validator("schema_version")
    @classmethod
    def _accepted_major(cls, value: str) -> str:
        current = _major(cls.SCHEMA_VERSION)
        received = _major(value)
        if received not in (current, current - 1):
            raise ValueError(
                f"unsupported_schema_version: {cls.SEAM or cls.__name__} accepts major "
                f"{current} or {current - 1}, received {value!r}"
            )
        return value

    @classmethod
    def build(cls, **fields: Any) -> Self:
        """Construct a payload stamped with this code's ``SCHEMA_VERSION``."""
        return cls(schema_version=cls.SCHEMA_VERSION, **fields)

    def to_wire(self) -> dict[str, Any]:
        """The JSON-compatible dict a producer sends."""
        return self.model_dump(mode="json")


class SeamRejection(ValueError):
    """A consumer refused a seam payload; ``error`` is the shape to return or log."""

    def __init__(self, error: ErrorShape) -> None:
        self.error = error
        super().__init__(f"{error.code}: {error.message}")

    @property
    def field_paths(self) -> list[str]:
        return list(self.error.field_paths)


def _code_for(errors: list[Any]) -> str:
    for err in errors:
        if "unsupported_schema_version" in str(err.get("msg", "")):
            return "unsupported_schema_version"
    if any(err.get("type") == "missing" for err in errors):
        return "missing_required_field"
    if any(err.get("type") == "extra_forbidden" for err in errors):
        return "unknown_field"
    return "invalid_field_value"


def parse_seam[M: SeamModel](model: type[M], payload: Mapping[str, Any]) -> M:
    """Parse ``payload`` as ``model`` or raise :class:`SeamRejection` with a reason code.

    Codes: ``missing_required_field``, ``unknown_field``, ``invalid_field_value``,
    ``unsupported_schema_version``. ``field_paths`` lists every offending field.
    """
    try:
        return model.model_validate(dict(payload))
    except ValidationError as exc:
        errors = exc.errors()
        paths = sorted(
            {".".join(str(part) for part in err["loc"]) or "<root>" for err in errors}
        )
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in errors
        )
        raise SeamRejection(
            ErrorShape(
                code=_code_for(list(errors)),
                field_paths=paths,
                message=f"{model.SEAM or model.__name__} payload rejected: {detail}",
            )
        ) from exc
