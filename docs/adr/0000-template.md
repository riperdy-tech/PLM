# ADR-0000: <short imperative title>

- Status: `template` (copy me to `docs/adr/NNNN-<slug>.md`)
- Date: `YYYY-MM-DD`
- Ruling: `R<n>` — the row in `docs/contracts/DECISION_LOG.md` this ADR reasons from
- Seam: `C<n>` (the `docs/contracts/CONTRACT_REGISTRY.md` entry it amends) or `none`
- Supersedes / superseded by: `—`

## Context

What was observed, and why it needed a decision: the conflict between documents,
what each side said, and what staying unresolved would cost. Name the documents.

## Decision

One or two sentences, imperative. What is now true that was not before.

## Evidence

The sections that disagree, with their locations — `PHASE-Pn §m` versus
`PHASE-Pn §k` (or `architecture.md PART <Roman>`). Quote the field or term when
the whole conflict is the name of one thing.

## Consequences

What changes (or must change) as a result: proposed amendments to phase
documents, the registry entry's required-amendments line, freeze order between
waves. What becomes forbidden.

## Alternatives considered

- *<alternative>* — why it was rejected.

## Cost if wrong

What breaks if the ruling is mistaken, and the remedy. Remedies for a seam are
always the same shape: a new ADR, then the registry amendment, then producer and
consumer in the same change — never a prose edit (P0 §9).
