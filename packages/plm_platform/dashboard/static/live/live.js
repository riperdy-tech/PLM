// PLM Control Deck: the six PART XVII views over live data (ES module, no dependencies).
//
// Every value is rendered with textContent. A null value is shown as "unknown"; a panel
// whose source could not answer is shown as "unavailable" with the source and reason.
// Exception actions go to the R3 exception queue; the server enforces every hold-class
// rule, and this page only mirrors them (a HARD_STOP never shows an approve button).

const TOKEN_HEADER = 'X-PLM-Service-Token';
const TOKEN_KEY = 'plmServiceToken';
const VIEWS = ['command-center', 'market-map', 'production-queue', 'experiment-board', 'economics', 'exceptions'];

let currentView = 'command-center';
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

function readToken() { try { return sessionStorage.getItem(TOKEN_KEY); } catch (e) { return null; } }
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

// ------------------------------------------------------------------------ shell

async function loadView(name) {
  currentView = name;
  for (const tab of document.querySelectorAll('.tab')) tab.classList.toggle('active', tab.dataset.view === name);
  const root = document.getElementById('view-root');
  try {
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
  loadView(VIEWS.includes(initial) ? initial : 'command-center');
});
