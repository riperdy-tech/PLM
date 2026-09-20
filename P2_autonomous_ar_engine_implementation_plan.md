# P2 — Autonomous A&R / Opportunity Allocation Engine
## Detailed Implementation Plan

**Document status:** Implementation-ready specification  
**Phase:** P2 of the Autonomous AI Music Media Engine  
**Primary objective:** Convert P1's evidence-backed market opportunities into an autonomous, measurable A&R decision system that chooses what to test, how often to test it, how much to exploit vs. explore, and how to update beliefs from observed results.

---

# 1. Purpose

P2 is the decision layer between **market intelligence (P1)** and **content/experiment execution (P3+)**.

Its job is not to generate music and not to predict a guaranteed viral winner.

Its job is to answer:

> **Given the current market evidence, our historical experiment results, our available production capacity, and our exploration requirements, which content opportunities should receive the next production slots?**

P2 must convert uncertain opportunities into a controlled sequence of experiments.

The core design principle is:

```text
P1 = external market evidence
P2 = decision policy
P3+ = execution and measurement
```

P2 must remain independent from any specific music-generation vendor, video generator, distributor, or publishing mechanism.

---

# 2. Strategic intent

The system must not ask a human:

> "Should we make R&B or city pop?"

Instead, it should maintain competing hypotheses such as:

```text
R&B × late-night drive × English vocal
Rainy city pop × instrumental
Luxury deep house × hotel lounge
Jazz café × instrumental
Ambient sleep × long-form
```

and allocate production opportunities according to observed evidence.

The policy should learn over time.

However, it must never collapse into a single permanent winner simply because one early result was unusually strong.

---

# 3. Scope

## 3.1 In scope

P2 must implement:

1. Ingestion of P1 market-opportunity arms.
2. Canonical opportunity eligibility checks.
3. Historical experiment-result ingestion contract.
4. Cold-start priors derived from P1.
5. Empirical performance updates.
6. Contextual arm representation.
7. Exploration vs. exploitation policy.
8. Thompson-sampling or equivalent uncertainty-aware allocation.
9. Diversity controls.
10. Novelty/exploration controls.
11. Market non-stationarity handling.
12. Arm lifecycle management.
13. Production-slot allocation.
14. Experiment generation directives.
15. Confidence-aware decisioning.
16. Decision provenance.
17. Policy/version tracking.
18. Budget/capacity constraints.
19. Cooldowns and fatigue controls.
20. Decision APIs for P3+.
21. Simulation/backtesting of policy behavior using synthetic or historical data.
22. Unit, property, integration and policy tests.
23. Observability and decision audit logs.

## 3.2 Explicitly out of scope

Do not implement in P2:

- Music generation.
- Suno integration.
- Local music-generation inference.
- Audio mastering.
- Visual generation.
- YouTube upload.
- Spotify upload/distribution.
- YouTube Analytics ingestion implementation.
- Spotify royalties ingestion implementation.
- Thumbnail rendering.
- Video rendering.
- Comment automation.
- Final copyright review implementation.
- Full channel-level financial optimization.

P2 may define interfaces that those phases will satisfy later.

---

# 4. Dependency model

P2 consumes P1.

Required P1 outputs:

```text
content arm
market opportunity score
confidence
component market scores
freshness
source counts
observation counts
taxonomy
score version
```

P2 also requires experimental outcome data.

That outcome data is ultimately produced by P9/P10, but P2 should define a provider-neutral result contract now so later phases can connect without changing P2's decision logic.

Conceptually:

```text
P1 market evidence
        +
experiment history
        +
production constraints
        +
policy constraints
        ↓
      P2
        ↓
production directives
        ↓
P3+ execution
        ↓
observed results
        ↓
P2 update
```

---

# 5. Core principle: P2 selects experiments, not outcomes

P2 must never claim:

```text
"this video will get 100,000 views"
```

It may calculate:

```text
posterior expected reward
posterior uncertainty
selection probability
expected value under current model
```

The difference is important.

A posterior estimate is a model output, not a guarantee.

---

# 6. Decision unit: the content arm

The fundamental decision unit is the `content_arm` supplied by P1.

An arm represents a production hypothesis such as:

```text
Genre = R&B
Mood = melancholic/dreamy
Use case = late night drive
Language = English
Vocal mode = vocal
Energy = low-medium
BPM = 82–96
Duration = 45–75 minutes
Visual world = rainy Asian city
Title archetype = time-place-mood
```

An arm is **not** a single song.

An arm may produce many:

```text
playlists
videos
tracks
shorts
releases
```

The same arm can therefore accumulate repeated observations over time.

---

# 7. Arm identity and arm similarity

P2 must distinguish:

1. the exact same arm;
2. a near-neighbor arm;
3. a genuinely novel arm.

Use a canonical arm fingerprint based on normalized P1 dimensions.

Example:

```text
rnb|melancholic|late_night_drive|english|vocal|low_medium|45_75m|rainy_asian_city|time_place_mood
```

Store a stable hash of the canonical representation.

Do not use free-form strings from an LLM as the only identity mechanism.

---

# 8. Near-neighbor detection

Two arms can be different but closely related.

Example:

```text
Arm A:
R&B + late-night drive + English + female vocal

Arm B:
R&B + night drive + English + male vocal
```

These arms should not be treated as unrelated.

Near-neighbor similarity is needed for:

- transfer of prior information;
- controlled exploration;
- preventing redundant experiments;
- finding adjacent opportunities.

Represent the arm as a structured feature vector.

Use a deterministic similarity layer first.

Optional embedding similarity may be added later, but embedding similarity must not replace structured feature comparison.

---

# 9. Arm lifecycle

Supported statuses:

```text
candidate
active
cooldown
paused
retired
blocked
archived
```

### candidate

Visible from P1 but not yet selected for production.

### active

Eligible for allocation.

### cooldown

Temporarily ineligible because it was tested too recently or has exceeded a frequency/fatigue threshold.

### paused

Manually or automatically paused because of temporary conditions.

### retired

No longer worth spending meaningful production capacity on under the current policy.

### blocked

Cannot be used because of policy, rights, safety or provider constraints.

### archived

Historical state only; not eligible for allocation.

---

# 10. Cold-start prior

New arms have no first-party performance data.

Therefore P2 must initialize priors from P1 market evidence.

This is **not** the same thing as treating P1's score as a guaranteed reward.

Define a transform:

```text
P1 market evidence
        ↓
prior mean
prior variance
prior confidence
```

The transform must be versioned.

Example conceptually:

```text
market_opportunity_score = 82
confidence = 0.70

→ relatively optimistic prior
→ moderate uncertainty
```

while:

```text
market_opportunity_score = 82
confidence = 0.20

→ similar prior direction
→ much wider uncertainty
```

The exact statistical transform is implementation-configurable, but must be deterministic once the configuration/version is fixed.

---

# 11. Recommended initial statistical policy

Use a **two-layer policy** rather than jumping directly to a complex deep-learning recommender.

## Layer A — Bayesian arm model

Start with Thompson Sampling using a scalar reward representation.

Each arm maintains a posterior distribution for an abstract `normalized_reward` value.

Recommended implementation:

```text
Gaussian / Normal-Normal style posterior
```

rather than Beta-Bernoulli because the reward will be continuous.

The implementation should support replacing the posterior model later without changing the public P2 API.

## Layer B — contextual adjustment

Use structured arm features to allow information transfer between similar arms.

The first production implementation may use a Bayesian linear model or another lightweight contextual model.

Do not start with a deep neural network.

The dataset will be too small during cold start, and the system needs interpretability.

---

# 12. Why not pure deterministic ranking?

A deterministic ranking such as:

```text
sort by market_opportunity_score
```

will repeatedly select the same arms and will not learn efficiently.

It also encourages premature convergence.

P2 instead needs uncertainty-aware sampling.

A high-performing but uncertain arm should be tested more than a mediocre arm with low uncertainty.

Likewise, a novel but weakly understood arm must receive some exploration capacity.

---

# 13. Exploration policy

Initial default allocation:

```text
70% exploitation
20% adjacent exploration
10% radical exploration
```

These percentages must be configuration values.

P2 must not assume this ratio is universally correct.

The configuration should support:

```text
exploit_fraction
adjacent_explore_fraction
radical_explore_fraction
```

and enforce:

```text
sum = 1.0
```

with a startup validation error otherwise.

---

# 14. Exploration definitions

## 14.1 Exploitation

Choose arms with strong posterior economic/engagement reward under current evidence.

## 14.2 Adjacent exploration

Choose arms close to known promising arms, but with one or more changed dimensions.

Examples:

```text
female vocal → male vocal
45–75m → 75–120m
R&B → alt R&B
Seoul → Tokyo visual world
```

## 14.3 Radical exploration

Choose arms with insufficient historical evidence and meaningful distance from the current portfolio.

This prevents the system from converging into a monoculture.

---

# 15. Portfolio-level diversity

P2 must not allocate all production slots to one arm or one nearly identical cluster simply because of a temporary spike.

At minimum apply configurable caps at these levels:

```text
genre
use_case
language
visual_world
arm_cluster
exact_arm
```

Example configuration:

```text
max_exact_arm_share = 0.50
max_genre_share = 0.60
max_use_case_share = 0.60
max_language_share = 0.85
```

These are defaults only.

Do not hard-code them into application logic.

---

# 16. Why caps are necessary

Suppose one arm gets an unusually large first result.

Without portfolio constraints:

```text
R&B 97%
Other 3%
```

The system may stop learning about the rest of the market.

With diversity constraints:

```text
R&B 55%
Adjacent R&B 15%
City Pop 15%
House 10%
Novel 5%
```

The portfolio continues to learn.

---

# 17. Reward definition

P2 needs a provider-neutral reward schema because P9 will later supply actual platform metrics.

Define a `RewardObservation` contract containing both raw metrics and normalized reward components.

Possible raw inputs:

```text
views
watch_time_minutes
average_view_duration_seconds
average_percentage_viewed
subscriber_gain
likes
comments
shares
estimated_revenue
spotify_streams
spotify_revenue
production_cost
```

P2 must not require all of these to exist.

Every metric should be nullable.

---

# 18. Reward windows

A result should be observable at multiple horizons.

Recommended initial windows:

```text
24h
72h
7d
28d
90d
lifetime
```

P2 should not treat a 24-hour observation as final.

Use explicit status:

```text
provisional
mature
closed
```

---

# 19. Reward maturity

A result should move from provisional to mature only when the required observation window is complete or the metric is otherwise marked closed by the upstream analytics system.

Example:

```text
24h result → provisional
7d result  → mature_for_early_signal
28d result → mature_for_normal_comparison
90d result → mature_for_lifetime_proxy
```

Do not overwrite historical reward observations.

---

# 20. Initial normalized reward

Before actual revenue is available, use a configurable engagement proxy.

Example starting configuration:

```text
30% normalized watch-time signal
25% normalized view velocity
15% retention signal
10% subscriber conversion
10% engagement
10% qualitative/catalog signal
```

These are initial priors only and should later be replaced or supplemented by actual economic reward.

Store the full decomposition.

---

# 21. Economic reward after monetization

Once revenue data is reliable, P2 should support an economic reward mode:

```text
reward
=
YouTube revenue
+
Spotify revenue
+
other verified catalog revenue
-
variable production cost
```

Optionally include a bounded future-value term for catalog effects.

Do not allow speculative future revenue to dominate observed cash revenue.

The exact financial function belongs jointly to P2 and P9, with P9 owning accounting truth.

---

# 22. Reward normalization

All reward models need normalized values so a single large channel metric cannot dominate forever.

Use robust percentile normalization or a model-specific standardized transformation.

Avoid lifetime raw views as a primary reward input.

For example, prefer:

```text
7-day views per eligible exposure
```

over:

```text
lifetime views
```

when comparing new videos.

The upstream P9 pipeline must provide consistent denominators where needed.

---

# 23. Delayed outcomes

P2 must support delayed feedback.

An arm can be selected today but only generate reliable revenue information weeks later.

Therefore maintain separate state for:

```text
selection
production
publication
observation
reward maturity
posterior update
```

Do not update the posterior merely because a video was published.

---

# 24. Attribution

A reward can only update the arm if the system can trace the result back to an arm.

Minimum attribution path:

```text
arm_id
 → decision_id
 → production_directive_id
 → content_id
 → published_asset_id
 → analytics observation
```

If attribution is ambiguous, quarantine the result rather than updating the wrong arm.

---

# 25. Cannibalization / correlated experiments

Two nearly identical experiments launched simultaneously may compete for the same audience and distort measurement.

P2 should therefore calculate a `similarity_cluster_id` for near-neighbor arms.

Within the same cluster, impose configurable concurrency limits.

Example:

```text
max_concurrent_experiments_per_cluster = 2
```

This reduces redundant experiments.

---

# 26. Audience saturation / fatigue

Repeatedly publishing the same concept can cause fatigue.

P2 should track recent exposure by arm and cluster.

Inputs:

```text
videos_last_7d
videos_last_28d
playlist_share_last_28d
recent_impressions_share if available
```

Implement a fatigue penalty that grows with recent production frequency.

Do not use a permanent penalty.

Fatigue decays over time.

Conceptually:

```text
recent exposure high
→ temporary penalty
→ cooldown
→ penalty decays
```

The exact decay function must be configurable.

---

# 27. Non-stationarity

Music demand changes.

A posterior based entirely on old results can become misleading.

P2 should include temporal decay.

Initial recommendation:

```text
recent observations receive more weight
older observations decay gradually
```

Do not discard historical data.

Store both:

```text
raw historical reward
weighted current reward
```

The decay configuration must be versioned.

---

# 28. Regime/change detection

P2 should monitor whether an arm's performance appears to change materially.

Example signals:

```text
7d reward << 28d baseline
7d reward >> 28d baseline
multiple neighboring arms shift together
market opportunity score shifts materially
```

When a significant regime shift is detected:

```text
increase exploration temporarily
reduce confidence in stale priors
refresh P1 data
```

Do not automatically retire an arm from one anomalous result.

---

# 29. Decision pipeline

Every allocation cycle should follow this order:

```text
1. Load eligible P1 arms
2. Refresh arm states
3. Load recent experiment history
4. Load posterior state
5. Apply freshness constraints
6. Apply blocked/policy constraints
7. Apply cooldown/fatigue
8. Calculate contextual features
9. Sample posterior reward for each eligible arm
10. Apply diversity constraints
11. Apply capacity/budget constraints
12. Build selected experiment portfolio
13. Create decision record
14. Create production directives
15. Return result to P3
```

Do not reverse this order without documenting the effect.

---

# 30. Decision cycle inputs

Required:

```text
as_of
available_production_slots
available_budget
active_channel_id
eligible_market_arms
arm_posteriors
policy_config
```

Optional:

```text
brand profile
channel maturity
platform-specific constraints
seasonality context
provider availability
```

---

# 31. Production slot model

P2 should work in discrete decision cycles.

Example:

```text
weekly_cycle
slots = 10
```

or:

```text
daily_cycle
slots = 2
```

The implementation must not assume one schedule.

Use:

```text
DecisionCycle
```

with:

```text
cycle_id
start_at
end_at
slot_count
budget
policy_version
```

---

# 32. Production directive

P2 outputs a directive rather than directly calling P3.

Suggested schema:

```json
{
  "directive_id": "uuid",
  "decision_id": "uuid",
  "arm_id": "rnb_latenight_drive_en_vocal_60",
  "allocation_type": "exploit",
  "priority": 1,
  "quantity": 1,
  "brief": {
    "genre": "rnb",
    "mood": ["melancholic", "dreamy"],
    "use_case": "late_night_drive",
    "language": "english",
    "vocal_mode": "vocal",
    "duration_bucket": "45_75m",
    "visual_world": "rainy_asian_city"
  },
  "expected_value": 0.72,
  "uncertainty": 0.31,
  "policy_version": "p2-v1",
  "expires_at": "..."
}
```

P3 is responsible for translating this into concrete production work.

---

# 33. Directive flexibility

The directive should define a **creative search region**, not an over-constrained exact song prompt.

Good:

```text
BPM: 82–96
vocal: English female/mixed
mood: dreamy, nocturnal
visual: rainy Asian city
```

Bad:

```text
exact chord progression = Cmin7 → Abmaj7 → ...
exact lyric = ...
exact melody = ...
```

P2 is strategic A&R, not a low-level music composer.

---

# 34. Allocation classes

Every directive must be labeled:

```text
exploit
adjacent_explore
radical_explore
```

This classification is required for analytics later.

The learning system must know whether a result came from exploiting a known winner or exploring uncertainty.

---

# 35. Selection score vs. market score

Do not combine these concepts into one field.

P1:

```text
market_opportunity_score
```

P2:

```text
posterior_expected_reward
sampled_reward
selection_priority
```

P1 asks:

> "Does external market evidence look attractive?"

P2 asks:

> "Given what we know and don't know, where should we spend the next production slot?"

---

# 36. Confidence handling

P2 must distinguish:

```text
high market confidence
high empirical confidence
high total decision confidence
```

These are not interchangeable.

Example:

```text
P1 confidence = 0.90
P2 empirical confidence = 0.20
```

means the market evidence is strong but the channel has not tested the idea sufficiently.

Conversely:

```text
P1 confidence = 0.30
P2 empirical confidence = 0.90
```

means the market data is weak but the channel has substantial first-party evidence.

---

# 37. Promotion policy

A promising arm should receive more production capacity when:

```text
posterior reward strong
AND
uncertainty acceptable
AND
recent fatigue acceptable
AND
portfolio constraints allow
```

Do not use a hard rule such as:

```text
if score > 80 then allocate 80%
```

Use the probabilistic policy.

---

# 38. Retirement policy

An arm may be retired when a configurable combination of evidence indicates persistently poor performance.

For example:

```text
minimum mature observations >= N
AND
posterior probability of beating baseline < threshold
AND
recent performance weak
AND
market opportunity weak or deteriorating
```

Do not retire an arm with one bad experiment.

Retirement should be reversible by reactivation if new P1 evidence changes materially.

---

# 39. Arm reopening

A retired/cooldown arm may be reconsidered when:

```text
market score rises materially
OR
new adjacent trends emerge
OR
brand/audience context changes
OR
new first-party evidence invalidates prior assumptions
```

Store the reopen reason.

---

# 40. Baseline arm

P2 should maintain a small number of evergreen baseline concepts.

Purpose:

```text
control for platform volatility
control for channel-wide changes
compare experiments against a stable reference
```

A baseline is not necessarily the highest-scoring arm.

It is a measurement control.

Recommended initial baseline allocation:

```text
5–15%
```

Configuration-driven.

---

# 41. Control experiment

P2 should periodically allocate a slot to a stable baseline even when current exploration signals are noisy.

This gives P9/P10 a reference point for assessing whether all content moved together because of external conditions.

---

# 42. Seasonality

Do not build a full seasonal model in P2 initially.

However, P2 must preserve timestamps and support a future seasonal feature.

If P1 supplies a seasonal signal, it may influence a configurable feature weight.

Do not hard-code assumptions such as Christmas always being better.

---

# 43. Geographic/language context

P2 should support multiple audience hypotheses without forcing one language globally.

Example:

```text
English-global
Korean
Japanese
Traditional Chinese
instrumental/no-language
```

Each can be an experimental dimension.

Do not infer that the user's language or geography is the target market.

Channel-level geography comes from actual performance data later.

---

# 44. P2 database model

Add at minimum:

```text
content_arm_state
arm_feature_snapshot
arm_posterior
experiment_result
reward_observation
allocation_policy
allocation_cycle
allocation_decision
production_directive
arm_similarity
arm_exposure
baseline_config
```

---

# 45. `content_arm_state`

Fields:

```text
id
arm_id
status
first_selected_at
last_selected_at
selection_count
mature_result_count
provisional_result_count
recent_exposure_score
retirement_reason
cooldown_until
last_updated_at
state_version
```

---

# 46. `arm_feature_snapshot`

Persist the exact features used at decision time.

Fields:

```text
id
arm_id
captured_at
p1_score
p1_confidence
demand_score
growth_score
competition_score
cross_platform_score
production_fit_score
freshness_seconds
feature_json
feature_version
```

Do not recompute historical decisions from current P1 values.

Historical decisions must use historical feature snapshots.

---

# 47. `arm_posterior`

Fields:

```text
id
arm_id
model_type
model_version
prior_mean
prior_variance
posterior_mean
posterior_variance
observation_count
effective_observation_count
last_update_at
update_version
```

If using a contextual Bayesian model, also persist the model-specific parameter state in a versioned format.

---

# 48. `experiment_result`

Fields:

```text
id
experiment_id
arm_id
content_id
observation_window
status
observed_at
raw_metrics_json
normalized_reward
reward_version
attribution_confidence
source_ids
```

---

# 49. `allocation_policy`

Fields:

```text
id
name
version
exploit_fraction
adjacent_explore_fraction
radical_explore_fraction
baseline_fraction
max_exact_arm_share
max_cluster_share
max_genre_share
max_use_case_share
fatigue_config
nonstationarity_config
retirement_config
created_at
active
```

Do not store these only in code.

---

# 50. `allocation_cycle`

Fields:

```text
id
channel_id
started_at
finished_at
status
slot_count
budget
policy_id
p1_snapshot_time
decision_count
selected_count
rejected_count
warning_count
```

---

# 51. `allocation_decision`

Fields:

```text
id
cycle_id
arm_id
allocation_type
sampled_reward
posterior_expected_reward
selection_priority
rank_before_constraints
rank_after_constraints
constraint_adjustments_json
reason_codes
policy_version
feature_snapshot_id
created_at
```

This table is critical for debugging the autonomous system.

---

# 52. `production_directive`

Fields:

```text
id
decision_id
arm_id
quantity
brief_json
priority
status
expires_at
created_at
consumed_at
```

Possible statuses:

```text
queued
accepted
in_progress
completed
cancelled
expired
failed
```

---

# 53. Arm similarity

Store similarity relationships rather than recomputing every time.

Fields:

```text
arm_id_a
arm_id_b
similarity_score
similarity_method
feature_version
computed_at
```

Only store pairs above a configured similarity threshold.

---

# 54. Exposure tracking

`arm_exposure` should capture recent production exposure.

Fields:

```text
arm_id
window_start
window_end
selection_count
content_count
estimated_audience_exposure
exposure_score
calculation_version
```

Where impressions are unavailable, use production-count proxies and label them accordingly.

---

# 55. Contextual feature set

P2's first contextual feature vector should remain interpretable.

Suggested features:

```text
P1 market opportunity score
P1 confidence
Demand
Growth
Competition
Audience quality
Cross-platform evidence
Production fit
Freshness
Taxonomy categorical features
Recent arm reward
Recent cluster reward
Observation count
Time since last selection
Fatigue score
```

Use normalized numeric forms plus encoded categorical features.

---

# 56. Avoid feature leakage

When calculating the reward or features for a historical experiment, do not use data that was only available after the decision was made.

Example:

Incorrect:

```text
decision on Monday
feature uses P1 score from Wednesday
```

Correct:

```text
decision on Monday
feature snapshot from Monday
```

This requirement is mandatory for simulation validity.

---

# 57. Decision reason codes

Every allocation must include structured reason codes.

Examples:

```text
STRONG_POSTERIOR
HIGH_UNCERTAINTY
MARKET_SIGNAL_STRONG
ADJACENT_TO_WINNER
RADICAL_EXPLORATION
BASELINE_CONTROL
LOW_FATIGUE
RECOVERING_MARKET_SIGNAL
DIVERSITY_CONSTRAINT
```

Reason codes are for observability, not for generating prose only.

---

# 58. Constraint resolution

Sometimes the top posterior arms violate portfolio constraints.

The policy must resolve constraints deterministically.

Recommended process:

```text
1. Rank by sampled reward.
2. Walk ranked list.
3. Add an arm if constraints remain satisfied.
4. Otherwise record rejection reason and continue.
5. Continue until slots are filled.
```

This is intentionally simple and explainable.

If a smarter optimizer is added later, it must produce equivalent auditability.

---

# 59. What happens when too few eligible arms exist

Do not fabricate diversity.

If fewer arms are available than required slots:

```text
fill available slots
mark cycle PARTIAL
emit warning
reduce output quantity
```

Do not select blocked/retired arms just to fill a quota.

---

# 60. What happens when all arms are low confidence

P2 should increase exploration rather than pretending certainty.

The cycle may output:

```text
high_uncertainty_market
```

and allocate a broader experimental portfolio.

---

# 61. What happens when one arm is overwhelmingly strong

Do not necessarily allocate 100% to it.

Enforce:

```text
max_exact_arm_share
cluster caps
baseline share
minimum exploration
```

This provides a controlled answer to winner-take-all overreaction.

---

# 62. What happens when market evidence and first-party evidence disagree

Example:

```text
P1 market score = 90
P2 empirical reward = poor
```

or:

```text
P1 score = 45
P2 empirical reward = excellent
```

Do not manually force one to win.

The model should keep both signals visible.

Use explicit fields:

```text
market_evidence
first_party_evidence
combined_posterior
```

P2's job is precisely to resolve this uncertainty over repeated experimentation.

---

# 63. First-party evidence eventually outranks generic market evidence, but not instantly

As the channel accumulates data, the empirical posterior should naturally gain weight.

Do not hard-code a rule such as:

```text
10 videos = ignore P1
```

Use statistical weighting based on observation count, variance, recency and context similarity.

---

# 64. Transfer learning between adjacent arms

A new arm can inherit information from similar arms.

Example:

```text
R&B night drive
```

has 30 observations.

New:

```text
R&B rainy commute
```

can receive a weak prior contribution from the first arm.

The transfer weight should be bounded.

A new arm must never start with the same certainty as the proven arm.

---

# 65. Transfer-learning safety

Never transfer between arms solely because their free-form descriptions sound similar.

At minimum require structural similarity across dimensions such as:

```text
genre
mood
use_case
language
vocal_mode
energy
```

Then optionally incorporate semantic similarity.

---

# 66. Statistical implementation contract

Create an internal interface similar to:

```python
class RewardModel(Protocol):
    def initialize_prior(self, arm: ContentArm, market_evidence: MarketEvidence) -> Posterior:
        ...

    def update(self, posterior: Posterior, observation: RewardObservation) -> Posterior:
        ...

    def sample(self, posterior: Posterior, rng: RandomSource) -> float:
        ...

    def expected_value(self, posterior: Posterior) -> float:
        ...
```

The policy should depend on the interface, not one statistical implementation.

---

# 67. Randomness requirements

Thompson sampling requires randomness.

For production:

```text
use cryptographically secure or high-quality seeded RNG where appropriate
```

For tests/simulation:

```text
always permit explicit seed injection
```

Store the RNG seed or decision-randomness metadata for simulations and debugging where practical.

A production decision does not need to store every sampled floating-point draw if the decision itself and policy version are preserved, but simulation results must be reproducible.

---

# 68. Simulation framework

P2 must include an offline simulation environment before production allocation is enabled.

The simulator should accept:

```text
arm set
true underlying reward distribution
prior configuration
policy configuration
number of cycles
random seed
```

It should output:

```text
cumulative reward
regret proxy
arm selection counts
exploration ratio
winner concentration
posterior calibration
```

---

# 69. Synthetic benchmark scenarios

At minimum simulate:

### Scenario A — one clear winner

One arm is materially better than all others.

Expected:

```text
system discovers winner
system increases exploitation
system retains some exploration
```

### Scenario B — two close winners

Expected:

```text
system does not arbitrarily collapse too early
```

### Scenario C — deceptive early winner

An arm performs extremely well initially but has a lower long-term mean.

Expected:

```text
system eventually recognizes uncertainty/reversion
```

### Scenario D — regime change

The previous winner becomes weak and another arm becomes strong.

Expected:

```text
system adapts
```

### Scenario E — sparse observations

Most arms have very little data.

Expected:

```text
system explores broadly
```

### Scenario F — high correlation

Five arms are essentially variations of the same idea.

Expected:

```text
cluster constraints prevent redundant over-allocation
```

### Scenario G — source shock

P1 market signals suddenly degrade.

Expected:

```text
confidence drops
exploration increases
```

---

# 70. Simulation is not production backtesting

A synthetic simulator only validates policy mechanics.

It must not be used to claim expected real-world revenue.

Historical replay can be added later, but only if historical feature snapshots and result windows avoid leakage.

---

# 71. API contract

P2 must expose decision functionality through a service boundary.

### `POST /ar/cycles`

Creates a decision cycle.

Request:

```json
{
  "channel_id": "...",
  "slot_count": 10,
  "budget": 100000,
  "as_of": "...",
  "policy_version": "p2-v1"
}
```

### `GET /ar/cycles/{cycle_id}`

Returns cycle status and summary.

### `GET /ar/cycles/{cycle_id}/decisions`

Returns selected and rejected arms with reason codes.

### `GET /ar/directives`

Returns queued production directives.

### `GET /ar/arms/{arm_id}/state`

Returns current state and posterior summary.

### `POST /ar/results`

Ingests provider-neutral reward observations.

### `POST /ar/update`

Applies pending reward observations to arm posteriors.

The exact endpoint naming may adapt to an existing backend convention, but these logical capabilities must exist.

---

# 72. Result ingestion contract

Example:

```json
{
  "experiment_id": "...",
  "arm_id": "...",
  "content_id": "...",
  "observation_window": "7d",
  "status": "mature",
  "observed_at": "2026-10-01T00:00:00Z",
  "raw_metrics": {
    "views": 42000,
    "watch_time_minutes": 185000,
    "average_percentage_viewed": 41.2,
    "subscriber_gain": 613,
    "estimated_revenue": 184000
  },
  "normalized_reward": 0.74,
  "reward_version": "reward-v1",
  "attribution_confidence": 0.99
}
```

P2 must validate:

```text
arm exists
experiment exists
content attribution consistent
window valid
reward in allowed range
metrics internally consistent where possible
```

---

# 73. Idempotency

Result ingestion must be idempotent.

A repeated API call for the same observation must not double-update the posterior.

Use a unique identity such as:

```text
experiment_id + observation_window
```

or an upstream provider observation ID.

---

# 74. Posterior update idempotency

Even if a reward observation is stored once, the update worker may retry.

Persist update state:

```text
reward_observation_id
posterior_version_before
posterior_version_after
update_status
updated_at
```

A retry must not apply the same observation twice.

---

# 75. Concurrency control

Only one authoritative posterior update operation should modify a given arm at a time.

Use database locking, optimistic version checks, or another equivalent mechanism.

Do not allow concurrent workers to silently overwrite posterior state.

---

# 76. Policy versioning

Every decision must record:

```text
policy_version
reward_version
feature_version
normalization_version
P1_score_version
model_version
```

This is required for later analysis of why the AI made a decision.

---

# 77. Decision reproducibility

Given:

```text
same eligible arms
same feature snapshots
same posterior state
same policy version
same random seed
same constraints
```

P2 should generate the same decision portfolio.

Production stochastic decisions can vary because the RNG is intentionally stochastic, but simulation/tests must support deterministic replay.

---

# 78. LLM role in P2

The LLM may be used for:

- summarizing why a market arm may be interesting;
- proposing human-readable experiment briefs;
- identifying semantic relationships between market concepts;
- producing diagnostic narratives.

The LLM must not be the authoritative allocator.

Do not implement:

```text
ask Claude "which genre should we make?"
→ publish Claude's first answer
```

The authoritative allocation must be produced by deterministic policy/statistical code.

---

# 79. Local-model role

Cheap local models can perform:

```text
classification
feature extraction
taxonomy matching
semantic similarity
batch diagnostics
```

The policy decision itself should remain in the core P2 service so it is:

```text
versioned
repeatable
testable
auditable
```

---

# 80. Budget constraints

P2 must support a production budget in addition to slot count.

Each directive may have an estimated cost supplied by P3/P4 capabilities.

If a cost estimate is unavailable, use the configured conservative default and mark the assumption.

Do not call an expensive provider in P2 simply to determine cost.

P2 is a planner.

---

# 81. Capacity constraints

Example:

```text
weekly slots = 10
music-generation capacity = 100 candidate tracks
video-render capacity = 20 videos
```

P2 must consider only the capacities explicitly supplied to it.

Do not hard-code machine-specific hardware assumptions.

---

# 82. Provider availability

If a provider becomes unavailable:

```text
mark affected production capability unavailable
exclude directives requiring that capability
reallocate if possible
otherwise produce partial cycle
```

Do not silently replace one music model with another if their licenses or output properties differ.

That substitution belongs to P4/P5.

---

# 83. Opportunity freshness gate

P2 should reject or downweight P1 opportunities that are too stale for the active strategy.

Use:

```text
max_market_data_age
```

as a policy setting.

When stale:

```text
reduce confidence
request P1 refresh if possible
```

Do not silently use expired market data as if current.

---

# 84. P1 score changes

When P1 materially changes an arm's market score:

P2 should not immediately overwrite first-party evidence.

Instead:

```text
retain current empirical posterior
update market prior/context features
allow future decisions to incorporate the new evidence
```

This preserves the distinction between external market evidence and channel-specific evidence.

---

# 85. Market shock handling

If many arms change simultaneously, suspect a market-data regime change rather than independent arm improvement.

P2 should log:

```text
MARKET_REGIME_CHANGE_SUSPECTED
```

and may temporarily increase exploration.

---

# 86. Auditability requirements

For every selected arm, a human-readable decision explanation should be reconstructable from structured data.

Example:

```text
Selected:
R&B × Late Night Drive

Allocation:
EXPLOIT

Why:
- strong posterior reward
- recent market signal remains strong
- low recent fatigue
- within genre concentration cap
- sufficient mature observations

Uncertainty:
0.21

Policy:
p2-v1
```

The prose is generated from structured reason codes and metrics.

It should not become the source of truth.

---

# 87. Metrics / observability

Expose at minimum:

```text
allocation_cycles_total
allocation_cycle_failures
eligible_arm_count
selected_arm_count
selection_entropy
exploration_fraction
exploitation_fraction
baseline_fraction
arm_retirement_count
arm_reactivation_count
posterior_updates_total
posterior_update_failures
reward_observation_lag
constraint_rejections
stale_arm_count
```

Also emit structured decision logs.

---

# 88. Alerts

Raise alerts for:

```text
zero eligible arms
posterior update failures
large schema mismatch from P1
all selected arms from one cluster
exploration fraction below configured minimum
exploration fraction above configured maximum
mass retirement event
mass posterior shift
stale P1 data
negative/invalid reward values
idempotency conflicts
```

---

# 89. Recommended repository structure

Adapt to the existing project structure if one already exists.

Suggested logical modules:

```text
src/
  autonomous_ar/
    domain/
      arm.py
      posterior.py
      reward.py
      decision.py
      directive.py
      policy.py
    ingestion/
      p1_adapter.py
      result_adapter.py
    modeling/
      prior.py
      reward_model.py
      contextual_model.py
      similarity.py
      decay.py
      fatigue.py
    policy/
      allocation.py
      constraints.py
      exploration.py
      lifecycle.py
    decisioning/
      cycle.py
      planner.py
      portfolio.py
    storage/
      repositories.py
      models.py
    api/
      routes.py
      schemas.py
    simulation/
      simulator.py
      scenarios.py
      metrics.py
    observability/
      logging.py
      metrics.py

 tests/
  unit/
  property/
  integration/
  simulation/
  contract/
```

Do not create unnecessary microservices for P2 at this stage.

A modular service is preferable to a distributed system until load requires separation.

---

# 90. Configuration model

All policy values belong in versioned configuration.

Suggested configuration:

```yaml
policy:
  exploit_fraction: 0.70
  adjacent_explore_fraction: 0.20
  radical_explore_fraction: 0.10
  baseline_fraction: 0.10
  max_exact_arm_share: 0.50
  max_cluster_share: 0.60
  max_genre_share: 0.60
  max_use_case_share: 0.60
  max_language_share: 0.85
  max_market_data_age_hours: 168
  fatigue:
    enabled: true
    decay_half_life_days: 14
  nonstationarity:
    enabled: true
    decay_half_life_days: 45
  retirement:
    min_mature_observations: 5
    weak_posterior_threshold: 0.25
```

Exact values are startup defaults, not guaranteed optimal policy settings.

---

# 91. Configuration validation

On startup or policy activation:

```text
all fractions >= 0
all fractions <= 1
sum constraints valid
thresholds logically ordered
half-lives > 0
slot counts > 0
```

Reject invalid configurations before running a decision cycle.

---

# 92. Policy migration

Policy versions must be immutable once used in a production decision.

To change:

```text
create new policy version
validate
activate
```

Do not mutate old policies in place.

---

# 93. Data migration expectations

If the reward-model implementation changes:

- retain old posterior states;
- create a new model version;
- provide a migration/re-initialization strategy;
- never silently compare scores generated by incompatible model versions.

---

# 94. Logging format

Each allocation cycle should emit structured JSON logs with:

```text
cycle_id
channel_id
policy_version
p1_snapshot_time
arm_count
selected_ids
allocation_type_by_arm
constraints_triggered
warnings
runtime_ms
```

Avoid logging full provider secrets or large raw payloads.

---

# 95. Unit tests

At minimum test:

### Prior initialization

```text
high P1 score + high confidence
high P1 score + low confidence
low P1 score + high confidence
missing secondary source
stale P1 evidence
```

### Posterior update

```text
positive reward
negative reward
multiple observations
recency decay
zero variance edge case
```

### Sampling

```text
fixed seed
identical seed → identical result
higher posterior → higher expected selection tendency over many trials
```

### Similarity

```text
identical arms = 1.0
near-neighbor > distant arm
```

### Fatigue

```text
recent exposure increases penalty
penalty decays over time
```

### Constraints

```text
exact-arm cap
cluster cap
genre cap
baseline reservation
insufficient eligible arms
```

---

# 96. Property tests

Important invariants:

```text
selection_count <= configured slot_count

selected arms satisfy all hard constraints

blocked/retired arms are never selected

allocation fractions sum to approximately 1 after normalization

posterior variance >= 0

confidence values remain in [0,1]

sampling does not mutate posterior state

reward update is monotonic in the direction defined by the reward model
```

---

# 97. Integration tests

Create a deterministic test environment with:

```text
20 P1 arms
3 genres
4 use cases
3 languages
5 similarity clusters
multiple confidence levels
10 historical experiments
2 arms with mature results
3 arms with provisional results
1 blocked arm
1 retired arm
1 stale opportunity
```

Run:

```text
P1 snapshot
→ P2 decision cycle
→ directives
→ synthetic reward observations
→ posterior update
→ second decision cycle
```

Verify that the policy changes after observed results.

---

# 98. Policy simulation acceptance tests

### Test 1 — clear winner

After sufficient simulated observations, the strongest arm should receive materially more exploitation allocation while exploration remains nonzero.

Do not require a specific exact percentage unless the policy config defines it.

### Test 2 — deceptive winner

A high-variance arm with a lucky first result should not permanently dominate.

### Test 3 — regime change

When the simulated best arm changes, the allocation should shift in the new direction within a reasonable number of cycles.

### Test 4 — exploration floor

Radical/adjacent exploration must remain above their configured minimum when eligible arms exist.

### Test 5 — diversity

No selected portfolio may violate configured caps.

---

# 99. Offline benchmark metrics

Measure at least:

```text
cumulative normalized reward
selection concentration
arm discovery rate
average time to discover top arm
regret proxy
exploration coverage
posterior calibration
constraint violation count
```

The system should compare policy versions on the same random seeds/scenarios.

---

# 100. Initial benchmark expectations

Do not set arbitrary numeric performance claims such as:

```text
"P2 must discover the winner within 20 videos"
```

without empirical simulation.

Instead define acceptance as relative behavior:

```text
better policy should show lower regret proxy
or
higher discovery speed
without unacceptable concentration
```

---

# 101. Operational workflow

Recommended daily/weekly flow:

```text
P1 refresh
   ↓
P2 snapshot
   ↓
eligible-arm filtering
   ↓
posterior load
   ↓
policy allocation
   ↓
production directives
   ↓
P3 execution
   ↓
P7/P8 publication
   ↓
P9 metrics
   ↓
P2 result ingestion
   ↓
posterior update
```

P2 should be runnable independently with mocked downstream systems.

---

# 102. Failure modes

## P1 unavailable

Use the most recent valid P1 snapshot within policy freshness limits.

If none exists:

```text
do not invent market opportunities
produce a degraded decision cycle or stop
```

## Result pipeline unavailable

Do not update posteriors.

Existing posterior state remains valid.

## Database unavailable

Fail the cycle; do not make an unlogged autonomous decision.

## Policy invalid

Refuse activation.

## Model state corrupt

Restore last valid posterior snapshot or rebuild from immutable observations if supported.

---

# 103. Disaster recovery

Because P2's decisions affect future production, posterior state should be reconstructable.

Required recovery path:

```text
immutable reward observations
+
policy/model versions
+
P1 feature snapshots
→
rebuild posterior
→
resume allocation
```

Do not make the system dependent on a single mutable posterior table with no rebuild path.

---

# 104. Backup strategy

Back up:

```text
P1 arm snapshots
P2 decisions
reward observations
policy versions
posterior snapshots
configuration
```

The reward observations are especially important because they are the underlying evidence needed to reconstruct the model.

---

# 105. Security

P2 should not store platform credentials.

It only receives references/availability state from P0/P4/P7/P8 as required.

Protect:

```text
channel identifiers
financial settings
budget configuration
internal experiment history
provider error metadata
```

Do not send unnecessary internal financial information to external LLMs.

---

# 106. Human intervention boundary

Normal P2 cycles require no human choice.

Human intervention should occur only for exceptions such as:

```text
policy configuration change
budget limit change
blocked rights state
unknown provider capability
corrupt data
major strategy reset
```

Changing the selected genre for a single cycle should not be the normal control path.

---

# 107. P2 should support a manual override — but make it explicit

A human may override an arm or policy when necessary.

However, the override must create an explicit event:

```text
MANUAL_OVERRIDE
```

with:

```text
who
when
what changed
why
expiry
```

A manual override must not silently rewrite the statistical model.

---

# 108. Override expiry

Temporary overrides should support expiration.

Example:

```text
force_more_jazz
expires = 7 days
```

After expiration, P2 returns to autonomous behavior.

Do not create indefinite hidden overrides.

---

# 109. Decision explanation generator

Provide a deterministic explanation layer that converts structured decision data into text.

Template components:

```text
allocation type
posterior reward
uncertainty
market score
recent performance
fatigue
constraints
```

An LLM may improve readability, but the explanation must be reconstructable without it.

---

# 110. Example decision

```text
Cycle: 2026-W42
Slots: 10

Selected:

1. R&B × Late Night Drive
   Type: exploit
   Posterior reward: 0.76
   Uncertainty: 0.18
   Market score: 82

2. Rainy City Pop × Instrumental
   Type: exploit
   Posterior reward: 0.71
   Uncertainty: 0.24
   Market score: 78

3. R&B × Rainy Commute
   Type: adjacent_explore
   Posterior reward: 0.67
   Uncertainty: 0.39
   Similar to: R&B × Late Night Drive

4. Luxury Deep House × Hotel Lounge
   Type: radical_explore
   Posterior reward: 0.42
   Uncertainty: 0.51

...
```

This is an example of output structure, not expected actual results.

---

# 111. What P2 must not do

Do not:

```text
choose a winner using an LLM opinion

use raw P1 score as the only allocation signal

use lifetime views as the only reward

update a posterior twice for the same observation

fill every slot with one arm

retire an arm after one poor result

ignore uncertainty

ignore data freshness

mix historical feature snapshots with future information

let blocked content through to production

silently change policy parameters

hard-code provider-specific logic into the decision policy
```

---

# 112. What the implementation AI may decide

The vibe-coding/implementation AI may choose:

- exact ORM/query strategy;
- exact statistics library;
- whether to use NumPy/SciPy/PyMC or a lightweight custom implementation;
- internal class/module decomposition;
- exact async implementation;
- cache implementation;
- API framework patterns if FastAPI conventions already exist;
- exact logging package;
- exact database indexes;
- exact simulation utilities.

Provided that external behavior and invariants in this document are preserved.

---

# 113. What the implementation AI must not silently change

It must not silently change:

- P1/P2 separation;
- arm definition;
- market score vs. first-party posterior separation;
- exploration/exploitation concept;
- uncertainty-aware allocation;
- portfolio diversity constraints;
- result idempotency;
- historical feature snapshots;
- posterior versioning;
- decision provenance;
- hard blocked/retired exclusion;
- simulation/replay requirement;
- provider abstraction;
- P3 interface boundary.

If a different architecture is materially better, record it as a decision before implementing it and preserve compatibility with the downstream contract.

---

# 114. Implementation sequence

## P2.1 — Domain contracts

Define:

```text
ContentArm
MarketEvidence
Posterior
RewardObservation
PolicyConfig
AllocationCycle
AllocationDecision
ProductionDirective
```

Add schema validation and enums.

## P2.2 — P1 adapter

Implement mapping from P1's market opportunity API/data into P2 domain objects.

Do not query P1 database tables directly from policy code.

## P2.3 — Persistence

Implement migrations/repositories for the P2 tables.

## P2.4 — Similarity

Implement structured arm similarity and clustering.

## P2.5 — Prior model

Implement P1 → statistical prior transformation.

## P2.6 — Reward model

Implement posterior initialization and update.

## P2.7 — Exploration policy

Implement Thompson sampling or equivalent uncertainty-aware sampling.

## P2.8 — Constraints

Implement exact-arm, cluster, genre, use-case, language, baseline and capacity constraints.

## P2.9 — Fatigue/nonstationarity

Implement exposure decay and observation recency weighting.

## P2.10 — Allocation cycle

Implement end-to-end selection and production-directive generation.

## P2.11 — Result ingestion

Implement idempotent result storage and posterior updates.

## P2.12 — Simulation

Implement synthetic scenarios and policy comparison.

## P2.13 — API

Expose P2 service endpoints.

## P2.14 — Observability

Add structured logs, metrics and alerts.

## P2.15 — Acceptance suite

Run unit/property/integration/simulation tests.

## P2.16 — Controlled production smoke test

Run one small allocation cycle against real P1 data but with downstream production disabled/dry-run.

---

# 115. Dry-run mode

P2 must have a `dry_run` mode.

In dry-run:

```text
read real P1 data
calculate real decisions
persist decision records
DO NOT create actionable production jobs
```

This is mandatory before enabling autonomous production.

---

# 116. Shadow mode

After dry-run, support a shadow mode where P2 makes decisions while an existing/manual strategy continues operating.

Store:

```text
P2 decision
actual/manual decision
```

Compare them without allowing P2 to control production.

This gives an empirical way to evaluate the policy before granting control.

---

# 117. Production activation gate

P2 may become authoritative only when:

```text
unit tests pass
property tests pass
integration tests pass
simulation suite passes
dry-run passes
shadow mode produces valid decisions
no critical data-integrity bugs
policy version approved
hard constraints verified
```

The implementation should expose a state such as:

```text
DEVELOPMENT
DRY_RUN
SHADOW
AUTONOMOUS
SUSPENDED
```

---

# 118. Acceptance criteria

P2 is complete only when all of the following are true.

## AC-01 — P1 integration

P2 can ingest ranked P1 opportunity arms with score, confidence, taxonomy, freshness and evidence references.

## AC-02 — Cold start

A new arm receives a prior without fabricated performance observations.

## AC-03 — Learning

Observed reward updates the arm posterior exactly once.

## AC-04 — Exploration

P2 allocates explicit exploration capacity when eligible arms exist.

## AC-05 — Exploitation

After repeated positive results, a strong arm receives increased exploitation probability without violating caps.

## AC-06 — Uncertainty

High uncertainty affects sampling and selection; P2 does not treat point estimates as certainty.

## AC-07 — Diversity

Selected portfolios honor all hard diversity constraints.

## AC-08 — Fatigue

Repeated recent exposure reduces selection pressure temporarily and the effect decays over time.

## AC-09 — Non-stationarity

Recent evidence can change allocation without deleting historical data.

## AC-10 — Attribution

An outcome without reliable arm attribution cannot update the wrong arm.

## AC-11 — Reproducibility

A fixed seed and identical inputs reproduce simulation decisions.

## AC-12 — Auditability

Every decision records policy/model/feature versions and reason codes.

## AC-13 — Dry run

Real P1 opportunities can produce a valid dry-run decision cycle with downstream production disabled.

## AC-14 — API

P3 can retrieve production directives through a stable service interface.

## AC-15 — Failure handling

P1 unavailability, result-update failures, stale data and invalid policy configuration produce explicit degraded/failure states rather than silent behavior.

---

# 119. Definition of done

P2 is done when the system can autonomously take a current P1 market snapshot plus historical experiment evidence and produce an auditable, uncertainty-aware portfolio of production directives that balances exploitation and exploration, obeys configurable capacity/diversity constraints, and can update its beliefs from later results without double-counting or leaking future information.

The most important behavioral property is:

> **The system must learn which content concepts deserve more or less production capacity from evidence, while preserving enough uncertainty and exploration to continue discovering new opportunities.**

---

# 120. P2 output expected by P3

P3 should not need to know how Thompson Sampling, market scoring, fatigue or posterior updates work.

It should receive something conceptually like:

```text
ProductionDirective[]
```

where each directive contains:

```text
arm_id
allocation_type
priority
quantity
creative search region
expected value
uncertainty
decision_id
policy version
expiration
```

This keeps the architecture clean:

```text
P1
market understanding

P2
decision / allocation

P3
experiment and content specification

P4+
execution
```

---

# 121. Final architectural warning

Do not over-engineer P2 into a giant AI "CEO".

The first version should be a small, reliable decision engine with:

```text
strong data contracts
simple Bayesian logic
clear uncertainty
controlled exploration
strict attribution
versioned policy
excellent simulation
```

A complicated neural recommender trained on a tiny, noisy dataset will create the appearance of intelligence without the statistical foundation needed to trust it.

The goal of P2 is to create a **scientifically testable autonomous A&R loop**, not to create an impressive-looking AI agent.
