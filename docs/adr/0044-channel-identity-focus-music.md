# ADR-0044 — Channel identity locked: instrumental deep-focus electronic music

- Status: accepted by the owner, 2026-11-01. **No pipeline change made yet**; P2, P3 and the held run are untouched.
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
3. **Video length 90–120 min, replacing ADR-0036's 45–75 min for this brand.**
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

## Consequences

- The held live run `b0eb2a4f-4850-5977-9beb-e609e4e11063` (19 vocal R&B
  sheets) is outside this identity. What to do with it is the owner's call.
- P2 needs limiting to focus-music arms, and P3 templates need checking
  against the IN/OUT lists. Both are follow-up work that needs approval.
- The main open risk is that focus music's higher RPM is estimated, not
  measured. The 90-day metrics in the ChatGPT report are the check.
