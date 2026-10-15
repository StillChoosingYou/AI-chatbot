/* School ID property management – role-based frontend */
let records = [], selected = null, editing = null, bulkSelected = new Set();
let workspaceRole = localStorage.getItem('workspaceRole') || '';
const baseSession = localStorage.getItem('chatSessionId') || (() => {
  const id = crypto.randomUUID();
  localStorage.setItem('chatSessionId', id);
  return id;
})();

const NAV = {
  coordinator: [
    { id: 'inventory', label: '▤  ID inventory' },
    { id: 'queue', label: '📅  Queue' },
    { id: 'requests', label: '📥  Requests' },
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

function renderMarkdown(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get('content-type') || '';
  const body = contentType.includes('application/json') ? await response.json() : {};
  if (!response.ok) throw new Error(body.error || 'Unable to complete the request.');
  return body;
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
  const label = (NAV[workspaceRole] || []).find(item => item.id === viewId);
  $('topbar-section').textContent = label ? label.label.replace(/^[^\w]+/, '').trim() : 'Home';
  history.replaceState(null, '', `#${viewId}`);
  closeMobileNav();

  if (viewId === 'queue') loadQueue();
  if (viewId === 'requests') loadRequests();
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
  } catch (error) { notify(error.message); }
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

  const pendingCount = records.filter(r => r.status === 'Pending').length;
  document.querySelectorAll('#role-nav .nav').forEach(nav => {
    nav.querySelector('.notification-badge')?.remove();
    if (nav.dataset.view === 'inventory' && pendingCount > 0) {
      nav.insertAdjacentHTML('beforeend', `<span class="notification-badge pulse">${pendingCount}</span>`);
    }
  });

  const query = $('search').value.trim().toLowerCase();
  const shown = records.filter(r =>
    `${r.name} ${r.student_id}`.toLowerCase().includes(query) &&
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
            <div class="student-tags"><span>${escapeHtml(r.course)}</span><span>Section ${escapeHtml(r.section)}</span><span>Year ${r.year_level}</span></div>
            <div class="record-actions">
              <a href="/sticker/${r.id}" target="_blank" rel="noopener">Print sticker ↗</a>
              <a href="/card/${r.id}" target="_blank" rel="noopener">Preview card ↗</a>
              <button type="button" data-select="${r.id}">Ask about ID</button>
              <button type="button" data-delete="${r.id}" class="text-button danger-text">Delete</button>
            </div>
          </div>
        </div>
        <div class="status-side">
          <span class="status ${r.status.toLowerCase().replaceAll(' ','-')}"><span>●</span> ${escapeHtml(r.status)}</span>
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

function addMessage(text, kind, meta = {}) {
  const node = document.createElement('div');
  node.className = `message ${kind}`;
  let html = renderMarkdown(text);
  html += `<div class="message-time">${meta.timestamp ? formatTime(meta.timestamp) : formatNow()}</div>`;
  if (kind === 'bot' && meta.confidence !== undefined && meta.confidence > 0) {
    const pct = Math.round(meta.confidence * 100);
    const cls = pct >= 80 ? 'high' : pct >= 50 ? 'medium' : 'low';
    html += `<span class="confidence-badge ${cls}">${pct}% confidence</span>`;
  }
  if (kind === 'bot' && meta.actions && meta.actions.length > 0) {
    html += '<div class="action-chips">' + meta.actions.map(a =>
      `<button type="button" class="action-chip" data-action-type="${escapeHtml(a.type)}">${escapeHtml(a.label)}</button>`
    ).join('') + '</div>';
  }
  if (kind === 'bot') {
    html += '<div class="emoji-reactions"><button type="button" class="emoji-btn" data-reaction="up">👍</button><button type="button" class="emoji-btn" data-reaction="down">👎</button></div>';
  }
  node.innerHTML = html;
  $('messages').appendChild(node);
  $('messages').scrollTop = $('messages').scrollHeight;
  node.querySelectorAll('.action-chip').forEach(btn => {
    btn.onclick = () => handleAction(btn.dataset.actionType);
  });
  node.querySelectorAll('.emoji-btn').forEach(btn => {
    btn.onclick = () => {
      node.querySelectorAll('.emoji-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      notify(btn.dataset.reaction === 'up' ? 'Thanks for the feedback.' : 'Thanks — try a shorter question or pick a suggestion.');
    };
  });
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

async function send(text) {
  addMessage(text, 'user');
  $('chat-form').querySelector('button').disabled = true;
  showTyping();
  await new Promise(r => setTimeout(r, 250));
  try {
    const response = await api('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: text,
        record_id: selected,
        session_id: sessionId(),
        workspace_role: workspaceRole || 'coordinator',
      }),
    });
    hideTyping();
    addMessage(response.reply, 'bot', {
      confidence: response.confidence,
      actions: response.actions,
      timestamp: response.timestamp,
    });
  } catch (error) {
    hideTyping();
    addMessage(error.message, 'bot');
  } finally {
    $('chat-form').querySelector('button').disabled = false;
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
  $('messages').innerHTML = '';
  addMessage(
    workspaceRole
      ? `Hi! You are in the ${ROLE_META[workspaceRole].title.toLowerCase()} workspace. ${ROLE_META[workspaceRole].blurb}`
      : 'Hi! Choose a role so I can show the right tools.',
    'bot',
    { confidence: 0 }
  );
  if (!workspaceRole) return;
  try {
    const data = await api(`/api/chat/history?session_id=${encodeURIComponent(sessionId())}`);
    if (data.messages && data.messages.length > 0) {
      $('messages').innerHTML = '';
      data.messages.forEach(m => addMessage(m.message, m.role, {
        confidence: m.confidence,
        timestamp: m.timestamp,
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
    $('queue-list').innerHTML = data.appointments.map(row => `
      <article class="tool-row">
        <div>
          <h3>${escapeHtml(row.name)}</h3>
          <p>${escapeHtml(row.student_id)} · ${escapeHtml(row.course)} · ${escapeHtml(row.status)}</p>
          <p class="appointment"><strong>${escapeHtml(row.schedule_type)}</strong> · ${escapeHtml(row.schedule_date)} ${escapeHtml(row.schedule_time)}</p>
        </div>
        <div class="row-actions">
          ${row.schedule_date === data.today ? '<span class="status ready-for-release">Today</span>' : ''}
          <button type="button" class="text-button" data-queue-select="${row.id}">Ask about ID</button>
        </div>
      </article>`).join('');
  } catch (error) { notify(error.message); }
}

async function loadRequests() {
  try {
    const data = await api('/api/requests');
    if (!data.requests.length) {
      $('request-inbox').innerHTML = emptyState('No student requests yet.');
      return;
    }
    $('request-inbox').innerHTML = data.requests.map(row => `
      <article class="tool-row">
        <div>
          <h3>${escapeHtml(row.request_type)} · ${escapeHtml(row.student_id)}</h3>
          <p>${escapeHtml(row.name || 'Unnamed')} · ${escapeHtml(row.status)} · ${escapeHtml(row.timestamp)}</p>
          <p>${escapeHtml(row.detail || 'No details')}</p>
        </div>
        <div class="row-actions">
          ${row.status !== 'Accepted' ? `<button type="button" class="primary" data-request-status="${row.id}" data-status="Accepted">Accept</button>` : ''}
          ${row.status !== 'Closed' ? `<button type="button" class="text-button" data-request-status="${row.id}" data-status="Closed">Close</button>` : ''}
          ${row.status === 'Submitted' ? `<button type="button" class="text-button" data-request-status="${row.id}" data-status="In review">Mark in review</button>` : ''}
        </div>
      </article>`).join('');
  } catch (error) { notify(error.message); }
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
      <p>${escapeHtml(row.student_id)} · ${escapeHtml(row.course)} · Section ${escapeHtml(row.section)} · Year ${row.year_level}</p>
      ${gateHint(row.status)}
    </div>
    <div class="row-actions">
      <button type="button" class="primary" data-verify-select="${row.id}" data-student="${escapeHtml(row.student_id)}">Use in chat</button>
      <button type="button" class="text-button" data-gate-student="${escapeHtml(row.student_id)}" data-gate-name="${escapeHtml(row.name)}">Check in</button>
      <a href="/student/${encodeURIComponent(row.token)}" target="_blank" rel="noopener">Open profile ↗</a>
    </div>
  </article>`;
}

async function loadGate() {
  try {
    const data = await api('/api/gate');
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
  $('my-id-card').innerHTML = `
    <article class="my-id">
      <img src="/qr/${row.id}.png" alt="QR code for ${escapeHtml(row.student_id)}">
      <div>
        <h3>${escapeHtml(row.name)}</h3>
        <p>${escapeHtml(row.student_id)} · ${escapeHtml(row.course)} · Section ${escapeHtml(row.section)} · Year ${row.year_level}</p>
        <p><span class="status ${row.status.toLowerCase().replaceAll(' ','-')}">${escapeHtml(row.status)}</span> ${escapeHtml(row.request_type)}</p>
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

/* ── Event wiring ── */
for (const id of ['search', 'status-filter', 'course-filter', 'year-filter', 'section-filter', 'kind-filter']) {
  $(id).addEventListener('input', render);
  $(id).addEventListener('change', render);
}
$('reset-filters').onclick = () => {
  for (const id of ['search', 'status-filter', 'course-filter', 'year-filter', 'section-filter', 'kind-filter']) $(id).value = '';
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
  const bulk = event.target.closest('.bulk-check');
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
$('chat-form').onsubmit = event => {
  event.preventDefault();
  const text = $('chat-input').value.trim();
  if (text) { $('chat-input').value = ''; send(text); }
};
$('new-chat').onclick = async () => {
  try {
    await api(`/api/chat/history?session_id=${encodeURIComponent(sessionId())}`, { method: 'DELETE' });
  } catch { /* ignore */ }
  $('messages').innerHTML = '';
  addMessage('New chat started. How can I help?', 'bot', { confidence: 0 });
};
$('bulk-apply').onclick = bulkUpdateStatus;
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
  const reqBtn = event.target.closest('[data-request-status]');
  if (reqBtn) {
    api(`/api/requests/${reqBtn.dataset.requestStatus}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: reqBtn.dataset.status }),
    }).then(() => { notify(`Request marked ${reqBtn.dataset.status}.`); loadRequests(); load(); }).catch(err => notify(err.message));
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
  const gatePrefill = event.target.closest('[data-gate-student]');
  if (gatePrefill) {
    showView('gate');
    $('gate-form').elements.student_id.value = gatePrefill.dataset.gateStudent;
    $('gate-form').elements.name.value = gatePrefill.dataset.gateName;
    $('gate-form').elements.direction.value = 'in';
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

$('verify-form').onsubmit = async event => {
  event.preventDefault();
  const q = $('verify-query').value.trim();
  try {
    const data = await api(`/api/verify?q=${encodeURIComponent(q)}`);
    $('verify-result').innerHTML = data.matches.length
      ? data.matches.map(verifyCard).join('')
      : emptyState('No matching student. File an incident if they have no ID.');
  } catch (error) {
    $('verify-result').innerHTML = emptyState(escapeHtml(error.message));
  }
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
  const body = Object.fromEntries(new FormData(event.target));
  try {
    await api('/api/requests', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    event.target.reset();
    notify('Request submitted. Staff will see it in the coordinator inbox.');
  } catch (error) { notify(error.message); }
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
