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

## Progress (2026-10-30)

- Gap 1 done: `PLM_PROVIDER_MODE__MUSIC=real` selects Suno in `serve`.
- Gap 2 done: `PLM_WRITE_AUTHORIZATIONS_FILE` grants reach the P4 guard; `expires_at` is
  required. The number of calls is capped by the budget ceiling, not by the grant.
- Gap 3 needs no code: the price and ceiling are ledger data, set at run time with
  `plm budget set-rate --capability music.generate ...` and `plm budget set-ceiling ...`.
- Gap 4 stays open until the first authorized call (D12).

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

## D10 findings (2026-10-30)

Owner: "sudo plan, the 20 dollar one" (a paid Suno plan). suno.com/pricing lists Pro
($8/month billed annually, 2,500 credits) and Premier ($24, 10,000 credits); both
include commercial use rights. Which of the two is unconfirmed.

suno.com/terms, read the same day (not legal advice):

- *User Conduct* bars obtaining content "through any means not intentionally made
  available or provided for through the Service" and any "robots, scraping, or similar
  data gathering". The P4 client drives Suno's private web endpoints with the owner's
  session cookie, which these clauses appear to cover. Risk: account suspension.
- *Permitted Commercial Use* grants commercial rights only for Output that follows
  the conduct rules **and** was obtained through a permitted Download, within the
  tier's monthly download allowance. Songs fetched by the automated client may
  therefore not carry commercial rights even on a paid plan.

So D10 is open: the owner must choose between accepting that risk, or making songs
in Suno's own interface and having PLM import the downloaded files (no import path
exists yet).
