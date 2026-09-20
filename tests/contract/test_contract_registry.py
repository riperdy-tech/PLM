"""The default suite's registry check (W0.10).

``tests/contract/_TEMPLATE_test_seam.py`` checks the registry only when it is
copied or invoked explicitly (the leading underscore keeps it out of a default
``pytest -q``). This module makes the frozen registry part of the default suite:
``docs/contracts/CONTRACT_REGISTRY.md`` must exist, define exactly ``C1``–``C10``
in order, and carry a complete entry per seam — producer, consumer, the
endpoint/event, a field list, the status before its ruling, the ruling(s) and the
seam test that must exist. The registry is the source of truth for every
cross-phase seam (P0 §9 R2), so when it and reality disagree, this fails loudly.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from tests.support.contract import SeamRegistry
from tests.support.paths import REPO_ROOT

EXPECTED_SEAM_IDS = tuple(f"C{n}" for n in range(1, 11))

REQUIRED_ENTRY_LABELS: tuple[tuple[str, ...], ...] = (
    ("**Producer:**", "**Producers:**"),
    ("**Consumer:**", "**Consumers:**"),
    ("**Endpoint / event:**",),
    ("**Status before ruling:**",),
    ("**Ruling:**",),
    ("**Required amendments",),
    ("**Seam test:**",),
)

FIELD_LIST_MARKERS = (
    "producer fields",
    "frozen field list",
    "frozen prefixes",
    "frozen fields",
)

ENTRY_HEADING = re.compile(r"^## (C\d+) · ", re.MULTILINE)
SEAM_TEST_PATH = re.compile(r"tests/contract/(test_c\d+_[a-z0-9_]+\.py)")


def _read_registry(registry: SeamRegistry) -> str:
    assert registry.exists, (
        f"contract registry missing ({registry.path}) — docs/contracts/CONTRACT_REGISTRY.md "
        "is the frozen source of truth for every cross-phase seam (P0 §9 R2)"
    )
    return registry.path.read_text(encoding="utf-8")


def _entry_chunks(text: str) -> Iterator[tuple[str, str]]:
    """Yield ``(seam_id, entry_text)`` for each ``## Cn · …`` registry entry."""
    starts = list(ENTRY_HEADING.finditer(text))
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        yield match.group(1), text[match.start() : end]


def test_registry_defines_exactly_the_ten_seams(contract_registry: SeamRegistry) -> None:
    """C1–C10, in order, one entry each — the W0 gate's "ten seams registered"."""
    text = _read_registry(contract_registry)
    found = [seam_id for seam_id, _ in _entry_chunks(text)]
    assert found == list(EXPECTED_SEAM_IDS), (
        f"registry entries must be exactly {list(EXPECTED_SEAM_IDS)} in order; found {found}"
    )


def test_every_entry_is_complete(contract_registry: SeamRegistry) -> None:
    """Each entry names both sides, the handoff, a field list, a status and a ruling."""
    text = _read_registry(contract_registry)
    for seam_id, chunk in _entry_chunks(text):
        for alternatives in REQUIRED_ENTRY_LABELS:
            label = " or ".join(alternatives)
            assert any(candidate in chunk for candidate in alternatives), (
                f"{seam_id}: entry is missing {label!r}"
            )
        lowered = chunk.lower()
        assert any(marker in lowered for marker in FIELD_LIST_MARKERS), (
            f"{seam_id}: entry has no field list section — a seam entry without fields "
            "is not a frozen contract"
        )
        assert "**pending**" in chunk or "**exists**" in chunk, (
            f"{seam_id}: the seam-test line must say whether the test is pending or exists"
        )


def test_seam_tests_match_the_registry(contract_registry: SeamRegistry) -> None:
    """Each entry names a ``tests/contract/test_c<n>_*.py`` and the file's presence agrees."""
    text = _read_registry(contract_registry)
    for seam_id, chunk in _entry_chunks(text):
        match = SEAM_TEST_PATH.search(chunk)
        assert match is not None, (
            f"{seam_id}: no tests/contract/test_c<n>_*.py path in the entry; every seam "
            "names the test that must exist for it"
        )
        path = REPO_ROOT / "tests" / "contract" / match.group(1)
        if "**pending**" in chunk:
            assert not path.exists(), (
                f"{seam_id}: registry says {match.group(1)} is pending, but the file exists — "
                "mark it exists in the registry (the registry is the source of truth)"
            )
        else:
            assert path.exists(), (
                f"{seam_id}: registry names {match.group(1)}, but the file is not there"
            )


def test_seam_ids_resolve_through_the_registry(contract_registry: SeamRegistry) -> None:
    """The accessor the template uses must find every seam id — the real check, not a skip."""
    for seam_id in EXPECTED_SEAM_IDS:
        contract_registry.require_entry(seam_id)
