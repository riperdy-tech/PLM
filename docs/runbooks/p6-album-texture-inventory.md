# P6 legacy album texture inventory (read-only)

## Purpose

Use `plm media inspect-album-textures --brand-id BRAND_ID` to review
**stored metadata only** for previously READY/PUBLISHED P6 packages of one
explicit brand. No MinIO audio is fetched, no P5/P7 operation runs, and no
database rows are inserted or updated. This is an operator diagnostic,
not a release-approval workflow.

```text
plm media inspect-album-textures --brand-id BRAND_ID --limit 20
plm media inspect-album-textures --brand-id BRAND_ID --limit 20 --after-id PACKAGE_ID
```

Supply the exact `--after-id` printed on a page with `more: True`.
The maximum page size is 100 and each request reads at most 101 records.
The order is lexical package ID; a sequence of pages is **not** a single
transactional snapshot. Concurrent writes can affect the combined listing.
Missing and failed packages are not included; inspect every page before
drawing a catalog-wide conclusion. A full list also does **not** bypass
P6's separate fail-closed 100-package comparison ceiling.

## Interpretation

- `missing_evidence`: no measurable texture evidence recorded.
  May be a candidate for the **separate, explicitly selected** dry-run
  backfill, but this inventory does not verify the audio exists.
- `incompatible_version`: stored policy or extractor version differs
  from the current version. The ordinary backfill does not overwrite
  existing incompatible evidence. Investigate a separately approved
  migration/reverification strategy.
- `invalid_evidence` or `unreadable_record`: saved report, ordered
  tracklist, checksums or normalized signatures fail metadata validation.
  Album rows must contain consecutive 1-based integer positions, distinct
  nonempty song IDs, nonempty source artifact IDs and matching ordered
  measured signature identities; malformed metadata cannot be trusted.
  Do not treat the package as verified, and do not force a write.
- `source_metadata_unverifiable`: the saved signature looks valid
  but the original P6 playlist header or ordered track records are missing
  or disagree on experiment, arm, source artifact or stored audio SHA256.
  Stop and investigate; do not interpret the mismatch as a rights decision.
- `evidence_structure_valid`: the stored report passes signature normalization
  and ordered-track checks, and the *stored P6 source metadata* agrees
  on playlist identity, song IDs, artifacts and recorded SHA256 strings.
  **It does not mean SHA256 was recomputed from MinIO bytes, the original
  audio still exists, the content is unique, rights are cleared, or release
  approval has been granted.** These are separate queries within the
  read transaction, not an atomic snapshot under concurrent writes.

If choosing a package for independent verification, use
`plm media backfill-album-texture --package-id PACKAGE_ID` **without**
`--apply` first. This does fetch and remeasure original audio; it
remains a separate action from the metadata-only inventory. Actual
database writes require explicit `--apply`, review of refusal codes,
and operator authorization under project rules. Never bulk-apply based
on an inventory label alone. Do not use this inventory to override P5/P7
rights, quality or publication holds.

The inventory uses no wall-clock selection and reports no human claims
about acoustic originality. It is for reconciling the historical
catalog before any production remediation.
