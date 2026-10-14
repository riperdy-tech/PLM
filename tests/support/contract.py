"""Seam-contract helpers and the frozen-registry accessor.

Nine independently-written phase specs are the program's biggest drift hazard
(P0 §4 risk 3): P2 renames ``experiment_id``, its own tests still pass, and P3
breaks three phases later — or a producer starts emitting a field the
consumer's ``extra="forbid"`` schema rejects. The pattern this module supports
(in ``tests/contract/_TEMPLATE_test_seam.py``):

* the **producer** serialises its payload (JSON) — that is the wire;
* the **consumer's expected schema** is a pydantic model whose field names are
  copied from the frozen contract registry entry (``CONTRACT_REGISTRY.md``,
  written by W0.10 — field names live *there*, per P0 §9 R2 authority order);
* :func:`assert_seam_payload` proves the two agree, and its failure messages
  name the missing and unknown fields, so a rename reads as what it is.

Unlike the golden machinery, a seam failure is never "updated" from the test
side: the fix is decided against the registry (amend it first when the wire
really changes), then applied to producer and consumer in the same change.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from pydantic import BaseModel, ValidationError

from tests.support.paths import REPO_ROOT, display_path

REGISTRY_PATH: Final[Path] = REPO_ROOT / "docs" / "contracts" / "CONTRACT_REGISTRY.md"
"""The frozen contract registry (W0.10). Field names live only there (P0 §9 R2)."""


@dataclass(frozen=True, slots=True)
class SeamRegistry:
    """Read-only access to the frozen contract registry file."""

    path: Path = REGISTRY_PATH

    @property
    def exists(self) -> bool:
        """Whether the registry file has landed yet."""
        return self.path.is_file()

    def require_entry(self, entry: str) -> None:
        """Require ``entry`` to be defined in the registry.

        While W0.10 has not landed the file is absent: the test *skips* with a
        reason that says so, because silently passing a seam test would read
        as a verified seam. Once the file exists, a missing entry is a
        failure — a seam test for an entry the registry does not define is
        testing a contract nobody froze.
        """
        if not self.exists:
            pytest.skip(
                f"contract registry not present yet ({display_path(self.path)}) — "
                "added by W0.10; the declared fields are enforced once it lands"
            )
        text = self.path.read_text(encoding="utf-8")
        if entry not in text:
            raise AssertionError(
                f"registry entry {entry!r} not found in {display_path(self.path)}: "
                "the registry is the single source of truth (P0 §9 R2) — add the "
                "entry first, then the producer and consumer in the same change"
            )


def assert_seam_payload(
    payload: Mapping[str, Any],
    schema: type[BaseModel],
    *,
    seam: str,
    producer: str = "producer",
    consumer: str | None = None,
) -> BaseModel:
    """Assert a producer's serialised payload satisfies the consumer's schema.

    Checks the *field names* first — the drift this exists to catch — then
    validates values through ``schema.model_validate``. ``schema`` must forbid
    extra fields (``ConfigDict(extra="forbid")``); the name check here is what
    turns pydantic's generic message into a drift diagnosis.
    """
    expected = set(schema.model_fields)
    actual = set(payload)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    consumer_name = consumer or schema.__name__
    if missing or unknown:
        header = (
            f"{seam}: producer payload ({producer}) does not match the consumer "
            f"schema ({consumer_name})"
        )
        lines = [header]
        if missing:
            lines.append(
                f"  missing (consumer reads it, producer never sends it): {missing}"
            )
        if unknown:
            lines.append(
                f"  unknown (producer sends it, consumer cannot read it): {unknown}"
            )
        if len(missing) == 1 and len(unknown) == 1:
            lines.append(
                f"  likely a rename: {unknown[0]!r} -> {missing[0]!r} "
                "(or the registry disagrees with both)"
            )
        lines.append(
            "  a wire change is decided against the registry entry first (P0 §9 R2), "
            "then applied to producer and consumer in the same change"
        )
        raise AssertionError("\n".join(lines))

    try:
        return schema.model_validate(payload)
    except ValidationError as exc:
        details = "\n".join(
            f"  {'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()
        )
        raise AssertionError(
            f"{seam}: payload field names match {consumer_name} but values do not validate:\n"
            f"{details}"
        ) from exc


# --- behavioural seam checks (ADR-0025) --------------------------------------------------
#
# The checks above compare names. The ones below exercise the real consumer: a seam is
# fail-closed only if removing any required fact, adding an unknown one, or sending a
# future major version makes the consumer refuse the payload.


def without(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    """A copy of ``payload`` with one (possibly dotted) field removed."""
    copy: dict[str, Any] = json.loads(json.dumps(payload, default=str))
    *parents, leaf = field.split(".")
    node = copy
    for part in parents:
        node = node[part]
    del node[leaf]
    return copy


def with_value(payload: Mapping[str, Any], field: str, value: Any) -> dict[str, Any]:
    """A copy of ``payload`` with one (possibly dotted) field replaced."""
    copy: dict[str, Any] = json.loads(json.dumps(payload, default=str))
    *parents, leaf = field.split(".")
    node = copy
    for part in parents:
        node = node[part]
    node[leaf] = value
    return copy


def assert_refused(
    consume: Callable[[dict[str, Any]], object],
    payload: dict[str, Any],
    refusal: type[BaseException] | tuple[type[BaseException], ...],
    *,
    because: str,
) -> BaseException:
    """``consume(payload)`` must raise ``refusal``; return the exception for inspection."""
    try:
        consume(payload)
    except refusal as exc:
        return exc
    raise AssertionError(f"consumer accepted a payload it must refuse ({because})")
