# Contract (seam) tests

One file per **registry entry** — a producer/consumer pair frozen in
`docs/contracts/CONTRACT_REGISTRY.md` (W0.10). These tests assert that the
*producer's serialised payload* satisfies the *consumer's expected schema*
when the field names are taken from the frozen registry. They exist because
nine independently-written specs drift (P0 §4 risk 3): a rename or an added
field passes the producer's own tests and breaks the consumer three phases
later.

## Writing one

1. `cp _TEMPLATE_test_seam.py test_c<N>_<producer>_to_<consumer>.py` — the
   leading underscore keeps the template itself out of a default `pytest -q`.
2. Fill in the four PASTE spots: the registry entry id, the entry's field list,
   the producer stub (swap for the real producer when it exists), and the
   consumer schema (`extra="forbid"` is mandatory).
3. `pytest tests/contract/test_c<N>_<producer>_to_<consumer>.py -v`.

The stub stays until the real producer lands — the registry freezes the wire
first, so both sides of a seam can be written against it before either exists.

## What failure means

A drift failure names both halves of the problem:

```
AssertionError: C2-ar-to-experiments: producer payload (producer_stub) does not match the consumer schema (ConsumerSchema)
  missing (consumer reads it, producer never sends it): ['experiment_id']
  unknown (producer sends it, consumer cannot read it): ['experimentId']
  likely a rename: 'experimentId' -> 'experiment_id' (or the registry disagrees with both)
```

**Resolution order is fixed** (P0 §9 R2): amend the registry entry first (an
ADR in W0.10+), then the producer and consumer in the same change. A seam test
is never "updated" from the test side — unlike a golden file, there is no
`PLM_UPDATE_GOLDEN` escape hatch here, because regenerating an expectation
regenerates the contract.

## Helpers and conventions

- `tests.support.contract.assert_seam_payload` does the name check and the
  value validation; `SeamRegistry.require_entry` validates the registry entry
  (the skip only exists while the registry file itself is absent — it landed in
  W0.10).
- `test_contract_registry.py` is the suite's default registry check: the
  registry must exist, define exactly `C1`–`C10` in order, carry a complete
  entry per seam, and its pending/exists marks must agree with the files on
  disk.
- Fixtures (`contract_registry`, `mock_http`, `frozen_clock`, `seeded_rng`)
  come from `tests/conftest.py`; helper modules live under `tests/support/`.
- Consumer-side provider seams (P1/P7/P8/P9 connectors) use
  `tests.support.http.MockHTTP` — canned responses, recorded requests, and
  scripted 429/500/timeout/malformed-body failures.
- This suite must stay service-free: contract tests run in the fast CI step,
  before any container starts.
