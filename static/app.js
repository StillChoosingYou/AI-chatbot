/* School ID property management – enhanced frontend */
/* globals: fetch, localStorage, window, document, crypto */

let records = [], selected = null, editing = null, bulkSelected = new Set();
const sessionId = localStorage.getItem('chatSessionId') || (() => { const id = crypto.randomUUID(); localStorage.setItem('chatSessionId', id); return id; })();

const $ = (id) => document.getElementById(id);
const escapeHtml = (v) => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

/* ── Light markdown: **bold** ── */
function renderMarkdown(text) {
  return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}

/* ── API helper ── */
async function api(url, options = {}) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Unable to complete the request.');
  return body;
}

/* ── Toast notifications ── */
let toastTimer = null;
function notify(message, duration = 3500) {
  clearTimeout(toastTimer);
  $('toast').textContent = message;
  toastTimer = setTimeout(() => { $('toast').textContent = ''; }, duration);
}

/* ── Dark mode ── */
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

/* ── Time formatting ── */
function formatTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts.replace(' ', 'T'));
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}
function formatNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/* ── Load inventory ── */
async function load() {
  try {
    records = (await api('/api/inventory')).records;
    for (const [field, id, label] of [['course','course-filter','All courses'],['section','section-filter','All sections']]) {
      const prev = $(id).value;
      $(id).innerHTML = `<option value="">${label}</option>` + [...new Set(records.map(r => r[field]))].sort().map(v => `<option>${escapeHtml(v)}</option>`).join('');
      $(id).value = prev;
    }
    render();
  } catch (error) { notify(error.message); }
}

/* ── Render records ── */
function render() {
  /* Stats */
  const groups = [
    ['Total IDs', records.length, 'All student records'],
    ['Pending', records.filter(r => r.status === 'Pending').length, 'Awaiting requirements'],
    ['Ready for release', records.filter(r => r.status === 'Ready for release').length, 'Available for pickup'],
    ['Released', records.filter(r => r.status === 'Released').length, 'Successfully collected'],
  ];
  $('stats').innerHTML = groups.map(([label, total, detail], i) =>
    `<div class="stat"><div>${label}<span class="stat-symbol">${['▤','◷','✓','↗'][i]}</span></div><strong>${total.toString().padStart(2,'0')}</strong><small>${detail}</small></div>`
  ).join('');

  /* Pending notification badge */
  const pendingCount = records.filter(r => r.status === 'Pending').length;
  const navItems = document.querySelectorAll('.nav');
  navItems.forEach(nav => {
    const existing = nav.querySelector('.notification-badge');
    if (existing) existing.remove();
    if (nav.textContent.includes('ID inventory') && pendingCount > 0) {
      nav.insertAdjacentHTML('beforeend', `<span class="notification-badge pulse">${pendingCount}</span>`);
    }
  });

  /* Filter */
  const query = $('search').value.trim().toLowerCase();
  const shown = records.filter(r =>
    `${r.name} ${r.student_id}`.toLowerCase().includes(query) &&
    [['status','status-filter'],['course','course-filter'],['year_level','year-filter'],['section','section-filter'],['request_type','kind-filter']]
      .every(([field, id]) => !$(id).value || String(r[field]) === $(id).value)
  );
  $('count').textContent = `${shown.length} of ${records.length}`;

  /* Records */
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
              <button data-select="${r.id}">Ask about ID</button>
              <button data-delete="${r.id}" class="text-button" style="color:var(--danger)">Delete</button>
            </div>
          </div>
        </div>
        <div class="status-side">
          <span class="status ${r.status.toLowerCase().replaceAll(' ','-')}"><span>●</span> ${escapeHtml(r.status)}</span>
          <p class="appointment">${r.schedule_date ? `${escapeHtml(r.schedule_type)}<br><strong>${escapeHtml(r.schedule_date)} · ${escapeHtml(r.schedule_time)}</strong>` : 'No appointment scheduled'}</p>
          ${r.status === 'Pending' && r.pending_reason ? `<p class="reason">${escapeHtml(r.pending_reason)}</p>` : ''}
          <button class="text-button" data-edit="${r.id}">Update / schedule ↗</button>
        </div>
      </article>`).join('')
    : '<div class="empty">No IDs match these filters.<br>Try another search or reset the filters.</div>';

  updateBulkToolbar();
}

/* ── Bulk actions ── */
function updateBulkToolbar() {
  const toolbar = $('bulk-toolbar');
  if (bulkSelected.size > 0) {
    toolbar.classList.add('visible');
    $('bulk-count').textContent = `${bulkSelected.size} selected`;
  } else {
    toolbar.classList.remove('visible');
  }
}

async function bulkUpdateStatus() {
  const status = $('bulk-status').value;
  if (!status || bulkSelected.size === 0) return;
  try {
    const result = await api('/api/inventory/bulk', {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ record_ids: [...bulkSelected], status }),
    });
    notify(`✅ Updated ${result.updated} record(s) to "${status}".`);
    bulkSelected.clear();
    await load();
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

/* ── Delete record ── */
async function deleteRecord(id) {
  const rec = records.find(r => r.id === id);
  if (!rec || !confirm(`Delete ${rec.name} (${rec.student_id})? This cannot be undone.`)) return;
  try {
    await api(`/api/inventory/${id}`, { method: 'DELETE' });
    if (selected === id) { selected = null; $('selected-student').textContent = 'No student selected'; }
    bulkSelected.delete(id);
    notify(`🗑️ Deleted ${rec.name}.`);
    await load();
  } catch (error) { notify(error.message); }
}

/* ── CSV export ── */
function exportCSV() {
  window.open('/api/inventory/export', '_blank');
  notify('📥 Downloading inventory CSV...');
}

/* ── Filters ── */
for (const id of ['search','status-filter','course-filter','year-filter','section-filter','kind-filter']) {
  $(id).addEventListener('input', render);
  $(id).addEventListener('change', render);
}
$('reset-filters').onclick = () => {
  for (const id of ['search','status-filter','course-filter','year-filter','section-filter','kind-filter']) $(id).value = '';
  render();
};

/* ── Record click handlers ── */
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
  if (select) {
    selected = Number(select.dataset.select);
    const rec = records.find(r => r.id === selected);
    $('selected-student').textContent = rec ? `📌 ${rec.name} — ${rec.student_id}` : 'No student selected';
    render();
    updateSuggestions();
    $('chat-input').focus();
  }
  if (edit) openDialog(records.find(r => r.id === Number(edit.dataset.edit)));
  if (del) deleteRecord(Number(del.dataset.delete));
};

$('clear-selected').onclick = () => {
  selected = null;
  $('selected-student').textContent = 'No student selected';
  render();
  updateSuggestions();
};

/* ── Context-aware suggestions ── */
function updateSuggestions() {
  const container = document.querySelector('.suggestions');
  if (!container) return;
  if (selected) {
    const rec = records.find(r => r.id === selected);
    const suggestions = [
      { q: 'What is the status of my student ID?', label: 'ID status' },
      { q: 'When can I collect my school ID?', label: 'Release schedule' },
      { q: 'Why is my student ID pending?', label: 'Pending reason' },
      { q: 'When is my pending ID follow-up appointment?', label: 'Follow-up date' },
    ];
    if (rec && rec.request_type === 'Lost ID') {
      suggestions.push({ q: 'When is my lost ID replacement schedule?', label: 'Replacement date' });
    }
    container.innerHTML = suggestions.map(s => `<button data-question="${escapeHtml(s.q)}">${s.label}</button>`).join('');
  } else {
    container.innerHTML = [
      { q: 'What is the status of my student ID?', label: 'ID status' },
      { q: 'When can I collect my school ID?', label: 'Release schedule' },
      { q: 'How can I print a small QR sticker?', label: 'QR sticker' },
      { q: 'What can you do?', label: 'Help' },
      { q: 'Download the inventory as CSV', label: 'Export data' },
    ].map(s => `<button data-question="${escapeHtml(s.q)}">${s.label}</button>`).join('');
  }
  container.querySelectorAll('[data-question]').forEach(btn => {
    btn.onclick = () => send(btn.dataset.question);
  });
}

/* ── Dialog ── */
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
    for (const key of ['status','schedule_type','schedule_date','schedule_time','pending_reason']) {
      $('record-form').elements[key].value = row[key];
    }
  }
  $('record-dialog').showModal();
}
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
    notify('✅ Record saved.');
  } catch (error) {
    $('form-error').textContent = error.message;
  } finally {
    $('save-record').disabled = false;
  }
};

/* ── Chat: typing indicator ── */
function showTyping() {
  const indicator = document.createElement('div');
  indicator.className = 'typing-indicator';
  indicator.id = 'typing';
  indicator.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
  $('messages').appendChild(indicator);
  $('messages').scrollTop = $('messages').scrollHeight;
}
function hideTyping() {
  const el = $('typing');
  if (el) el.remove();
}

/* ── Chat: add message to DOM ── */
function addMessage(text, kind, meta = {}) {
  const node = document.createElement('div');
  node.className = `message ${kind}`;

  let html = renderMarkdown(text);

  /* Timestamp */
  const time = meta.timestamp ? formatTime(meta.timestamp) : formatNow();
  html += `<div class="message-time">${time}</div>`;

  /* Confidence badge (bot only) */
  if (kind === 'bot' && meta.confidence !== undefined && meta.confidence > 0) {
    const pct = Math.round(meta.confidence * 100);
    const cls = pct >= 80 ? 'high' : pct >= 50 ? 'medium' : 'low';
    html += `<span class="confidence-badge ${cls}">${pct}% confidence</span>`;
  }

  /* Action chips (bot only) */
  if (kind === 'bot' && meta.actions && meta.actions.length > 0) {
    html += '<div class="action-chips">' + meta.actions.map(a =>
      `<button class="action-chip" data-action-type="${escapeHtml(a.type)}">${escapeHtml(a.label)}</button>`
    ).join('') + '</div>';
  }

  /* Emoji reactions (bot only) */
  if (kind === 'bot') {
    html += '<div class="emoji-reactions"><button class="emoji-btn" data-reaction="up">👍</button><button class="emoji-btn" data-reaction="down">👎</button></div>';
  }

  node.innerHTML = html;
  $('messages').appendChild(node);
  $('messages').scrollTop = $('messages').scrollHeight;

  /* Wire action chips */
  node.querySelectorAll('.action-chip').forEach(btn => {
    btn.onclick = () => handleAction(btn.dataset.actionType);
  });

  /* Wire emoji reactions */
  node.querySelectorAll('.emoji-btn').forEach(btn => {
    btn.onclick = () => {
      node.querySelectorAll('.emoji-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    };
  });
}

/* ── Chat: handle quick actions ── */
function handleAction(type) {
  switch (type) {
    case 'view_record':
      if (selected) {
        const rec = records.find(r => r.id === selected);
        if (rec) document.querySelector(`[data-select="${rec.id}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      break;
    case 'update_record':
      if (selected) {
        const rec = records.find(r => r.id === selected);
        if (rec) openDialog(rec);
      }
      break;
    case 'print_sticker':
      if (selected) window.open(`/sticker/${selected}`, '_blank');
      break;
    case 'new_record':
      openDialog();
      break;
    case 'reset_filters':
      $('reset-filters').click();
      break;
    case 'export_csv':
      exportCSV();
      break;
    case 'select_all':
      selectAllVisible();
      break;
  }
}

/* ── Chat: send message ── */
async function send(text) {
  addMessage(text, 'user');
  $('chat-form').querySelector('button').disabled = true;
  showTyping();
  /* Artificial delay for realistic feel */
  await new Promise(r => setTimeout(r, 400 + Math.random() * 600));
  try {
    const response = await api('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, record_id: selected, session_id: sessionId }),
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
  }
}

/* ── Chat form submit ── */
$('chat-form').onsubmit = event => {
  event.preventDefault();
  const text = $('chat-input').value.trim();
  if (text) { $('chat-input').value = ''; send(text); }
};

/* ── Chat: load history on page load ── */
async function loadChatHistory() {
  try {
    const data = await api(`/api/chat/history?session_id=${encodeURIComponent(sessionId)}`);
    if (data.messages && data.messages.length > 0) {
      /* Clear the default welcome message and show history */
      $('messages').innerHTML = '';
      data.messages.forEach(m => {
        addMessage(m.message, m.role, {
          confidence: m.confidence,
          timestamp: m.timestamp,
        });
      });
    }
  } catch { /* silently ignore history errors */ }
}

/* ── Keyboard shortcuts ── */
document.addEventListener('keydown', event => {
  /* Ctrl+K → focus search */
  if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
    event.preventDefault();
    $('search').focus();
    $('search').select();
  }
  /* Ctrl+N → new record */
  if ((event.ctrlKey || event.metaKey) && event.key === 'n') {
    event.preventDefault();
    openDialog();
  }
  /* Escape → close dialog */
  if (event.key === 'Escape') {
    const dialog = $('record-dialog');
    if (dialog.open) dialog.close();
  }
  /* Ctrl+E → export */
  if ((event.ctrlKey || event.metaKey) && event.key === 'e') {
    event.preventDefault();
    exportCSV();
  }
});

/* ── Bulk toolbar handlers ── */
$('bulk-apply').onclick = bulkUpdateStatus;
$('bulk-clear').onclick = () => { bulkSelected.clear(); render(); };
$('select-all-btn').onclick = selectAllVisible;
$('export-btn').onclick = exportCSV;

/* ── Theme toggle ── */
$('theme-toggle').onclick = toggleTheme;

/* ── Suggestion chip handlers ── */
document.querySelectorAll('[data-question]').forEach(btn => {
  btn.onclick = () => send(btn.dataset.question);
});

/* ── Initialize ── */
initTheme();
load();
loadChatHistory();
updateSuggestions();
