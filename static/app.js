/* School ID property management – role-based frontend */
let records = [], selected = null, editing = null, bulkSelected = new Set();
let attentionFilter = '', requestFilter = '', feedbackFilter = 'review';
let stats = null, serverToday = '', sending = false, historyLoadId = 0, lookedUpStudent = '';
let intentLabels = {}, knownIntents = [];
const badgeCounts = { inventory: 0, requests: 0, feedback: 0 };
let workspaceRole = localStorage.getItem('workspaceRole') || '';
const baseSession = localStorage.getItem('chatSessionId') || (() => {
  const id = crypto.randomUUID();
  localStorage.setItem('chatSessionId', id);
  return id;
})();

const NAV = {
  coordinator: [
    { id: 'overview', label: '🏠  Overview' },
    { id: 'inventory', label: '▤  ID inventory' },
    { id: 'queue', label: '📅  Queue' },
    { id: 'requests', label: '📥  Requests' },
    { id: 'feedback', label: '👍  Feedback' },
    { id: 'activity', label: '🕓  Activity' },
    { id: 'assistant', label: '◌  Assistant' },
  ],
  guard: [
    { id: 'verify', label: '🔎  Verify ID' },
    { id: 'gate', label: '🚪  Gate log' },
    { id: 'incidents', label: '⚠️  Incidents' },
    { id: 'assistant', label: '◌  Assistant' },
  ],
  student: [
    { id: 'my-id', label: '🪪  My ID' },
    { id: 'student-request', label: '📝  Request help' },
    { id: 'assistant', label: '◌  Assistant' },
  ],
};

const ROLE_META = {
  coordinator: { title: 'Coordinator', avatar: 'PM', caption: 'Property Management', blurb: 'Ask about a record, the queue, or QR stickers.' },
  guard: { title: 'Guard', avatar: 'GD', caption: 'Campus gate', blurb: 'Verify an ID, then ask about gate status or incidents.' },
  student: { title: 'Student', avatar: 'ST', caption: 'Self-service', blurb: 'Look up your student ID, then ask about status or requirements.' },
};

const $ = (id) => document.getElementById(id);
const escapeHtml = (v) => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function sessionId() { return `${baseSession}:${workspaceRole || 'none'}`; }
const statusClass = (status) => String(status).toLowerCase().replace(/[^a-z0-9]+/g, '-');   // safe inside class=""
const JSON_HEADERS = { 'Content-Type': 'application/json' };
const postJson = (body, method = 'POST') => ({ method, headers: JSON_HEADERS, body: JSON.stringify(body) });

function renderMarkdown(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}

async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch {
    throw new Error('Cannot reach the server. Check your connection and try again.');
  }
  const contentType = response.headers.get('content-type') || '';
  const body = contentType.includes('application/json') ? await response.json() : {};
  if (!response.ok) throw new Error(body.error || 'Unable to complete the request.');
  return body;
}

/* A date as YYYY-MM-DD in the browser's own time zone (used only until the server's date is known). */
function localDate() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}
const todayString = () => serverToday || localDate();

/* Sidebar badges: how many things are waiting on each page. */
function renderBadges() {
  document.querySelectorAll('#role-nav .nav').forEach(nav => {
    nav.querySelector('.notification-badge')?.remove();
    const count = badgeCounts[nav.dataset.view] || 0;
    if (count > 0) {
      const pulse = nav.dataset.view === 'inventory' ? ' pulse' : '';
      nav.insertAdjacentHTML('beforeend', `<span class="notification-badge${pulse}" title="${count} waiting">${count}</span>`);
    }
  });
}

let toastTimer = null;
function notify(message, duration = 3500) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  toastTimer = setTimeout(() => { $('toast').textContent = ''; }, duration);
}

function initTheme() {
  const saved = localStorage.getItem('theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = saved || (prefersDark ? 'dark' : 'light');
  document.documentElement.setAttribute('data-theme', theme);
  updateThemeIcon(theme);
}
function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  updateThemeIcon(next);
}
function updateThemeIcon(theme) {
  const btn = $('theme-toggle');
  if (btn) btn.textContent = theme === 'dark' ? '☀️' : '🌙';
}

function formatTime(ts) {
  if (!ts) return '';
  try {
    return new Date(ts.replace(' ', 'T')).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}
function formatNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function closeMobileNav() {
  document.body.classList.remove('nav-open');
  $('menu-toggle').setAttribute('aria-expanded', 'false');
  $('nav-backdrop').hidden = true;
}
function toggleMobileNav() {
  const open = document.body.classList.toggle('nav-open');
  $('menu-toggle').setAttribute('aria-expanded', String(open));
  $('nav-backdrop').hidden = !open;
}

function setRole(role) {
  if (!ROLE_META[role]) return;
  workspaceRole = role;
  localStorage.setItem('workspaceRole', role);
  selected = null;
  $('selected-student').textContent = 'No student selected';
  const meta = ROLE_META[role];
  $('role-badge').textContent = meta.title;
  $('role-avatar').textContent = meta.avatar;
  $('role-caption').innerHTML = `${escapeHtml(meta.caption)}<small>Local prototype · sample records</small>`;
  $('assistant-blurb').textContent = meta.blurb;
  renderNav();
  const first = NAV[role][0].id;
  showView(first);
  updateSuggestions();
  loadChatHistory();
  if (role === 'coordinator') {
    load();
    loadQueue();
    loadRequests();
    loadActivity();
    loadStats();
  }
  if (role === 'guard') {
    loadGate();
    loadIncidents();
  }
}

function renderNav() {
  const items = NAV[workspaceRole] || [];
  const html = items.map(item =>
    `<a class="nav" href="#${item.id}" data-view="${item.id}">${item.label}</a>`
  ).join('');
  $('role-nav').innerHTML = html;
  $('mobile-dock').innerHTML = items.map(item =>
    `<a href="#${item.id}" data-view="${item.id}">${escapeHtml(item.label.replace(/^[^\w]+/, '').trim())}</a>`
  ).join('');
  renderBadges();
}

function showView(viewId) {
  const allowed = (NAV[workspaceRole] || []).some(item => item.id === viewId);
  if (!allowed) viewId = (NAV[workspaceRole] || [{ id: 'assistant' }])[0].id;
  const mobile = window.matchMedia('(max-width: 760px)').matches;

  if (viewId === 'assistant') {
    if (mobile) {
      document.querySelectorAll('.workspace').forEach(el => { el.hidden = true; });
    } else {
      const fallback = `view-${NAV[workspaceRole][0].id}`;
      const anyOpen = [...document.querySelectorAll('.workspace')].some(el => !el.hidden);
      if (!anyOpen) {
        document.querySelectorAll('.workspace').forEach(el => { el.hidden = el.id !== fallback; });
      }
    }
    $('assistant').hidden = false;
    if (!mobile) $('assistant').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } else {
    document.querySelectorAll('.workspace').forEach(el => {
      el.hidden = el.id !== `view-${viewId}`;
    });
    $('assistant').hidden = mobile;
  }

  document.querySelectorAll('#role-nav .nav, #mobile-dock a').forEach(link => {
    link.classList.toggle('active', link.dataset.view === viewId);
  });
  const dock = $('mobile-dock');                     // with 7 tabs the dock scrolls; keep the open one in view
  const current = dock && dock.querySelector('a.active');
  if (current && dock.clientWidth) dock.scrollTo({ left: current.offsetLeft - (dock.clientWidth - current.offsetWidth) / 2, behavior: 'smooth' });
  const label = (NAV[workspaceRole] || []).find(item => item.id === viewId);
  $('topbar-section').textContent = label ? label.label.replace(/^[^\w]+/, '').trim() : 'Home';
  history.replaceState(null, '', `#${viewId}`);
  closeMobileNav();

  if (viewId === 'overview') loadStats();
  if (viewId === 'queue') loadQueue();
  if (viewId === 'requests') loadRequests();
  if (viewId === 'feedback') loadFeedback();
  if (viewId === 'activity') loadActivity();
  if (viewId === 'gate') loadGate();
  if (viewId === 'incidents') loadIncidents();
}

function emptyState(text) {
  return `<div class="empty">${text}</div>`;
}

async function load() {
  try {
    records = (await api('/api/inventory')).records;
    for (const [field, id, label] of [['course', 'course-filter', 'All courses'], ['section', 'section-filter', 'All sections']]) {
      const prev = $(id).value;
      $(id).innerHTML = `<option value="">${label}</option>` + [...new Set(records.map(r => r[field]))].sort().map(v => `<option>${escapeHtml(v)}</option>`).join('');
      $(id).value = prev;
    }
    render();
    loadStats();
  } catch (error) { notify(error.message); }
}

/* "Needs attention" filters for the inventory. The tests run against the server's date. */
const ATTENTION = [
  { id: 'overdue', label: 'Overdue', alert: true, test: (r, today) => r.schedule_date && r.schedule_date < today && r.status !== 'Released' },
  { id: 'today', label: 'Today', test: (r, today) => r.schedule_date === today },
  { id: 'unscheduled', label: 'Ready, not scheduled', test: r => r.status === 'Ready for release' && !r.schedule_date },
  { id: 'no-reason', label: 'Pending, no reason', test: r => r.status === 'Pending' && !(r.pending_reason || '').trim() },
];

function renderAttention() {
  const today = todayString();
  const counts = Object.fromEntries(ATTENTION.map(a => [a.id, records.filter(r => a.test(r, today)).length]));
  $('attention-chips').innerHTML =
    `<button type="button" class="filter-chip" data-attention="" aria-pressed="${attentionFilter === ''}">All</button>` +
    ATTENTION.map(a =>
      `<button type="button" class="filter-chip${a.alert && counts[a.id] ? ' alert' : ''}" data-attention="${a.id}" aria-pressed="${attentionFilter === a.id}">${escapeHtml(a.label)} <b>${counts[a.id]}</b></button>`
    ).join('');
}

function render() {
  const groups = [
    ['Total IDs', records.length, 'All student records', ''],
    ['Pending', records.filter(r => r.status === 'Pending').length, 'Awaiting requirements', 'Pending'],
    ['Ready for release', records.filter(r => r.status === 'Ready for release').length, 'Available for pickup', 'Ready for release'],
    ['Released', records.filter(r => r.status === 'Released').length, 'Successfully collected', 'Released'],
  ];
  $('stats').innerHTML = groups.map(([label, total, detail, filter], i) =>
    `<button type="button" class="stat" data-status-filter="${escapeHtml(filter)}"><div>${label}<span class="stat-symbol">${['▤','◷','✓','↗'][i]}</span></div><strong>${total.toString().padStart(2,'0')}</strong><small>${detail}</small></button>`
  ).join('');

  badgeCounts.inventory = records.filter(r => r.status === 'Pending').length;
  renderBadges();
  renderAttention();

  const query = $('search').value.trim().toLowerCase();
  const today = todayString();
  const attention = ATTENTION.find(a => a.id === attentionFilter);
  const shown = records.filter(r =>
    `${r.name} ${r.student_id}`.toLowerCase().includes(query) &&
    (!attention || attention.test(r, today)) &&
    [['status', 'status-filter'], ['course', 'course-filter'], ['year_level', 'year-filter'], ['section', 'section-filter'], ['request_type', 'kind-filter']]
      .every(([field, id]) => !$(id).value || String(r[field]) === $(id).value)
  );
  $('count').textContent = `${shown.length} of ${records.length}`;

  $('records').innerHTML = shown.length
    ? shown.map(r => `<article class="record ${selected === r.id ? 'selected' : ''}">
        <div class="student-side">
          <input type="checkbox" class="bulk-check" data-bulk="${r.id}" ${bulkSelected.has(r.id) ? 'checked' : ''} aria-label="Select ${escapeHtml(r.name)}">
          <a href="/student/${encodeURIComponent(r.token)}" target="_blank" rel="noopener" class="qr" aria-label="Open student details for ${escapeHtml(r.name)}">
            <img src="/qr/${r.id}.png" alt="QR code for ${escapeHtml(r.student_id)}" loading="lazy">
          </a>
          <div>
            <h3>${escapeHtml(r.name)}</h3>
            <p>${escapeHtml(r.student_id)} <span class="divider">·</span> ${escapeHtml(r.request_type)}</p>
            <div class="student-tags"><span>${escapeHtml(r.course)}</span><span>Section ${escapeHtml(r.section)}</span><span>Year ${escapeHtml(r.year_level)}</span></div>
            <div class="record-actions">
              <a href="/sticker/${r.id}" target="_blank" rel="noopener">Print sticker ↗</a>
              <a href="/card/${r.id}" target="_blank" rel="noopener">Preview card ↗</a>
              ${r.status === 'Ready for release' ? `<button type="button" class="release-btn" data-release="${r.id}" title="Record that the student collected the ID">✓ Mark released</button>` : ''}
              <button type="button" data-select="${r.id}">Ask about ID</button>
              <button type="button" data-delete="${r.id}" class="text-button danger-text">Delete</button>
            </div>
          </div>
        </div>
        <div class="status-side">
          <span class="status ${statusClass(r.status)}"><span>●</span> ${escapeHtml(r.status)}</span>
          <p class="appointment">${r.schedule_date ? `${escapeHtml(r.schedule_type)}<br><strong>${escapeHtml(r.schedule_date)} · ${escapeHtml(r.schedule_time)}</strong>` : 'No appointment scheduled'}</p>
          ${r.status === 'Pending' && r.pending_reason ? `<p class="reason">${escapeHtml(r.pending_reason)}</p>` : ''}
          ${r.notes ? `<p class="reason">${escapeHtml(r.notes)}</p>` : ''}
          <button class="text-button" type="button" data-edit="${r.id}">Update / schedule ↗</button>
        </div>
      </article>`).join('')
    : emptyState('No IDs match these filters.<br>Try another search or reset the filters.');
  updateBulkToolbar();
}

function updateBulkToolbar() {
  const toolbar = $('bulk-toolbar');
  if (bulkSelected.size > 0) toolbar.classList.add('visible');
  else toolbar.classList.remove('visible');
  $('bulk-count').textContent = `${bulkSelected.size} selected`;
}

async function bulkUpdateStatus() {
  const status = $('bulk-status').value;
  if (!status) { notify('Choose a status first.'); return; }
  if (bulkSelected.size === 0) { notify('Select at least one record.'); return; }
  try {
    const result = await api('/api/inventory/bulk', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record_ids: [...bulkSelected], status }),
    });
    notify(`Updated ${result.updated} record(s) to "${status}".`);
    bulkSelected.clear();
    await load();
    loadActivity();
  } catch (error) { notify(error.message); }
}

function selectAllVisible() {
  const checkboxes = document.querySelectorAll('.bulk-check');
  const allChecked = [...checkboxes].every(cb => cb.checked);
  checkboxes.forEach(cb => {
    const id = Number(cb.dataset.bulk);
    if (allChecked) { bulkSelected.delete(id); cb.checked = false; }
    else { bulkSelected.add(id); cb.checked = true; }
  });
  updateBulkToolbar();
}

async function deleteRecord(id) {
  const rec = records.find(r => r.id === id);
  if (!rec || !confirm(`Delete ${rec.name} (${rec.student_id})? This cannot be undone.`)) return;
  try {
    await api(`/api/inventory/${id}`, { method: 'DELETE' });
    if (selected === id) { selected = null; $('selected-student').textContent = 'No student selected'; }
    bulkSelected.delete(id);
    notify(`Deleted ${rec.name}.`);
    await load();
  } catch (error) { notify(error.message); }
}

function exportCSV() {
  window.open('/api/inventory/export', '_blank');
  notify('Downloading inventory CSV…');
}

function printStickers() {
  const ids = [...bulkSelected];
  const max = Number($('bulk-stickers').dataset.max) || 60;
  if (!ids.length) { notify('Select at least one record first.'); return; }
  if (ids.length > max) { notify(`A sticker sheet holds up to ${max} students (you selected ${ids.length}).`); return; }
  window.open(`/stickers?ids=${ids.join(',')}`, '_blank');
}

async function markReleased(id, button) {
  const rec = records.find(r => r.id === id);
  if (!rec) return;
  button.disabled = true;
  try {
    await api(`/api/inventory/${id}`, postJson({ status: 'Released' }, 'PATCH'));
    notify(`${rec.name} is now marked as released.`);
    await load();
    loadActivity();
  } catch (error) {
    notify(error.message);
    button.disabled = false;
  }
}

function selectRecord(id, announce = true) {
  selected = id;
  const rec = records.find(r => r.id === selected);
  $('selected-student').textContent = rec ? `📌 ${rec.name} — ${rec.student_id}` : 'No student selected';
  if (workspaceRole === 'coordinator') render();
  updateSuggestions();
  if (announce && rec) notify(`Selected ${rec.name}. You can ask the assistant about this ID.`);
  $('chat-input').focus();
}

function openDialog(row = null) {
  editing = row ? row.id : null;
  $('record-form').reset();
  $('form-error').textContent = '';
  $('dialog-title').textContent = row ? `Update ${row.name}` : 'Add student ID';
  $('identity-fields').hidden = !!row;
  $('processing-fields').hidden = !row;
  $('schedule-hint').hidden = !row;
  for (const input of $('identity-fields').querySelectorAll('input,select')) input.disabled = !!row;
  for (const input of $('processing-fields').querySelectorAll('input,select,textarea')) input.disabled = !row;
  if (row) {
    for (const key of ['status', 'schedule_type', 'schedule_date', 'schedule_time', 'pending_reason', 'notes']) {
      if ($('record-form').elements[key]) $('record-form').elements[key].value = row[key] || '';
    }
  }
  $('record-dialog').showModal();
}

function showTyping() {
  const indicator = document.createElement('div');
  indicator.className = 'typing-indicator';
  indicator.id = 'typing';
  indicator.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
  $('messages').appendChild(indicator);
  $('messages').scrollTop = $('messages').scrollHeight;
}
function hideTyping() {
  $('typing')?.remove();
}

/* The ANN's score is how closely the words matched a known topic, not a probability that the
   answer is right, so people see a plain label and the number only as a tooltip. */
const QUIET_INTENTS = new Set(['out_of_scope', 'greeting', 'thanks_goodbye']);
function matchLabel(score) {
  if (score >= 0.6) return ['high', 'Strong match'];
  if (score >= 0.35) return ['medium', 'Possible match'];
  return ['low', 'Weak match'];
}

function chipRow(title, chips) {
  if (!chips || !chips.length) return '';
  return '<div class="reply-chips">' +
    (title ? `<div class="reply-chips-title">${escapeHtml(title)}</div>` : '') +
    '<div class="chips">' + chips.map(c =>
      `<button type="button" class="reply-chip" data-question="${escapeHtml(c.question)}">${escapeHtml(c.label)}</button>`
    ).join('') + '</div></div>';
}

function addMessage(text, kind, meta = {}) {
  const node = document.createElement('div');
  node.className = `message ${kind}`;
  let html = renderMarkdown(text);
  html += `<div class="message-time">${meta.timestamp ? formatTime(meta.timestamp) : formatNow()}</div>`;
  if (kind === 'bot' && meta.confidence > 0 && !QUIET_INTENTS.has(meta.intent)) {
    const [cls, label] = matchLabel(meta.confidence);
    const tip = `Match score ${Number(meta.confidence).toFixed(2)}. This shows how closely your words matched a known topic. It does not guarantee the answer is right.`;
    html += `<span class="confidence-badge ${cls}" title="${escapeHtml(tip)}">${label}</span>`;
  }
  if (kind === 'bot' && meta.actions && meta.actions.length > 0) {
    html += '<div class="action-chips">' + meta.actions.map(a =>
      `<button type="button" class="action-chip" data-action-type="${escapeHtml(a.type)}">${escapeHtml(a.label)}</button>`
    ).join('') + '</div>';
  }
  if (kind === 'bot' && meta.retryText) {
    html += '<div class="action-chips"><button type="button" class="action-chip" data-retry>↻ Try again</button></div>';
  }
  if (kind === 'bot') {
    html += chipRow(meta.suggestionsTitle, meta.suggestions) + chipRow('You could also ask:', meta.followUps);
  }
  if (kind === 'bot' && meta.messageId) {
    html += '<div class="emoji-reactions">' +
      '<button type="button" class="emoji-btn" data-rate="up" aria-pressed="false" aria-label="This answer helped" title="This answer helped">👍</button>' +
      '<button type="button" class="emoji-btn" data-rate="down" aria-pressed="false" aria-label="This answer did not help" title="This answer did not help">👎</button>' +
      '<span class="feedback-note" role="status"></span></div>';
  }
  node.innerHTML = html;
  if (kind === 'bot' && meta.messageId) {
    node.dataset.messageId = meta.messageId;
    node.dataset.session = meta.sessionId || sessionId();
    node.appendChild($('feedback-template').content.firstElementChild.cloneNode(true));
    setRating(node, meta.rating || '');
  }
  $('messages').appendChild(node);
  $('messages').scrollTop = $('messages').scrollHeight;
  node.querySelectorAll('.action-chip[data-action-type]').forEach(btn => {
    btn.onclick = () => handleAction(btn.dataset.actionType);
  });
  node.querySelectorAll('.reply-chip').forEach(btn => {
    btn.onclick = () => send(btn.dataset.question);
  });
  node.querySelector('[data-retry]')?.addEventListener('click', () => {
    node.remove();
    send(meta.retryText, { retry: true });
  });
}

/* ── Rating a chatbot answer ──
   A thumb is saved straight away (it holds no text). 👎 then offers optional details; the
   question text is only sent to staff when the person ticks the share box. */
function setRating(node, rating, note = '') {
  node.dataset.rating = rating || '';
  node.querySelectorAll('[data-rate]').forEach(btn => {
    const on = btn.dataset.rate === rating;
    btn.classList.toggle('active', on);
    btn.setAttribute('aria-pressed', String(on));
  });
  const noteEl = node.querySelector('.feedback-note');
  if (noteEl) noteEl.textContent = note;
}

function feedbackIds(node) {
  return { message_id: Number(node.dataset.messageId), session_id: node.dataset.session };
}

/* The details form is wider than a normal reply, so the bubble stretches while it is open. */
function togglePanel(node, open) {
  const panel = node && node.querySelector('.feedback-panel');
  if (!panel) return;
  panel.hidden = !open;
  node.classList.toggle('rating-open', open);
}

async function rateMessage(node, rating) {
  if (!node || node.dataset.busy) return;
  const ids = feedbackIds(node);
  node.dataset.busy = '1';
  node.querySelectorAll('[data-rate]').forEach(btn => { btn.disabled = true; });
  try {
    if (node.dataset.rating === rating) {            // tapping the same thumb again takes the rating back
      await api(`/api/chat/feedback?message_id=${ids.message_id}&session_id=${encodeURIComponent(ids.session_id)}`, { method: 'DELETE' });
      togglePanel(node, false);
      setRating(node, '', 'Rating removed.');
    } else {
      await api('/api/chat/feedback', postJson({ ...ids, rating }));
      setRating(node, rating, rating === 'up' ? 'Thanks for the feedback.' : '');
      togglePanel(node, rating === 'down');
      if (rating === 'down') {
        const panel = node.querySelector('.feedback-panel');
        panel.querySelector('input[name="reason"]')?.focus({ preventScroll: true });
        panel.scrollIntoView({ block: 'nearest' });
      }
    }
    loadStats();                                     // keeps the coordinator's "needs review" badge current
  } catch (error) {
    notify(error.message);
  } finally {
    delete node.dataset.busy;
    node.querySelectorAll('[data-rate]').forEach(btn => { btn.disabled = false; });
  }
}

async function sendFeedbackDetails(node, form) {
  const data = new FormData(form);
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    const result = await api('/api/chat/feedback', postJson({
      ...feedbackIds(node),
      rating: 'down',
      reason: data.get('reason') || '',
      comment: String(data.get('comment') || '').trim(),
      share_text: data.get('share_text') === 'on',
    }));
    togglePanel(node, false);
    setRating(node, 'down', result.shared
      ? 'Thanks. Your question was shared with the office (personal details removed).'
      : 'Thanks. The office will see this. Your question was not shared.');
  } catch (error) {
    notify(error.message);
  } finally {
    button.disabled = false;
  }
}

function handleAction(type) {
  const actions = {
    view_record: () => {
      showView('inventory');
      if (selected) document.querySelector(`[data-select="${selected}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      else notify('Select a record first.');
    },
    update_record: () => {
      const rec = records.find(r => r.id === selected);
      if (rec) openDialog(rec);
      else notify('Select a record to update.');
    },
    print_sticker: () => {
      if (selected) window.open(`/sticker/${selected}`, '_blank');
      else notify('Select or look up a student first.');
    },
    preview_card: () => {
      if (selected) window.open(`/card/${selected}`, '_blank');
      else notify('Select or look up a student first.');
    },
    new_record: () => openDialog(),
    reset_filters: () => $('reset-filters').click(),
    export_csv: () => exportCSV(),
    select_all: () => selectAllVisible(),
    open_queue: () => showView('queue'),
    open_requests: () => showView('requests'),
    open_feedback: () => showView('feedback'),
    open_verify: () => { showView('verify'); $('verify-query').focus(); },
    open_gate: () => { showView('gate'); $('gate-form').elements.student_id.focus(); },
    open_incident: () => { showView('incidents'); $('incident-form').elements.detail.focus(); },
    open_lookup: () => { showView('my-id'); $('lookup-id').focus(); },
    open_student_request: () => showView('student-request'),
    ask_office: () => send('Where is the Property Management Office and what are the hours?'),
    ask_human: () => send('I need to talk to a person at the office'),
  };
  (actions[type] || (() => notify('That action is not available in this role.')))();
}

async function send(text, { retry = false } = {}) {
  if (sending) { notify('One moment, I am still answering.'); return; }
  sending = true;
  const session = sessionId();                  // the answer belongs to the chat that asked
  const button = $('chat-form').querySelector('button');
  if (!retry) addMessage(text, 'user');
  button.disabled = true;
  showTyping();
  try {
    await new Promise(r => setTimeout(r, 250));
    const response = await api('/api/chat', postJson({
      message: text,
      record_id: selected,
      session_id: session,
      workspace_role: workspaceRole || 'coordinator',
    }));
    hideTyping();
    if (session !== sessionId()) return;        // the role was switched meanwhile: that chat is not on screen
    addMessage(response.reply, 'bot', {
      confidence: response.confidence,
      intent: response.intent,
      actions: response.actions,
      timestamp: response.timestamp,
      messageId: response.message_id,
      sessionId: session,
      suggestions: response.suggestions,
      suggestionsTitle: response.suggestions_title,
      followUps: response.follow_ups,
    });
  } catch (error) {
    hideTyping();
    if (session === sessionId()) addMessage(error.message, 'bot', { retryText: text });
  } finally {
    sending = false;
    button.disabled = false;
    $('chat-input').focus();
  }
}

function updateSuggestions() {
  const container = document.querySelector('.suggestions');
  if (!container) return;
  let suggestions;
  if (workspaceRole === 'guard') {
    suggestions = [
      { q: 'What is the status of my student ID?', label: 'ID status' },
      { q: 'What information shows after scanning the QR code?', label: 'QR scan' },
      { q: 'I lost my school ID', label: 'Lost ID at gate' },
      { q: 'Where is the Property Management Office?', label: 'Office' },
    ];
  } else if (workspaceRole === 'student') {
    suggestions = selected ? [
      { q: 'What is the status of my student ID?', label: 'My status' },
      { q: 'When can I collect my school ID?', label: 'Release date' },
      { q: 'Why is my student ID pending?', label: 'Pending reason' },
      { q: 'What documents are needed for a new student ID?', label: 'Requirements' },
    ] : [
      { q: 'What documents are needed for a new student ID?', label: 'Requirements' },
      { q: 'Where should I collect my student ID?', label: 'Office hours' },
      { q: 'How do I scan the QR code on an ID?', label: 'QR help' },
      { q: 'I lost my school ID', label: 'Lost ID' },
      { q: 'What can you do?', label: 'Help' },
    ];
  } else if (selected) {
    const rec = records.find(r => r.id === selected);
    suggestions = [
      { q: 'What is the status of my student ID?', label: 'ID status' },
      { q: 'When can I collect my school ID?', label: 'Release schedule' },
      { q: 'Why is my student ID pending?', label: 'Pending reason' },
      { q: 'When is my pending ID follow-up appointment?', label: 'Follow-up date' },
    ];
    if (rec && rec.request_type === 'Lost ID') {
      suggestions.push({ q: 'When is my lost ID replacement schedule?', label: 'Replacement date' });
    }
  } else {
    suggestions = [
      { q: 'What can you do?', label: 'Help' },
      { q: 'Show the student ID inventory', label: 'Inventory' },
      { q: 'Download the inventory as CSV', label: 'Export data' },
      { q: 'How can I print a small QR sticker?', label: 'QR sticker' },
      { q: 'Where is the Property Management Office?', label: 'Office hours' },
    ];
  }
  container.innerHTML = suggestions.map(s => `<button type="button" data-question="${escapeHtml(s.q)}">${s.label}</button>`).join('');
  container.querySelectorAll('[data-question]').forEach(btn => {
    btn.onclick = () => send(btn.dataset.question);
  });
}

async function loadChatHistory() {
  const loadId = ++historyLoadId;               // a slower, older load must not overwrite a newer one
  $('messages').innerHTML = '';
  addMessage(
    workspaceRole
      ? `Hi! I’m an automated assistant for school IDs. You are in the ${ROLE_META[workspaceRole].title.toLowerCase()} workspace. ${ROLE_META[workspaceRole].blurb}`
      : 'Hi! Choose a role so I can show the right tools.',
    'bot',
    { confidence: 0 }
  );
  if (!workspaceRole) return;
  const session = sessionId();
  try {
    const data = await api(`/api/chat/history?session_id=${encodeURIComponent(session)}`);
    if (loadId !== historyLoadId) return;
    if (data.messages && data.messages.length > 0) {
      $('messages').innerHTML = '';
      data.messages.forEach(m => addMessage(m.message, m.role, {
        confidence: m.confidence,
        intent: m.intent,
        timestamp: m.timestamp,
        messageId: m.role === 'bot' ? m.id : undefined,
        rating: m.rating,
        sessionId: session,
      }));
    }
  } catch { /* ignore */ }
}

async function loadQueue() {
  try {
    const data = await api('/api/queue');
    if (!data.appointments.length) {
      $('queue-list').innerHTML = emptyState('No appointments scheduled yet. Open a record and use Update / schedule.');
      return;
    }
    const overdue = row => row.schedule_date < data.today && row.status !== 'Released';
    const counts = {
      overdue: data.appointments.filter(overdue).length,
      today: data.appointments.filter(row => row.schedule_date === data.today).length,
    };
    counts.upcoming = data.appointments.filter(row => row.schedule_date > data.today).length;
    $('queue-list').innerHTML =
      `<div class="pill-row"><span class="${counts.overdue ? 'alert' : ''}"><strong>${counts.overdue}</strong>overdue</span><span><strong>${counts.today}</strong>today</span><span><strong>${counts.upcoming}</strong>upcoming</span></div>` +
      data.appointments.map(row => `
      <article class="tool-row">
        <div>
          <h3>${escapeHtml(row.name)}</h3>
          <p>${escapeHtml(row.student_id)} · ${escapeHtml(row.course)} · ${escapeHtml(row.status)}</p>
          <p class="appointment"><strong>${escapeHtml(row.schedule_type)}</strong> · ${escapeHtml(row.schedule_date)} ${escapeHtml(row.schedule_time)}</p>
        </div>
        <div class="row-actions">
          ${row.schedule_date === data.today ? '<span class="status ready-for-release">Today</span>' : ''}
          ${overdue(row) ? '<span class="status overdue">Overdue</span>' : ''}
          <button type="button" class="text-button" data-queue-select="${row.id}">Ask about ID</button>
        </div>
      </article>`).join('');
  } catch (error) { notify(error.message); }
}

const REQUEST_BUTTONS = { 'In review': 'Mark in review', Accepted: 'Accept', Closed: 'Close' };

async function loadRequests() {
  try {
    const query = requestFilter ? `?status=${encodeURIComponent(requestFilter)}` : '';
    const data = await api(`/api/requests${query}`);
    const total = Object.values(data.counts).reduce((sum, n) => sum + n, 0);
    badgeCounts.requests = data.counts.Submitted + data.counts['In review'];
    renderBadges();
    $('request-chips').innerHTML =
      `<button type="button" class="filter-chip" data-request-filter="" aria-pressed="${requestFilter === ''}">All <b>${total}</b></button>` +
      data.statuses.map(status =>
        `<button type="button" class="filter-chip" data-request-filter="${escapeHtml(status)}" aria-pressed="${requestFilter === status}">${escapeHtml(status)} <b>${data.counts[status]}</b></button>`
      ).join('');
    if (!data.requests.length) {
      $('request-inbox').innerHTML = emptyState(requestFilter ? `No requests are ${escapeHtml(requestFilter.toLowerCase())} right now.` : 'No student requests yet.');
      return;
    }
    $('request-inbox').innerHTML = data.requests.map(row => `
      <article class="tool-row">
        <div>
          <h3>${escapeHtml(row.request_type)} · ${escapeHtml(row.student_id)}</h3>
          <p>${escapeHtml(row.name || 'Unnamed')} · <span class="status ${statusClass(row.status)}">${escapeHtml(row.status)}</span> · ${escapeHtml(row.timestamp)}</p>
          <p>${escapeHtml(row.detail || 'No details')}</p>
        </div>
        <div class="row-actions">
          ${(data.flow[row.status] || []).map(next => `<button type="button" class="${next === 'Accepted' ? 'primary' : 'text-button'}" data-request-status="${row.id}" data-status="${escapeHtml(next)}"${next === 'Accepted' && row.request_type === 'Lost ID' ? ' title="Marks this student’s ID as Pending (Lost ID) so staff can process a replacement"' : ''}>${escapeHtml(REQUEST_BUTTONS[next] || next)}</button>`).join('')}
        </div>
      </article>`).join('');
  } catch (error) { notify(error.message); }
}

/* ── Overview (coordinator) ── */
async function loadStats() {
  if (workspaceRole !== 'coordinator') return;
  try {
    stats = await api('/api/stats');
    const dateChanged = serverToday !== stats.today;
    serverToday = stats.today;
    badgeCounts.requests = stats.open_requests;
    badgeCounts.feedback = stats.new_feedback;
    renderBadges();
    renderOverview();
    if (dateChanged && records.length) render();   // the attention filters depend on today's date
  } catch (error) { notify(error.message); }
}

function tileHtml({ label, value, detail, alert = false, attrs = '' }) {
  const tag = attrs ? 'button' : 'div';
  const button = attrs ? ' type="button"' : '';
  return `<${tag}${button} class="tile${alert && Number(value) > 0 ? ' alert' : ''}"${attrs}><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></${tag}>`;
}

/* Horizontal bars. The server's JSON objects come back with alphabetical keys, so bars are sorted
   by size here unless the caller already has a meaningful order. */
function barsHtml(entries, cls = '', sort = true) {
  if (!entries.length) return '<p class="subtle">Nothing to show yet.</p>';
  const max = Math.max(1, ...entries.map(([, n]) => n));
  const rows = sort ? [...entries].sort((a, b) => b[1] - a[1]) : entries;
  return rows.map(([label, n]) =>
    `<div class="bar-row"><span>${escapeHtml(label)}</span><span class="bar-track" aria-hidden="true"><span class="bar-fill ${cls}" style="width:${Math.round(100 * n / max)}%"></span></span><b>${Number(n)}</b></div>`
  ).join('');
}

function renderOverview() {
  if (!stats) return;
  $('overview-tiles').innerHTML = [
    { label: 'Appointments today', value: stats.today_appointments, detail: 'Release and follow-up visits', attrs: ' data-attention-jump="today"' },
    { label: 'Overdue', value: stats.overdue_appointments, detail: 'Past their date, not released', alert: true, attrs: ' data-attention-jump="overdue"' },
    { label: 'Open requests', value: stats.open_requests, detail: 'Waiting for the office', attrs: ' data-view="requests"' },
    { label: 'Answers to review', value: stats.new_feedback, detail: '👎 ratings not yet reviewed', attrs: ' data-view="feedback"' },
    { label: 'Open incidents', value: stats.open_incidents, detail: 'Reported by guards' },
    { label: 'Student IDs', value: stats.total, detail: 'All records', attrs: ' data-view="inventory"' },
  ].map(tileHtml).join('');
  $('overview-next').innerHTML = stats.upcoming_appointments.length
    ? stats.upcoming_appointments.map(row => `<div class="mini-row"><span><strong>${escapeHtml(row.name)}</strong> · ${escapeHtml(row.student_id)}</span><span>${escapeHtml(row.schedule_type)} · ${escapeHtml(row.schedule_date)} ${escapeHtml(row.schedule_time)}${row.schedule_date === stats.today ? ' <span class="status ready-for-release">Today</span>' : ''}</span></div>`).join('')
    : '<p class="subtle">No upcoming appointments. Open a record and use Update / schedule.</p>';
  const statusOrder = [...$('status-filter').options].map(option => option.value).filter(Boolean);   // workflow order
  $('overview-status').innerHTML = barsHtml(
    statusOrder.filter(status => status in stats.by_status).map(status => [status, stats.by_status[status]]), '', false);
  $('overview-course').innerHTML = barsHtml(Object.entries(stats.by_course));
  $('overview-kind').innerHTML = barsHtml(Object.entries(stats.by_request_type));
}

/* ── Chatbot feedback (coordinator) ── */
const FEEDBACK_FILTERS = [
  ['review', 'Needs review', '?rating=down&status=New', s => s.new_down],
  ['down', 'All 👎', '?rating=down', s => s.down],
  ['up', '👍', '?rating=up', s => s.up],
  ['all', 'Everything', '', s => s.total],
];
const topicName = (intent) => intentLabels[intent] || String(intent || 'Unknown').replaceAll('_', ' ');

async function loadFeedback() {
  if (workspaceRole !== 'coordinator') return;
  const filter = FEEDBACK_FILTERS.find(f => f[0] === feedbackFilter) || FEEDBACK_FILTERS[0];
  try {
    const data = await api(`/api/feedback${filter[2]}`);
    intentLabels = data.intent_labels || {};
    knownIntents = data.intents || [];
    renderFeedback(data);
  } catch (error) { notify(error.message); }
}

function renderFeedback({ summary, items }) {
  badgeCounts.feedback = summary.new_down;
  renderBadges();
  $('feedback-tiles').innerHTML = [
    { label: 'Found helpful', value: summary.helpful_percent === null ? '–' : `${summary.helpful_percent}%`, detail: `${summary.up} 👍 · ${summary.down} 👎` },
    { label: 'Needs review', value: summary.new_down, detail: '👎 not yet reviewed', alert: true },
    { label: 'Questions shared', value: summary.shared, detail: 'People who ticked “share”' },
    { label: 'Total ratings', value: summary.total, detail: 'All time' },
  ].map(tileHtml).join('');
  const struggled = summary.by_intent.filter(row => row.down > 0);
  $('feedback-topics').innerHTML = struggled.length
    ? struggled.map(row => `<div class="bar-row"><span>${escapeHtml(topicName(row.intent))}</span><span class="bar-track" aria-hidden="true"><span class="bar-fill down" style="width:${Math.round(100 * row.down / Math.max(1, struggled[0].down))}%"></span></span><b>👎 ${row.down} · 👍 ${row.up}</b></div>`).join('')
    : '<p class="subtle">No 👎 ratings yet.</p>';
  $('feedback-reasons').innerHTML = barsHtml(Object.entries(summary.by_reason), 'down');
  $('feedback-chips').innerHTML = FEEDBACK_FILTERS.map(([id, label, , count]) =>
    `<button type="button" class="filter-chip" data-feedback-filter="${id}" aria-pressed="${feedbackFilter === id}">${escapeHtml(label)} <b>${count(summary)}</b></button>`
  ).join('');
  if (!items.length) {
    $('feedback-list').innerHTML = emptyState(feedbackFilter === 'review'
      ? 'Nothing waiting for review. When someone rates an answer 👎 it shows up here.'
      : 'No feedback in this view yet.');
    return;
  }
  const options = (current) => '<option value="">Not set</option>' + knownIntents.map(intent =>
    `<option value="${escapeHtml(intent)}"${intent === current ? ' selected' : ''}>${escapeHtml(topicName(intent))}</option>`).join('');
  $('feedback-list').innerHTML = items.map(item => {
    const down = item.rating === 'down';
    return `<article class="tool-row feedback-item">
      <div>
        <h3>${down ? '👎' : '👍'} ${escapeHtml(down ? (item.reason || 'No reason given') : 'Found helpful')}</h3>
        <p>Answered as <strong>${escapeHtml(topicName(item.intent))}</strong> · match ${Number(item.confidence).toFixed(2)} · ${escapeHtml(item.workspace_role || 'unknown role')} · ${escapeHtml(item.timestamp)}</p>
        ${item.shared_question ? `<blockquote>${escapeHtml(item.shared_question)}</blockquote>` : '<p class="subtle">The person did not share their question.</p>'}
        ${item.comment ? `<p>“${escapeHtml(item.comment)}”</p>` : ''}
      </div>
      ${down ? `<div class="row-actions">
        <label class="fix-topic">The right topic was<select data-feedback-topic="${item.id}">${options(item.correct_intent)}</select></label>
        <span class="status ${statusClass(item.status)}">${escapeHtml(item.status)}</span>
        <button type="button" class="${item.status === 'New' ? 'primary' : 'text-button'}" data-feedback-status="${item.id}" data-status="${item.status === 'New' ? 'Reviewed' : 'New'}">${item.status === 'New' ? 'Mark reviewed' : 'Reopen'}</button>
      </div>` : ''}
    </article>`;
  }).join('');
}

async function reviewFeedback(id, changes) {
  await api(`/api/feedback/${id}`, postJson(changes, 'PATCH'));
}

async function loadActivity() {
  try {
    const data = await api('/api/activity');
    $('activity-list').innerHTML = data.events.length
      ? data.events.map(row => `
        <article class="tool-row">
          <div>
            <h3>${escapeHtml(row.action)}</h3>
            <p>${escapeHtml(row.actor_role)} · ${escapeHtml(row.detail)} · ${escapeHtml(row.timestamp)}</p>
          </div>
        </article>`).join('')
      : emptyState('No activity yet. Changes will appear here.');
  } catch (error) { notify(error.message); }
}

function gateHint(status) {
  if (status === 'Released') return '<span class="status ready-for-release">Released — may enter with physical ID</span>';
  if (status === 'Ready for release') return '<span class="status pending">Ready for pickup — send to PMO if they need the card</span>';
  return `<span class="status processing">ID is ${escapeHtml(status)} — not a completed card</span>`;
}

function verifyCard(row) {
  return `<article class="tool-row">
    <div>
      <h3>${escapeHtml(row.name)}</h3>
      <p>${escapeHtml(row.student_id)} · ${escapeHtml(row.course)} · Section ${escapeHtml(row.section)} · Year ${escapeHtml(row.year_level)}</p>
      ${gateHint(row.status)}
    </div>
    <div class="row-actions">
      <button type="button" class="gate-btn" data-gate-log="in" data-student="${escapeHtml(row.student_id)}">Check in</button>
      <button type="button" class="gate-btn out" data-gate-log="out" data-student="${escapeHtml(row.student_id)}">Check out</button>
      <button type="button" class="text-button" data-verify-select="${row.id}" data-student="${escapeHtml(row.student_id)}">Use in chat</button>
      <a href="/student/${encodeURIComponent(row.token)}" target="_blank" rel="noopener">Open profile ↗</a>
    </div>
  </article>`;
}

/* One tap logs the gate event for a verified student (the manual form is still there for visitors). */
async function logGate(button) {
  const direction = button.dataset.gateLog;
  button.disabled = true;
  try {
    const row = await api('/api/gate', postJson({ student_id: button.dataset.student, direction }));
    notify(`${direction === 'in' ? 'Checked in' : 'Checked out'} ${row.name} at ${formatTime(row.timestamp)}.`);
    loadGate();
  } catch (error) {
    notify(error.message);
  } finally {
    setTimeout(() => { button.disabled = false; }, 1200);   // stops a double tap from logging twice
  }
}

async function runVerify(query, { fromScan = false } = {}) {
  try {
    const data = await api(`/api/verify?q=${encodeURIComponent(query)}`);
    if (fromScan && data.matches.length === 1) {
      $('verify-query').value = data.matches[0].student_id;     // show the ID, not the long token
      notify(`Scanned ${data.matches[0].name}.`);
    }
    $('verify-result').innerHTML = data.matches.length
      ? data.matches.map(verifyCard).join('')
      : emptyState(fromScan
        ? 'That QR code does not match any student. The record may have been deleted. File an incident if they have no ID.'
        : 'No matching student. File an incident if they have no ID.');
  } catch (error) {
    $('verify-result').innerHTML = emptyState(escapeHtml(error.message));
  }
}

/* ── QR scanner (camera) ──
   The decoder (jsQR, vendored in static/vendor) is loaded only when the scanner opens, so it works in
   any browser with camera access. getUserMedia needs a secure page: https:// or localhost. */
let scanStream = null, scanTimer = 0, jsQrLoading = null;
const scanCanvas = document.createElement('canvas');

function loadJsQr() {
  if (window.jsQR) return Promise.resolve(window.jsQR);
  jsQrLoading ??= new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = '/static/vendor/jsQR.js';
    script.onload = () => (window.jsQR ? resolve(window.jsQR) : reject(new Error('The QR reader did not start.')));
    script.onerror = () => { jsQrLoading = null; reject(new Error('The QR reader could not be loaded.')); };
    document.head.appendChild(script);
  });
  return jsQrLoading;
}

function stopScanner() {
  clearTimeout(scanTimer);
  scanStream?.getTracks().forEach(track => track.stop());
  scanStream = null;
  $('scan-video').srcObject = null;
}

/* Only QR codes made by this app (a /student/<token> link) are accepted. */
function tokenFromScan(raw) {
  const match = String(raw || '').trim().match(/\/student\/([A-Za-z0-9_-]{8,100})(?:[/?#]|$)/);
  return match ? match[1] : '';
}

function decodeFrame(video) {
  const width = video.videoWidth, height = video.videoHeight;
  if (!width || !height) return '';
  const scale = Math.min(1, 640 / width);
  scanCanvas.width = Math.round(width * scale);
  scanCanvas.height = Math.round(height * scale);
  const context = scanCanvas.getContext('2d', { willReadFrequently: true });
  context.drawImage(video, 0, 0, scanCanvas.width, scanCanvas.height);
  const image = context.getImageData(0, 0, scanCanvas.width, scanCanvas.height);
  const code = window.jsQR(image.data, image.width, image.height, { inversionAttempts: 'dontInvert' });
  return code ? code.data : '';
}

function scanLoop() {
  const video = $('scan-video');
  const tick = () => {
    if (!scanStream) return;
    try {
      const raw = video.readyState >= 2 ? decodeFrame(video) : '';
      if (raw) {
        const token = tokenFromScan(raw);
        if (token) { finishScan(token); return; }
        $('scan-status').textContent = 'That QR code is not from this system. Try the sticker or card again.';
      }
    } catch { /* one unreadable frame is fine; try the next */ }
    scanTimer = setTimeout(tick, 150);
  };
  tick();
}

function finishScan(token) {
  $('scan-dialog').close();
  $('verify-query').value = token;
  runVerify(token, { fromScan: true });
}

async function openScanner() {
  const dialog = $('scan-dialog'), video = $('scan-video'), status = $('scan-status');
  video.hidden = true;
  status.textContent = 'Starting the camera…';
  dialog.showModal();
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    status.textContent = 'The camera only works on https:// pages or on localhost. Open this app that way, or type the student ID instead.';
    return;
  }
  try {
    await loadJsQr();
    const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
    if (!dialog.open) { stream.getTracks().forEach(track => track.stop()); return; }   // closed while the permission prompt was open
    scanStream = stream;
    video.srcObject = stream;
    video.hidden = false;
    await video.play();
    if (!dialog.open) { stopScanner(); return; }
    status.textContent = 'Point the camera at the QR code on the sticker or card.';
    scanLoop();
  } catch (error) {
    stopScanner();
    video.hidden = true;
    if (error && error.name === 'AbortError') return;      // the dialog was closed while the camera was starting
    status.textContent = error && error.name === 'NotAllowedError'
      ? 'Camera permission was denied. Allow it in the browser settings, or type the student ID instead.'
      : error && error.name === 'NotFoundError'
        ? 'No camera was found on this device. Type the student ID instead.'
        : `${error && error.message ? error.message : 'The camera could not start.'} Type the student ID instead.`;
  }
}

async function loadGate() {
  try {
    const data = await api('/api/gate');
    $('gate-summary').innerHTML = `<span><strong>${data.today.in}</strong>in today</span><span><strong>${data.today.out}</strong>out today</span>`;
    $('gate-list').innerHTML = data.logs.length
      ? data.logs.map(row => `
        <article class="tool-row">
          <div>
            <h3>${escapeHtml(row.direction === 'in' ? 'Check in' : 'Check out')} · ${escapeHtml(row.name)}</h3>
            <p>${escapeHtml(row.student_id || 'Visitor')} · ${escapeHtml(row.timestamp)}</p>
            <p>${escapeHtml(row.note || '')}</p>
          </div>
        </article>`).join('')
      : emptyState('No gate events yet.');
  } catch (error) { notify(error.message); }
}

async function loadIncidents() {
  try {
    const data = await api('/api/incidents');
    $('incident-list').innerHTML = data.incidents.length
      ? data.incidents.map(row => `
        <article class="tool-row">
          <div>
            <h3>${escapeHtml(row.category)} · ${escapeHtml(row.status)}</h3>
            <p>${escapeHtml(row.location || 'Unspecified')} · ${escapeHtml(row.student_id || 'No student ID')} · ${escapeHtml(row.timestamp)}</p>
            <p>${escapeHtml(row.detail)}</p>
          </div>
          <div class="row-actions">
            ${row.status === 'Open'
              ? `<button type="button" class="primary" data-incident="${row.id}" data-status="Resolved">Resolve</button>`
              : `<button type="button" class="text-button" data-incident="${row.id}" data-status="Open">Reopen</button>`}
          </div>
        </article>`).join('')
      : emptyState('No incidents filed.');
  } catch (error) { notify(error.message); }
}

function renderMyId(row) {
  selected = row.id;
  if (!records.some(r => r.id === row.id)) records = [row, ...records.filter(r => r.id !== row.id)];
  $('selected-student').textContent = `📌 ${row.name} — ${row.student_id}`;
  updateSuggestions();
  lookedUpStudent = row.student_id;
  $('student-request-form').elements.student_id.value = row.student_id;   // saves retyping on the request form
  $('student-request-form').elements.name.value = row.name;
  loadMyRequests(row.student_id);
  $('my-id-card').innerHTML = `
    <article class="my-id">
      <img src="/qr/${row.id}.png" alt="QR code for ${escapeHtml(row.student_id)}">
      <div>
        <h3>${escapeHtml(row.name)}</h3>
        <p>${escapeHtml(row.student_id)} · ${escapeHtml(row.course)} · Section ${escapeHtml(row.section)} · Year ${escapeHtml(row.year_level)}</p>
        <p><span class="status ${statusClass(row.status)}">${escapeHtml(row.status)}</span> ${escapeHtml(row.request_type)}</p>
        <p class="appointment">${row.schedule_date ? `${escapeHtml(row.schedule_type)} · <strong>${escapeHtml(row.schedule_date)} ${escapeHtml(row.schedule_time)}</strong>` : 'No appointment scheduled yet.'}</p>
        ${row.pending_reason ? `<p class="reason">${escapeHtml(row.pending_reason)}</p>` : ''}
        <div class="record-actions">
          <a href="/sticker/${row.id}" target="_blank" rel="noopener">Print my QR sticker</a>
          <a href="/card/${row.id}" target="_blank" rel="noopener">Preview sample ID card</a>
          <button type="button" class="primary" data-ask-status>Ask assistant about my ID</button>
        </div>
      </div>
    </article>`;
}

/* A student sees only the type, status and time of their own requests, not the staff's handling. */
const REQUEST_HELP = {
  Submitted: 'Waiting for the office to look at it.',
  'In review': 'Staff are looking at it.',
  Accepted: 'Accepted. Follow the office’s instructions.',
  Closed: 'Finished.',
};

async function loadMyRequests(studentId = lookedUpStudent) {
  if (!studentId) return;
  let html = '';
  try {
    const data = await api('/api/student/requests', postJson({ student_id: studentId }));
    if (data.requests.length) {
      html = '<h3 class="list-title">Your requests</h3>' + data.requests.map(row => `
        <article class="tool-row">
          <div>
            <h3>${escapeHtml(row.request_type)} <span class="subtle">#${Number(row.id)}</span></h3>
            <p>${escapeHtml(row.timestamp)} · ${escapeHtml(REQUEST_HELP[row.status] || '')}</p>
          </div>
          <div class="row-actions"><span class="status ${statusClass(row.status)}">${escapeHtml(row.status)}</span></div>
        </article>`).join('');
    }
  } catch { /* the tracker is a convenience; the rest of the page still works */ }
  $('my-requests').innerHTML = html;
  $('request-tracker').innerHTML = html;
}

/* ── Event wiring ── */
for (const id of ['search', 'status-filter', 'course-filter', 'year-filter', 'section-filter', 'kind-filter']) {
  $(id).addEventListener('input', render);
  $(id).addEventListener('change', render);
}
$('reset-filters').onclick = () => {
  for (const id of ['search', 'status-filter', 'course-filter', 'year-filter', 'section-filter', 'kind-filter']) $(id).value = '';
  attentionFilter = '';
  render();
};
$('stats').onclick = event => {
  const card = event.target.closest('[data-status-filter]');
  if (!card) return;
  $('status-filter').value = card.dataset.statusFilter;
  render();
};
$('records').onclick = event => {
  const select = event.target.closest('[data-select]');
  const edit = event.target.closest('[data-edit]');
  const del = event.target.closest('[data-delete]');
  const release = event.target.closest('[data-release]');
  const bulk = event.target.closest('.bulk-check');
  if (release) { markReleased(Number(release.dataset.release), release); return; }
  if (bulk) {
    const id = Number(bulk.dataset.bulk);
    if (bulk.checked) bulkSelected.add(id); else bulkSelected.delete(id);
    updateBulkToolbar();
    return;
  }
  if (select) selectRecord(Number(select.dataset.select));
  if (edit) openDialog(records.find(r => r.id === Number(edit.dataset.edit)));
  if (del) deleteRecord(Number(del.dataset.delete));
};
$('clear-selected').onclick = () => {
  selected = null;
  $('selected-student').textContent = 'No student selected';
  if (workspaceRole === 'coordinator') render();
  updateSuggestions();
};
$('new-record').onclick = () => openDialog();
$('close-dialog').onclick = () => $('record-dialog').close();
$('record-form').onsubmit = async event => {
  event.preventDefault();
  $('save-record').disabled = true;
  const body = Object.fromEntries(new FormData(event.target));
  try {
    await api(editing ? `/api/inventory/${editing}` : '/api/inventory', {
      method: editing ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    $('record-dialog').close();
    await load();
    notify('Record saved.');
  } catch (error) {
    $('form-error').textContent = error.message;
  } finally {
    $('save-record').disabled = false;
  }
};
function updateChatCount() {
  const length = $('chat-input').value.length;
  $('chat-count').hidden = length < 800;
  $('chat-count').textContent = `${length} / ${$('chat-input').maxLength}`;
  $('chat-count').classList.toggle('near', length >= 950);
}
$('chat-input').addEventListener('input', updateChatCount);
$('chat-form').onsubmit = event => {
  event.preventDefault();
  const text = $('chat-input').value.trim();
  if (text && !sending) { $('chat-input').value = ''; updateChatCount(); send(text); }
};
$('new-chat').onclick = async () => {
  historyLoadId++;
  try {
    await api(`/api/chat/history?session_id=${encodeURIComponent(sessionId())}`, { method: 'DELETE' });
  } catch { /* ignore */ }
  $('messages').innerHTML = '';
  addMessage('New chat started. How can I help?', 'bot', { confidence: 0 });
};
/* Thumbs, feedback panel and Try-again chips live inside the messages, so one listener on the log handles them. */
$('messages').addEventListener('click', event => {
  const rate = event.target.closest('[data-rate]');
  if (rate) rateMessage(rate.closest('.message'), rate.dataset.rate);
  if (event.target.closest('[data-feedback-skip]')) togglePanel(event.target.closest('.message'), false);
});
$('messages').addEventListener('submit', event => {
  const form = event.target.closest('.feedback-panel');
  if (!form) return;
  event.preventDefault();
  sendFeedbackDetails(form.closest('.message'), form);
});
$('bulk-apply').onclick = bulkUpdateStatus;
$('bulk-stickers').onclick = printStickers;
$('scan-btn').onclick = openScanner;
$('scan-close').onclick = () => $('scan-dialog').close();
$('scan-dialog').addEventListener('close', stopScanner);
$('feedback-export').onclick = () => {
  window.open('/api/feedback/export', '_blank');
  notify('Downloading feedback CSV…');
};
$('bulk-clear').onclick = () => { bulkSelected.clear(); render(); };
$('select-all-btn').onclick = selectAllVisible;
$('export-btn').onclick = exportCSV;
$('theme-toggle').onclick = toggleTheme;
$('menu-toggle').onclick = toggleMobileNav;
$('nav-backdrop').onclick = closeMobileNav;
$('switch-role').onclick = () => $('role-dialog').showModal();
$('role-form').addEventListener('submit', event => {
  const role = event.submitter && event.submitter.value;
  if (role) setRole(role);
});
$('role-dialog').addEventListener('cancel', event => {
  if (!workspaceRole) event.preventDefault();
});

document.addEventListener('click', event => {
  const nav = event.target.closest('[data-view]');
  if (nav) {
    event.preventDefault();
    showView(nav.dataset.view);
  }
  const queueSelect = event.target.closest('[data-queue-select]');
  if (queueSelect) {
    selectRecord(Number(queueSelect.dataset.queueSelect));
    showView('assistant');
  }
  const attention = event.target.closest('[data-attention]');
  if (attention) {
    attentionFilter = attention.dataset.attention === attentionFilter ? '' : attention.dataset.attention;
    render();
  }
  const jump = event.target.closest('[data-attention-jump]');
  if (jump) {                                   // an Overview tile: open the inventory already filtered
    attentionFilter = jump.dataset.attentionJump;
    showView('inventory');
    render();
  }
  const requestChip = event.target.closest('[data-request-filter]');
  if (requestChip) {
    requestFilter = requestChip.dataset.requestFilter;
    loadRequests();
  }
  const feedbackChip = event.target.closest('[data-feedback-filter]');
  if (feedbackChip) {
    feedbackFilter = feedbackChip.dataset.feedbackFilter;
    loadFeedback();
  }
  const feedbackStatus = event.target.closest('[data-feedback-status]');
  if (feedbackStatus) {
    feedbackStatus.disabled = true;
    reviewFeedback(feedbackStatus.dataset.feedbackStatus, { status: feedbackStatus.dataset.status })
      .then(() => { notify(feedbackStatus.dataset.status === 'Reviewed' ? 'Marked as reviewed.' : 'Moved back to needs review.'); loadFeedback(); loadStats(); loadActivity(); })
      .catch(err => { notify(err.message); feedbackStatus.disabled = false; });
  }
  const gateLogBtn = event.target.closest('[data-gate-log]');
  if (gateLogBtn) logGate(gateLogBtn);
  const reqBtn = event.target.closest('[data-request-status]');
  if (reqBtn) {
    reqBtn.disabled = true;
    api(`/api/requests/${reqBtn.dataset.requestStatus}`, postJson({ status: reqBtn.dataset.status }, 'PATCH'))
      .then(() => { notify(`Request marked ${reqBtn.dataset.status}.`); loadRequests(); load(); loadActivity(); })
      .catch(err => { notify(err.message); loadRequests(); });   // reload: someone else may have moved it already
  }
  const verifySelect = event.target.closest('[data-verify-select]');
  if (verifySelect) {
    selected = Number(verifySelect.dataset.verifySelect);
    const name = verifySelect.closest('.tool-row').querySelector('h3').textContent;
    $('selected-student').textContent = `📌 ${name} — ${verifySelect.dataset.student}`;
    updateSuggestions();
    showView('assistant');
    $('chat-input').focus();
  }
  const incidentBtn = event.target.closest('[data-incident]');
  if (incidentBtn) {
    api(`/api/incidents/${incidentBtn.dataset.incident}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: incidentBtn.dataset.status }),
    }).then(() => { notify(`Incident ${incidentBtn.dataset.status.toLowerCase()}.`); loadIncidents(); }).catch(err => notify(err.message));
  }
  if (event.target.closest('[data-ask-status]')) {
    showView('assistant');
    send('What is the status of my student ID?');
  }
});

document.addEventListener('change', event => {
  const topic = event.target.closest('[data-feedback-topic]');
  if (!topic) return;
  topic.disabled = true;
  reviewFeedback(topic.dataset.feedbackTopic, { correct_intent: topic.value })
    .then(() => notify(topic.value ? `Saved. The right topic is “${topicName(topic.value)}”.` : 'Topic cleared.'))
    .catch(err => notify(err.message))
    .finally(() => { topic.disabled = false; });
});

$('verify-form').onsubmit = event => {
  event.preventDefault();
  runVerify($('verify-query').value.trim());
};

$('gate-form').onsubmit = async event => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.target));
  try {
    await api('/api/gate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    event.target.reset();
    notify('Gate event saved.');
    loadGate();
  } catch (error) { notify(error.message); }
};

$('incident-form').onsubmit = async event => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.target));
  try {
    await api('/api/incidents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    event.target.reset();
    notify('Incident filed.');
    loadIncidents();
  } catch (error) { notify(error.message); }
};

$('lookup-form').onsubmit = async event => {
  event.preventDefault();
  try {
    const data = await api('/api/student/lookup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ student_id: $('lookup-id').value.trim() }),
    });
    renderMyId(data.record);
    notify(`Found ${data.record.name}.`);
  } catch (error) {
    $('my-id-card').innerHTML = emptyState(escapeHtml(error.message));
  }
};

$('student-request-form').onsubmit = async event => {
  event.preventDefault();
  const form = event.target;
  const body = Object.fromEntries(new FormData(form));
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    const created = await api('/api/requests', postJson(body));
    form.reset();
    lookedUpStudent = created.student_id;
    notify(`Request #${created.id} submitted. It is now “${created.status}”; you can follow it below.`, 6000);
    loadMyRequests(created.student_id);
  } catch (error) {
    notify(error.message);
  } finally {
    button.disabled = false;
  }
};

document.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
    event.preventDefault();
    if (workspaceRole === 'coordinator') { showView('inventory'); $('search').focus(); $('search').select(); }
    else if (workspaceRole === 'guard') { showView('verify'); $('verify-query').focus(); }
    else { showView('my-id'); $('lookup-id').focus(); }
  }
  if ((event.ctrlKey || event.metaKey) && event.key === 'n') {
    event.preventDefault();
    if (workspaceRole === 'coordinator') openDialog();
    else if (workspaceRole === 'student') showView('student-request');
    else showView('incidents');
  }
  if (event.key === 'Escape') {
    if ($('record-dialog').open) $('record-dialog').close();
    closeMobileNav();
  }
  if ((event.ctrlKey || event.metaKey) && event.key === 'e') {
    event.preventDefault();
    if (workspaceRole === 'coordinator') exportCSV();
  }
});

/* New requests and feedback arrive from other people's browsers, so the coordinator's badges and
   Overview refresh every minute and whenever the tab becomes visible again. */
setInterval(() => { if (!document.hidden) loadStats(); }, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadStats(); });

window.addEventListener('hashchange', () => {
  const view = location.hash.replace('#', '');
  if (view && workspaceRole) showView(view);
});
window.addEventListener('resize', () => {
  if (workspaceRole) {
    const view = location.hash.replace('#', '') || NAV[workspaceRole][0].id;
    showView(view);
  }
});

initTheme();
if (workspaceRole && ROLE_META[workspaceRole]) {
  setRole(workspaceRole);
} else {
  renderNav();
  $('role-dialog').showModal();
  loadChatHistory();
}
updateSuggestions();
