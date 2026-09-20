# services/autonomous_ar/storage/ids.py
"""Ids are derived, never minted: uuid5 over a row's stable identity parts.

The rule imported from P1's fix round: a computation path never touches `uuid4` or a
clock, so replaying the same cycle produces the same rows and the same directive ids.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

__all__ = ["deterministic_id"]

_ID_NAMESPACE: UUID = uuid5(NAMESPACE_URL, "plm:ar:p2")


def deterministic_id(*parts: str) -> str:
    """uuid5 over the identity parts, joined with `|` — stable across processes."""
    if not parts or any(not part for part in parts):
        raise ValueError("deterministic_id needs at least one non-empty part")
    return str(uuid5(_ID_NAMESPACE, "|".join(parts)))
