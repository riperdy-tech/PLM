# R6 — real Suno in P4: what is missing (2026-10-30)

**Authority:** D8 covers step 1 (no live calls). Every paid Suno generation needs its
own owner authorization. This plan lists the code gaps and the owner decisions; it
authorizes nothing.

## What already works

- `get_provider_adapter("suno")` builds `SunoAdapter` (submit → poll → WAV download).
- The engine runs every REAL call through `ExternalWriteGuard`: idempotency key,
  plan refusals (`plan_provider_not_allowed`, money and credit ceilings), execution
  policy, authorization grant, budget reservation, then settle.
- The Suno login refresh is fixed and one billing read succeeded (D9).

## Gaps (code)

1. **Nothing selects Suno.** The P4 API uses `MusicFactorySettings.default_provider`,
   always `"mock"`; `PLM_PROVIDER_MODE__MUSIC` is never read. YouTube already maps
   `PLM_PROVIDER_MODE__YOUTUBE=real` to its real client; music should do the same.
2. **No authorization can ever be granted.** `create_app` builds the guard with no
   `authorizations`, and no code anywhere loads a `WriteAuthorization`. So `LIVE`
   always ends `no_authorization`, for music and every other capability. Needed: a
   loader for an owner-written, time-limited grant (capability, granted_by,
   granted_at, expires_at, reference), plus a cap on how many calls one grant covers.
3. **No Suno price.** The engine passes `cost_estimate=None`; the persisted ledger
   prices it from its rate card, which has no Suno entry, so the call is unfunded.
   Needed: a Suno credits rate and a credit ceiling for the run.
4. **Generate, poll and download are untested against Suno itself.** Only billing was
   recorded. The first authorized generation doubles as the recording.

## Owner decisions

- **D10 — Suno terms and plan tier.** The client uses Suno's private web endpoints
  with a session cookie, not a published API. Suno's terms on that are unchecked, and
  commercial use of the songs on YouTube depends on the plan tier at generation time.
- **D11 — credit ceiling** for the first run (one song, not an album).
- **D12 — the first authorized call:** one song through the guarded P4 path, recorded
  as a `recorded-live` fixture, no upload.

## Order

Gaps 1–3 in code with tests (no live call) → D10/D11 → D12 single song → review →
album-sized run → guarded upload (separate authorization).
