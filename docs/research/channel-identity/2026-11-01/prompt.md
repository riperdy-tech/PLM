# Deep research brief: find the best permanent concept for an AI-assisted YouTube music playlist channel

## Before you start

- **Use only this brief.** Ignore any memory, saved preferences or earlier conversations you may have about this project or its owner.
- **Start from a blank slate.** No genre, mood, use case, time of day, era, city, country, culture, aesthetic, channel name, language or audience market is preferred or assumed. Nothing in this brief hints at an answer: where it lists examples, they are only there to show the range, and they are not candidates being favoured. If you catch yourself leaning towards something because it seems implied here, drop it and decide on evidence alone.
- You are one of three independent researchers. You cannot see the others' work, so give your own conclusion.

## Your role

Act as a market researcher and YouTube music-channel strategist. Research the current (2026) landscape using live sources, then pick **one** channel concept and defend it with evidence. Answering "it depends", or presenting several options as equal, counts as a failure.

## The goal (fixed)

Build a **sustainable YouTube music playlist channel that earns the most revenue possible** over a multi-year horizon. "Sustainable" means all of these:
- it keeps monetization through YouTube's reviews and policy enforcement;
- it doesn't depend on one viral hit;
- the audience comes back again and again;
- the owner can keep producing it under the constraints below.

The concept is chosen **once** and will never change. A wrong pick, or drifting later, is treated as fatal, so weigh downside risk as heavily as upside.

## How the channel is produced

### What is fixed

- **The music is original and AI-generated.** The models available are:
  - **Google Flow Music** (Google's Lyria 3.5 model). The owner makes each song by hand in the web app under a Google AI Pro subscription. It allows about 2,000 songs a month, Google's terms grant commercial use, and there is no API.
  - **Google's Gemini API** (Lyria 3.5). About US$0.08 a song, songs up to about 3 minutes, SynthID watermark.
  - **Stable Audio 3.0.** About US$0.08 a generation, up to 380 seconds, instrumental only.

  Suno was ruled out because it allows only 20 downloads a month. The Lyria models can sing as well as play.
- **The owner's time is the bottleneck.** A paid API can replace the owner's hand work, but only at a cost you must state.
- **Vocal songs need extra work:** written lyrics for every song, a lyrics check, and screening so the song doesn't sound like a real artist. Weigh that cost against whatever vocal niches offer.
- **The channel is faceless.** There is no on-camera person and no live-action filming.
- **The owner writes English and Korean.** Titles in other languages are possible with translation.

### What the software pipeline does

The channel is run by a mostly automated pipeline:
1. **Market intelligence.** Reads public YouTube data on music niches.
2. **Allocation.** An algorithm chooses which sub-style ("arm") each upload explores. It will be limited to the concept you choose, so the concept must be **coherent enough to be one channel** yet **wide enough to hold 6–12 sub-styles** to explore.
3. **Release design.** Plans each upload as one coherent concept with song-to-song variation.
4. **Quality and rights checks.** Automated checks that songs don't sound like each other or like protected artists.
5. **Video assembly.** Generated visuals with chapters and three thumbnail variants per video.
6. **Publishing.** The owner approves each upload.
7. **Analytics.** Results feed back into the allocation step.

### What is open (don't let the current setup decide)

- **Format and length.** The pipeline currently makes 45–75 minute album videos of about 15–23 songs, using an illustrated-scene video style. Other options can be built if the evidence favours them, including:
  - shorter or much longer videos (for example 2–10 hours);
  - 24/7 livestreams;
  - Shorts;
  - a different visual style, including AI video (Google Veo credits are available).

  Recommend whatever format your niche needs, and say what would be new work.
- **Everything else:** name, look, genre, language and audience. Two test videos exist; ignore them, as they can be deleted.

## What to research (with sources)

Consider **at least 12 candidate niches**, drawn widely across instrumental and vocal music, genres, moods, use cases, eras and cultures. List any niche you ruled out early, with one line on why. Research and report on each:

1. **Demand.** Search and viewing demand, its trend over the last 2–3 years, and seasonality. Is it growing, stable or declining?
2. **Listening behaviour.** Is it background listening (for an activity) or active listening? Typical session length and how often people return. Long sessions and repeat listening drive watch time and ad revenue.
3. **Revenue per view.** Realistic RPM and CPM ranges for this kind of content, for each likely audience country. Does the format change the picture (mid-roll ads on long videos, livestreams)? Cite the sources and say how reliable they are.
4. **Other income.** Memberships, Super Chat, distribution to Spotify, Apple Music and other platforms (including their 2025–2026 rules on AI music and distributors' rules), licensing to businesses and creators, sponsorships, and anything else relevant. Which are realistic for AI-made music, and which are closed to it?
5. **Competition and saturation.** At least 5 real comparable channels per niche:
   - subscribers;
   - typical views per upload;
   - upload cadence;
   - video length;
   - whether they are AI-made;
   - start year.

   How hard is it for a new channel to break in now? Where are the gaps incumbents underserve?
6. **AI and policy risk:**
   - YouTube Partner Program requirements, including announced future changes;
   - the "inauthentic content" policy (renamed from "repetitious content" in July 2025) and real 2025–2026 cases of AI music channels demonetized or approved, and what separated the two;
   - rules on disclosing AI-made content;
   - Content ID: false claims against AI music, and whether AI music can be registered;
   - platform or distributor bans.

   Is this niche especially exposed?
7. **AI quality fit.** How well current models, especially Lyria 3.5, handle this music compared with human-made incumbents. Is the "AI sound" or listening fatigue noticeable over a long session?
8. **Production fit.**
   - How many songs or hours of music a month the concept needs at a competitive cadence.
   - The owner's minutes per finished song by hand, as your estimate.
   - Whether one person can keep up, or what the paid API would cost each month.
   - What visual format the niche expects, and whether the pipeline can supply it.
9. **Durability.** Fad or evergreen? How exposed is it to one platform or algorithm change?
10. **Vocals.** Decide instrumental or vocal explicitly, with evidence. A hypothesis to confirm or refute: channels built around an activity almost never carry vocals, vocal playlist channels rarely carry instrumentals, and some genre or era channels mix the two.

## Method rules

- Use live, dated sources. Cite every number: who reported it and when.
- Label every figure as **measured**, **reported by a third party** or **your estimate**.
- Never invent channels, subscriber counts, RPMs or growth rates. If you can't find a figure, write "not found". Give a URL for every channel you name.
- Keep evidence and opinion separate.
- Watch for survivorship bias. Big channels that started years ago don't prove a new AI channel can do the same in 2026, so prefer evidence about channels started in 2024–2026, especially AI-made ones.
- Disclose your biggest uncertainties.

## Required output

Use exactly these headings, so the three reports can be compared side by side.

1. **Recommendation.** One concept, in one paragraph.
2. **Scoring table.** Every niche considered, scored 1–5 on each of:
   - demand trend;
   - watch time and repeat listening;
   - RPM;
   - other income;
   - competition (5 = least saturated);
   - AI/policy safety;
   - AI quality fit;
   - production feasibility;
   - durability.

   State and justify your weights, and give the weighted total. Explain every score of 1 or 5.
3. **The chosen concept, fully specified:**
   - 3 channel name ideas;
   - a positioning line;
   - target audience and countries;
   - title language;
   - genre boundaries: what is IN, and what is explicitly OUT;
   - instrumental or vocal (if vocal: language and voice type);
   - mood and use case;
   - 6–12 sub-styles the allocation algorithm may explore, and what must stay the same on every upload;
   - format, length and upload cadence;
   - songs needed per month;
   - visual identity (style, palette, thumbnail and title conventions);
   - how the channel stays clearly human-curated and distinct, so it isn't flagged as mass-produced.
4. **Revenue model.** Income streams in order. 12-month and 36-month projections, each with low, base and high cases. Show every assumption (views per upload, RPM, upload count, growth), each labelled measured, reported or estimate.
5. **Comparable channels.** A table with columns: name, URL, subscribers, typical views, cadence, length, AI-made or not, start year, source and date.
6. **Risks and mitigations.** The top 5 risks, including policy and demonetization, each with a concrete mitigation.
7. **Runner-up.** The second-best concept, and the specific evidence that would make it beat your pick.
8. **Validation plan for the first 90 days.**
   - Which metrics (CTR, average view duration, returning viewers, RPM, and others) and which thresholds confirm the choice.
   - Which early warning signs mean something needs fixing within the concept.
9. **Sources.** Full list with dates.
10. **Confidence.** Low, medium or high overall, and the 3 facts you are least sure of.
