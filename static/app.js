'use strict';

// ── State ─────────────────────────────────────────────────────
let targets  = [];
let alerts   = [];
let filterText = '';
let editingPatternsFor = null;

// ── Init ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadTargets();
  loadAlerts();
  loadRuleGroups();

  document.getElementById('addForm').addEventListener('submit', handleAdd);
  document.getElementById('searchInput').addEventListener('input', e => {
    filterText = e.target.value.toLowerCase();
    renderTargets();
  });

  // Auto-refresh every 30 s
  setInterval(() => { loadTargets(); loadAlerts(); }, 30_000);
});

// ── Data loaders ──────────────────────────────────────────────
async function loadTargets() {
  const data = await api('/api/targets');
  if (!data) return;
  targets = data;
  renderTargets();
  updateHeaderStats();
}

async function loadAlerts() {
  const data = await api('/api/alerts?limit=50');
  alerts = Array.isArray(data) ? data : [];  // always render, even on fetch failure
  renderAlerts();
  updateHeaderStats();
}

async function loadRuleGroups() {
  const data = await api('/api/noise-rules');
  const el = document.getElementById('ruleGroups');
  if (el && data && data.groups) el.textContent = data.groups.join(', ');
}

// ── Add targets ───────────────────────────────────────────────
async function handleAdd(e) {
  e.preventDefault();
  const btn     = document.getElementById('addBtn');
  const errEl   = document.getElementById('addError');
  const urlsRaw = document.getElementById('urlInput').value.trim();
  const label   = document.getElementById('labelInput').value.trim();
  const interval= parseInt(document.getElementById('intervalInput').value) || 15;
  const patterns= document.getElementById('patternsInput').value;

  errEl.classList.add('hidden');

  if (!urlsRaw) {
    showError(errEl, 'Please enter at least one URL.');
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Adding…';

  const result = await api('/api/targets', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      urls_raw: urlsRaw, label_prefix: label,
      check_interval: interval, ignore_patterns: patterns,
    }),
  });

  btn.disabled = false;
  btn.innerHTML = '<span class="btn-icon">＋</span> Add Targets';

  if (result && result.added) {
    document.getElementById('urlInput').value   = '';
    document.getElementById('labelInput').value = '';
    await loadTargets();
    flashBanner(`✓ Added ${result.added.length} target(s). Baseline check running in the background.`);
  } else {
    showError(errEl, result?.error || 'Failed to add targets.', result?.details);
  }
}

// ── Render targets ────────────────────────────────────────────
function renderTargets() {
  const tbody   = document.getElementById('targetsBody');
  const table   = document.getElementById('targetsTable');
  const empty   = document.getElementById('targetsEmpty');
  const loading = document.getElementById('targetsLoading');

  loading.classList.add('hidden');

  const filtered = targets.filter(t =>
    !filterText ||
    t.label.toLowerCase().includes(filterText) ||
    t.url.toLowerCase().includes(filterText)
  );

  if (filtered.length === 0) {
    table.classList.add('hidden');
    empty.classList.remove('hidden');
    return;
  }

  empty.classList.add('hidden');
  table.classList.remove('hidden');

  tbody.innerHTML = filtered.map(t => `
    <tr class="${t.active ? '' : 'inactive-row'}" id="row-${t.id}">
      <td><strong>${esc(t.label)}</strong></td>
      <td class="url-cell"><a href="${esc(t.url)}" target="_blank" rel="noopener">${esc(t.url)}</a></td>
      <td>${platformBadge(t.platform)}</td>
      <td>${t.check_interval}m</td>
      <td>${t.last_checked ? relTime(t.last_checked) : '<span style="color:var(--muted)">pending</span>'}</td>
      <td>${statusBadge(t.status)}</td>
      <td>
        <div class="action-group">
          <button class="btn btn-sm btn-ghost" onclick="checkNow(${t.id})" title="Check now">↻</button>
          <button class="btn btn-sm btn-ghost" onclick="openPatterns(${t.id})"
            title="${t.ignore_patterns ? 'Edit ignore patterns' : 'Add ignore patterns'}"
            >${t.ignore_patterns ? '⚙•' : '⚙'}</button>
          <button class="btn btn-sm btn-ghost" onclick="toggleTarget(${t.id}, ${t.active})"
            title="${t.active ? 'Pause' : 'Resume'}">${t.active ? '⏸' : '▶'}</button>
          <button class="btn btn-sm btn-danger" onclick="deleteTarget(${t.id})" title="Delete">✕</button>
        </div>
      </td>
    </tr>
  `).join('');
}

// ── Render alerts ─────────────────────────────────────────────
function renderAlerts() {
  const list    = document.getElementById('alertsList');
  const empty   = document.getElementById('alertsEmpty');
  const loading = document.getElementById('alertsLoading');
  const badge   = document.getElementById('alertsBadge');

  loading.classList.add('hidden');

  if (alerts.length === 0) {
    list.innerHTML = '';
    empty.classList.remove('hidden');
    badge.classList.add('hidden');
    return;
  }

  empty.classList.add('hidden');
  badge.textContent = alerts.length;
  badge.classList.remove('hidden');

  list.innerHTML = alerts.map(a => `
    <div class="alert-card" onclick="openModal(${a.id})">
      <div class="alert-dot"></div>
      <div class="alert-content">
        <div class="alert-label">${esc(a.target_label)}</div>
        <a class="alert-url" href="${esc(a.target_url)}" target="_blank" rel="noopener"
           onclick="event.stopPropagation()">${esc(a.target_url)}</a>
        <div class="alert-time">${fmtDate(a.detected_at)}</div>
      </div>
      <div class="alert-meta">
        ${a.email_sent
          ? '<span class="email-sent">✉ Email sent</span>'
          : '<span class="email-unsent">✉ Not sent</span>'}
        <button class="btn btn-sm btn-ghost" onclick="event.stopPropagation(); openModal(${a.id})">
          View diff
        </button>
      </div>
    </div>
  `).join('');
}

// ── Target actions ────────────────────────────────────────────
async function deleteTarget(id) {
  if (!confirm('Remove this target? All history will be deleted.')) return;
  await api(`/api/targets/${id}`, { method: 'DELETE' });
  await loadTargets();
}

async function toggleTarget(id, currentlyActive) {
  await api(`/api/targets/${id}/toggle`, { method: 'POST' });
  await loadTargets();
}

async function checkNow(id) {
  const row = document.getElementById(`row-${id}`);
  if (row) row.style.opacity = '.5';
  await api(`/api/targets/${id}/check`, { method: 'POST' });
  await loadTargets();
  await loadAlerts();
}

// ── Ignore patterns editor ────────────────────────────────────
function openPatterns(id) {
  const t = targets.find(x => x.id === id);
  if (!t) return;

  editingPatternsFor = id;
  document.getElementById('patternsTitle').textContent = `Ignore Patterns — ${t.label}`;
  document.getElementById('patternsEdit').value = t.ignore_patterns || '';
  document.getElementById('patternsError').classList.add('hidden');
  document.getElementById('patternsModal').classList.remove('hidden');
}

function closePatterns() {
  editingPatternsFor = null;
  document.getElementById('patternsModal').classList.add('hidden');
}

async function savePatterns() {
  if (editingPatternsFor === null) return;

  const btn   = document.getElementById('patternsSave');
  const errEl = document.getElementById('patternsError');
  errEl.classList.add('hidden');
  btn.disabled = true;

  const result = await api(`/api/targets/${editingPatternsFor}/patterns`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ignore_patterns: document.getElementById('patternsEdit').value }),
  });

  btn.disabled = false;

  if (result && result.id) {
    closePatterns();
    await loadTargets();
    flashBanner('✓ Ignore patterns saved. They apply from the next check.');
  } else {
    showError(errEl, result?.error || 'Failed to save patterns.', result?.details);
  }
}

document.getElementById('patternsModal').addEventListener('click', e => {
  if (e.target === document.getElementById('patternsModal')) closePatterns();
});

// ── Modal ─────────────────────────────────────────────────────
function openModal(alertId) {
  const a = alerts.find(x => x.id === alertId);
  if (!a) return;

  document.getElementById('modalTitle').textContent = `Change — ${a.target_label}`;
  document.getElementById('modalMeta').innerHTML = `
    <strong>URL:</strong> <a href="${esc(a.target_url)}" target="_blank">${esc(a.target_url)}</a><br>
    <strong>Detected:</strong> ${fmtDate(a.detected_at)}<br>
    <strong>Email alert:</strong> ${a.email_sent ? '✅ Sent' : '❌ Not sent (check SMTP config)'}
  `;

  const diffEl = document.getElementById('modalDiff');
  diffEl.innerHTML = colorizeDiff(a.change_summary || '(no diff available)');

  document.getElementById('modal').classList.remove('hidden');
}

function closeModal() {
  document.getElementById('modal').classList.add('hidden');
}

document.getElementById('modal').addEventListener('click', e => {
  if (e.target === document.getElementById('modal')) closeModal();
});

// ── Header stats ──────────────────────────────────────────────
function updateHeaderStats() {
  const active = targets.filter(t => t.active).length;
  document.getElementById('statActive').textContent = active;

  const today = new Date().toISOString().slice(0, 10);
  const todayAlerts = alerts.filter(a => a.detected_at.startsWith(today)).length;
  document.getElementById('statAlerts').textContent = todayAlerts;
}

// ── Helpers ───────────────────────────────────────────────────
function platformBadge(platform) {
  const map = {
    twitter:  ['badge-twitter',   '𝕏 Twitter'],
    linkedin: ['badge-linkedin',  'in LinkedIn'],
    facebook: ['badge-facebook',  'f Facebook'],
    generic:  ['badge-generic',   '🌐 Generic'],
  };
  const [cls, label] = map[platform] || map.generic;
  return `<span class="platform-badge ${cls}">${label}</span>`;
}

function statusBadge(status) {
  if (!status || status === 'pending') return '<span class="status-badge status-pending">Pending</span>';
  if (status === 'ok')                 return '<span class="status-badge status-ok">✓ OK</span>';
  if (status === 'changed')            return '<span class="status-badge status-changed">★ Changed</span>';
  if (status.startsWith('error'))      return `<span class="status-badge status-error" title="${esc(status)}">✗ Error</span>`;
  return `<span class="status-badge status-pending">${esc(status)}</span>`;
}

function colorizeDiff(raw) {
  return raw.split('\n').map(line => {
    const safe = esc(line);
    if (line.startsWith('+++') || line.startsWith('---')) return `<span class="diff-hunk">${safe}</span>`;
    if (line.startsWith('@@'))  return `<span class="diff-hunk">${safe}</span>`;
    if (line.startsWith('+'))   return `<span class="diff-add">${safe}</span>`;
    if (line.startsWith('-'))   return `<span class="diff-del">${safe}</span>`;
    return safe;
  }).join('\n');
}

function relTime(iso) {
  const d   = new Date(iso + 'Z');
  const sec = Math.floor((Date.now() - d) / 1000);
  if (sec < 60)   return 'just now';
  if (sec < 3600) return `${Math.floor(sec/60)}m ago`;
  if (sec < 86400)return `${Math.floor(sec/3600)}h ago`;
  return `${Math.floor(sec/86400)}d ago`;
}

function fmtDate(iso) {
  const d = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
  return d.toLocaleString();
}

function esc(s) {
  if (s == null) return '';
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

function showError(el, msg, details) {
  el.innerHTML = esc(msg) + (details?.length
    ? '<ul>' + details.map(d => `<li>${esc(d)}</li>`).join('') + '</ul>'
    : '');
  el.classList.remove('hidden');
}

function flashBanner(msg) {
  const b = document.createElement('div');
  Object.assign(b.style, {
    position:'fixed', bottom:'24px', right:'24px', background:'#27ae60',
    color:'#fff', padding:'12px 20px', borderRadius:'8px', fontWeight:'600',
    fontSize:'14px', boxShadow:'0 4px 12px rgba(0,0,0,.2)', zIndex:'999',
    transition:'opacity .4s',
  });
  b.textContent = msg;
  document.body.appendChild(b);
  setTimeout(() => { b.style.opacity = '0'; setTimeout(() => b.remove(), 400); }, 3500);
}

async function api(url, opts = {}) {
  try {
    const res  = await fetch(url, opts);
    const json = await res.json();
    return json;
  } catch(e) {
    console.error('API error', url, e);
    return null;
  }
}
