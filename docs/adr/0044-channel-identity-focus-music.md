# ADR-0044 — Channel identity locked: instrumental deep-focus electronic music

- Status: accepted by the owner, 2026-11-01; amended the same day (implementation decisions, then the video-length revision below). Applied across P1–P8: see `docs/reviews/2026-11-01-focus-identity-rectification.md`.
- Date: 2026-11-01
- Owner: channel owner (decision); P2/P3 (follow-up work)
- Related: `docs/research/channel-identity/2026-11-01/` (prompt, three identity reports, sleep-vs-focus follow-up)

## Context

The goal is a sustainable YouTube music playlist channel that earns the most
revenue possible. P2 must explore sub-styles inside one fixed identity, so the
identity had to be chosen once.

An unbiased brief went to three AIs (Claude, ChatGPT, Brave; Gemini failed).
Claude and Brave picked instrumental sleep music; ChatGPT picked instrumental
deep-focus electronic music. A narrow follow-up on ad revenue (sleep vs focus)
went to four AIs: DeepSeek, Perplexity and ChatGPT picked focus, Brave picked
sleep. The deciding evidence, all creator-reported and unverified:

- sleep creators switch mid-rolls off, leaving about one ad per view
  (one 8–10 h sleep channel reports about $1 RPM against a $2.28 music median);
- real sleep average view durations are minutes to an hour, not hours;
- the 1,000-subscriber bar, not watch hours, is the hard gate, and sleep
  listeners subscribe rarely.

Verified on YouTube Help: the Feb 1, 2027 bar of 8,000 qualified watch hours
in 365 days (answer 12843009); RPM already includes Premium (answer 9314357).

## Decision

The channel is **instrumental deep-focus electronic music for coding,
concentrated desk work and long knowledge-work sessions**, as specified in
`docs/research/channel-identity/2026-11-01/chatgpt-report.md`:

- IN: future garage, low-intensity chillstep, atmospheric electronica,
  restrained synthwave, downtempo, soft breakbeat, subtle ambient techno,
  minimal electronic, organic-electronic hybrids, piano/synth crossover.
- OUT: vocals or spoken word, lo-fi hip-hop clichés, trap, EDM drops, festival
  house, high-BPM drum and bass, sleep drones, nature-sound-only, café jazz,
  orchestral trailer music, any "in the style of" a real artist.
- Instrumental only; English-first titles; audience aimed at US, UK, Canada,
  Australia, Germany, Netherlands and the Nordics.

The identity does not change. The report's name ideas, nine sub-styles,
70–110 BPM range, 90–120 minute format and two-a-week cadence are its
proposals, not yet confirmed by the owner.

## Amendment 2026-11-01: implementation decisions

The owner ruled that every P1–P9 part conflicting with this identity is to be
rectified without further approval, and that the open choices are decided by
how well they serve the project target, not by preference. Decisions:

1. **Sub-styles (P2 arms, P3 subgenres):** the report's nine:
   `future_garage_focus`, `soft_cyber_electronica`, `minimal_pulse`,
   `ambient_breakbeat`, `downtempo_synth`, `deep_work_chillstep`,
   `restrained_retro_future`, `organic_electronic`,
   `piano_synth_concentration`. They span the IN list without crossing into
   OUT, and nine is inside the 6–12 the bandit needs.
2. **Tempo 70–110 BPM on every track.** Below about 70 drifts into sleep and
   ambient-drone territory (OUT). Above about 110 drifts into dance (OUT). Focus
   listening wants a steady, subdued pulse.
3. **Video length 90–120 min, replacing ADR-0036's 45–75 min for this brand.** *(Superseded by the 20–30 min revision below.)*
   Focus won the revenue comparison only because awake listeners accept
   mid-rolls, and ad load per view grows with length. 90–120 min matches one
   deep-work block (the report's session unit) and builds watch hours toward the
   8,000-hour bar about twice as fast per view as 45–75 min. Cost: about 33–44
   tracks per video, so supply by hand is heavier. That is a production problem
   to solve (API supply, with paid calls authorized per run), not a reason to cut
   the revenue lever.
4. **Which P1 arms P2 may pick:** use case `study_focus` or `focus`; genre
   `focus_electronic` or unset (P3 infers `focus_electronic` for focus use
   cases); vocal mode not vocal. Everything else is out of identity. An arm with
   no use case measures a general market, not focus demand. Ambient, synthwave
   and deep-house arms are out too, because P3 would build them from their own
   templates (drone, neon, build-and-drop dance). That leaves 6 of today's 77
   arms; P1's new focus vocabulary (5f87cb8) adds measured focus arms as it
   collects.

## Revision 2026-11-01: 20–30 minute videos, supply by hand

The owner will not spend money on a paid music API. Supply is by hand through
Flow Music / Gemini on the owner's Gemini Pro free credit. Decision 3 changes
from 90–120 minutes to **20–30 minutes**, with variety between videos. Why this
fits the target:

- Even 90–120 min videos expect a median watch of about 20 minutes (the ChatGPT
  report's own success test), so most of the extra length goes unwatched and
  carries few of its mid-rolls.
- A 20–30 min video still allows mid-rolls (eligible after 8 minutes), and
  stays under YouTube's "continue watching?" prompt (30 min mobile, 60 web).
- About 6–9 tracks per video instead of 33–44, so two videos a week by hand is
  feasible; more uploads also give P2 more measured results.
- Cost: watch hours per view are capped at the video length, and 20–30 min
  videos compete less for "2 hours coding music" searches. A later remedy, not
  yet built: periodic 1–2 h "session" compilations made from existing tracks,
  which need no new generation.

Variety: when an arm's brief names no sub-style (or one not listed), P3 picks one
of the nine that was not used by the genre's most recent experiments, so
consecutive videos differ. A sub-style the arm names is kept.

## Consequences

- The held live run `b0eb2a4f-4850-5977-9beb-e609e4e11063` (19 vocal R&B
  sheets) was cancelled by the owner on 2026-11-01.
- P2 is limited to focus-music arms and P3's templates follow the IN/OUT lists
  (rectification register above).
- No paid Lyria / Gemini API adapter is planned; every track is made by hand.
- The main open risk is that focus music's higher RPM is estimated, not
  measured. The 90-day metrics in the ChatGPT report are the check.
