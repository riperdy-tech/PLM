// PLM Visual Control Deck Frontend Application (ES6 Module)

const API = {
  stats: '/api/stats',
  arms: '/api/arms',
  candidates: '/api/candidates',
  releases: '/api/releases',
  runPipeline: '/api/pipeline/run',
  events: '/api/pipeline/events',
  resolveQA: '/api/qa/resolve',
};

// Mutating routes require the platform service token (R15). It is asked for once per
// browser session and kept only in sessionStorage.
const TOKEN_HEADER = 'X-PLM-Service-Token';
const TOKEN_KEY = 'plmServiceToken';

function readToken() {
  try { return sessionStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
}

function storeToken(token) {
  try { sessionStorage.setItem(TOKEN_KEY, token); } catch (e) { /* storage unavailable */ }
}

async function authedPost(url, body) {
  const send = (token) => fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { [TOKEN_HEADER]: token } : {}) },
    body: JSON.stringify(body),
  });
  let res = await send(readToken());
  if (res.status === 401) {
    const entered = window.prompt('Service token (PLM_SERVICE_TOKEN) required for this action:');
    if (entered) {
      storeToken(entered);
      res = await send(entered);
    }
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const msg = (detail.detail && detail.detail.message) || res.statusText;
    throw new Error(`${res.status}: ${msg}`);
  }
  return res.json();
}

// State
let eventSource = null;
let currentTrack = null;
let audioContext = null;
let isAudioPlaying = false;

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initPlayer();
  initModal();
  initTelemetrySSE();

  // Load initial data
  loadAllData();

  // Button handlers
  const headerRunBtn = document.getElementById('header-run-btn');
  const triggerRunBtn = document.getElementById('trigger-run-btn');
  const clearTerminalBtn = document.getElementById('clear-terminal-btn');

  if (headerRunBtn) headerRunBtn.addEventListener('click', triggerPipelineRun);
  if (triggerRunBtn) triggerRunBtn.addEventListener('click', triggerPipelineRun);
  if (clearTerminalBtn) {
    clearTerminalBtn.addEventListener('click', () => {
      const tb = document.getElementById('terminal-body');
      if (tb) tb.innerHTML = '<div class="log-line system">[SYSTEM] Terminal cleared.</div>';
    });
  }
});

// Tab Navigation
function initTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabPanes = document.querySelectorAll('.tab-pane');

  tabBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const targetId = `view-${btn.dataset.tab}`;
      tabBtns.forEach((b) => b.classList.remove('active'));
      tabPanes.forEach((p) => p.classList.remove('active'));

      btn.classList.add('active');
      const targetPane = document.getElementById(targetId);
      if (targetPane) targetPane.classList.add('active');
    });
  });
}

// Data Loaders
async function loadAllData() {
  await Promise.all([
    loadStats(),
    loadArms(),
    loadCandidates(),
    loadReleases(),
    loadQACases(),
  ]);
}

async function loadStats() {
  try {
    const res = await fetch(API.stats);
    const d = await res.json();
    document.getElementById('kpi-cycles').textContent = d.total_cycles;
    document.getElementById('kpi-arms').textContent = d.active_arms;
    document.getElementById('kpi-candidates').textContent = d.candidates_generated;
    document.getElementById('kpi-releases').textContent = d.cleared_releases;
    document.getElementById('kpi-revenue').textContent = `$${Number(d.total_revenue_usd).toFixed(2)}`;
    document.getElementById('kpi-reward').textContent = Number(d.avg_normalized_reward).toFixed(2);
  } catch (err) {
    console.error('Failed to load stats:', err);
  }
}

async function loadArms() {
  try {
    const res = await fetch(API.arms);
    const arms = (await res.json()).items;
    const container = document.getElementById('arms-grid');
    if (!container) return;

    container.innerHTML = arms.map((arm) => {
      const winRatePct = Math.round(arm.posterior_mean * 100);
      const isExploit = arm.allocation_type === 'EXPLOIT';
      const badgeClass = isExploit ? 'pill-passed' : 'badge-idle';
      return `
        <div class="arm-card">
          <div class="arm-header">
            <div>
              <div class="arm-title">${escapeHtml(arm.name)}</div>
              <div class="track-genre">ID: ${escapeHtml(arm.arm_id)}</div>
            </div>
            <span class="pill ${badgeClass}">${arm.allocation_type}</span>
          </div>

          <div class="arm-gauge-row">
            <div style="display: flex; justify-content: space-between; font-size: 0.8rem; font-family: var(--font-mono);">
              <span>Posterior Win Rate</span>
              <span style="color: var(--accent-cyan); font-weight: 700;">${winRatePct}%</span>
            </div>
            <div class="arm-bar-container">
              <div class="arm-bar" style="width: ${winRatePct}%;"></div>
            </div>
          </div>

          <div class="track-meta-row" style="margin-bottom: 0;">
            <div>Pulls: <strong>${arm.observation_count}</strong></div>
            <div>CI: <strong>[${arm.confidence_interval[0]}, ${arm.confidence_interval[1]}]</strong></div>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    console.error('Failed to load arms:', err);
  }
}

async function loadCandidates() {
  try {
    const res = await fetch(API.candidates);
    const candidates = (await res.json()).items;
    const container = document.getElementById('candidates-grid');
    if (!container) return;

    container.innerHTML = candidates.map((c) => {
      const isCleared = c.qa_disposition === 'PASSED';
      const pillClass = isCleared ? 'pill-passed' : 'pill-quarantine';
      return `
        <div class="candidate-card">
          <div>
            <div class="card-top">
              <div>
                <div class="track-name">${escapeHtml(c.title)}</div>
                <div class="track-genre">${escapeHtml(c.genre)}</div>
              </div>
              <span class="pill ${pillClass}">${c.qa_disposition}</span>
            </div>

            <p class="track-prompt">"${escapeHtml(c.prompt)}"</p>

            <div class="track-meta-row">
              <div>BPM: <strong>${c.bpm}</strong></div>
              <div>Key: <strong>${c.key}</strong></div>
              <div>Len: <strong>${c.duration_seconds}s</strong></div>
            </div>
          </div>

          <div class="candidate-card-actions">
            <button class="btn btn-primary btn-sm play-track-btn" data-id="${c.candidate_id}" data-title="${escapeHtml(c.title)}" data-genre="${escapeHtml(c.genre)}">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
              Play
            </button>
            <button class="btn btn-secondary btn-sm inspect-btn" data-id="${c.candidate_id}" data-title="${escapeHtml(c.title)}">
              Inspect Manifest
            </button>
          </div>
        </div>
      `;
    }).join('');

    // Attach listeners
    container.querySelectorAll('.play-track-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        playCandidateAudio(btn.dataset.id, btn.dataset.title, btn.dataset.genre);
      });
    });

    container.querySelectorAll('.inspect-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        showManifestModal(btn.dataset.id, btn.dataset.title);
      });
    });
  } catch (err) {
    console.error('Failed to load candidates:', err);
  }
}

async function loadReleases() {
  try {
    const res = await fetch(API.releases);
    const releases = (await res.json()).items;
    const tbody = document.getElementById('releases-table-body');
    if (!tbody) return;

    tbody.innerHTML = releases.map((r) => `
      <tr>
        <td><code>${escapeHtml(r.release_id)}</code></td>
        <td><strong>${escapeHtml(r.title)}</strong></td>
        <td>${escapeHtml(r.artist)}</td>
        <td><code>${escapeHtml(r.isrc)}</code></td>
        <td><span class="pill pill-passed">${escapeHtml(r.youtube_status)}</span></td>
        <td><span class="pill pill-passed">${escapeHtml(r.dsp_status)}</span></td>
        <td><code>${escapeHtml(r.manifest_id)}</code></td>
      </tr>
    `).join('');
  } catch (err) {
    console.error('Failed to load releases:', err);
  }
}

function loadQACases() {
  const casesList = document.getElementById('qa-cases-list');
  if (!casesList) return;

  casesList.innerHTML = `
    <div class="candidate-card" style="margin-bottom: 12px;">
      <div class="card-top">
        <div>
          <div class="track-name">Sublevel Glitch (Candidate: cand_cyber_04)</div>
          <div class="track-genre" style="color: var(--accent-rose);">Quarantine Flag: Duration Delta (-1.5s)</div>
        </div>
        <span class="pill pill-quarantine">QUARANTINED</span>
      </div>
      <p class="track-prompt">Expected target duration 180.0s &plusmn; 1.0s, candidate length measured 178.5s. LUFS and lyric checks clean.</p>
      <div class="candidate-card-actions">
        <button id="qa-override-btn" class="btn btn-primary btn-sm">
          Auditor Override & Clear Release
        </button>
      </div>
    </div>
  `;

  const btn = document.getElementById('qa-override-btn');
  if (btn) {
    btn.addEventListener('click', async () => {
      try {
        const d = await authedPost(API.resolveQA, {
          case_id: 'case_001',
          resolution: 'CLEARED_BY_OVERRIDE',
          notes: 'Auditor evaluated delta as acceptable fadeout tail.',
        });
        logToTerminal(`[QA AUDITOR] Case ${d.case_id} resolved: ${d.resolution}`, 'success');
        casesList.innerHTML = `<div class="log-line success" style="padding: 16px; background: rgba(16,185,129,0.1); border-radius: 8px;">✓ All quarantined cases cleared by auditor. Review queue empty.</div>`;
        loadStats();
      } catch (err) {
        console.error('QA resolve failed:', err);
        logToTerminal(`[QA AUDITOR] Resolve refused: ${err.message}`, 'error');
      }
    });
  }
}

// Pipeline Execution & SSE Telemetry
async function triggerPipelineRun() {
  const statusBadge = document.getElementById('pipeline-status-text');
  const runBtns = [document.getElementById('header-run-btn'), document.getElementById('trigger-run-btn')];

  runBtns.forEach((b) => { if (b) b.disabled = true; });
  if (statusBadge) {
    statusBadge.className = 'badge badge-running';
    statusBadge.textContent = 'RUNNING...';
  }

  // Reset stepper nodes
  document.querySelectorAll('.step-node').forEach((n) => {
    n.className = 'step-node';
    const b = n.querySelector('.node-badge');
    if (b) b.textContent = 'Waiting';
  });
  document.querySelectorAll('.step-connector').forEach((c) => c.classList.remove('completed'));

  try {
    const d = await authedPost(API.runPipeline, { mode: 'full' });
    logToTerminal(`Scripted playback started (Run ID: ${d.run_id})`, 'info');
  } catch (err) {
    logToTerminal(`Trigger refused: ${err.message}`, 'error');
    runBtns.forEach((b) => { if (b) b.disabled = false; });
    if (statusBadge) {
      statusBadge.className = 'badge badge-idle';
      statusBadge.textContent = 'STATUS: IDLE';
    }
  }
}

function initTelemetrySSE() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource(API.events);

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handlePipelineEvent(data);
    } catch (e) {
      console.error('Error parsing SSE event:', e);
    }
  };

  eventSource.onerror = () => {
    // SSE auto-reconnects
  };
}

function handlePipelineEvent(e) {
  const statusBadge = document.getElementById('pipeline-status-text');

  switch (e.type) {
    case 'pipeline_start':
      logToTerminal(`[PIPELINE START] ${e.message}`, 'info');
      break;

    case 'stage_start': {
      const node = document.getElementById(`node-${e.stage}`);
      if (node) {
        node.className = 'step-node running';
        const badge = node.querySelector('.node-badge');
        if (badge) badge.textContent = 'Running';
      }
      break;
    }

    case 'stage_log':
      logToTerminal(`[${e.stage.toUpperCase()}] ${e.message}`, e.level || 'info');
      break;

    case 'stage_complete': {
      const node = document.getElementById(`node-${e.stage}`);
      if (node) {
        node.className = 'step-node completed';
        const badge = node.querySelector('.node-badge');
        if (badge) badge.textContent = `${e.duration_ms}ms`;

        // Mark connector after node if present
        const connector = node.nextElementSibling;
        if (connector && connector.classList.contains('step-connector')) {
          connector.classList.add('completed');
        }
      }
      break;
    }

    case 'pipeline_complete': {
      logToTerminal(`[PIPELINE COMPLETE] ${e.message} (Reward: ${e.final_reward})`, 'success');
      if (statusBadge) {
        statusBadge.className = 'badge badge-idle';
        statusBadge.textContent = 'STATUS: IDLE';
      }
      const runBtns = [document.getElementById('header-run-btn'), document.getElementById('trigger-run-btn')];
      runBtns.forEach((b) => { if (b) b.disabled = false; });

      // Refresh stats and catalog
      loadAllData();
      break;
    }

    case 'pipeline_error': {
      logToTerminal(`[PIPELINE HALTED] ${e.message}`, 'error');
      if (statusBadge) {
        statusBadge.className = 'badge badge-idle';
        statusBadge.textContent = 'STATUS: HALTED';
      }
      const runBtns = [document.getElementById('header-run-btn'), document.getElementById('trigger-run-btn')];
      runBtns.forEach((b) => { if (b) b.disabled = false; });
      break;
    }
  }
}

function logToTerminal(msg, level = 'info') {
  const terminal = document.getElementById('terminal-body');
  if (!terminal) return;

  const div = document.createElement('div');
  div.className = `log-line ${level}`;
  div.textContent = `${new Date().toLocaleTimeString()} [DEMO] ${msg}`;
  terminal.appendChild(div);
  terminal.scrollTop = terminal.scrollHeight;
}

// Audio Player & Waveform
function initPlayer() {
  const audio = document.getElementById('global-audio-element');
  const playBtn = document.getElementById('player-play-btn');
  const playIcon = document.getElementById('play-icon');
  const pauseIcon = document.getElementById('pause-icon');
  const seekSlider = document.getElementById('seek-slider');
  const volumeSlider = document.getElementById('volume-slider');
  const currentTimeEl = document.getElementById('current-time');
  const totalTimeEl = document.getElementById('total-time');

  if (!audio || !playBtn) return;

  playBtn.addEventListener('click', () => {
    if (!currentTrack) return;
    if (audio.paused) {
      audio.play();
      isAudioPlaying = true;
      playIcon.classList.add('hidden');
      pauseIcon.classList.remove('hidden');
    } else {
      audio.pause();
      isAudioPlaying = false;
      playIcon.classList.remove('hidden');
      pauseIcon.classList.add('hidden');
    }
  });

  audio.addEventListener('timeupdate', () => {
    if (!audio.duration) return;
    const pct = (audio.currentTime / audio.duration) * 100;
    if (seekSlider) seekSlider.value = pct;
    if (currentTimeEl) currentTimeEl.textContent = formatTime(audio.currentTime);
  });

  audio.addEventListener('loadedmetadata', () => {
    if (totalTimeEl) totalTimeEl.textContent = formatTime(audio.duration);
  });

  audio.addEventListener('ended', () => {
    isAudioPlaying = false;
    playIcon.classList.remove('hidden');
    pauseIcon.classList.add('hidden');
  });

  if (seekSlider) {
    seekSlider.addEventListener('input', () => {
      if (audio.duration) {
        audio.currentTime = (seekSlider.value / 100) * audio.duration;
      }
    });
  }

  if (volumeSlider) {
    volumeSlider.addEventListener('input', () => {
      audio.volume = Number(volumeSlider.value);
    });
  }

  initWaveformVisualizer();
}

function playCandidateAudio(candidateId, title, genre) {
  const audio = document.getElementById('global-audio-element');
  const titleEl = document.getElementById('player-title');
  const metaEl = document.getElementById('player-meta');
  const playIcon = document.getElementById('play-icon');
  const pauseIcon = document.getElementById('pause-icon');

  currentTrack = candidateId;
  if (titleEl) titleEl.textContent = title;
  if (metaEl) metaEl.textContent = `${genre} • Synthesized Audio Stream`;

  audio.src = `/api/audio/${candidateId}`;
  audio.play().then(() => {
    isAudioPlaying = true;
    if (playIcon) playIcon.classList.add('hidden');
    if (pauseIcon) pauseIcon.classList.remove('hidden');
  }).catch((err) => console.log('Audio autoplay policy caught:', err));
}

function initWaveformVisualizer() {
  const canvas = document.getElementById('waveform-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  function renderWave() {
    requestAnimationFrame(renderWave);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const bars = 48;
    const barWidth = canvas.width / bars;
    const t = Date.now() / 200;

    for (let i = 0; i < bars; i++) {
      let h = 4;
      if (isAudioPlaying) {
        h = 4 + Math.sin(t + i * 0.4) * 10 + Math.cos(t * 1.5 + i * 0.2) * 8;
        h = Math.max(3, Math.min(26, h));
      }
      ctx.fillStyle = isAudioPlaying ? '#8b5cf6' : 'rgba(255,255,255,0.1)';
      ctx.fillRect(i * barWidth + 2, (canvas.height - h) / 2, barWidth - 4, h);
    }
  }
  renderWave();
}

// Modal Inspector
function initModal() {
  const modal = document.getElementById('inspector-modal');
  const closeBtn = document.getElementById('modal-close-btn');

  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      if (modal) modal.classList.add('hidden');
    });
  }

  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.classList.add('hidden');
    });
  }
}

function showManifestModal(candidateId, title) {
  const modal = document.getElementById('inspector-modal');
  const titleEl = document.getElementById('modal-title');
  const preEl = document.getElementById('modal-json');

  if (titleEl) titleEl.textContent = `Release Manifest: ${title} (${candidateId})`;

  const sampleManifest = {
    schema_version: "1.0",
    seam: "C5",
    ruling: "R7_R8",
    p5_manifest_id: `rel_man_${candidateId}`,
    production_directive_id: "dir_71829",
    experiment_id: "exp_81723",
    experiment_family_id: "fam_synthwave_v1",
    content_arm_id: "arm_synthwave_night",
    content_genome_id: "gen_88192",
    media_package_id: "med_pkg_91823",
    verdict: "PASSED",
    technical_evaluation: {
      lufs: -14.1,
      true_peak_dbfs: -1.2,
      duration_seconds: 184.0,
      channels: 2,
      sample_rate_hz: 44100
    },
    rights_snapshot: {
      tier: "commercial_pro",
      synthetic_media_disclosure: true,
      license_id: "lic_8819203",
      commercial_cleared: true
    },
    lineage_verified: true,
    created_at: new Date().toISOString()
  };

  if (preEl) preEl.textContent = JSON.stringify(sampleManifest, null, 2);
  if (modal) modal.classList.remove('hidden');
}

// Helpers
function formatTime(secs) {
  if (!secs || isNaN(secs)) return '0:00';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  return `${m}:${s < 10 ? '0' : ''}${s}`;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
