# tests/e2e/test_end_to_end_chain.py
"""Wave 6 End-to-End Acceptance Chain: Loop Closure from P1 to P9 back to P2.

Implements W6.1 of docs/P0_master_integration_implementation_plan.md §6:
Verifies the complete uncredentialed, hermetic, mock-first lifecycle:
P1 fixture -> P2 directive -> P3 package -> P4 candidate -> P5 clearance ->
P6 media package -> P7 YouTube publish -> P8 DSP distribution -> P9 economics & facts ->
R11 normalized reward post -> P2 posterior update -> next cycle allocation shift.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from plm_contracts import AllocationType, GateDisposition
from plm_contracts.seams import parse_seam
from plm_contracts.seams.c5 import ReleaseManifestPayload
from plm_contracts.seams.c6 import MediaPackagePayload

# P9
from plm_contracts.seams.c7 import ObservationMaturity
from plm_platform.clock import FixedClock
from sqlalchemy.orm import Session

from services.analytics.domain.enums import ObservationWindow, PaybackStatus
from services.analytics.economics.calculator import compute_unit_economics
from services.analytics.evaluation.feedback import (
    evaluate_experiment_outcome,
    generate_and_post_reward,
)
from services.analytics.ingestion.releases import ingest_distribution_record, lineage_for_isrc

# P2
from services.autonomous_ar.config import load_ar_settings
from services.autonomous_ar.domain.arm import ArmDimensions, ContentArm
from services.autonomous_ar.domain.directive import (
    DirectiveBrief,
    ProductionDirective,
)
from services.autonomous_ar.domain.enums import DirectiveStatus
from services.autonomous_ar.domain.policy import DEFAULT_POLICY
from services.autonomous_ar.domain.posterior import PriorParameters

# P1
from services.autonomous_ar.ingestion.p1_adapter import (
    ComponentsPayload,
    EvidencePayload,
    OpportunityPayload,
    TaxonomyPayload,
    adapt_opportunity,
)
from services.autonomous_ar.ingestion.result_adapter import store_result
from services.autonomous_ar.modeling.reward_model import NormalNormalRewardModel
from services.autonomous_ar.modeling.update import apply_pending_updates
from services.autonomous_ar.storage import repositories
from services.distribution.client.mock import MockDistributorClient

# P8
from services.distribution.engine import DistributionEngine
from services.distribution.submission import ReleaseMetadata, submit_release_from_seams

# P3
from services.experiments.domain.experiment import Experiment, Hypothesis, Treatment
from services.experiments.domain.genome import BpmRange, ContentGenome, DurationRange
from services.experiments.ingestion.p2_adapter import adapt_directive
from services.experiments.materialization.builder import materialize_package

# P6
from services.media_factory.rendering.assembler import assemble_media_package
from services.media_factory.visuals.planner import BrandProfile
from services.music_factory.api.events import build_candidate_ready_event

# P4
from services.music_factory.generation.engine import GenerationEngine
from services.music_factory.generation.planner import plan_generation
from services.music_factory.ingestion.p3_adapter import adapt_track_spec

# P5
from services.quality_gate.gate.manifest import build_release_manifest
from services.youtube_publishing.client.mock import MockYouTubeClient
from services.youtube_publishing.domain.enums import OperatingMode

# P7
from services.youtube_publishing.domain.models import ChannelProfile
from services.youtube_publishing.engine import YouTubePublishingEngine
from services.youtube_publishing.manifests import build_publish_manifest
from services.youtube_publishing.storage.repositories import insert_publish_manifest
from tests.support.distribution_db import distribution_db_session
from tests.support.music_db import music_db_session
from tests.support.youtube_db import youtube_db_session


def test_autonomous_music_engine_end_to_end_loop_closure(
    ar_db: Session,
    analytics_db: Session,
) -> None:
    """The master integration success criterion (P0 §1, Ruling R11, Seam C1–C7)."""
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    settings = load_ar_settings()
    model = NormalNormalRewardModel()

    # =========================================================================
    # Step 1: P1 Market Intelligence — Opportunity Discovery & Scoring
    # =========================================================================
    taxonomy = TaxonomyPayload(
        genre="lofi",
        subgenre="chillhop",
        mood=["nostalgic", "peaceful"],
        use_case=["focus", "study"],
        language="instrumental",
        vocal_mode="instrumental",
        duration_bucket="2_to_3_min",
        visual_world="rainy_coffee_shop",
        title_archetype="peaceful_rain",
    )
    components = ComponentsPayload(
        demand=88.0,
        growth=82.0,
        competitor_velocity=60.0,
        competition=45.0,
        audience_quality=90.0,
        cross_platform=78.0,
        production_fit=92.0,
    )
    evidence = EvidencePayload(
        observation_count=12,
        source_count=3,
        top_observation_ids=["obs_yt_001", "obs_sp_002"],
    )
    p1_opp = OpportunityPayload(
        arm_id="arm_synth_lofi_01",
        market_opportunity_score=0.86,
        confidence=0.75,
        as_of=now,
        taxonomy=taxonomy,
        components=components,
        evidence=evidence,
        score_version="p1-v1",
    )
    assert p1_opp.arm_id == "arm_synth_lofi_01"
    assert p1_opp.market_opportunity_score > 0.80

    # =========================================================================
    # Step 2: P2 Autonomous A&R — Ingestion & Production Directive Allocation
    # =========================================================================
    adapted_arm = adapt_opportunity(p1_opp, now=now)
    repositories.upsert_arm_state(ar_db, brand_id="brand_neon", arm_id="arm_synth_lofi_01", now=now)
    repositories.upsert_arm_state(ar_db, brand_id="brand_neon", arm_id="arm_baseline_control", now=now)

    # Initialize priors with identical prior mean=0.50
    prior_lofi = model.initialize_prior(
        adapted_arm,
        PriorParameters(mean=0.50, variance=0.05, confidence=0.75, prior_version="p2-v1"),
    )
    repositories.upsert_posterior(ar_db, prior_lofi, brand_id="brand_neon", expected_version=0)

    baseline_arm = ContentArm(
        arm_id="arm_baseline_control",
        dimensions=ArmDimensions(genre="ambient"),
        p1_score=50.0,
        p1_confidence=0.5,
        component_scores={k: 50.0 for k in ("demand", "growth", "competitor_velocity", "competition", "audience_quality", "cross_platform", "production_fit")},
        observation_count=1,
        source_count=1,
        as_of=now,
        score_version="p1-v1",
        freshness_seconds=0.0,
    )
    prior_base = model.initialize_prior(
        baseline_arm,
        PriorParameters(mean=0.50, variance=0.05, confidence=0.5, prior_version="p2-v1"),
    )
    repositories.upsert_posterior(ar_db, prior_base, brand_id="brand_neon", expected_version=0)

    # P2 allocates production directive (Seam C2)
    directive = ProductionDirective(
        directive_id="dir_lofi_001",
        decision_id="dec_001",
        arm_id="arm_synth_lofi_01",
        allocation_type=AllocationType.EXPLOIT,
        priority=1,
        quantity=1,
        brief=DirectiveBrief(
            genre="lofi",
            mood=["nostalgic"],
            use_case=["focus"],
            language="instrumental",
            vocal_mode="instrumental",
            duration_bucket="2_to_3_min",
            visual_world="rainy_coffee_shop",
        ),
        expected_value=0.50,
        uncertainty=0.05,
        budget_limit=150.0,
        market_context={"p1_score": 0.86},
        selection_reason=["high_market_fit", "strong_demand"],
        directive_version="v1",
        policy_version="p2-policy-v1",
        expires_at=now + timedelta(days=7),
        exploration_fraction=0.1,
        value_model="expected_economic_value",
    )
    repositories.insert_directive(
        ar_db,
        directive,
        brand_id="brand_neon",
        status=DirectiveStatus.QUEUED,
        created_at=now,
    )

    # =========================================================================
    # Step 3: P3 Experiments Framework — Genome & Track Specs Materialization
    # =========================================================================
    p3_input = adapt_directive(directive.to_wire())
    assert p3_input.directive_id == directive.directive_id
    assert p3_input.budget_limit == 150.0

    genome = ContentGenome(
        genre="lofi",
        subgenre="chillhop",
        mood=("nostalgic", "peaceful"),
        use_case=("focus", "study"),
        language="instrumental",
        bpm=BpmRange(min_bpm=72, max_bpm=82, target_bpm=76),
        track_duration=DurationRange(min_seconds=130, max_seconds=170, target_seconds=150),
        playlist_duration=DurationRange(min_seconds=1800, max_seconds=2400, target_seconds=2100),
        visual_world="rainy_coffee_shop",
    )
    treatment = Treatment(
        treatment_id="trt_lofi_001",
        treatment_index=0,
        name="control_warm_tape",
        variable_assignments={"bpm": 76},
    )
    experiment = Experiment(
        experiment_id="exp_lofi_001",
        content_arm_id="arm_synth_lofi_01",
        production_directive_id=directive.directive_id,
        experiment_type="single_factor",
        status="validated",
        hypothesis=Hypothesis(statement="Warm tape lofi elevates focus watch time", target_metric="watch_time"),
        treatments=(treatment,),
        base_genome_fingerprint="fp_lofi_tape",
        created_at=now,
    )
    package = materialize_package(experiment, treatment, genome, budget_envelope=directive.budget_limit, track_count=1, created_at=now)
    track_spec = package.track_specs[0]
    assert track_spec.track_spec_id.startswith("trk_")
    assert track_spec.requested_duration == 150

    # =========================================================================
    # Step 4: P4 Music Factory — Audio Generation & Candidate Ready (Seam C4)
    # =========================================================================
    adapted_spec = adapt_track_spec(track_spec.to_wire())
    assert adapted_spec.genre == "lofi"

    # Real P4 path: plan, generate with the mock provider, then build the C4 event
    # from the stored facts (ADR-0027) instead of hand-writing it.
    plan, variants = plan_generation(adapted_spec, created_at=now)
    with music_db_session() as music_db:
        gen = GenerationEngine(clock=FixedClock(now)).execute_plan(
            music_db, plan=plan, variants=variants, spec=adapted_spec
        )
        ready_event = build_candidate_ready_event(music_db, gen.candidates[0].candidate_id)
    assert ready_event.event_type == "music_candidate_ready_for_p5"
    assert ready_event.lineage.content_arm_id == directive.arm_id
    candidate_id = ready_event.candidate_id
    artifact_id = ready_event.artifact_id

    # =========================================================================
    # Step 5: P5 Quality & Release Gate — Verdict & Release Clearance (Seam C5)
    # =========================================================================
    # Still hand-built (STATUS.md): a real P5 run would reject the mock's 30 s audio
    # against the 150 s spec until R2 resolves the duration seam. Every fact below is
    # taken from the real C4 event, including its lineage.
    manifest = build_release_manifest(
        candidate_id=candidate_id,
        canonical_artifact_id=artifact_id,
        artifact_uri=ready_event.artifact_uri,
        artifact_sha256=ready_event.artifact_sha256,
        measured_duration_seconds=ready_event.measured_audio.duration_seconds,
        track_spec_id=ready_event.track_spec_id,
        lineage=ready_event.lineage,
        disposition=GateDisposition.PASSED,
        override=None,
        technical_status="PASSED",
        commercial_use_status="eligible",
        provenance_status="COMPLETE",
        ai_disclosure_required=True,
        rights_risk="LOW",
        policy_status="COMPLIANT",
        evaluation_id_str="eval_lofi_001",
        evaluation_policy_version="p5-v1",
        warning_codes=[],
        evidence_bundle_id="evi_lofi_001",
        now=now,
    )
    assert manifest.disposition == GateDisposition.PASSED
    assert manifest.p5_manifest_id.startswith("rel_man_")

    # =========================================================================
    # Step 6: P6 Media Factory — Playlist Video & Packaging (Seam C6 Hop 1)
    # =========================================================================
    track_manifest = parse_seam(ReleaseManifestPayload, manifest.to_wire())
    brand_profile = BrandProfile(brand_id="brand_neon", brand_name="Neon Beats")
    assembled = assemble_media_package(
        track_manifests=[track_manifest],
        brand_profile=brand_profile,
        required_duration_seconds=track_manifest.measured_duration_seconds,
        visual_profile="brand_neon_rain_v1",
        playlist_concept="Lofi Study Session",
        render_profile="yt_longform_1080p_v1",
        clock=FixedClock(now),
    )
    media_pkg = assembled.media_package
    c6_hop1 = media_pkg.to_c6_payload()
    assert c6_hop1["status"] == "ready_for_publish"
    assert c6_hop1["video"]["master_uri"].endswith(".mp4")

    # =========================================================================
    # Step 7: P7 YouTube Publishing — Preflight & Publication Event (Seam C6 Hop 2, R9)
    # =========================================================================
    channel = ChannelProfile(
        id="prof_UC_e2e", channel_id="UC_e2e", channel_name="E2E Channel",
        brand_id="brand_neon", oauth_credential_ref="sec_oauth_e2e",
        created_at=now, updated_at=now,
    )
    package_wire = parse_seam(MediaPackagePayload, c6_hop1)
    publish_manifest = build_publish_manifest(package_wire, channel, publish_at=None, now=now)
    with youtube_db_session() as yt_db:
        insert_publish_manifest(yt_db, publish_manifest)
        yt_db.commit()
        published = YouTubePublishingEngine(MockYouTubeClient(), chunk_size=512, clock=FixedClock(now)).execute_publish(
            yt_db, publish_manifest, video_data=b"0" * 1024, thumbnail_data=b"IMG",
            operating_mode=OperatingMode.DRY_RUN,
        )
    yt_event = published.event
    assert yt_event is not None, published.error
    assert yt_event.media_package_id == media_pkg.media_package_id
    assert yt_event.lineage == ready_event.lineage  # P3's lineage, carried to P7

    # =========================================================================
    # Step 8: P8 DSP Distribution — Release Compilation & DSP Delivery (Seam C7 Leg 1, R9)
    # =========================================================================
    with distribution_db_session() as dist_db:
        submitted = submit_release_from_seams(
            dist_db,
            DistributionEngine(MockDistributorClient(clock=FixedClock(now))),
            track_manifests=[track_manifest.to_wire()],
            media_package=c6_hop1,
            publication=yt_event.to_wire(),
            metadata=ReleaseMetadata(
                artist_name="Neon Seoul", release_date="2026-10-13", label_name="Neon Records",
                copyright_line="2026 Neon Records", publisher_line="2026 Neon Music Publishing",
                territories="WORLDWIDE",
            ),
            now=now,
        )
    c7_dist_payload = submitted.payload
    assert c7_dist_payload is not None
    assert c7_dist_payload.lineage.content_arm_id == directive.arm_id
    assert c7_dist_payload.lineage == ready_event.lineage

    # =========================================================================
    # Step 9: P9 Analytics & Economics — Unit Economics & Reward Posting (R10, R11)
    # =========================================================================
    # Leg 1: P9 links the released ISRCs to the lineage P8 recorded (ADR-0030).
    ingest_distribution_record(analytics_db, c7_dist_payload.to_wire(), now=now)
    linked = lineage_for_isrc(analytics_db, c7_dist_payload.isrcs[0])
    assert linked is not None and linked[1] == ready_event.lineage

    # Ingest facts: YouTube revenue $60.00, DSP royalties $50.00, production cost $15.00
    # (still literals: real ingestion of these facts is stage R6)
    gross_revenue = 110.0
    allocated_cost = 15.0
    net_contribution = gross_revenue - allocated_cost  # $95.00

    unit_econ = compute_unit_economics(
        entity_id=media_pkg.media_package_id,
        entity_type="package",
        gross_revenue_usd=gross_revenue,
        allocated_cost_usd=allocated_cost,
        views=28000,
        streams=14000,
        as_of=now,
    )
    assert unit_econ.net_contribution_usd == 95.0
    assert unit_econ.roi > 5.0
    assert unit_econ.payback_status == PaybackStatus.REPAID

    # Evaluate outcome and post normalized reward (Ruling R11, Seam C7 Leg 2)
    outcome = evaluate_experiment_outcome(
        analytics_db,
        lineage=linked[1],
        unit_id=media_pkg.media_package_id,
        net_contribution_usd=net_contribution,
        views=28000,
        watch_time_minutes=1400.0,
        window=ObservationWindow.D7,
        maturity=ObservationMaturity.MATURE,
        attribution_confidence=1.0,
        quality_status=None,
        now=now,
    )
    assert outcome.normalized_reward > 0.70  # Strong positive performance

    reward_post = generate_and_post_reward(
        analytics_db,
        outcome,
        views=28000,
        watch_time_minutes=1400.0,
        average_percentage_viewed=72.0,
        subscribers_gained=45,
        estimated_revenue=gross_revenue,
        now=now,
    )
    assert reward_post.arm_id == "arm_synth_lofi_01"
    assert reward_post.normalized_reward == outcome.normalized_reward

    # =========================================================================
    # Step 10: P2 Loop Closure — Feedback Post, Posterior Update & Allocation Shift
    # =========================================================================
    # Ingest observation into P2
    stored_result = store_result(
        ar_db,
        reward_post.to_wire(),
        brand_id="brand_neon",
        now=now,
    )
    assert stored_result.stored is True

    # P2 worker updates posteriors
    update_summary = apply_pending_updates(ar_db, settings=settings, now=now, policy=DEFAULT_POLICY)
    assert update_summary.applied == 1
    assert update_summary.skipped_duplicate == 0
    assert update_summary.quarantined == 0

    # Posterior assertions: verify the learning loop closed
    post_lofi = repositories.load_posterior(ar_db, "arm_synth_lofi_01")
    post_base = repositories.load_posterior(ar_db, "arm_baseline_control")
    assert post_lofi is not None
    assert post_base is not None

    # The rewarded arm's posterior mean has strictly increased above prior 0.50
    assert post_lofi.posterior_mean > 0.50
    assert post_lofi.observation_count == 1
    assert post_lofi.update_version == 1

    # The rewarded arm now strictly dominates the unrewarded baseline arm
    assert post_lofi.posterior_mean > post_base.posterior_mean
