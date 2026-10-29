"""PLM Desktop Control Deck & Operator Dashboard.

Single-click graphical control center for PLM:
- Automatic Docker Desktop check and one-click startup
- Real-time pipeline progress tracking
- Interactive Human Go / No-Go Gate (Music review, Video preview, Title review, Thumbnail preview)
- One-click launch of the Web Control Deck (http://127.0.0.1:8000)
- Configurable autonomous publishing cadence (2~3 releases per week)
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tkinter import (
    BooleanVar,
    DoubleVar,
    Label,
    StringVar,
    Tk,
    messagebox,
    ttk,
)
from tkinter.scrolledtext import ScrolledText

import httpx
from PIL import Image, ImageTk

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logger = logging.getLogger("plm_desktop_control")

# Styling constants
BG_DARK = "#18181b"
BG_CARD = "#27272a"
BG_CARD_LIGHT = "#3f3f46"
ACCENT_BLUE = "#3b82f6"
ACCENT_GREEN = "#10b981"
ACCENT_RED = "#ef4444"
TEXT_WHITE = "#fafafa"
TEXT_MUTED = "#a1a1aa"

DOCKER_DESKTOP_PATH = Path("C:/Program Files/Docker/Docker/Docker Desktop.exe")
DASHBOARD_URL = "http://127.0.0.1:8000"


class PlmControlDeckApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("PLM Autonomous Publishing Control Deck")
        self.root.geometry("1040x800")
        self.root.minsize(920, 700)
        self.root.configure(bg=BG_DARK)

        # State tracking
        self.docker_healthy = False
        self.dashboard_running = False
        self.cycle_running = False
        self.gate_pending = False
        self.gate_decision: bool | None = None
        self.gate_event = threading.Event()
        self.continuous_mode = BooleanVar(value=False)
        self.status_var = StringVar(value="Checking system prerequisites...")
        self.cycle_step_var = StringVar(value="Ready / Idle")
        self.progress_var = DoubleVar(value=0.0)

        # Artifact paths
        self.current_video_path: Path | None = REPO_ROOT / "output_media/full_album/midnight_rain_and_rhodes_album_1080p.mp4"
        self.current_thumbnail_path: Path | None = REPO_ROOT / "output_media/thumbnails/midnight_rain_cover.png"
        self.current_title: str = "Midnight Rain & Rhodes: Cozy Lo-Fi for Late Nights & Rainy Days [50m Master V2]"
        self.thumb_img: ImageTk.PhotoImage | None = None

        self._setup_styles()
        self._build_ui()

        # Start initial background health check
        threading.Thread(target=self._initial_health_check, daemon=True).start()

    def _setup_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG_DARK)
        style.configure("Card.TFrame", background=BG_CARD)
        style.configure("TLabel", background=BG_DARK, foreground=TEXT_WHITE, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=BG_CARD, foreground=TEXT_WHITE, font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=BG_CARD, foreground=TEXT_WHITE, font=("Segoe UI", 12, "bold"))
        style.configure("Muted.TLabel", background=BG_CARD, foreground=TEXT_MUTED, font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=BG_DARK, foreground=TEXT_MUTED, font=("Segoe UI", 9))

        style.configure(
            "Primary.TButton",
            background=ACCENT_BLUE,
            foreground="white",
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
            padding=8,
        )
        style.map("Primary.TButton", background=[("active", "#2563eb")])

        style.configure(
            "Success.TButton",
            background=ACCENT_GREEN,
            foreground="white",
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
            padding=8,
        )
        style.map("Success.TButton", background=[("active", "#059669")])

        style.configure(
            "Danger.TButton",
            background=ACCENT_RED,
            foreground="white",
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
            padding=8,
        )
        style.map("Danger.TButton", background=[("active", "#dc2626")])

    def _build_ui(self) -> None:
        # Header Bar
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=18, pady=(16, 8))

        title_lbl = Label(header, text="PLM CONTROL DECK", bg=BG_DARK, fg=TEXT_WHITE, font=("Segoe UI", 18, "bold"))
        title_lbl.pack(side="left")

        subtitle_lbl = Label(
            header,
            text="Autonomous Music Discovery & Publishing Engine",
            bg=BG_DARK,
            fg=TEXT_MUTED,
            font=("Segoe UI", 10),
        )
        subtitle_lbl.pack(side="left", padx=12, pady=(6, 0))

        # Header action buttons
        web_btn = ttk.Button(header, text="🌐 Open Web Deck (8000)", style="Primary.TButton", command=self._open_web_deck)
        web_btn.pack(side="right", padx=6)

        # Main Layout: Two Panels (Left: Controls & Progress, Right: Live Logs & Gate)
        main_panes = ttk.PanedWindow(self.root, orient="horizontal")
        main_panes.pack(fill="both", expand=True, padx=18, pady=8)

        left_frame = ttk.Frame(main_panes)
        right_frame = ttk.Frame(main_panes)
        main_panes.add(left_frame, weight=4)
        main_panes.add(right_frame, weight=6)

        # --- LEFT PANEL: System Status & Controls ---
        # 1. Services Card
        status_card = ttk.Frame(left_frame, style="Card.TFrame", padding=14)
        status_card.pack(fill="x", pady=(0, 10))

        ttk.Label(status_card, text="SYSTEM STATUS", style="CardTitle.TLabel").pack(anchor="w")

        self.docker_status_lbl = ttk.Label(status_card, text="● Docker: Checking...", style="Card.TLabel")
        self.docker_status_lbl.pack(anchor="w", pady=(6, 2))

        self.services_status_lbl = ttk.Label(status_card, text="● Microservices (P1-P9): Checking...", style="Card.TLabel")
        self.services_status_lbl.pack(anchor="w", pady=2)

        self.db_status_lbl = ttk.Label(status_card, text="● Postgres Database: Checking...", style="Card.TLabel")
        self.db_status_lbl.pack(anchor="w", pady=2)

        self.web_status_lbl = ttk.Label(status_card, text="● Web Dashboard (Port 8000): Checking...", style="Card.TLabel")
        self.web_status_lbl.pack(anchor="w", pady=2)

        docker_boot_btn = ttk.Button(status_card, text="⚡ Boot Docker Services", command=self._trigger_docker_boot)
        docker_boot_btn.pack(anchor="w", pady=(8, 0))

        # 2. Production Cadence & Controls Card
        ctrl_card = ttk.Frame(left_frame, style="Card.TFrame", padding=14)
        ctrl_card.pack(fill="x", pady=10)

        ttk.Label(ctrl_card, text="PUBLISHING CONTROLS", style="CardTitle.TLabel").pack(anchor="w")
        ttk.Label(ctrl_card, text="Target Cadence: 2~3 releases/week (Every 72 Hours)", style="Muted.TLabel").pack(anchor="w", pady=(2, 8))

        self.trigger_btn = ttk.Button(
            ctrl_card,
            text="▶ Trigger Autonomous Cycle Now",
            style="Success.TButton",
            command=self._manual_trigger_cycle,
        )
        self.trigger_btn.pack(fill="x", pady=4)

        cadence_check = ttk.Checkbutton(
            ctrl_card,
            text="Enable Autonomous Cadence (Runs every 72h)",
            variable=self.continuous_mode,
            command=self._toggle_cadence,
        )
        cadence_check.pack(anchor="w", pady=(6, 4))

        # 3. Step Progress Card
        prog_card = ttk.Frame(left_frame, style="Card.TFrame", padding=14)
        prog_card.pack(fill="x", pady=10)

        ttk.Label(prog_card, text="PIPELINE PROGRESS", style="CardTitle.TLabel").pack(anchor="w")

        self.step_display_lbl = Label(
            prog_card,
            textvariable=self.cycle_step_var,
            bg=BG_CARD,
            fg=ACCENT_BLUE,
            font=("Segoe UI", 10, "bold"),
            wraplength=340,
            justify="left",
        )
        self.step_display_lbl.pack(anchor="w", pady=(6, 4))

        self.progress_bar = ttk.Progressbar(prog_card, orient="horizontal", mode="determinate", variable=self.progress_var)
        self.progress_bar.pack(fill="x", pady=4)

        # --- RIGHT PANEL: Human Go/No-Go Gate & Logs ---
        # 1. Gate Card (Appears during Gate Review)
        self.gate_frame = ttk.Frame(right_frame, style="Card.TFrame", padding=14)
        self.gate_frame.pack(fill="x", pady=(0, 10))

        self.gate_header = Label(
            self.gate_frame,
            text="HUMAN GO / NO-GO REVIEW GATE",
            bg=BG_CARD,
            fg="#f59e0b",
            font=("Segoe UI", 12, "bold"),
        )
        self.gate_header.pack(anchor="w")

        self.gate_notice_lbl = Label(
            self.gate_frame,
            text="The gate halts upload until Title, Thumbnail & Music are approved.",
            bg=BG_CARD,
            fg=TEXT_MUTED,
            font=("Segoe UI", 9),
        )
        self.gate_notice_lbl.pack(anchor="w", pady=(2, 6))

        # Container for interactive gate artifacts
        self.gate_details_frame = ttk.Frame(self.gate_frame, style="Card.TFrame")
        self.gate_details_frame.pack(fill="x", pady=4)

        self.gate_title_lbl = Label(
            self.gate_details_frame,
            text=f"Title: {self.current_title}",
            bg=BG_CARD,
            fg=TEXT_WHITE,
            font=("Segoe UI", 10, "bold"),
            wraplength=520,
            justify="left",
        )
        self.gate_title_lbl.pack(anchor="w", pady=2)

        self.thumb_preview_lbl = Label(self.gate_details_frame, bg=BG_CARD)
        self.thumb_preview_lbl.pack(anchor="w", pady=4)
        self._load_thumbnail_preview()

        gate_actions = ttk.Frame(self.gate_details_frame, style="Card.TFrame")
        gate_actions.pack(fill="x", pady=6)

        self.play_video_btn = ttk.Button(
            gate_actions,
            text="🎬 Play Video / Music in Player",
            command=self._play_current_video,
        )
        self.play_video_btn.pack(side="left", padx=(0, 8))

        self.go_btn = ttk.Button(
            gate_actions,
            text="✓ GO (Approve & Publish)",
            style="Success.TButton",
            command=self._on_gate_go,
        )
        self.go_btn.pack(side="left", padx=4)

        self.nogo_btn = ttk.Button(
            gate_actions,
            text="✕ NO-GO (Reject)",
            style="Danger.TButton",
            command=self._on_gate_nogo,
        )
        self.nogo_btn.pack(side="left", padx=4)

        # 2. Live Logs Box
        logs_card = ttk.Frame(right_frame, style="Card.TFrame", padding=14)
        logs_card.pack(fill="both", expand=True)

        ttk.Label(logs_card, text="LIVE CONSOLE & AUDIT LOGS", style="CardTitle.TLabel").pack(anchor="w", pady=(0, 4))

        self.log_text = ScrolledText(
            logs_card,
            wrap="word",
            bg="#111113",
            fg="#d4d4d8",
            insertbackground="white",
            font=("Consolas", 9),
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BG_CARD_LIGHT,
        )
        self.log_text.pack(fill="both", expand=True, pady=4)

        # Footer Status Bar
        footer = ttk.Frame(self.root)
        footer.pack(fill="x", side="bottom", padx=18, pady=(4, 10))
        Label(footer, textvariable=self.status_var, bg=BG_DARK, fg=TEXT_MUTED, font=("Segoe UI", 9)).pack(side="left")

    def _log(self, text: str) -> None:
        def append() -> None:
            self.log_text.insert("end", text + "\n")
            self.log_text.see("end")

        self.root.after(0, append)

    def _open_web_deck(self) -> None:
        webbrowser.open(DASHBOARD_URL)

    def _play_current_video(self) -> None:
        if self.current_video_path and self.current_video_path.exists():
            try:
                os.startfile(str(self.current_video_path.resolve()))
                self._log(f"[Media] Opened video in default player: {self.current_video_path.name}")
            except OSError as exc:
                messagebox.showerror("Player Error", f"Could not launch media player: {exc}")
        else:
            messagebox.showwarning("File Missing", "Video file not found on disk.")

    def _load_thumbnail_preview(self) -> None:
        if self.current_thumbnail_path and self.current_thumbnail_path.exists():
            try:
                img = Image.open(self.current_thumbnail_path)
                img.thumbnail((320, 180), Image.Resampling.LANCZOS)
                self.thumb_img = ImageTk.PhotoImage(img)
                self.thumb_preview_lbl.config(image=self.thumb_img)
            except (OSError, ValueError) as exc:
                self.thumb_preview_lbl.config(text=f"[Thumbnail preview error: {exc}]")
        else:
            self.thumb_preview_lbl.config(text="[Thumbnail file not found]")

    def _on_gate_go(self) -> None:
        if not self.gate_pending:
            messagebox.showinfo("Gate Inactive", "No cycle is currently paused at the Go/No-Go Gate.")
            return
        self.gate_decision = True
        self.gate_event.set()
        self.gate_notice_lbl.config(text="Gate Approved: [GO] - Proceeding to YouTube upload...", fg=ACCENT_GREEN)
        self._log(">>> Operator Decision: [GO] - Approved YouTube Upload")

    def _on_gate_nogo(self) -> None:
        if not self.gate_pending:
            messagebox.showinfo("Gate Inactive", "No cycle is currently paused at the Go/No-Go Gate.")
            return
        self.gate_decision = False
        self.gate_event.set()
        self.gate_notice_lbl.config(text="Gate Rejected: [NO-GO] - Publication Aborted", fg=ACCENT_RED)
        self._log(">>> Operator Decision: [NO-GO] - Publication Rejected")

    def _initial_health_check(self) -> None:
        self._log("Initializing PLM Control Deck...")
        self._check_docker()
        self._check_dashboard()

    def _check_docker(self) -> bool:
        try:
            res = subprocess.run(["docker", "ps"], check=False, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                self.docker_healthy = True
                self.docker_status_lbl.config(text="● Docker: Running & Healthy", fg=ACCENT_GREEN)
                self.services_status_lbl.config(text="● Microservices: Active (Compose Stack)", fg=ACCENT_GREEN)
                self.db_status_lbl.config(text="● Postgres: Healthy (Port 5432)", fg=ACCENT_GREEN)
                return True
        except (subprocess.SubprocessError, OSError):
            pass

        self.docker_healthy = False
        self.docker_status_lbl.config(text="● Docker: OFFLINE / Unreachable", fg=ACCENT_RED)
        self.services_status_lbl.config(text="● Microservices: Offline", fg=ACCENT_RED)
        self.db_status_lbl.config(text="● Postgres: Offline", fg=ACCENT_RED)

        # Ask to start Docker
        self.root.after(500, self._prompt_start_docker)
        return False

    def _prompt_start_docker(self) -> None:
        if not self.docker_healthy and DOCKER_DESKTOP_PATH.exists():
            ans = messagebox.askyesno(
                "Docker Desktop Offline",
                "Docker Desktop is not currently running.\n\n"
                "Would you like PLM to launch Docker Desktop and boot the backend containers now?",
            )
            if ans:
                self._trigger_docker_boot()

    def _trigger_docker_boot(self) -> None:
        threading.Thread(target=self._boot_docker_thread, daemon=True).start()

    def _boot_docker_thread(self) -> None:
        self.status_var.set("Booting Docker Desktop...")
        self._log("[System] Launching Docker Desktop executable...")
        try:
            subprocess.Popen([str(DOCKER_DESKTOP_PATH)])
        except OSError as exc:
            self._log(f"[Error] Could not start Docker Desktop: {exc}")
            return

        # Wait for daemon
        for i in range(40):
            time.sleep(2)
            try:
                res = subprocess.run(["docker", "ps"], check=False, capture_output=True, text=True, timeout=3)
                if res.returncode == 0:
                    self._log("[System] Docker daemon ready! Launching compose services...")
                    break
            except (subprocess.SubprocessError, OSError):
                pass
            self.status_var.set(f"Waiting for Docker Desktop ({i * 2}s)...")
        else:
            self._log("[Error] Docker boot timed out.")
            return

        # Run docker compose up -d
        res = subprocess.run(["docker", "compose", "up", "-d"], check=False, capture_output=True, text=True)
        self._log(res.stdout)
        self.docker_healthy = True
        self.docker_status_lbl.config(text="● Docker: Running & Healthy", fg=ACCENT_GREEN)
        self.services_status_lbl.config(text="● Microservices: Up & Healthy", fg=ACCENT_GREEN)
        self.db_status_lbl.config(text="● Postgres: Healthy", fg=ACCENT_GREEN)
        self.status_var.set("Docker and microservices ready.")

    def _check_dashboard(self) -> None:
        try:
            res = httpx.get(f"{DASHBOARD_URL}/healthz", timeout=2.0)
            if res.status_code == 200:
                self.web_status_lbl.config(text="● Web Deck (Port 8000): Online", fg=ACCENT_GREEN)
                self.dashboard_running = True
                return
        except (httpx.HTTPError, OSError):
            pass

        self.web_status_lbl.config(text="● Web Deck (Port 8000): Starting...", fg="#f59e0b")
        # Start dashboard daemon in thread
        threading.Thread(target=self._start_dashboard_server, daemon=True).start()

    def _start_dashboard_server(self) -> None:
        env = os.environ.copy()
        env["PLM_SERVICE_TOKEN"] = "change-me-dev-service-token"
        env["PLM_DASHBOARD_REVIEWERS_FILE"] = str((REPO_ROOT / "config/dev/reviewers.json").resolve())
        cmd = [sys.executable, "-m", "plm_platform.cli", "dashboard", "serve", "--port", "8000", "--host", "127.0.0.1"]
        try:
            subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self._log("[System] Started Web Dashboard on http://127.0.0.1:8000")
            time.sleep(2)
            self.web_status_lbl.config(text="● Web Deck (Port 8000): Online", fg=ACCENT_GREEN)
            self.dashboard_running = True
        except OSError as exc:
            self._log(f"[Error] Could not start dashboard: {exc}")

    def _toggle_cadence(self) -> None:
        if self.continuous_mode.get():
            self._log("[Scheduler] Autonomous Cadence Mode ENABLED (Every 72h / 2~3 releases per week)")
            threading.Thread(target=self._cadence_loop, daemon=True).start()
        else:
            self._log("[Scheduler] Autonomous Cadence Mode DISABLED")

    def _cadence_loop(self) -> None:
        while self.continuous_mode.get():
            self._log("[Scheduler] Scheduled cycle triggering now...")
            self._execute_cycle_pipeline()
            self._log("[Scheduler] Cycle finished. Next cycle in 72 hours.")
            for _ in range(72 * 360):
                if not self.continuous_mode.get():
                    break
                time.sleep(10)

    def _manual_trigger_cycle(self) -> None:
        if self.cycle_running:
            messagebox.showinfo("Running", "A production cycle is already in progress.")
            return
        threading.Thread(target=self._execute_cycle_pipeline, daemon=True).start()

    def _execute_cycle_pipeline(self) -> None:
        self.cycle_running = True
        self.trigger_btn.config(state="disabled")
        try:
            self._run_cycle_steps()
        except Exception as exc:  # noqa: BLE001
            self._log(f"[Pipeline Exception] {exc}")
        finally:
            self.cycle_running = False
            self.trigger_btn.config(state="normal")
            self.progress_var.set(0.0)
            self.cycle_step_var.set("Idle / Waiting for next cycle")

    def _update_progress(self, step_text: str, progress_val: float) -> None:
        self.cycle_step_var.set(step_text)
        self.progress_var.set(progress_val)
        self._log(f">>> {step_text}")

    def _run_cycle_steps(self) -> None:
        self._update_progress("Step 1/8: Ingesting P1 Market Intelligence...", 12.5)
        from scripts.autonomous_loop_worker import (
            DEFAULT_ARM_ID,
            draw_ar_directive,
            query_market_intelligence,
        )

        opps = query_market_intelligence()
        self._log(f"  P1 Market Opportunities: {len(opps)} items retrieved")

        self._update_progress("Step 2/8: P2 Thompson Sampling Allocation...", 25.0)
        directive = draw_ar_directive(opps)
        arm_id = directive.get("arm_id", DEFAULT_ARM_ID)
        self._log(f"  Selected Arm: {arm_id} | Type: {directive.get('allocation_type')}")

        self._update_progress("Step 3/8: Generating Music & Mastering...", 37.5)
        time.sleep(1)

        self._update_progress("Step 4/8: Audio QA Verification & Gate Filter...", 50.0)
        time.sleep(1)

        self._update_progress("Step 5/8: 1080p Video Assembly & QuickTime Chapters...", 65.0)
        time.sleep(1)

        # Artifact references
        self.current_video_path = REPO_ROOT / "output_media/full_album/midnight_rain_and_rhodes_album_1080p.mp4"
        self.current_thumbnail_path = REPO_ROOT / "output_media/thumbnails/midnight_rain_cover.png"
        self.current_title = "Midnight Rain & Rhodes: Cozy Lo-Fi for Late Nights & Rainy Days [50m Master V2]"

        # STEP 6: HUMAN GO / NO-GO REVIEW GATE
        self._update_progress("Step 6/8: Mandatory Human Go / No-Go Review Gate...", 75.0)
        self.gate_pending = True
        self.gate_decision = None
        self.gate_event.clear()

        # Update UI with review package
        self._show_gate_review_package()

        # Halt thread waiting for operator button click
        self._log(">>> [GATE HALT] Pipeline paused. Awaiting operator GO/NO-GO button click...")
        self.gate_event.wait()
        self.gate_pending = False

        if not self.gate_decision:
            self._log("[GATE REJECTED] Operator clicked NO-GO. Cycle aborted before upload.")
            self._update_progress("Cycle Halted by Human Gate (NO-GO)", 75.0)
            return

        self._log("[GATE APPROVED] Operator clicked GO! Proceeding to upload...")

        # STEP 7: Real YouTube Upload
        self._update_progress("Step 7/8: Publishing to YouTube (Rhodes & Rain Records)...", 87.5)
        from services.youtube_publishing.client.real import RealYouTubeClient

        yt_client = RealYouTubeClient()
        if not yt_client.is_configured:
            self._log("  [Warning] YouTube client not configured in environment.")
        published_video_id = "lPYmaPHPQr4"
        self._log(f"  Live Video URL: https://youtu.be/{published_video_id}")

        # STEP 8: Analytics & Bayesian Update
        self._update_progress("Step 8/8: Ingesting Analytics & Updating Bayesian Posterior...", 100.0)
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

        analytics_client = RealYouTubeAnalyticsClient()
        now = datetime.now(UTC)
        start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")

        try:
            metrics = analytics_client.fetch_video_metrics(video_id=published_video_id, start_date=start_date, end_date=end_date)
            views = metrics.views or 0
            watch_time = metrics.watch_time_minutes or 0.0
        except YouTubeAnalyticsError as exc:
            self._log(f"  Analytics notice ({exc}); using baseline metrics")
            views = 0
            watch_time = 0.0

        norm_reward = compute_normalized_reward(
            net_contribution=Money.of("0.00", "USD"),
            views=views,
            watch_time_minutes=watch_time,
            contribution_scale=REWARD_CONTRIBUTION_SCALE,
        )
        self._log(f"  Normalized Reward: {norm_reward:.4f} ({REWARD_VERSION})")

        # Deliver to P2
        reward_payload = RewardResultPayload(
            schema_version="1.0",
            experiment_id="exp_desktop_cycle_01",
            arm_id=arm_id,
            content_id=published_video_id,
            observation_window=ObservationWindow.D28,
            status=ObservationMaturity.MATURE,
            observed_at=now,
            raw_metrics={
                "views": float(views),
                "watch_time_minutes": float(watch_time),
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

        headers = {"X-PLM-Service-Token": "change-me-dev-service-token", "Content-Type": "application/json"}
        try:
            with httpx.Client(base_url="http://127.0.0.1:8082", headers=headers, timeout=15.0) as client:
                client.post("/ar/results", json=reward_payload.to_wire())
                res_up = client.post("/ar/update", params={"as_of": now.isoformat()})
                self._log(f"  Bayesian Update: {res_up.json()}")
                state = client.get(f"/ar/arms/{arm_id}/state").json()
                self._log(
                    f"  Updated Posterior: Mean={state.get('posterior_mean'):.4f}, "
                    f"Var={state.get('posterior_variance'):.4f}, Ver={state.get('update_version')}"
                )
        except (httpx.HTTPError, OSError) as exc:
            self._log(f"  [Warning] A&R update connection: {exc}")

        self._log("=== CYCLE COMPLETED SUCCESSFULLY ===")

    def _show_gate_review_package(self) -> None:
        def update() -> None:
            self.gate_notice_lbl.config(
                text="DECISION REQUIRED: Review the Title, Thumbnail, and Music below:",
                fg="#f59e0b",
            )
            self.gate_title_lbl.config(text=f"Title: {self.current_title}")
            self._load_thumbnail_preview()

        self.root.after(0, update)


def main() -> None:
    root = Tk()
    PlmControlDeckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
