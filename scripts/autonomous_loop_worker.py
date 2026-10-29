"""Autonomous Loop Worker with Human Go/No-Go Gate Review.

Implements the continuous publishing cadence (2~3 releases per week / every 48-72h):
1. Market Intelligence (P1) -> Opportunity Scoring
2. Autonomous A&R (P2) -> Thompson Sampling Directive
3. Experiment & Specs (P3) -> Track Specs
4. Music Factory (P4) -> Suno Audio Generation & Lossless Extraction
5. Audio QA & Mastering (P5) -> Highpass, Lowshelf, Loudnorm Filter Chain
6. Media Assembly (P6) -> 1080p Longform Assembly + Container Chapters + Custom Thumbnail
7. [HUMAN GO/NO-GO GATE] -> Mandatory review of Music, Title, and Thumbnail before upload
8. YouTube Publishing (P7) -> Real YouTube Data API v3 Chunked Upload
9. Closed-Loop Learning (P8/P9) -> YouTube Analytics API v2 -> Bayesian Posterior Update
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
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
from services.youtube_publishing.client.real import RealYouTubeClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("autonomous_worker")

P2_BASE_URL = "http://127.0.0.1:8082"
SERVICE_TOKEN = "change-me-dev-service-token"
DEFAULT_CADENCE_HOURS = 72  # ~2.3 releases per week
CHANNEL_NAME = "Rhodes & Rain Records"
DEFAULT_BRAND_ID = "neon_seoul"
DEFAULT_ARM_ID = "lofi_study_focus"


def query_market_intelligence() -> dict[str, float]:
    """Query Phase P1 for active opportunities."""
    logger.info("--- [Step 1/8] P1 Market Intelligence Ingestion ---")
    headers = {"X-PLM-Service-Token": SERVICE_TOKEN}
    try:
        with httpx.Client(base_url="http://127.0.0.1:8081", headers=headers, timeout=10.0) as client:
            resp = client.get("/market/v1/opportunities")
            if resp.status_code == 200:
                opps = resp.json().get("items", [])
                scores = {item["arm_id"]: item.get("market_opportunity_score", 0.0) for item in opps}
                logger.info("  Active Market Opportunities: %s", scores)
                return scores
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("  Could not reach Market API (%s); using default opportunities", exc)
    return {"lofi_study_focus": 25.0, "rnb": 63.6}


def draw_ar_directive(arm_scores: dict[str, float]) -> dict[str, str]:
    """Query Phase P2 Autonomous A&R for next directive via Thompson Sampling."""
    logger.info("--- [Step 2/8] P2 Autonomous A&R Directive Draw ---")
    headers = {"X-PLM-Service-Token": SERVICE_TOKEN, "Content-Type": "application/json"}
    now = datetime.now(UTC)
    payload = {
        "brand_id": DEFAULT_BRAND_ID,
        "slot_count": 1,
        "budget": 10.0,
        "as_of": now.isoformat(),
        "policy_version": "p2-v1",
        "activation_state": "AUTONOMOUS",
    }
    try:
        with httpx.Client(base_url=P2_BASE_URL, headers=headers, timeout=15.0) as client:
            resp = client.post("/ar/cycles", json=payload)
            if resp.status_code == 200:
                cycle = resp.json()
                cycle_id = cycle.get("cycle_id")
                logger.info("  Allocated Cycle ID: %s (Selected: %s)", cycle_id, cycle.get("selected_count"))
                # Fetch decision details
                if cycle_id:
                    decisions = client.get(f"/ar/cycles/{cycle_id}/decisions").json()
                    for d in decisions:
                        logger.info("  Decision: Arm=%s | Type=%s | Sampled TS=%.4f | Post. Expected=%.4f",
                                    d.get("arm_id"), d.get("allocation_type"),
                                    d.get("sampled_reward"), d.get("posterior_expected_reward"))
                        return {
                            "cycle_id": cycle_id,
                            "arm_id": d.get("arm_id", DEFAULT_ARM_ID),
                            "allocation_type": d.get("allocation_type", "exploit"),
                        }
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("  Could not reach A&R API (%s); using active fallback arm '%s'", exc, DEFAULT_ARM_ID)
    return {"cycle_id": "fallback_cycle", "arm_id": DEFAULT_ARM_ID, "allocation_type": "exploit"}


def human_gate_review(
    video_path: Path,
    thumbnail_path: Path,
    title: str,
    description: str,
    tracklist: list[dict[str, str]],
    auto_approve: bool = False,
) -> bool:
    """Mandatory Go / No-Go Human Gate Review before publishing to YouTube.

    Presents the music audio/video, the video title, and the thumbnail image.
    Halts execution until the operator explicitly decides 'GO' or 'NO-GO'.
    """
    logger.info("\n" + "=" * 70)
    logger.info("      [STEP 7/8] MANDATORY HUMAN GO / NO-GO GATE REVIEW")
    logger.info("=" * 70)
    logger.info("The production package is fully assembled and mastered.")
    logger.info("Please review the following three artifacts before external upload:")
    logger.info("")
    logger.info("  1. VIDEO TITLE:")
    logger.info("     %s", title)
    logger.info("")
    logger.info("  2. THUMBNAIL ARTIFACT:")
    logger.info("     File Path: %s", thumbnail_path.resolve())
    logger.info("     Exists: %s (Size: %d bytes)", thumbnail_path.exists(),
                thumbnail_path.stat().st_size if thumbnail_path.exists() else 0)
    logger.info("")
    logger.info("  3. MASTER VIDEO / MUSIC ARTIFACT:")
    logger.info("     File Path: %s", video_path.resolve())
    logger.info("     Exists: %s (Size: %d bytes)", video_path.exists(),
                video_path.stat().st_size if video_path.exists() else 0)
    logger.info("")
    logger.info("  4. TRACKLIST & CHAPTERS (%d tracks):", len(tracklist))
    for t in tracklist:
        logger.info("     - %s (%s)", t.get("title"), t.get("timestamp"))
    logger.info("=" * 70)

    if auto_approve:
        logger.info("Operator passed --auto-approve flag. Gate automatically marked: GO")
        return True

    # Interactive prompt for the operator
    print("\n>>> GO / NO-GO REVIEW DECISION REQUIRED <<<")
    print("Enter 'GO' to approve YouTube publication, or 'NO-GO' (or 'n') to reject:")
    sys.stdout.flush()

    try:
        choice = input().strip().upper()
    except EOFError:
        logger.warning("No interactive input received (EOF); defaulting to NO-GO for safety.")
        return False

    if choice in ("GO", "G", "Y", "YES"):
        logger.info("Operator decision confirmed: [GO] - Proceeding to Phase P7 YouTube Upload.")
        return True
    else:
        logger.warning("Operator decision confirmed: [NO-GO] - Publication blocked by human gate.")
        return False


def run_cycle(auto_approve: bool = False) -> None:
    """Refuse the prototype path until it can prove a fresh guarded publication.

    This script retains its legacy implementation for reference, but must not emit
    rewards for a hardcoded old video or assert 28-day maturity from 7-day data.
    Use the verified phase pipeline, not this prototype, for live publishing.
    """
    raise RuntimeError(
        "autonomous_loop_worker prototype disabled: no verified new upload; "
        "hardcoded video identity and invalid reward maturity would poison P2 learning"
    )
    logger.info("\n" + "#" * 70)
    logger.info("### STARTING AUTONOMOUS PRODUCTION CYCLE (Rhodes & Rain Records) ###")
    logger.info("#" * 70)

    # 1. Market Intelligence
    opps = query_market_intelligence()

    # 2. A&R Allocation
    directive = draw_ar_directive(opps)
    arm_id = directive["arm_id"]
    logger.info("Directive allocated for Arm: %s (Mode: %s)", arm_id, directive["allocation_type"])

    # 3. Reference Existing Production Package or Generate Next
    # In production, uses existing mastered 50m album or triggers new generation batch
    video_path = Path("output_media/full_album/midnight_rain_and_rhodes_album_1080p.mp4")
    thumbnail_path = Path("output_media/thumbnails/midnight_rain_cover.png")
    title = "Midnight Rain & Rhodes: Cozy Lo-Fi for Late Nights & Rainy Days [50m Master V2]"
    description = (
        "00:00 - First Drop at Dusk\n"
        "03:26 - Windowpane Reflections\n"
        "06:38 - Midnight Coffee Steam\n"
        "09:47 - Rain Over Neon Puddles\n"
        "12:54 - Slow Train Through the Mist\n"
        "15:53 - Late Night Study Sanctuary\n"
        "18:59 - Gentle Thunder Far Away\n"
        "22:15 - Quiet Attic Glow\n"
        "25:22 - Damp Leaves on the Pavement\n"
        "28:38 - Low Fidelity Heartbeat\n"
        "31:51 - Steam Rising from Asphalt\n"
        "35:05 - 2 AM Solitude\n"
        "38:15 - Amber Streetlight Shadows\n"
        "41:25 - Echoes in the Downpour\n"
        "44:31 - Fading Static in the Fog\n"
        "47:38 - First Light After the Storm\n\n"
        "Cozy lo-fi beats with vintage Rhodes electric piano and soft rain sounds for deep study, focus, and sleep.\n"
        "Published autonomously by Rhodes & Rain Records."
    )
    tracklist = [
        {"title": "First Drop at Dusk", "timestamp": "00:00"},
        {"title": "Windowpane Reflections", "timestamp": "03:26"},
        {"title": "Midnight Coffee Steam", "timestamp": "06:38"},
        {"title": "Rain Over Neon Puddles", "timestamp": "09:47"},
        {"title": "Slow Train Through the Mist", "timestamp": "12:54"},
        {"title": "Late Night Study Sanctuary", "timestamp": "15:53"},
        {"title": "Gentle Thunder Far Away", "timestamp": "18:59"},
        {"title": "Quiet Attic Glow", "timestamp": "22:15"},
        {"title": "Damp Leaves on the Pavement", "timestamp": "25:22"},
        {"title": "Low Fidelity Heartbeat", "timestamp": "28:38"},
        {"title": "Steam Rising from Asphalt", "timestamp": "31:51"},
        {"title": "2 AM Solitude", "timestamp": "35:05"},
        {"title": "Amber Streetlight Shadows", "timestamp": "38:15"},
        {"title": "Echoes in the Downpour", "timestamp": "41:25"},
        {"title": "Fading Static in the Fog", "timestamp": "44:31"},
        {"title": "First Light After the Storm", "timestamp": "47:38"},
    ]

    # 4. Mandatory Go / No-Go Human Review Gate
    approved = human_gate_review(
        video_path=video_path,
        thumbnail_path=thumbnail_path,
        title=title,
        description=description,
        tracklist=tracklist,
        auto_approve=auto_approve,
    )

    if not approved:
        logger.warning("Cycle halted at Go/No-Go Gate. External upload aborted.")
        return

    # 5. External Upload & Publish
    logger.info("\n--- [Step 7/8] P7 Real YouTube Upload & Publish ---")
    yt_client = RealYouTubeClient()
    if not yt_client.is_configured:
        logger.error("YouTube credentials not configured!")
        return

    # In live runs: uses existing published ID or uploads new package
    published_video_id = "lPYmaPHPQr4"
    logger.info("Video live on YouTube: https://youtu.be/%s", published_video_id)

    # 6. Closed-Loop Analytics & Bayesian Posterior Update
    logger.info("\n--- [Step 8/8] P8/P9 Analytics Ingestion & Bayesian Update ---")
    analytics_client = RealYouTubeAnalyticsClient()
    now = datetime.now(UTC)
    start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    try:
        metrics = analytics_client.fetch_video_metrics(
            video_id=published_video_id,
            start_date=start_date,
            end_date=end_date,
        )
        views = metrics.views or 0
        watch_time_minutes = metrics.watch_time_minutes or 0.0
    except YouTubeAnalyticsError as exc:
        logger.warning("Analytics query error: %s; using baseline", exc)
        views = 0
        watch_time_minutes = 0.0

    norm_reward = compute_normalized_reward(
        net_contribution=Money.of("0.00", "USD"),
        views=views,
        watch_time_minutes=watch_time_minutes,
        contribution_scale=REWARD_CONTRIBUTION_SCALE,
    )
    logger.info("  Computed Normalized Reward: %.4f (Version: %s)", norm_reward, REWARD_VERSION)

    # Post Result to P2
    reward_payload = RewardResultPayload(
        schema_version="1.0",
        experiment_id="exp_continuous_cycle_01",
        arm_id=arm_id,
        content_id=published_video_id,
        observation_window=ObservationWindow.D28,
        status=ObservationMaturity.MATURE,
        observed_at=now,
        raw_metrics={
            "views": float(views),
            "watch_time_minutes": float(watch_time_minutes),
            "average_percentage_viewed": 0.0,
            "subscriber_gain": 0.0,
            "estimated_revenue": 0.0,
        },
        normalized_reward=norm_reward,
        reward_version=REWARD_VERSION,
        attribution_confidence=0.95,
        quality_status=QualityStatus.VALID,
        economic_terms=None,
    )

    headers = {"X-PLM-Service-Token": SERVICE_TOKEN, "Content-Type": "application/json"}
    try:
        with httpx.Client(base_url=P2_BASE_URL, headers=headers, timeout=15.0) as client:
            # Deliver C7 Leg 2
            res_post = client.post("/ar/results", json=reward_payload.to_wire())
            logger.info("  C7 Leg-2 Delivery: %s", res_post.status_code)

            # Trigger Bayesian Update
            res_update = client.post("/ar/update", params={"as_of": now.isoformat()})
            logger.info("  Bayesian Update: %s %s", res_update.status_code, res_update.text)

            # Log updated arm posterior
            res_state = client.get(f"/ar/arms/{arm_id}/state")
            if res_state.status_code == 200:
                arm_state = res_state.json()
                logger.info("  Updated Arm State for '%s': Mean=%.4f, Var=%.4f, Version=%s",
                            arm_id,
                            arm_state.get("posterior_mean", 0.0),
                            arm_state.get("posterior_variance", 0.0),
                            arm_state.get("update_version"))
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("  Could not reach A&R API (%s) for feedback; delivery will retry on next cycle", exc)

    logger.info("\n" + "=" * 70)
    logger.info("### CYCLE COMPLETE: System ready for next cadence window ###")
    logger.info("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="Autonomous Publishing Loop Worker with Human Gate Review")
    parser.add_argument("--cadence-hours", type=float, default=DEFAULT_CADENCE_HOURS,
                        help=f"Hours between cycles (default: {DEFAULT_CADENCE_HOURS}h, ~2.3 per week)")
    parser.add_argument("--run-now", action="store_true",
                        help="Run one complete cycle immediately")
    parser.add_argument("--auto-approve", action="store_true",
                        help="Bypass interactive Go/No-Go gate prompt (unattended test mode)")
    args = parser.parse_args()

    cadence_seconds = args.cadence_hours * 3600.0
    logger.info("Starting Autonomous Loop Worker (Cadence: %.1f hours / ~%.1f per week)",
                args.cadence_hours, 168.0 / args.cadence_hours)

    if args.run_now:
        run_cycle(auto_approve=args.auto_approve)
        return

    # Continuous Daemon Mode
    while True:
        try:
            run_cycle(auto_approve=args.auto_approve)
        except Exception:
            logger.exception("Error during autonomous cycle execution")

        next_run = datetime.now(UTC) + timedelta(seconds=cadence_seconds)
        logger.info("Sleeping for %.1f hours. Next production cycle scheduled for: %s",
                    args.cadence_hours, next_run.strftime("%Y-%m-%d %H:%M:%S UTC"))
        time.sleep(cadence_seconds)


if __name__ == "__main__":
    main()
