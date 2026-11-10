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
    studioBusy += 1;
    const out = await run().finally(() => { studioBusy -= 1; });
    button.disabled = false;
    if (!out.ok) { result.className = 'result refused'; result.textContent = refusal(out); return; }
    result.className = 'result done';
    result.textContent = 'Done.';
    setTimeout(() => refreshStudio(true), 800);
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
    studioBusy += 1;
    const out = await authedSend(url, 'PUT', file, 'application/octet-stream').finally(() => { studioBusy -= 1; });
    if (!out.ok) { result.className = 'result refused'; result.textContent = refusal(out); return; }
    result.className = 'result done';
    result.textContent = `saved as ${out.body.saved}`;
    setTimeout(() => refreshStudio(true), 500);
  } });
  return h('span', {}, input, ' ', result);
}

function postJson(url, payload) {
  return authedSend(url, 'POST', JSON.stringify(payload), 'application/json');
}

function noteList(notes, title) {
  if (!notes || !notes.length) return null;
  return h('div', { class: 'notes' }, h('strong', {}, title),
    notes.map((n) => h('p', { class: 'note' }, h('span', { class: 'muted' }, `${(n.at || '').slice(0, 16).replace('T', ' ')} `), n.note)));
}

// Pick A, B and C from every image saved for this video, or upload a new one; then the
// video is made again from the new A and comes back here for approval.
function pictureChooser(r, p, caseUrl, note) {
  const lib = p.art_library || [];
  const base = `/api/studio/art-library/${p.media_package_id}`;
  const chosen = {};
  const box = h('div', { class: 'chooser' }, h('label', {}, 'Pictures: click one for A, B and C (A is also the picture of the whole video), or upload a new one'));
  for (const letter of ['A', 'B', 'C']) {
    const info = (p.art_prompts || {})[letter] || {};
    const row = h('div', { class: 'pick-row' });
    const select = (path, img) => {
      chosen[letter] = path;
      for (const el of row.querySelectorAll('img')) el.classList.toggle('chosen', el === img);
    };
    const tile = (path, label) => {
      const img = h('img', { src: `${base}/${path.split('/').map(encodeURIComponent).join('/')}`, alt: label, title: label });
      img.addEventListener('click', () => select(path, img));
      return img;
    };
    // Preselect the newest image named for this letter.
    let first = null;
    for (const item of lib) {
      const img = tile(item.path, item.label);
      row.append(img);
      if (!first && item.letter === letter) first = [item.path, img];
    }
    if (first) select(...first);
    const result = h('span', { class: 'result' });
    const upload = h('input', { type: 'file', accept: 'image/png,image/jpeg,image/webp', onchange: async () => {
      const file = upload.files && upload.files[0];
      if (!file) return;
      result.className = 'result';
      result.textContent = `uploading ${file.name}...`;
      studioBusy += 1;
      const out = await authedSend(base, 'PUT', file, 'application/octet-stream').finally(() => { studioBusy -= 1; });
      if (!out.ok) { result.className = 'result refused'; result.textContent = refusal(out); return; }
      const img = tile(out.body.path, file.name);
      row.prepend(img);
      select(out.body.path, img);
      result.className = 'result done';
      result.textContent = 'uploaded and chosen';
      upload.value = '';
    } });
    box.append(h('div', { class: 'variant' },
      h('strong', {}, `${letter}${info.text ? `: ${info.style_id} ("${info.text}")` : ''}`),
      row,
      h('div', { class: 'row-actions' }, info.prompt ? copyButton(info.prompt) : null, h('span', { class: 'muted' }, 'Upload new:'), upload, result)));
  }
  box.append(h('div', { class: 'row-actions' }, actionButton('Use these pictures and make the video again', 'btn-ok', () => postJson(`${caseUrl}/swap-art`, {
    package_id: p.media_package_id, choices: { ...chosen }, note: note.value }),
    { confirm: 'Use the chosen pictures? The video is made again from picture A (a few minutes) and comes back here for approval.', busy: 'Starting...' })));
  return box;
}

function changesPanel(r, p) {
  const caseUrl = `/api/studio/approvals/${r.case_id}/changes`;
  const note = h('textarea', { rows: 2, placeholder: 'Optional note: what should change and why (shown at the step it goes back to)' });
  const title = h('input', { type: 'text', maxlength: 100, value: p.title || '' });
  const description = h('textarea', { rows: 8 });
  description.value = p.description || '';
  const tags = h('input', { type: 'text', value: (p.tags || []).join(', ') });
  const thumb = h('select', {}, p.thumbnails.map((t, i) => h('option', { value: t }, i === 0 ? `${t} (now first)` : t)));
  const songs = h('div', { class: 'songs-pick' }, (p.tracks || []).map((t) => h('label', { class: 'pick' },
    h('input', { type: 'checkbox', value: t.song_id }), ` ${t.start} ${t.title}`)));
  const picked = () => [...songs.querySelectorAll('input:checked')].map((i) => i.value);
  return h('details', { class: 'changes', 'data-key': `changes:${r.case_id}:${p.media_package_id}` },
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
    pictureChooser(r, p, caseUrl, note),
    h('label', {}, 'Songs to replace'), songs,
    h('div', { class: 'row-actions' }, actionButton('Replace song', 'btn', () => postJson(`${caseUrl}/replace-songs`, {
      song_ids: picked(), note: note.value }),
      { confirm: 'Replace the ticked songs? The run goes back to make new ones, then the video is made again. Write in the note what the new song should be like.', busy: 'Sending the run back for new songs...' })));
}

// The approval of a project's video (inside its page's "3. Video and review").
function reviewStep(reviews) {
  const card = h('div', {});
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
        (p.sounds_like || []).length ? h('p', { class: 'notice' },
          `Sounds close to your recent video${p.sounds_like.length > 1 ? 's' : ''}: ` +
          p.sounds_like.map((v) => `"${v.title || v.media_package_id}" (${Math.round(v.similarity * 100)}% alike)`).join(', ') +
          '. Listen before approving; Replace song in Request changes can make it differ.') : null,
        p.status === 'ready_for_publish' ? changesPanel(r, p)
          : remakeLine((remakesNow || {})[p.media_package_id])));
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

let remakesNow = {};
function remakeLine(remake) {
  if (remake && remake.state === 'running') {
    return h('p', { class: 'notice' }, `Making the video again with your new pictures, since ${new Date(remake.since).toLocaleTimeString()}. This takes several minutes; it keeps going if you close or refresh the page.`);
  }
  if (remake && remake.state === 'failed') {
    return h('p', { class: 'result refused' }, `Making the video again failed: ${remake.error}. Fix the pictures in step 2 and press Import there.`);
  }
  return h('p', { class: 'notice' }, 'Waiting for new art in the thumbnail step before it can be approved.');
}

// ------------------------------------------------------------------------ studio: projects
//
// Owner 2026-11-09/10: every video is a numbered project with its own page (#studio/17);
// every step stays visible after it is done; the page follows the run by itself (no F5)
// and says loudly, with the reason and what to do, whenever a project stops.

let studioRoute = null;          // null: the project list; a number: that project's page
let studioShown = '';            // the JSON last drawn, to redraw only on a change
let studioPending = null;        // data that arrived while the owner was busy on the page
let studioUpdatedAt = 0;
let alerted = new Set();         // projects already announced as needing the owner
let studioBusy = 0;

function minutes(seconds) {
  if (seconds === null || seconds === undefined) return '?';
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
function lengthLabel(range) { return range ? `${Math.round(range[0] / 60)}-${Math.round(range[1] / 60)} min` : 'length unknown'; }
function when(iso) { return iso ? new Date(iso).toLocaleString() : 'unknown'; }
function clock(iso) { return iso ? new Date(iso).toLocaleTimeString() : ''; }

function statsText(y) {
  const st = y && y.stats;
  if (!st) return '';
  return [`${(st.views ?? 0).toLocaleString()} views`, st.likes !== undefined ? `${st.likes} likes` : null,
    st.comments !== undefined ? `${st.comments} comments` : null].filter(Boolean).join(' · ');
}

function statusBadge(p) {
  if (p.your_turn) return h('span', { class: 'badge badge-review' }, 'your turn');
  if (p.problem) return h('span', { class: 'badge badge-hard' }, 'stopped');
  if (p.finished) return h('span', { class: 'badge' }, p.state.toLowerCase());
  return h('span', { class: 'badge badge-state' }, 'working');
}

// -- the list ------------------------------------------------------------------------------

let styleSelect = null;
let lengthSelect = null;
function styleName(styles, id) { return (styles.labels || {})[id] || id || 'no sub-style'; }

function startCard(s) {
  if (!s.start_available) return h('section', { class: 'step' }, h('h2', {}, 'Start a new video'),
    h('p', { class: 'notice' }, 'Starting runs is not configured (platform settings or service token missing).'));
  const styles = s.styles || {};
  const keepStyle = styleSelect ? styleSelect.value : '';
  const keepLength = lengthSelect ? lengthSelect.value : '20_30m';
  styleSelect = h('select', { 'data-keep': '1' },
    h('option', { value: '' }, 'Let P2 pick (avoids the last 2 videos\' styles)'),
    (styles.available || []).map((id) => h('option', { value: id }, styleName(styles, id))));
  styleSelect.value = (styles.available || []).includes(keepStyle) ? keepStyle : '';
  lengthSelect = h('select', { 'data-keep': '1' },
    Object.entries(s.lengths || { '20_30m': '20-30 minutes' }).map(([id, label]) => h('option', { value: id }, label)));
  lengthSelect.value = keepLength in (s.lengths || {}) ? keepLength : '20_30m';
  const recent = (styles.recent || []).map((r) => `${styleName(styles, r.subgenre)} (${(r.at || '').slice(0, 10)})`);
  // What the length asks of the owner, from P3's own plan (/api/studio/start-options).
  const preview = h('p', { class: 'muted' });
  const showPreview = () => {
    const plan = (s.song_counts || {})[lengthSelect.value];
    preview.textContent = plan
      ? `${plan.songs} song prompts of about ${minutes(plan.song_seconds)} each, for a ${lengthSelect.selectedOptions[0].textContent} video. If QA rejects some or they run short, the Studio asks for more.`
      : '';
  };
  lengthSelect.addEventListener('change', showPreview);
  showPreview();
  const start = (label, music) => actionButton(`Start with ${label} prompts`, 'btn-ok big',
    () => authedSend('/api/studio/start', 'POST', JSON.stringify({
      music, subgenre: styleSelect.value || null, length: lengthSelect.value }), 'application/json'),
    { confirm: `Start a new ${lengthSelect.selectedOptions[0].textContent} video, style: ${styleSelect.selectedOptions[0].textContent}, song prompts for ${label}?`,
      busy: 'Asking P2 for the next video and starting it...' });
  return h('section', { class: 'step' },
    h('h2', {}, 'Start a new video'),
    h('div', { class: 'row-actions' }, h('strong', {}, 'Style'), styleSelect, h('strong', {}, 'Length'), lengthSelect),
    preview,
    h('p', { class: 'muted' }, recent.length ? `Last videos, newest first: ${recent.join(', ')}` : 'No earlier videos yet.'),
    styles.error ? h('p', { class: 'notice' }, `Styles unavailable (${styles.error}); P2 will pick.`) : null,
    h('div', { class: 'row-actions' }, start('Suno', 'suno'), start('Flow Music', 'flow_music')));
}

function alertBanner(projects) {
  const needing = projects.filter((p) => p.your_turn || p.problem);
  if (!needing.length) return null;
  return h('section', { class: 'alert-banner' }, needing.map((p) => h('a', { href: `#studio/${p.number}`, class: 'alert-line' },
    h('strong', {}, `#${p.number} `), p.problem ? '⚠ ' : '● ', p.label, p.title ? ` — ${p.title}` : '')));
}

function projectRow(p) {
  return h('a', { class: `project-row ${p.your_turn ? 'yours' : ''} ${p.problem ? 'stopped' : ''}`, href: `#studio/${p.number}` },
    h('span', { class: 'project-number' }, `#${p.number}`),
    h('span', { class: 'project-main' },
      h('strong', {}, p.title || '(no title yet)'),
      h('span', { class: 'muted' }, ` ${p.style_label || ''} · ${lengthLabel(p.length)} · started ${when(p.started)}`),
      h('span', { class: 'project-label' }, p.label),
      p.working ? h('span', { class: 'notice' }, p.working.state === 'failed' ? ` picture ${p.working.kind} failed` : ` building the video now`) : null),
    h('span', {}, statusBadge(p), p.youtube ? h('a', { class: 'muted', href: p.youtube.url, target: '_blank', rel: 'noopener',
      onclick: (e) => e.stopPropagation() }, ` YouTube ${statsText(p.youtube) || '✓'}`) : null));
}

function listPage(data) {
  const projects = data.projects || [];
  const active = projects.filter((p) => !p.finished);
  const done = projects.filter((p) => p.finished);
  return h('div', {},
    alertBanner(projects),
    startCard(data.overview || {}),
    h('section', { class: 'step' }, h('h2', {}, `Projects in progress (${active.length})`),
      active.length ? active.map(projectRow) : h('p', { class: 'muted' }, 'Nothing in progress. Start a video above.')),
    h('section', { class: 'step' }, h('h2', {}, `Finished projects (${done.length})`),
      done.length ? done.map(projectRow) : h('p', { class: 'muted' }, 'None yet.')));
}

// -- one project ---------------------------------------------------------------------------

function holdBox(p) {
  const hold = p.hold;
  if (!hold) return null;
  const cls = hold.your_turn ? 'hold-box yours' : 'hold-box stopped';
  return h('section', { class: cls },
    h('h2', {}, hold.title),
    h('p', {}, hold.what),
    hold.detail ? h('pre', { class: 'prompt' }, hold.detail) : null,
    h('p', { class: 'muted' }, `Since ${when(hold.since)}. Case ${hold.case_id}.`),
    hold.action === 'retry' && hold.owner_may_act ? h('div', { class: 'row-actions' },
      actionButton('Try again', 'btn-ok big', () => authedSend(`/api/studio/holds/${hold.case_id}/retry`, 'POST'),
        { confirm: 'Try this step again? If the cause is not fixed it will stop here again.', busy: 'Continuing the run...' })) : null,
    hold.action === 'retry' && !hold.owner_may_act ? h('p', { class: 'result refused' }, hold.blocked_because) : null,
    hold.your_turn ? h('p', { class: 'muted' }, 'Your step is below.') : null);
}

function progressText(since, expected) {
  const elapsed = (Date.now() - new Date(since).getTime()) / 1000;
  const left = Math.max(0, Math.round((expected - elapsed) / 60));
  return `Started ${clock(since)}. ` + (expected ? (elapsed < expected ? `About ${left} min left (estimate).`
    : 'Taking longer than estimated; still working.') : 'Working...');
}

// The 1-second ticker moves a running render's bar and estimate between data updates.
function tickProgress() {
  for (const box of document.querySelectorAll('.hold-box.working[data-since]')) {
    const expected = Number(box.dataset.expected) || 0;
    const elapsed = (Date.now() - new Date(box.dataset.since).getTime()) / 1000;
    const bar = box.querySelector('.progress-bar');
    if (bar && expected) bar.style.width = `${Math.min(99, Math.round((elapsed / expected) * 100))}%`;
    const eta = box.querySelector('.eta');
    if (eta) eta.textContent = progressText(box.dataset.since, expected);
  }
}

function workingBox(p) {
  const w = p.working;
  if (!w) return null;
  if (w.state === 'failed') {
    return h('section', { class: 'hold-box stopped' }, h('h2', {}, `Making the video failed`),
      h('pre', { class: 'prompt' }, w.error || 'no reason given'),
      h('p', {}, 'Fix the pictures in step 2 and press Import again.'));
  }
  const elapsed = (Date.now() - new Date(w.since).getTime()) / 1000;
  const expected = w.expected_seconds || 0;
  const pct = expected ? Math.min(99, Math.round((elapsed / expected) * 100)) : null;
  return h('section', { class: 'hold-box working', 'data-since': w.since, 'data-expected': expected },
    h('h2', {}, w.kind === 'remake' ? 'Making the video again with your new pictures' : 'Making the video from picture A'),
    h('p', { class: 'eta' }, progressText(w.since, expected)),
    pct !== null ? h('div', { class: 'progress' }, h('div', { class: 'progress-bar', style: `width:${pct}%` })) : null,
    h('p', { class: 'muted' }, 'It keeps going if you close or refresh this page.'));
}

const LOG_ICON = { step: '›', done: '✓', wait: '●', problem: '⚠', you: '★' };
function logBox(p) {
  const lines = (p.log || []).slice().reverse();
  return h('details', { class: 'step log', open: true, 'data-key': `log:${p.number}` },
    h('summary', {}, h('strong', {}, 'Status log'), h('span', { class: 'muted' }, ` (${lines.length} entries, newest first)`)),
    h('ol', { class: 'log-list' }, lines.map((l) => h('li', { class: `log-${l.kind}` },
      h('span', { class: 'mono muted' }, `${new Date(l.at).toLocaleString()} `),
      h('span', { class: 'log-icon' }, LOG_ICON[l.kind] || '·'), ' ', l.text,
      l.detail ? h('div', { class: 'log-detail mono' }, l.detail) : null))));
}

function songLength(song) {
  if (!song.saved) return h('span', { class: 'missing' }, 'missing');
  if (song.seconds === null || song.seconds === undefined) return h('span', { class: 'saved' }, 'saved');
  const off = song.expected_seconds && (song.seconds < song.expected_seconds * 0.5 || song.seconds > song.expected_seconds * 1.6);
  return h('span', { class: off ? 'notice' : 'saved' },
    `saved, ${minutes(song.seconds)}` + (song.expected_seconds ? ` (asked about ${minutes(song.expected_seconds)})` : ''));
}

function batchUpload(songs) {
  const result = h('div', { class: 'result' });
  const input = h('input', { type: 'file', multiple: true, accept: '.wav,audio/wav', onchange: async () => {
    const files = [...(input.files || [])];
    if (!files.length) return;
    const missing = songs.filter((s) => !s.saved).map((s) => s.stem);
    const byName = new Map(files.map((f) => [f.name.replace(/\.wav$/i, ''), f]));
    const plan = [];
    const left = files.filter((f) => !missing.includes(f.name.replace(/\.wav$/i, '')));
    for (const stem of missing) {
      const f = byName.get(stem) || left.shift();
      if (f) plan.push([stem, f]);
    }
    if (!plan.length) { result.textContent = 'Every song is already saved.'; return; }
    const text = plan.map(([stem, f]) => `${f.name} -> ${stem}.wav`).join('\n');
    if (!window.confirm(`Save these files, in this order?\n\n${text}`)) { input.value = ''; return; }
    studioBusy += 1;
    try {
      const done = [];
      for (const [stem, f] of plan) {
        result.className = 'result';
        result.textContent = `uploading ${f.name} (${done.length + 1} of ${plan.length})...`;
        const out = await authedSend(`/api/studio/songs/${encodeURIComponent(stem)}`, 'PUT', f, 'application/octet-stream');
        if (!out.ok) { result.className = 'result refused'; result.textContent = `${f.name}: ${refusal(out)}`; return; }
        done.push(`${f.name}: ${minutes(out.body.seconds)}`);
      }
      result.className = 'result done';
      result.textContent = `Saved ${done.length}: ${done.join(', ')}`;
    } finally { studioBusy -= 1; input.value = ''; }
    refreshStudio(true);
  } });
  return h('div', { class: 'row-actions' }, h('strong', {}, 'Upload several at once:'), input, result);
}

function songsSection(p) {
  const sheets = p.songs.sheets || [];
  const all = sheets.flatMap((s) => s.songs);
  const missing = all.filter((s) => !s.saved).length;
  const madeSeconds = all.reduce((t, s) => t + (s.saved && s.seconds ? s.seconds : 0), 0);
  const target = p.length ? ` of ${Math.round(p.length[0] / 60)}-${Math.round(p.length[1] / 60)} min needed` : '';
  const card = h('section', { class: `step ${p.songs.waiting ? 'waiting' : ''}` },
    h('div', { class: 'step-head' }, h('h2', {}, `1. Songs (${all.length - missing} of ${all.length} saved, ${Math.round(madeSeconds / 60)} min${target})`),
      p.songs.waiting ? h('span', { class: 'badge badge-review' }, 'waiting for you') : h('span', { class: 'badge' }, missing ? 'not asked yet' : 'done')));
  if (!sheets.length) { card.append(h('p', { class: 'muted' }, 'No song prompts yet.')); return card; }
  if (p.songs.waiting && missing) card.append(batchUpload(all));
  let n = 0;
  for (const sheet of sheets) {
    for (const song of sheet.songs) {
      n += 1;
      const url = `/api/studio/songs/${encodeURIComponent(song.stem)}`;
      card.append(h('details', { class: 'song', open: !song.saved, 'data-key': `song:${song.stem}` },
        h('summary', {}, h('strong', {}, `Song ${n} `), h('span', { class: 'mono muted' }, `${song.stem}.wav (${sheet.source}) `), songLength(song)),
        h('div', { class: 'prompt' }, song.text),
        h('div', { class: 'row-actions' }, copyButton(song.text),
          p.songs.waiting ? h('span', {}, h('span', { class: 'muted' }, song.saved ? 'Replace file:' : 'Upload:'), uploadInput('.wav,audio/wav', url)) : null),
        song.saved ? h('audio', { controls: true, preload: 'none', src: `${url}/audio` }) : null,
        song.lyrics_needed && !song.lyrics_saved ? h('p', { class: 'notice' }, `This song has vocals: save its lyrics as ${song.stem}.lyrics.txt.`) : null));
    }
  }
  if (p.songs.waiting) {
    card.append(h('div', { class: 'row-actions' },
      actionButton(missing ? `Continue (${missing} still missing)` : 'Continue: send the songs', 'btn-ok big',
        () => authedSend('/api/studio/songs/done', 'POST'), { busy: 'Sending the songs to the run... P4 checks every file.' })));
  }
  return card;
}

function picturesSection(p) {
  const card = h('section', { class: `step ${p.art_waiting ? 'waiting' : ''}` },
    h('div', { class: 'step-head' }, h('h2', {}, '2. Pictures (thumbnail and video picture)'),
      p.art_waiting ? h('span', { class: 'badge badge-review' }, 'waiting for you') : h('span', { class: 'badge' }, (p.art || []).length ? 'done' : 'not asked yet')));
  if (!(p.art || []).length) { card.append(h('p', { class: 'muted' }, 'The pictures are asked for once the playlist is built.')); return card; }
  card.append(h('p', { class: 'muted' }, 'Make each picture in Nano Banana Pro (16:9, full size). Picture A is also the picture of the whole video.'));
  if (p.review) card.append(h('p', { class: 'notice' }, 'To change the pictures now: step 3, Request changes, Pictures. The video is made again from the new picture A, before anything is uploaded.'));
  for (const [i, job] of p.art.entries()) {
    const current = i === 0;
    const box = h('details', { class: 'variant', open: current, 'data-key': `art:${job.media_package_id}` },
      h('summary', {}, h('strong', {}, current ? 'Current pictures' : 'Earlier version'), h('span', { class: 'muted mono' }, ` ${job.media_package_id} (${job.status})`)));
    for (const from of current && p.art_waiting ? job.reuse_from || [] : []) {
      box.append(h('div', { class: 'row-actions' }, actionButton('Reuse the pictures of the previous version', 'btn',
        () => postJson(`/api/studio/thumbnails/${job.media_package_id}/reuse`, { from_package_id: from }))));
    }
    for (const v of job.variants) {
      const url = `/api/studio/thumbnails/${job.media_package_id}/${v.letter}`;
      box.append(h('div', { class: 'variant' },
        h('div', { class: 'step-head' }, h('strong', {}, `${v.letter}: ${v.style_id} ("${v.text}")`),
          v.saved.length ? h('span', { class: 'saved' }, `saved: ${v.saved.join(', ')}`) : h('span', { class: 'missing' }, 'missing')),
        h('details', { 'data-key': `prompt:${job.media_package_id}:${v.letter}`, open: !v.saved.length }, h('summary', {}, 'Prompt'), h('div', { class: 'prompt' }, v.prompt || '')),
        h('div', { class: 'row-actions' }, copyButton(v.prompt || ''),
          current && p.art_waiting ? uploadInput('image/png,image/jpeg,image/webp', url) : null),
        v.saved.length ? h('img', { class: 'preview', src: `${url}?v=${encodeURIComponent(v.saved.join(','))}`, alt: `picture ${v.letter}` }) : null));
    }
    if (current && p.art_waiting) {
      const ready = job.variants.every((v) => v.saved.length);
      const running = job.import && job.import.state === 'running';
      box.append(h('div', { class: 'row-actions' }, running ? h('span', { class: 'notice' }, 'Making the video now (see the top of the page).')
        : ready ? actionButton('Import and make the video', 'btn-ok big', () => authedSend(`/api/studio/thumbnails/${job.media_package_id}/import`, 'POST'),
          { busy: 'Starting the import...' })
          : h('span', { class: 'muted' }, 'Upload A, B and C to import.')));
    }
    card.append(box);
  }
  return card;
}

function replaceSongsBox(p) {
  const tracks = (p.video && p.video.tracks) || [];
  const note = h('textarea', { rows: 2, placeholder: 'What should the new song be like? (shown at the songs step)' });
  const list = h('div', { class: 'songs-pick' }, tracks.map((t) => h('label', { class: 'pick' },
    h('input', { type: 'checkbox', value: t.song_id }), ` ${t.start} ${t.title}`)));
  return h('details', { class: 'changes', 'data-key': `replace:${p.number}` },
    h('summary', {}, 'Replace songs'),
    h('p', { class: 'muted' }, 'Tick the songs to take out. The project goes back to the songs step for as many new ones, then builds the video again.'),
    list, h('label', {}, 'Note'), note,
    h('div', { class: 'row-actions' }, actionButton('Replace the ticked songs', 'btn', () => postJson(`/api/studio/projects/${p.number}/replace-songs`, {
      song_ids: [...list.querySelectorAll('input:checked')].map((i) => i.value), note: note.value }),
      { confirm: 'Replace the ticked songs? The project goes back to make new ones, then the video is built again.', busy: 'Sending the project back for new songs...' })));
}

function videoSection(p) {
  const v = p.video;
  const card = h('section', { class: `step ${p.review ? 'waiting' : ''}` },
    h('div', { class: 'step-head' }, h('h2', {}, '3. Video and review'),
      p.review ? h('span', { class: 'badge badge-review' }, 'waiting for you') : h('span', { class: 'badge' }, v && v.has_video ? 'built' : 'not built yet')));
  if (!v) { card.append(h('p', { class: 'muted' }, 'The video is built after the pictures are imported.')); return card; }
  if (v.error) { card.append(h('p', { class: 'notice' }, `${v.media_package_id}: ${v.error}`)); return card; }
  if (p.review) {
    card.append(reviewStep([p.review]));
  } else {
    const base = `/api/studio/packages/${v.media_package_id}`;
    card.append(h('h3', {}, v.title || v.media_package_id),
      v.has_video ? h('div', { class: 'review-media' },
        h('video', { controls: true, preload: 'none', src: `${base}/video` }),
        h('div', {}, v.thumbnails.length ? h('img', { class: 'preview', src: `${base}/thumbnails/${v.thumbnails[0]}`, alt: 'thumbnail' }) : null,
          h('p', { class: 'muted' }, `${Math.round((v.duration_seconds || 0) / 60)} min, ${v.tracks.length} songs`)))
        : h('p', { class: 'muted' }, `Playlist built (${Math.round((v.duration_seconds || 0) / 60)} min, ${v.tracks.length} songs); the video is made from picture A.`),
      h('details', { 'data-key': `tracks:${p.number}` }, h('summary', {}, `Tracklist (${v.tracks.length})`),
        h('ol', {}, v.tracks.map((t) => h('li', {}, `${t.start} ${t.title}`)))));
  }
  if (p.can_replace_songs && !p.review) card.append(replaceSongsBox(p));
  return card;
}

function youtubeSection(p) {
  const y = p.youtube;
  return h('section', { class: 'step' }, h('div', { class: 'step-head' }, h('h2', {}, '4. On YouTube'),
    h('span', { class: 'badge' }, y ? (y.privacy || 'uploaded') : 'not uploaded')),
    y ? h('div', {}, h('p', {}, h('a', { href: y.url, target: '_blank', rel: 'noopener' }, y.url), h('span', { class: 'muted' }, ` ${y.privacy || ''}, ${when(y.published_at)}`)),
      y.stats ? h('p', { class: 'kpi-line' }, statsText(y), h('span', { class: 'muted' }, ' (from YouTube, refreshed every 10 min)')) : h('p', { class: 'muted' }, 'Views: not available yet.'),
      y.unfinished ? h('p', { class: 'notice' }, `The video is on YouTube, but the upload step did not finish (P7: ${y.unfinished.replaceAll('_', ' ')}). See the stop above.`) : null)
      : h('p', { class: 'muted' }, 'Uploaded after you approve it.'));
}

function projectPage(p) {
  const head = h('section', { class: 'step project-head' },
    h('p', {}, h('a', { href: '#studio' }, '← All projects')),
    h('div', { class: 'step-head' },
      h('h2', {}, `#${p.number} ${p.title || '(no title yet)'}`), statusBadge(p)),
    h('p', { class: 'muted' }, `${p.style_label || 'style unknown'} · ${lengthLabel(p.length)}${p.visual_world ? ` · ${p.visual_world.replaceAll('_', ' ')}` : ''} · started ${when(p.started)}`),
    h('p', { class: 'project-label' }, p.label),
    p.finished ? null : h('div', { class: 'row-actions' }, actionButton('Cancel this project', 'btn-danger', () => postJson(`/api/studio/projects/${p.number}/cancel`, { reason: '' }),
      { confirm: `Cancel project #${p.number}? It stops for good: nothing more is made or uploaded.`, busy: 'Cancelling...' })));
  return h('div', {}, head, holdBox(p), workingBox(p), logBox(p),
    songsSection(p), picturesSection(p), videoSection(p), youtubeSection(p),
    noteList(p.notes, 'Your notes on this project'));
}

// -- live updates, alerts and routing --------------------------------------------------------

function studioUntouched() {
  const root = document.getElementById('view-root');
  if (root.querySelector('details.changes[open]')) return false;
  for (const el of root.querySelectorAll('textarea, input[type="text"], input[type="file"], input[type="checkbox"], select')) {
    if (el.dataset.keep) continue;  // kept across redraws (Start's style and length)
    if (el.type === 'file') { if (el.files && el.files.length) return false; continue; }
    if (el.type === 'checkbox') { if (el.checked) return false; continue; }
    if (el.tagName === 'SELECT') {
      const first = [...el.options].find((o) => o.defaultSelected) || el.options[0];
      if (first && el.value !== first.value) return false;
      continue;
    }
    if (el.value !== el.defaultValue) return false;
  }
  const active = document.activeElement;
  return !(active && ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName) && !active.dataset.keep);
}

function announce(projects) {
  const needing = projects.filter((p) => p.your_turn || p.problem);
  document.title = needing.length ? `(${needing.length}) PLM Studio: ${needing[0].problem ? 'stopped' : 'your turn'}` : 'PLM Control Deck';
  document.getElementById('studio-count').textContent = needing.length ? `(${needing.length})` : '';
  const fresh = needing.filter((p) => !alerted.has(`${p.number}:${p.label}`));
  for (const p of needing) alerted.add(`${p.number}:${p.label}`);
  if (fresh.length && 'Notification' in window && Notification.permission === 'granted') {
    for (const p of fresh) new Notification(`PLM #${p.number}: ${p.label}`, { body: p.title || '' });
  }
}

async function fetchStudio() {
  const list = await getJson('/api/studio/projects');
  announce(list.projects || []);
  const options = await getJson('/api/studio/start-options');
  remakesNow = options.remakes || {};
  if (studioRoute === null) return { kind: 'list', projects: list.projects, overview: options };
  const page = await getJson(`/api/studio/projects/${studioRoute}`);
  return { kind: 'page', page };
}

function drawStudio(data) {
  const root = document.getElementById('view-root');
  const wasOpen = new Map([...root.querySelectorAll('details[data-key]')].map((d) => [d.dataset.key, d.open]));
  const y = studioShown ? window.scrollY : 0;  // a new page opens at its top
  root.replaceChildren(data.kind === 'list' ? listPage(data) : projectPage(data.page));
  for (const d of root.querySelectorAll('details[data-key]')) if (wasOpen.has(d.dataset.key)) d.open = wasOpen.get(d.dataset.key);
  window.scrollTo(0, y);
  studioShown = JSON.stringify(data);
  studioPending = null;
  studioUpdatedAt = Date.now();
  const pill = document.getElementById('data-source-pill');
  pill.textContent = 'DATA SOURCE: LIVE';
  pill.className = 'pill pill-live';
}

// Redraw when something changed; while the owner is typing, picking or uploading, keep the
// page and offer the update instead (never lose what they are doing).
let studioFetching = false;
async function refreshStudio(force = false) {
  if (currentView !== 'studio' || (studioFetching && !force)) return;
  let data;
  studioFetching = true;
  try { data = await fetchStudio(); }
  catch (e) {
    const pill = document.getElementById('data-source-pill');
    pill.textContent = 'DATA SOURCE: UNREACHABLE';
    pill.className = 'pill pill-bad';
    if (force || !studioShown) document.getElementById('view-root').replaceChildren(h('div', { class: 'card unavailable' }, h('h2', {}, 'Studio unavailable'), h('p', { class: 'reason' }, String(e))));
    return;
  } finally { studioFetching = false; }
  studioUpdatedAt = Date.now();
  const pill = document.getElementById('data-source-pill');
  pill.textContent = 'DATA SOURCE: LIVE';  // reachable again, even when nothing changed
  pill.className = 'pill pill-live';
  if (!force && JSON.stringify(data) === studioShown) return;
  if (force || (studioBusy === 0 && studioUntouched())) { drawStudio(data); return; }
  studioPending = data;
}

function updatedLine() {
  const el = document.getElementById('generated-at');
  if (currentView !== 'studio') return;
  const ago = studioUpdatedAt ? Math.round((Date.now() - studioUpdatedAt) / 1000) : null;
  el.replaceChildren(ago === null ? 'updating...' : `updated ${ago}s ago (live)`,
    studioPending ? h('button', { class: 'btn btn-ok', type: 'button', onclick: () => drawStudio(studioPending) }, ' New update: show it') : '');
}

function routeFromHash() {
  const m = (location.hash || '').match(/^#studio\/(\d+)$/);
  return m ? Number(m[1]) : null;
}

setInterval(() => { refreshStudio(false); }, 5000);
setInterval(() => { updatedLine(); tickProgress(); }, 1000);
window.addEventListener('hashchange', () => {
  if (!(location.hash || '').startsWith('#studio')) return;
  studioRoute = routeFromHash();
  studioShown = '';
  loadView('studio');
});

// ------------------------------------------------------------------------ shell

async function loadView(name) {
  currentView = name;
  for (const tab of document.querySelectorAll('.tab')) tab.classList.toggle('active', tab.dataset.view === name);
  const root = document.getElementById('view-root');
  try {
    if (name === 'studio') {
      studioRoute = routeFromHash();
      if (!studioShown) root.replaceChildren(h('p', { class: 'muted' }, 'Loading...'));
      await refreshStudio(true);
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
  for (const tab of document.querySelectorAll('.tab')) tab.addEventListener('click', () => {
    if (tab.dataset.view === 'studio' && location.hash.startsWith('#studio/')) location.hash = '#studio';
    loadView(tab.dataset.view);
  });
  const refresh = document.getElementById('refresh-btn');
  refresh.addEventListener('click', async () => {
    refresh.disabled = true;
    refresh.textContent = 'Updating...';
    await loadView(currentView);
    refresh.textContent = 'Updated ✓';
    setTimeout(() => { refresh.textContent = 'Refresh'; refresh.disabled = false; }, 1200);
  });
  const alerts = document.getElementById('alerts-btn');
  if (alerts && 'Notification' in window && Notification.permission === 'default') {
    alerts.hidden = false;
    alerts.addEventListener('click', async () => { await Notification.requestPermission(); alerts.hidden = true; });
  }
  const initial = (location.hash || '').slice(1).split('/')[0];
  loadView(VIEWS.includes(initial) ? initial : 'studio');
});
