"""Phase P9: Closed-Loop Execution Runner.

Connects YouTube Analytics API v2 (P8/P9) -> Normalized Reward Computation (P9)
-> Seam C7 Leg 2 Delivery -> Phase P2 Autonomous A&R Posterior Update -> Cycle Allocation.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from plm_contracts import ObservationWindow, QualityStatus
from plm_contracts.money import Money
from plm_contracts.seams.c7 import ObservationMaturity, RewardResultPayload

from services.analytics.evaluation.feedback import (
    REWARD_CONTRIBUTION_SCALE,
    REWARD_VERSION,
    compute_normalized_reward,
)
from services.analytics.ingestion.youtube_client import (
    RealYouTubeAnalyticsClient,
    YouTubeAnalyticsError,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("phase_p9")

SERVICE_TOKEN = "change-me-dev-service-token"
P2_BASE_URL = "http://127.0.0.1:8082"
PUBLISHED_VIDEO_ID = "lPYmaPHPQr4"  # Midnight Rain & Rhodes (50m Master V2)
CONTENT_ARM_ID = "lofi_study_focus"
EXPERIMENT_ID = "exp_midnight_rhodes_rain_01"
BRAND_ID = "neon_seoul"


def main() -> None:
    """Refuse: this runner wrote the fabricated P2 rewards cleared on 2026-10-31.

    It reports the hardcoded old upload as new work, books zero views and watch time
    when Analytics fails, and posts the result to P2 as a reward. Use
    ``plm analytics ingest`` / ``plm analytics dispatch`` for the verified C7 path.
    """
    raise RuntimeError(
        "run_phase_p9_closed_loop prototype disabled: hardcoded video identity and "
        "zero-filled metrics would post fabricated rewards into P2"
    )
    logger.info("======================================================================")
    logger.info("       PLM PHASE P9: CLOSED-LOOP ATTRIBUTION & BAYESIAN UPDATE        ")
    logger.info("======================================================================")

    # 1. Query Real YouTube Analytics
    logger.info("[Step 1] Ingesting Live YouTube Analytics for video: %s", PUBLISHED_VIDEO_ID)
    yt_client = RealYouTubeAnalyticsClient()
    if not yt_client.is_configured:
        logger.error("YouTube Analytics client is not configured!")
        sys.exit(1)

    now = datetime.now(UTC)
    start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    try:
        metrics = yt_client.fetch_video_metrics(
            video_id=PUBLISHED_VIDEO_ID,
            start_date=start_date,
            end_date=end_date,
        )
        views = metrics.views or 0
        watch_time_minutes = metrics.watch_time_minutes or 0.0
        avg_percentage = metrics.average_percentage_viewed
        sub_gain = metrics.subscribers_gained
        logger.info("  Real Views: %s", views)
        logger.info("  Real Watch Time (mins): %s", watch_time_minutes)
        logger.info("  Average Percentage Viewed: %s", avg_percentage)
        logger.info("  Subscribers Gained: %s", sub_gain)
    except YouTubeAnalyticsError as exc:
        logger.warning("YouTube Analytics API warning: %s; falling back to initial window metrics", exc)
        views = 0
        watch_time_minutes = 0.0
        avg_percentage = None
        sub_gain = None

    # 2. Compute Normalized Reward under engagement-contribution-proxy-v1
    logger.info("\n[Step 2] Computing Normalized Reward via engagement-contribution-proxy-v1")
    # For a newly launched single/album, initial net contribution is booked:
    net_contribution = Money.of("0.00", "USD")
    scale = REWARD_CONTRIBUTION_SCALE  # USD 50.00

    norm_reward = compute_normalized_reward(
        net_contribution=net_contribution,
        views=views,
        watch_time_minutes=watch_time_minutes,
        contribution_scale=scale,
    )
    logger.info("  Net Contribution: %s", net_contribution)
    logger.info("  Contribution Scale: %s", scale)
    logger.info("  Normalized Reward Score: %.4f (Version: %s)", norm_reward, REWARD_VERSION)

    # 3. Construct Seam C7 Leg-2 Reward Payload
    logger.info("\n[Step 3] Assembling Frozen Seam C7 Leg-2 Payload")
    payload = RewardResultPayload(
        schema_version="1.0",
        experiment_id=EXPERIMENT_ID,
        arm_id=CONTENT_ARM_ID,
        content_id=PUBLISHED_VIDEO_ID,
        observation_window=ObservationWindow.D28,
        status=ObservationMaturity.MATURE,
        observed_at=now,
        raw_metrics={
            "views": float(views),
            "watch_time_minutes": float(watch_time_minutes),
            "average_percentage_viewed": float(avg_percentage) if avg_percentage is not None else None,
            "subscriber_gain": float(sub_gain) if sub_gain is not None else None,
            "estimated_revenue": 0.0,
        },
        normalized_reward=norm_reward,
        reward_version=REWARD_VERSION,
        attribution_confidence=0.95,
        quality_status=QualityStatus.VALID,
        economic_terms=None,
    )
    logger.info("  Seam: %s (Schema Version: %s)", payload.SEAM, payload.SCHEMA_VERSION)
    logger.info("  Payload Wire:\n%s", json.dumps(payload.to_wire(), indent=2, default=str))

    # 4. Check Arm State in P2 before update
    logger.info("\n[Step 4] Querying Arm '%s' Posterior State in P2 (Pre-Update)", CONTENT_ARM_ID)
    headers = {"X-PLM-Service-Token": SERVICE_TOKEN, "Content-Type": "application/json"}
    with httpx.Client(base_url=P2_BASE_URL, headers=headers, timeout=10.0) as client:
        resp_before = client.get(f"/ar/arms/{CONTENT_ARM_ID}/state")
        if resp_before.status_code == 200:
            arm_before = resp_before.json()
            logger.info("  Pre-Update Posterior Mean: %s", arm_before.get("posterior_mean"))
            logger.info("  Pre-Update Posterior Variance: %s", arm_before.get("posterior_variance"))
            logger.info("  Pre-Update Update Version: %s", arm_before.get("update_version"))
        else:
            logger.warning("  Could not fetch pre-update arm state: HTTP %s", resp_before.status_code)
            arm_before = {}

        # 5. Post Reward Result to P2 (C7 Leg 2)
        logger.info("\n[Step 5] Posting Reward Observation to P2: POST /ar/results")
        resp_post = client.post("/ar/results", json=payload.to_wire())
        logger.info("  HTTP Response: %s %s", resp_post.status_code, resp_post.text)
        if resp_post.status_code != 200:
            logger.error("Failed to post result to P2!")
            sys.exit(1)
        post_result_data = resp_post.json()
        logger.info("  Result Stored: %s (Observation ID: %s)",
                    post_result_data.get("stored"), post_result_data.get("observation_id"))

        # 6. Verify Result Lookup Reconciliation
        logger.info("\n[Step 6] Reconciling Outbox Delivery: GET /ar/results")
        resp_lookup = client.get(
            "/ar/results",
            params={
                "experiment_id": EXPERIMENT_ID,
                "content_id": PUBLISHED_VIDEO_ID,
                "observation_window": ObservationWindow.D28.value,
                "status": ObservationMaturity.MATURE.value,
            },
        )
        logger.info("  Reconciliation Lookup: %s", resp_lookup.json())

        # 7. Trigger Bayesian Posterior Update
        logger.info("\n[Step 7] Triggering Bayesian Posterior Update in P2: POST /ar/update")
        resp_update = client.post("/ar/update", params={"as_of": now.isoformat()})
        logger.info("  HTTP Response: %s %s", resp_update.status_code, resp_update.text)
        update_summary = resp_update.json()
        logger.info("  Update Applied: %s, Quarantined: %s, Skipped/Duplicate: %s",
                    update_summary.get("applied"), update_summary.get("quarantined"),
                    update_summary.get("skipped_duplicate"))

        # 8. Query Arm State in P2 after update
        logger.info("\n[Step 8] Querying Arm '%s' Posterior State in P2 (Post-Update)", CONTENT_ARM_ID)
        resp_after = client.get(f"/ar/arms/{CONTENT_ARM_ID}/state")
        arm_after = resp_after.json()
        logger.info("  Post-Update Posterior Mean: %s", arm_after.get("posterior_mean"))
        logger.info("  Post-Update Posterior Variance: %s", arm_after.get("posterior_variance"))
        logger.info("  Post-Update Update Version: %s", arm_after.get("update_version"))

        if arm_before and "posterior_mean" in arm_before and "posterior_mean" in arm_after:
            mu_diff = arm_after["posterior_mean"] - arm_before["posterior_mean"]
            var_diff = arm_after["posterior_variance"] - arm_before["posterior_variance"]
            logger.info("  ==> Posterior Mean Shift: %+.6f", mu_diff)
            logger.info("  ==> Uncertainty (Variance) Reduction: %+.6f", var_diff)

        # 9. Run Next Cycle with Thompson Sampling Draw
        logger.info("\n[Step 9] Executing Next Autonomous Allocation Cycle: POST /ar/cycles")
        cycle_payload = {
            "brand_id": BRAND_ID,
            "slot_count": 2,
            "budget": 20.0,
            "as_of": (now + timedelta(seconds=1)).isoformat(),
            "policy_version": "p2-v1",
            "activation_state": "AUTONOMOUS",
        }
        resp_cycle = client.post("/ar/cycles", json=cycle_payload)
        logger.info("  HTTP Response: %s", resp_cycle.status_code)
        cycle_result = resp_cycle.json()
        logger.info("  Allocated Cycle ID: %s", cycle_result.get("cycle_id"))
        logger.info("  Decisions Made: %s, Selected: %s",
                    cycle_result.get("decision_count"), cycle_result.get("selected_count"))

        # Fetch Decisions for this Cycle
        cycle_id = cycle_result.get("cycle_id")
        if cycle_id:
            resp_decisions = client.get(f"/ar/cycles/{cycle_id}/decisions")
            decisions = resp_decisions.json()
            for d in decisions:
                logger.info("    * Arm: %s | Type: %s | Sampled Reward (TS): %.4f | Post. Expected: %.4f",
                            d.get("arm_id"), d.get("allocation_type"),
                            d.get("sampled_reward"), d.get("posterior_expected_reward"))

    logger.info("\n======================================================================")
    logger.info("        PHASE P9 CLOSED-LOOP PIPELINE COMPLETED SUCCESSFULLY!         ")
    logger.info("======================================================================")


if __name__ == "__main__":
    main()
