// PLM Control Deck: the six PART XVII views over live data (ES module, no dependencies).
//
// Every value is rendered with textContent. A null value is shown as "unknown"; a panel
// whose source could not answer is shown as "unavailable" with the source and reason.
// Exception actions go to the R3 exception queue; the server enforces every hold-class
// rule, and this page only mirrors them (a HARD_STOP never shows an approve button).

const TOKEN_HEADER = 'X-PLM-Service-Token';
const TOKEN_KEY = 'plmServiceToken';
const VIEWS = ['studio', 'command-center', 'market-map', 'production-queue', 'experiment-board', 'economics', 'exceptions'];

let currentView = 'studio';
let reviewers = { configured: false, reviewers: [] };

// ------------------------------------------------------------------------ helpers

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') el.className = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? '' : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function unknown(label = 'unknown') { return h('span', { class: 'unknown' }, label); }

function val(value, format = (v) => String(v)) {
  return value === null || value === undefined ? unknown() : format(value);
}

function money(list) {
  if (!list || !list.length) return unknown('not reported');
  return list.map((m) => `${m.amount} ${m.currency}`).join(' + ');
}

function moneyKnown(summary) {
  if (!summary) return unknown();
  const meta = `${summary.entities_known} known, ${summary.entities_unknown} unknown`;
  return h('span', {}, money(summary.value), h('span', { class: 'muted' }, ` (${meta})`));
}

function countKnown(summary) {
  if (!summary) return unknown();
  const value = summary.value === null ? unknown('no measurement') : summary.value.toLocaleString();
  return h('span', {}, value, h('span', { class: 'muted' }, ` (${summary.entities_known} known, ${summary.entities_unknown} unknown)`));
}

function num(value, digits = 3) {
  return value === null || value === undefined ? unknown() : Number(value).toFixed(digits);
}

function kpi(label, value, meta) {
  return h('div', { class: 'kpi' }, h('div', { class: 'label' }, label), h('div', { class: 'value' }, value), meta ? h('div', { class: 'meta' }, meta) : null);
}

function table(headers, rows) {
  if (!rows.length) return h('p', { class: 'muted' }, 'No rows.');
  return h('div', { class: 'table-wrap' }, h('table', {},
    h('thead', {}, h('tr', {}, headers.map((x) => h('th', {}, x)))),
    h('tbody', {}, rows.map((r) => h('tr', {}, r.map((c) => h('td', {}, c)))))));
}

// A panel: rendered by `render(data)` when ok, else an "unavailable" card.
function panel(title, p, render) {
  if (!p) return h('div', { class: 'card unavailable' }, h('h2', {}, title), h('p', { class: 'reason' }, 'no panel in the response'));
  if (p.status !== 'ok') {
    return h('div', { class: 'card unavailable' },
      h('h2', {}, title),
      h('div', { class: 'source' }, `source: ${p.source}`),
      h('p', {}, h('strong', {}, 'Unavailable. '), 'Nothing is shown in its place.'),
      h('p', { class: 'reason' }, p.reason));
  }
  return h('div', { class: 'card' }, h('h2', {}, title), h('div', { class: 'source' }, `source: ${p.source}`), render(p.data));
}

function readToken() {
  // The page carries the token when the dashboard serves it locally (no prompt needed).
  const meta = document.querySelector('meta[name="plm-service-token"]');
  if (meta && meta.content) return meta.content;
  try { return sessionStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
}
function storeToken(t) { try { sessionStorage.setItem(TOKEN_KEY, t); } catch (e) { /* unavailable */ } }

async function getJson(url) {
  const res = await fetch(url);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(`${res.status} ${(body.detail && body.detail.code) || res.statusText}`);
  return body;
}

// POST with the service token; returns {ok, status, body}. Refusals are returned, not thrown.
async function authedPost(url, payload) {
  const send = (token) => fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { [TOKEN_HEADER]: token } : {}) },
    body: JSON.stringify(payload),
  });
  let res = await send(readToken());
  if (res.status === 401) {
    const entered = window.prompt('Service token (PLM_SERVICE_TOKEN) required for this action:');
    if (entered) { storeToken(entered); res = await send(entered); }
  }
  const body = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, body };
}

function describeOutcome(out) {
  if (!out.ok) {
    const d = out.body.detail || {};
    const fields = d.field_paths && d.field_paths.length ? `\nfields: ${d.field_paths.join(', ')}` : '';
    return { cls: 'refused', text: `REFUSED (${out.status}) ${d.code || ''}\n${d.message || ''}${fields}` };
  }
  const s = out.body.signal || {};
  let signal = '';
  if (s.status === 'sent') signal = `signal "${s.signal}" sent to ${s.workflow_id}`;
  else if (s.status === 'pending') signal = `decision recorded; signal "${s.signal}" PENDING: ${s.reason}`;
  else if (s.status === 'deferred') signal = `signal deferred: ${s.reason} (${(s.open_case_ids || []).join(', ')})`;
  else if (s.status === 'not_applicable') signal = 'not a pipeline hold: no signal needed';
  const c = out.body.case || {};
  return { cls: 'done', text: `${c.status || 'done'} ${c.resolution ? `(${c.resolution})` : ''}\n${signal}` };
}

// ------------------------------------------------------------------------ views

const renderers = {
  'command-center': (v) => {
    const p = v.panels;
    return h('div', {},
      panel('Revenue', p.revenue, (d) => h('div', {},
        h('div', { class: 'kpis' },
          kpi('Realized (statements)', moneyKnown(d.realized)),
          kpi('Estimated (YouTube, not booked)', moneyKnown(d.estimated)),
          kpi('Unattributed realized', money(d.unattributed_realized && d.unattributed_realized.value),
            d.unattributed_realized ? `${d.unattributed_realized.statements} statements, ${d.unattributed_realized.statements_without_currency} without currency` : null)),
        d.blocked_entities.length ? h('p', { class: 'notice' }, `Blocked by P9: ${d.blocked_entities.map((b) => `${b.entity_id} (${b.code})`).join(', ')}`) : null,
        h('ul', { class: 'muted' }, d.notes.map((n) => h('li', {}, n))))),
      h('div', { class: 'grid' },
        panel('Audience (lifetime window)', p.audience, (d) => h('div', { class: 'kpis' },
          kpi('Views', countKnown(d.views)),
          kpi('Streams', countKnown(d.streams)),
          kpi('Subscribers (net)', countKnown(d.subscribers_net)))),
        panel('Catalog', p.catalog, (d) => h('div', { class: 'kpis' },
          kpi('Published videos', d.published_videos),
          kpi('Distributed tracks', d.distributed_tracks),
          kpi('Releases by status', Object.keys(d.releases_by_status).length
            ? Object.entries(d.releases_by_status).map(([k, n]) => `${k}: ${n}`).join(', ') : unknown('none'))))),
      panel('Arms by posterior', p.arms, (d) => {
        const rows = (list) => list.map((a) => [h('span', { class: 'mono' }, a.arm_id), a.status, num(a.posterior.mean), num(a.posterior.variance, 4), a.posterior.observation_count]);
        return h('div', {},
          h('p', { class: 'muted' }, `${d.reward_label}. ${d.arms_with_observations} of ${d.arms_total} arms have an observed reward; ${d.arms_prior_only} prior only; ${d.arms_without_posterior} without a posterior.`),
          h('h3', {}, 'Best'), table(['Arm', 'Status', 'Mean', 'Variance', 'Obs.'], rows(d.best)),
          h('h3', {}, 'Worst'), table(['Arm', 'Status', 'Mean', 'Variance', 'Obs.'], rows(d.worst)));
      }));
  },

  'market-map': (v) => panel('Demand vs competition vs growth (P1)', v.panels.opportunities, (d) => {
    const bar = (x) => (x === null || x === undefined ? unknown() : h('div', { class: 'bar', title: String(x) }, h('span', { style: `width:${Math.max(0, Math.min(1, x)) * 100}%` })));
    return h('div', {},
      h('p', { class: 'muted' }, `as of ${d.as_of || 'unknown'}; score version ${d.score_version || 'unknown'}${d.truncated ? '; list truncated' : ''}. A component the evidence does not support is unknown.`),
      table(['Arm', 'Genre', 'Score', 'Conf.', 'Demand', 'Competition', 'Growth', 'Unknown', 'Obs.'],
        d.items.map((i) => [h('span', { class: 'mono' }, i.arm_id), val(i.genre), num(i.score, 1), num(i.confidence, 2),
          h('span', {}, bar(i.demand), ' ', num(i.demand)), h('span', {}, bar(i.competition), ' ', num(i.competition)),
          h('span', {}, bar(i.growth), ' ', num(i.growth)), i.unknown_components.join(', ') || '-', val(i.observation_count)])));
  }),

  'production-queue': (v) => panel('Pipeline runs (PART XIV)', v.panels.runs, (d) => {
    const runRow = (r) => [h('span', { class: 'mono' }, r.workflow_id), h('span', { class: 'badge badge-state' }, r.state),
      val(r.held_from), val(r.last_step), r.last_error_code ? h('span', {}, r.last_error_code, h('div', { class: 'muted' }, r.last_error_detail || '')) : '-',
      r.holds.length ? h('div', {}, r.holds.map((x) => h('div', {}, h('span', { class: `badge ${x.hold_class === 'HARD_STOP' ? 'badge-hard' : 'badge-review'}` }, x.hold_class), ` ${x.reason_code} (${x.status})`))) : '-',
      r.awaiting_signal ? resendButton(r.workflow_id) : '-', val(r.updated_at)];
    return h('div', {},
      h('div', { class: 'chips' }, d.by_state.length ? d.by_state.map((s) => h('span', { class: 'badge badge-state' }, `${s.state}: ${s.count}`)) : h('span', { class: 'muted' }, 'No pipeline runs recorded.')),
      d.unknown_states.length ? h('p', { class: 'notice' }, `States outside PART XIV: ${d.unknown_states.join(', ')}`) : null,
      h('h3', {}, `Active (${d.active.length})`),
      table(['Run', 'State', 'Held from', 'Last step', 'Error / reason', 'Holds', 'Signal', 'Updated'], d.active.map(runRow)),
      h('h3', {}, 'Recently finished'),
      table(['Run', 'State', 'Held from', 'Last step', 'Error / reason', 'Holds', 'Signal', 'Updated'], d.recent_finished.map(runRow)));
  }),

  'experiment-board': (v) => h('div', {},
    panel('Arms and posteriors (P2)', v.panels.arms, (d) => h('div', {},
      h('p', { class: 'muted' }, `${d.reward_label}. ${d.arms_excluded} arm(s) in ${d.excluded_statuses.join('/')} not shown.`),
      table(['Arm', 'Status', 'Posterior mean', 'Variance', 'Obs.', 'Eff. obs.', 'Selected', 'Updated'],
        d.arms.map((a) => [h('span', { class: 'mono' }, a.arm_id), a.status,
          a.posterior ? num(a.posterior.mean) : unknown('no posterior'), a.posterior ? num(a.posterior.variance, 4) : unknown(),
          a.posterior ? a.posterior.observation_count : unknown(), a.posterior ? num(a.posterior.effective_observation_count, 2) : unknown(),
          a.selection_count, a.posterior ? val(a.posterior.last_update_at) : unknown()])))),
    panel('Experiments (P3)', v.panels.experiments, (d) => h('div', {},
      h('div', { class: 'chips' }, Object.entries(d.by_status).map(([k, n]) => h('span', { class: 'badge' }, `${k}: ${n}`))),
      table(['Experiment', 'Arm', 'Type', 'Status', 'Treatments', 'Horizon', 'Created'],
        d.active.map((e) => [h('span', { class: 'mono' }, e.experiment_id), h('span', { class: 'mono' }, e.content_arm_id), val(e.experiment_type), e.status, e.treatments, val(e.outcome_horizon), val(e.created_at)])))))
  ,

  economics: (v) => panel('Revenue, cost and ROI (P9)', v.panels.economics, (d) => {
    const group = (key) => (rows) => table([key, 'Entities', 'Realized', 'Estimated', 'Cost', 'Net', 'ROI', 'Views', 'Streams'],
      rows.map((g) => [g[key], g.entities, moneyKnown(g.realized_revenue), moneyKnown(g.estimated_revenue), moneyKnown(g.allocated_cost),
        g.net_contribution.value ? money(g.net_contribution.value) : unknown(`blocked for ${g.net_contribution.entities_blocked}`),
        val(g.roi), countKnown(g.views), countKnown(g.streams)]));
    const entities = (rows) => table(['Entity', 'Realized', 'Estimated', 'Cost', 'Net', 'ROI', 'Views/Streams'],
      rows.map((r) => [h('span', { class: 'mono' }, r.entity_id), r.realized_revenue ? money([r.realized_revenue]) : unknown('not reported'),
        r.estimated_revenue ? money([r.estimated_revenue]) : unknown(), r.allocated_cost ? money([r.allocated_cost]) : unknown(),
        r.net_contribution ? money([r.net_contribution]) : unknown(r.net_contribution_blocked_reason || 'unknown'), val(r.roi),
        val(r.entity_type === 'video' ? r.views : r.streams)]));
    return h('div', {},
      h('p', { class: 'muted' }, `Reporting currency: ${d.reporting_currencies.join(', ') || 'none reported'}. ${d.entities_total} entities.`),
      h('ul', { class: 'muted' }, d.notes.map((n) => h('li', {}, n))),
      h('h3', {}, 'By genre'), group('genre')(d.by_genre),
      h('h3', {}, 'By channel'), group('channel_id')(d.by_channel),
      h('h3', {}, 'By track'), entities(d.by_track),
      h('h3', {}, 'By video'), entities(d.by_video),
      d.blocked.length ? h('div', {}, h('h3', {}, 'Blocked by P9'), table(['Entity', 'Type', 'Code', 'Message'], d.blocked.map((b) => [b.entity_id, b.entity_type, b.code, b.message]))) : null);
  }),

  exceptions: (v) => {
    const rv = v.panels.reviewers && v.panels.reviewers.data;
    const notice = rv && !rv.configured
      ? h('div', { class: 'card unavailable' }, h('p', { class: 'reason' }, 'No reviewer directory is configured (PLM_DASHBOARD_REVIEWERS_FILE): every action below is refused.'))
      : null;
    return h('div', {}, notice, panel('Open holds requiring human intervention', v.panels.open, (d) => {
      document.getElementById('exceptions-count').textContent = `(${d.count})`;
      if (!d.items.length) return h('p', { class: 'muted' }, 'No open holds.');
      return h('div', {}, d.items.map(caseCard));
    }));
  },
};

function reviewerSelect(role) {
  const eligible = reviewers.reviewers.filter((r) => r.roles.includes(role));
  const all = reviewers.reviewers;
  const select = h('select', { name: 'actor', required: true },
    h('option', { value: '' }, eligible.length ? 'choose reviewer' : `no reviewer holds ${role}`),
    all.map((r) => h('option', { value: r.actor }, `${r.actor} (${r.roles.join(', ')})`)));
  return select;
}

function actionForm({ title, cls, fields, submit, onSubmit }) {
  const result = h('div', { class: 'result' });
  const form = h('form', { class: 'action' }, h('strong', {}, title), fields, h('div', {}, h('button', { class: `btn ${cls}`, type: 'submit' }, submit)), result);
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const data = new FormData(form);
    result.className = 'result';
    result.textContent = 'sending…';
    try {
      const out = await onSubmit(data);
      const shown = describeOutcome(out);
      result.className = `result ${shown.cls}`;
      result.textContent = shown.text;
      if (out.ok) setTimeout(() => loadView('exceptions'), 1500);
    } catch (e) {
      result.className = 'result refused';
      result.textContent = String(e);
    }
  });
  return form;
}

function evidenceRows(names) {
  return names.map((n) => h('div', { class: 'row' }, h('label', {}, `evidence: ${n}`), h('input', { name: `evidence:${n}`, required: true, placeholder: 'reference (id, URL, document)' })));
}

function collectEvidence(data) {
  const evidence = {};
  for (const [k, v] of data.entries()) if (k.startsWith('evidence:') && String(v).trim()) evidence[k.slice(9)] = String(v).trim();
  return evidence;
}

function caseCard(c) {
  const hard = c.hold_class === 'HARD_STOP';
  const base = `/api/exceptions/${encodeURIComponent(c.case_id)}`;
  const roleRow = h('div', { class: 'row' }, h('label', {}, 'acting as role'), h('input', { name: 'role', value: c.reviewer_role, readonly: true }));
  const actorRow = () => h('div', { class: 'row' }, h('label', {}, 'reviewer'), reviewerSelect(c.reviewer_role));
  const notesRow = () => h('div', { class: 'row' }, h('label', {}, 'notes'), h('input', { name: 'notes' }));
  const forms = [];
  if (c.allowed_actions.includes('resume')) {
    forms.push(actionForm({ title: 'Release (resume the held work)', cls: 'btn-ok', submit: 'Resume', fields: [actorRow(), roleRow.cloneNode(true), ...evidenceRows(c.required_evidence), notesRow()],
      onSubmit: (d) => authedPost(`${base}/resume`, { actor: d.get('actor'), role: d.get('role'), evidence: collectEvidence(d), notes: d.get('notes') || '' }) }));
  }
  if (c.allowed_actions.includes('record_external_resolution')) {
    const outcome = h('div', { class: 'row' }, h('label', {}, 'external outcome'), h('select', { name: 'outcome' }, h('option', { value: 'CLEARED' }, 'CLEARED'), h('option', { value: 'ADVERSE' }, 'ADVERSE')));
    forms.push(actionForm({ title: 'Record the external process\'s resolution (the only exit of a hard stop)', cls: '', submit: 'Record resolution', fields: [actorRow(), roleRow.cloneNode(true), outcome, ...evidenceRows(c.required_evidence), notesRow()],
      onSubmit: (d) => authedPost(`${base}/external-resolution`, { actor: d.get('actor'), role: d.get('role'), outcome: d.get('outcome'), evidence: collectEvidence(d), notes: d.get('notes') || '' }) }));
  }
  if (c.allowed_actions.includes('reject')) {
    forms.push(actionForm({ title: 'Discard (reject the held work)', cls: 'btn-danger', submit: 'Reject', fields: [actorRow(), roleRow.cloneNode(true), notesRow()],
      onSubmit: (d) => authedPost(`${base}/reject`, { actor: d.get('actor'), role: d.get('role'), notes: d.get('notes') || '' }) }));
  }
  return h('div', { class: `case ${hard ? 'hard' : ''}` },
    h('div', { class: 'case-head' },
      h('div', {}, h('span', { class: `badge ${hard ? 'badge-hard' : 'badge-review'}` }, c.hold_class), ' ', h('strong', {}, c.reason_code), ' ', h('span', { class: 'badge' }, c.severity)),
      h('span', { class: 'mono muted' }, c.case_id)),
    c.description ? h('p', {}, c.description) : null,
    h('dl', {},
      h('dt', {}, 'Resolution path'), h('dd', {}, c.resolution_path),
      h('dt', {}, 'Approve available'), h('dd', {}, c.approve_available ? 'yes' : 'no'),
      h('dt', {}, 'Reviewer role'), h('dd', { class: 'mono' }, c.reviewer_role),
      h('dt', {}, 'Required evidence'), h('dd', { class: 'mono' }, c.required_evidence.join(', ')),
      h('dt', {}, 'Origin / subject'), h('dd', { class: 'mono' }, `${c.origin_phase} / ${c.subject_ref}`),
      h('dt', {}, 'Raised'), h('dd', {}, `${c.raised_at} by ${c.raised_by}`),
      h('dt', {}, 'Policy'), h('dd', {}, `${c.policy_version}${c.policy_mapped ? '' : ' (UNMAPPED reason code: treated as hard stop)'}`),
      c.pipeline ? [h('dt', {}, 'Pipeline run'), h('dd', { class: 'mono' }, `${c.pipeline.workflow_id} (${c.pipeline.run_state || 'state unknown'}) step ${c.pipeline.step}`)] : null,
      Object.keys(c.details).length ? [h('dt', {}, 'Details'), h('dd', { class: 'mono' }, JSON.stringify(c.details))] : null),
    forms);
}

function resendButton(workflowId) {
  const result = h('div', { class: 'result' });
  const select = h('select', {}, h('option', { value: '' }, 'reviewer'), reviewers.reviewers.map((r) => h('option', { value: r.actor }, r.actor)));
  const button = h('button', { class: 'btn', type: 'button', onclick: async () => {
    const out = await authedPost(`/api/pipeline/runs/${encodeURIComponent(workflowId)}/signal`, { actor: select.value });
    const shown = describeOutcome(out);
    result.className = `result ${shown.cls}`;
    result.textContent = shown.text;
  } }, 'Send pending signal');
  return h('div', {}, h('p', { class: 'notice' }, 'Holds closed; the run awaits its signal.'), select, ' ', button, result);
}

// ------------------------------------------------------------------------ studio
//
// The owner's steps of a run (dashboard/studio.py): songs, thumbnails, review. Uploads
// are sent as raw bytes with the service token; the server checks every file.

async function authedSend(url, method, body, contentType) {
  const send = (token) => fetch(url, {
    method, body,
    headers: { ...(contentType ? { 'Content-Type': contentType } : {}), ...(token ? { [TOKEN_HEADER]: token } : {}) },
  });
  let res = await send(readToken());
  if (res.status === 401) {
    const entered = window.prompt('Service token (PLM_SERVICE_TOKEN) required for this action:');
    if (entered) { storeToken(entered); res = await send(entered); }
  }
  const out = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, body: out };
}

function refusal(out) {
  const d = out.body.detail || {};
  return `REFUSED (${out.status}) ${d.code || ''}: ${d.message || ''}`;
}

function copyButton(text) {
  const button = h('button', { class: 'btn', type: 'button', onclick: async () => {
    try { await navigator.clipboard.writeText(text); button.textContent = 'Copied'; }
    catch { button.textContent = 'Copy failed: select the text'; }
    setTimeout(() => { button.textContent = 'Copy prompt'; }, 1500);
  } }, 'Copy prompt');
  return button;
}

function actionButton(label, cls, run, { confirm: question, busy } = {}) {
  const result = h('div', { class: 'result' });
  const button = h('button', { class: `btn ${cls || ''}`, type: 'button', onclick: async () => {
    if (question && !window.confirm(question)) return;
    button.disabled = true;
    result.className = 'result';
    result.textContent = busy || 'Working...';
    const out = await run();
    button.disabled = false;
    if (!out.ok) { result.className = 'result refused'; result.textContent = refusal(out); return; }
    result.className = 'result done';
    result.textContent = 'Done.';
    setTimeout(() => loadView('studio'), 1200);
  } }, label);
  return h('span', {}, button, ' ', result);
}

function uploadInput(accept, url) {
  const result = h('span', { class: 'result' });
  const input = h('input', { type: 'file', accept, onchange: async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    result.className = 'result';
    result.textContent = `uploading ${file.name}...`;
    const out = await authedSend(url, 'PUT', file, 'application/octet-stream');
    if (!out.ok) { result.className = 'result refused'; result.textContent = refusal(out); return; }
    result.className = 'result done';
    result.textContent = `saved as ${out.body.saved}`;
    setTimeout(() => loadView('studio'), 800);
  } });
  return h('span', {}, input, ' ', result);
}

function stepCard(title, waitingCount) {
  return h('section', { class: `step ${waitingCount ? 'waiting' : ''}` },
    h('div', { class: 'step-head' }, h('h2', {}, title),
      waitingCount ? h('span', { class: 'badge badge-review' }, 'waiting for you') : h('span', { class: 'badge' }, 'nothing waiting')));
}

function songsStep(songs, waiting, notes) {
  const card = stepCard('1. Songs', (waiting.MANUAL_AUDIO_AWAITED || []).length);
  const swaps = noteList((notes || []).filter((n) => n.kind === 'replace_song').slice(-3), 'Your notes for replaced songs');
  if (swaps) card.append(swaps);
  if (!songs.configured) {
    card.append(h('p', { class: 'notice' }, 'The music inbox is not configured (PLM_FLOW_MUSIC_MANUAL_INBOX).'));
    return card;
  }
  if (!songs.sheets.length) {
    if ((waiting.MANUAL_AUDIO_AWAITED || []).length) {
      // Every song is saved but the run has not been told yet: offer Continue.
      card.append(h('p', { class: 'saved' }, 'All songs are saved. Press Continue to send them to the run.'),
        h('div', { class: 'row-actions' },
          actionButton('Continue', 'btn-ok big', () => authedSend('/api/studio/songs/done', 'POST'),
            { busy: 'Sending the songs to the run... P4 checks every file.' })));
    } else {
      card.append(h('p', { class: 'muted' }, 'No song is waiting.'));
    }
    return card;
  }
  card.append(h('p', { class: 'muted' }, 'Make each song in the tool its sheet names (Suno or Flow Music) from its prompt, download it as WAV and upload it here. When every song is saved, press Continue.'));
  let missing = 0;
  for (const sheet of songs.sheets) {
    card.append(h('h3', {}, `${sheet.source || ''} ${sheet.source ? '- ' : ''}${sheet.sheet}`));
    for (const song of sheet.songs) {
      if (!song.saved) missing += 1;
      card.append(h('div', { class: 'song' },
        h('div', { class: 'step-head' }, h('strong', { class: 'mono' }, `${song.stem}.wav`),
          song.saved ? h('span', { class: 'saved' }, 'saved') : h('span', { class: 'missing' }, 'missing')),
        h('div', { class: 'prompt' }, song.text),
        h('div', { class: 'row-actions' }, copyButton(song.text), uploadInput('.wav,audio/wav', `/api/studio/songs/${encodeURIComponent(song.stem)}`)),
        song.lyrics_needed && !song.lyrics_saved ? h('p', { class: 'notice' }, `This song has vocals: save its lyrics as ${song.stem}.lyrics.txt in ${songs.inbox}.`) : null));
    }
  }
  card.append(h('div', { class: 'row-actions' },
    actionButton(missing ? `Continue (${missing} still missing)` : 'Continue', 'btn-ok big',
      () => authedSend('/api/studio/songs/done', 'POST'), { busy: 'Sending the songs to the run...' })));
  return card;
}

function thumbnailsStep(jobs, waiting) {
  const card = stepCard('2. Thumbnails', (waiting.THUMBNAIL_ART_AWAITED || []).length);
  if (!jobs.length) { card.append(h('p', { class: 'muted' }, 'No thumbnail is waiting.')); return card; }
  card.append(h('p', { class: 'muted' }, 'Make each image in Nano Banana Pro (16:9, full size) and upload it. Image A is also the picture of the whole video. Import checks all three and renders the video; it can take a few minutes.'));
  for (const job of jobs) {
    card.append(h('h3', {}, job.title || job.media_package_id));
    const jobNotes = noteList(job.notes, 'Your note for the new art');
    if (jobNotes) card.append(jobNotes);
    for (const from of job.reuse_from || []) {
      card.append(h('div', { class: 'row-actions' }, actionButton(`Reuse the images of the previous version`, 'btn',
        () => postJson(`/api/studio/thumbnails/${job.media_package_id}/reuse`, { from_package_id: from }))));
    }
    for (const v of job.variants) {
      const url = `/api/studio/thumbnails/${job.media_package_id}/${v.letter}`;
      card.append(h('div', { class: 'variant' },
        h('div', { class: 'step-head' }, h('strong', {}, `${v.letter}: ${v.style_id} ("${v.text}")`),
          v.saved.length ? h('span', { class: 'saved' }, `saved: ${v.saved.join(', ')}`) : h('span', { class: 'missing' }, 'missing')),
        h('div', { class: 'prompt' }, v.prompt || ''),
        h('div', { class: 'row-actions' }, copyButton(v.prompt || ''), uploadInput('image/png,image/jpeg,image/webp', url)),
        v.saved.length ? h('img', { class: 'preview', src: `${url}?t=${Date.now()}`, alt: `thumbnail ${v.letter}` }) : null));
    }
    const ready = job.variants.every((v) => v.saved.length);
    card.append(h('div', { class: 'row-actions' }, ready
      ? actionButton('Import and make the video', 'btn-ok big', () => authedSend(`/api/studio/thumbnails/${job.media_package_id}/import`, 'POST'),
        { busy: 'Checking the images and rendering the still video... this can take several minutes.' })
      : h('span', { class: 'muted' }, 'Upload A, B and C to import.')));
  }
  return card;
}

function postJson(url, payload) {
  return authedSend(url, 'POST', JSON.stringify(payload), 'application/json');
}

function noteList(notes, title) {
  if (!notes || !notes.length) return null;
  return h('div', { class: 'notes' }, h('strong', {}, title),
    notes.map((n) => h('p', { class: 'note' }, h('span', { class: 'muted' }, `${(n.at || '').slice(0, 16).replace('T', ' ')} `), n.note)));
}

function changesPanel(r, p) {
  const caseUrl = `/api/studio/approvals/${r.case_id}/changes`;
  const note = h('textarea', { rows: 2, placeholder: 'Your note: what should change and why' });
  const title = h('input', { type: 'text', maxlength: 100, value: p.title || '' });
  const description = h('textarea', { rows: 8 });
  description.value = p.description || '';
  const tags = h('input', { type: 'text', value: (p.tags || []).join(', ') });
  const thumb = h('select', {}, p.thumbnails.map((t, i) => h('option', { value: t }, i === 0 ? `${t} (now first)` : t)));
  const songs = h('div', { class: 'songs-pick' }, (p.tracks || []).map((t) => h('label', { class: 'pick' },
    h('input', { type: 'checkbox', value: t.song_id }), ` ${t.start} ${t.title}`)));
  const picked = () => [...songs.querySelectorAll('input:checked')].map((i) => i.value);
  return h('details', { class: 'changes' },
    h('summary', {}, 'Request changes'),
    h('p', { class: 'muted' }, 'Text and thumbnail changes apply now and the video stays here for approval. New art goes back to the thumbnail step; a replaced song goes back to the songs step. Translations keep their own text.'),
    h('label', {}, 'Note'), note,
    h('label', {}, 'Title'), title,
    h('label', {}, 'Description (keep every chapter line)'), description,
    h('label', {}, 'Tags (comma separated)'), tags,
    h('div', { class: 'row-actions' }, actionButton('Save text', 'btn-ok', () => postJson(`${caseUrl}/text`, {
      package_id: p.media_package_id, title: title.value, description: description.value,
      tags: tags.value.split(',').map((t) => t.trim()).filter(Boolean), note: note.value }))),
    h('label', {}, 'Thumbnail shown first on YouTube (the video picture stays image A)'),
    h('div', { class: 'row-actions' }, thumb, actionButton('Use this thumbnail', 'btn', () => postJson(`${caseUrl}/thumbnail`, {
      package_id: p.media_package_id, thumbnail_id: thumb.value, note: note.value }))),
    h('div', { class: 'row-actions' }, actionButton('New art', 'btn', () => postJson(`${caseUrl}/new-art`, {
      package_id: p.media_package_id, note: note.value }),
      { confirm: 'Send this video back to the thumbnail step for new art? Write in the note what to change.', busy: 'Sending back to the thumbnail step...' })),
    h('label', {}, 'Songs to replace'), songs,
    h('div', { class: 'row-actions' }, actionButton('Replace song', 'btn', () => postJson(`${caseUrl}/replace-songs`, {
      song_ids: picked(), note: note.value }),
      { confirm: 'Replace the ticked songs? The run goes back to make new ones, then the video is made again. Write in the note what the new song should be like.', busy: 'Sending the run back for new songs...' })));
}

function reviewStep(reviews) {
  const card = stepCard('3. Review and upload', reviews.length);
  if (!reviews.length) { card.append(h('p', { class: 'muted' }, 'No video is waiting for approval.')); return card; }
  for (const r of reviews) {
    for (const p of r.packages) {
      if (p.error) { card.append(h('p', { class: 'notice' }, `${p.media_package_id}: ${p.error}`)); continue; }
      const base = `/api/studio/packages/${p.media_package_id}`;
      card.append(h('div', { class: 'variant' },
        h('h3', {}, p.title || p.media_package_id),
        h('div', { class: 'review-media' },
          h('video', { controls: true, preload: 'metadata', src: `${base}/video` }),
          h('div', {}, h('strong', {}, 'Thumbnail'),
            p.thumbnails.length ? h('img', { class: 'preview', src: `${base}/thumbnails/${p.thumbnails[0]}`, alt: 'thumbnail' }) : h('p', { class: 'unknown' }, 'none'),
            h('p', { class: 'muted' }, `${Math.round((p.duration_seconds || 0) / 60)} min, ${p.chapters} chapters`))),
        h('strong', {}, 'Title'), h('div', { class: 'prompt' }, p.title || ''),
        h('strong', {}, 'Description'), h('div', { class: 'prompt' }, p.description || ''),
        h('p', { class: 'muted' }, `Tags: ${(p.tags || []).join(', ')}`),
        p.status === 'ready_for_publish' ? changesPanel(r, p)
          : h('p', { class: 'notice' }, 'Waiting for new art in the thumbnail step before it can be approved.')));
    }
    const history = noteList(r.notes, 'Your notes on this video');
    if (history) card.append(history);
    card.append(h('div', { class: 'row-actions' },
      actionButton('Approve and upload to YouTube', 'btn-ok big', () => authedSend(`/api/studio/approvals/${r.case_id}/approve`, 'POST'),
        { confirm: 'Upload this video to YouTube now? This publishes it on your channel.', busy: 'Granting the upload and resuming the run...' }),
      actionButton('Reject', 'btn-danger', () => authedSend(`/api/studio/approvals/${r.case_id}/reject`, 'POST'),
        { confirm: 'Reject this video? The run ends and nothing is uploaded.' })));
  }
  return card;
}

function startButton(label, music) {
  return actionButton(`Option: ${label}`, 'btn-ok big',
    () => authedSend('/api/studio/start', 'POST', JSON.stringify({ music }), 'application/json'),
    { confirm: `Start a new video with ${label} song prompts? P2 picks what to make and the run begins.`,
      busy: `Asking P2 for the next video and starting the run (${label} prompts)...` });
}

async function renderStudio() {
  const s = await getJson('/api/studio');
  const waiting = s.waiting || {};
  const count = Object.values(waiting).reduce((n, ids) => n + ids.length, 0);
  document.getElementById('studio-count').textContent = count ? `(${count})` : '';
  const head = h('section', { class: 'step' },
    h('div', { class: 'step-head' },
      h('div', {}, h('h2', {}, 'Studio'), h('p', { class: 'muted' }, 'Start a video, then answer each step when it waits for you. Nothing is uploaded until you approve it.')),
      s.start_available
        ? h('div', {}, h('strong', {}, 'Start a new video. Song prompts for:'),
          h('div', { class: 'row-actions' },
            startButton('Suno', 'suno'), startButton('Flow Music', 'flow_music')))
        : h('span', { class: 'notice' }, 'Starting runs is not configured (platform settings or service token missing).')));
  return h('div', {}, head, songsStep(s.songs, waiting, s.notes), thumbnailsStep(s.thumbnails, waiting), reviewStep(s.reviews || []));
}

// ------------------------------------------------------------------------ shell

async function loadView(name) {
  currentView = name;
  for (const tab of document.querySelectorAll('.tab')) tab.classList.toggle('active', tab.dataset.view === name);
  const root = document.getElementById('view-root');
  try {
    if (name === 'studio') {
      const studioPill = document.getElementById('data-source-pill');
      studioPill.textContent = 'DATA SOURCE: LIVE';
      studioPill.className = 'pill pill-live';
      document.getElementById('generated-at').textContent = `updated ${new Date().toLocaleTimeString()}`;
      root.replaceChildren(await renderStudio());
      return;
    }
    if (name === 'exceptions' || name === 'production-queue') reviewers = await getJson('/api/reviewers');
    const view = await getJson(`/api/views/${name}`);
    const pill = document.getElementById('data-source-pill');
    pill.textContent = `DATA SOURCE: ${String(view.data_source).toUpperCase()}`;
    pill.className = `pill ${view.data_source === 'live' ? 'pill-live' : 'pill-bad'}`;
    document.getElementById('generated-at').textContent = `generated ${view.generated_at}`;
    root.replaceChildren(renderers[name](view));
  } catch (e) {
    root.replaceChildren(h('div', { class: 'card unavailable' }, h('h2', {}, 'Dashboard API unavailable'), h('p', { class: 'reason' }, String(e))));
  }
}

document.addEventListener('DOMContentLoaded', () => {
  for (const tab of document.querySelectorAll('.tab')) tab.addEventListener('click', () => loadView(tab.dataset.view));
  document.getElementById('refresh-btn').addEventListener('click', () => loadView(currentView));
  const initial = (location.hash || '').slice(1);
  loadView(VIEWS.includes(initial) ? initial : 'studio');
});
