"""SEAM CONTRACT-TEST TEMPLATE — copy me, then fill in the four PASTE spots.

WHY THESE TESTS EXIST
    Nine independently-written phase specs are the program's biggest drift
    hazard (P0 §4 risk 3): a producer renames ``experiment_id`` to
    ``experimentId``, its own tests stay green, and the consumer breaks three
    phases later. One test file per registry entry, asserting that the
    *producer's serialised payload* satisfies the *consumer's expected schema*,
    catches that at the seam in the producer's own pipeline.

HOW TO USE
    1. Copy (the underscore keeps the template out of a default pytest run):

           cp tests/contract/_TEMPLATE_test_seam.py \
              tests/contract/test_c2_ar_to_experiments.py

    2. Fill in the four PASTE spots below:
         (1) REGISTRY_ENTRY    — the entry id from docs/contracts/CONTRACT_REGISTRY.md
         (2) REGISTRY_FIELDS   — the entry's field list, copied verbatim
         (3) producer_stub     — replace with the real producer once it exists
         (4) ConsumerSchema    — the consumer's schema, field names/types per the registry
    3. Run it: ``pytest tests/contract/test_c2_ar_to_experiments.py -v``

    Until the real producer exists, keep the stub: the registry freezes the
    wire first, so the consumer's seam test can be written — and pass — before
    either side is implemented. When the producer lands, swap the stub for an
    import; the assertions below do not change.

    W0.10 landed the registry. Running this template un-edited now *verifies
    the registry itself* — that it exists and defines exactly the seams
    ``C1``–``C10`` — and a copied template additionally requires its own pasted
    ``C``-entry. Nothing about the check skips any more: a seam test for an
    entry the registry does not define is testing a contract nobody froze.
    Copy it — do not leave it as a live test.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from tests.support.contract import SeamRegistry, assert_seam_payload

# (1) PASTE THE REGISTRY ENTRY ID HERE, exactly as it appears in the registry
#     (e.g. "C2 — P2 directive → P3 experiment adapter").
TEMPLATE_PLACEHOLDER = "C0-TEMPLATE"
REGISTRY_ENTRY = TEMPLATE_PLACEHOLDER

# (2) PASTE THE ENTRY'S FIELD LIST HERE, copied verbatim from the registry.
#     The registry is the single source of truth (P0 §9 R2); when the wire
#     changes, the registry entry is amended (via ADR) *first*, then this list,
#     the producer and the consumer all move together.
REGISTRY_FIELDS: tuple[str, ...] = (
    "brand_id",
    "experiment_id",
    "content_arm_id",
    "as_of",
    "observation_count",
)


def producer_stub() -> dict[str, Any]:
    """(3) Stand-in for the producer — replace with the real producer.

    Return the *serialisable* payload (what crosses the wire), never the
    producer's internal object. Values are illustrative; the types must match
    the registry's declared JSON types.
    """
    return {
        "brand_id": "neon_seoul",
        "experiment_id": "exp_01",
        "content_arm_id": "arm_control",
        "as_of": "2026-01-01T00:00:00+00:00",
        "observation_count": 42,
    }


class ConsumerSchema(BaseModel):
    """(4) The CONSUMER's expected schema — field names and types from the registry.

    ``extra="forbid"`` is required: without it a producer's added or renamed
    field would be silently dropped at the seam. ``datetime`` fields accept
    ISO-8601 strings because that is what a JSON wire carries.
    """

    model_config = ConfigDict(extra="forbid")

    brand_id: str
    experiment_id: str
    content_arm_id: str
    as_of: datetime
    observation_count: int


def test_declared_fields_match_the_consumer_schema() -> None:
    """The annotated registry copy and the pydantic schema must agree exactly."""
    assert set(ConsumerSchema.model_fields) == set(REGISTRY_FIELDS), (
        "REGISTRY_FIELDS and ConsumerSchema disagree — update both from the same "
        "registry entry, never one from the other"
    )


def test_producer_payload_validates_against_consumer_schema() -> None:
    """The seam proper: producer serialises, consumer schema accepts.

    The JSON round trip is deliberate — it forces the payload through the same
    wire form the real handoff uses, so in-memory shortcuts cannot mask a
    serialisation bug (a datetime that never survives ``json.dumps``).
    """
    payload = json.loads(json.dumps(producer_stub()))
    assert_seam_payload(payload, ConsumerSchema, seam=REGISTRY_ENTRY, producer="producer_stub")


def test_registry_entry_exists(contract_registry: SeamRegistry) -> None:
    """The frozen registry must exist, resolve ``C1``–``C10``, and — once this
    template has been copied and its ``C``-entry pasted — resolve that entry.

    No skip path: W0.10 created the registry, so its absence is a failure. The
    un-edited template cannot name a real entry (it ships the placeholder), so
    what it asserts is the registry it will be tested against; paste the entry
    id in spot (1) and the same assertion also requires that entry to exist.
    """
    assert contract_registry.exists, (
        f"contract registry missing ({contract_registry.path}) — W0.10 created it; "
        "a seam test cannot pin a contract whose registry is absent"
    )
    entry_ids = [
        line.removeprefix("## ").split("·", 1)[0].strip()
        for line in contract_registry.path.read_text(encoding="utf-8").splitlines()
        if line.startswith("## C")
    ]
    assert entry_ids == [f"C{n}" for n in range(1, 11)], (
        f"the registry must define exactly the ten seams C1..C10, in order; "
        f"found {entry_ids}"
    )
    if REGISTRY_ENTRY != TEMPLATE_PLACEHOLDER:
        contract_registry.require_entry(REGISTRY_ENTRY)


# ---------------------------------------------------------------------------
# WHAT A DRIFT FAILURE LOOKS LIKE
#
# Rename the producer field ``experiment_id`` to ``experimentId`` (a JSON
# export from a JavaScript service — the most common real-world drift) and the
# seam test prints exactly this:
#
#   AssertionError: C2-ar-to-experiments: producer payload (producer_stub) does not match the consumer schema (ConsumerSchema)
#     missing (consumer reads it, producer never sends it): ['experiment_id']
#     unknown (producer sends it, consumer cannot read it): ['experimentId']
#     likely a rename: 'experimentId' -> 'experiment_id' (or the registry disagrees with both)
#     a wire change is decided against the registry entry first (P0 §9 R2), then applied to producer and consumer in the same change
#
# One line names both halves of the rename, which is the whole point: the fix
# is decided against the registry, never against whichever side happens to be
# open. A schema-justified change (a genuinely new field) fails the same way in
# the consumer's ``extra="forbid"`` schema, so no drift reaches production
# silently. Golden files may be regenerated from the test side; a seam may not
# — the registry must move first.
