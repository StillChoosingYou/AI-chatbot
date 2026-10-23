/* School ID property management – role-based front end.
   One page, three workspaces. The Assistant is a full-page chat that works the same way for the
   coordinator, the guard and the student; the coordinator also gets the Dashboard of every student. */
'use strict';

/* ── Browser storage that never throws (private windows and blocked site data make it fail) ── */
const store = {
  get(key) { try { return localStorage.getItem(key) || ''; } catch { return ''; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* the page still works without it */ } },
  remove(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
};

/* crypto.randomUUID exists only on https:// pages and on localhost. A guard's phone may open this app over
   plain http on the school network, so there is a fallback instead of a crash. */
function uuid() {
  if (window.crypto && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.floor(Math.random() * 16);
    return (c === 'x' ? r : (r & 3) | 8).toString(16);
  });
}

/* ── State ── */
const PAGE_SIZE = 100;            // Dashboard rows drawn at a time
const NOT_MADE = 'not-made';      // the "Not yet made" status filter: Pending and Processing together
let records = [];                 // every student (coordinator only)
let statuses = ['Pending', 'Processing', 'For release', 'Released'];
let notYetMade = ['Pending', 'Processing'];
let editing = null;               // id of the student being edited in the dialog, null when adding
const bulkSelected = new Set();   // ids ticked on the Dashboard
let matching = [];                // the students that match the Dashboard filters right now
let statusFilter = '', attentionFilter = '', requestFilter = '', feedbackFilter = 'review';
let visibleRows = PAGE_SIZE;
let selected = null;              // {id, student_id, name}: the student the chat is about
let stats = null, serverToday = '', sending = false, historyLoadId = 0, lookedUpStudent = '';
let intentLabels = {}, knownIntents = [];
const badgeCounts = { dashboard: 0, requests: 0, feedback: 0, incidents: 0 };
let workspaceRole = store.get('workspaceRole');
let currentView = '';                              // the workspace page on screen, so a change of page starts at the top
const baseSession = store.get('chatSessionId') || (() => {
  const id = uuid();
  store.set('chatSessionId', id);
  return id;
})();

const NAV = {
  coordinator: [
    { id: 'dashboard', label: '▤  Dashboard' },
    { id: 'assistant', label: '◌  Assistant' },
    { id: 'queue', label: '📅  Appointments' },
    { id: 'requests', label: '📥  Requests' },
    { id: 'incidents', label: '⚠️  Incidents' },
    { id: 'feedback', label: '👍  Feedback' },
    { id: 'activity', label: '🕓  Activity' },
  ],
  guard: [
    { id: 'assistant', label: '◌  Assistant' },
    { id: 'verify', label: '🔎  Verify ID' },
    { id: 'incidents', label: '⚠️  Incidents' },
  ],
  student: [
    { id: 'assistant', label: '◌  Assistant' },
    { id: 'student-request', label: '📝  Request help' },
  ],
};

const ROLE_META = {
  coordinator: {
    title: 'Coordinator', avatar: 'PM', caption: 'Property Management', eyebrow: 'COORDINATOR',
    blurb: 'Type a student ID or name and I will show that student’s record, with address and contact number.',
    welcome: 'Hi! I’m an automated assistant for school IDs. Type a student ID or name and I will show that student’s record, with address and contact number. Type “records” to list the IDs that are not made yet, or “gate log” for today’s gate entries.',
  },
  guard: {
    title: 'Guard', avatar: 'GD', caption: 'Campus gate', eyebrow: 'GUARD',
    blurb: 'Type a student ID or name to see whether the ID is released, then check the student in or out.',
    welcome: 'Hi! I’m an automated assistant for school IDs. Type a student ID or name and I will show whether that student’s ID is released. To log the gate, type “check in” or “check out” and the student ID or name (for a visitor: “check in visitor Juan Dela Cruz”). Type “records” to list the IDs that are not made yet.',
  },
  student: {
    title: 'Student', avatar: 'ST', caption: 'Self-service', eyebrow: 'STUDENT',
    blurb: 'Type a student ID or name and I will show that student’s ID status.',
    welcome: 'Hi! I’m an automated assistant for school IDs. Type a student ID or name and I will show that student’s ID status. You can also ask about requirements, office hours or a lost ID.',
  },
};

/* ── Small helpers ── */
const $ = (id) => document.getElementById(id);
const escapeHtml = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sessionId = () => `${baseSession}:${workspaceRole || 'none'}`;
const statusClass = (status) => String(status).toLowerCase().replace(/[^a-z0-9]+/g, '-');   // safe inside class=""
const JSON_HEADERS = { 'Content-Type': 'application/json' };
const postJson = (body, method = 'POST') => ({ method, headers: JSON_HEADERS, body: JSON.stringify(body) });
const plainLabel = (label) => label.replace(/^[^\w]+/, '').trim();
const money = (amount) => `₱${Number(amount).toLocaleString('en-PH')}`;
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const emptyState = (text) => `<div class="empty">${text}</div>`;
const isMobile = () => window.matchMedia('(max-width: 760px)').matches;

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
  let body = {};
  if ((response.headers.get('content-type') || '').includes('application/json')) {
    try { body = await response.json(); } catch { body = {}; }
  }
  if (!response.ok) throw new Error(body.error || 'Unable to complete the request.');
  return body;
}

/* A link clicked by the script: a download, or a page in a new tab (no popup blocker, no window.opener). */
function clickLink(attributes) {
  const link = Object.assign(document.createElement('a'), attributes);
  document.body.appendChild(link);
  link.click();
  link.remove();
}
const download = (url) => clickLink({ href: url, download: '' });
const openTab = (url) => clickLink({ href: url, target: '_blank', rel: 'noopener' });

/* A date as YYYY-MM-DD in the browser's own time zone (used only until the server's date is known). */
function localDate() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}
const todayString = () => serverToday || localDate();

function formatTime(ts) {
  if (!ts) return '';
  try {
    return new Date(ts.replace(' ', 'T')).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  } catch { return ''; }
}
const formatNow = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

/* Sidebar badges: how many things are waiting on each page. */
function renderBadges() {
  document.querySelectorAll('#role-nav .nav').forEach(nav => {
    nav.querySelector('.notification-badge')?.remove();
    const count = badgeCounts[nav.dataset.view] || 0;
    if (count > 0) {
      const pulse = nav.dataset.view === 'dashboard' ? ' pulse' : '';
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

/* ── Theme and mobile navigation ── */
function initTheme() {
  const saved = store.get('theme');
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = saved || (prefersDark ? 'dark' : 'light');
  document.documentElement.setAttribute('data-theme', theme);
  updateThemeIcon(theme);
}
function toggleTheme() {
  const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  store.set('theme', next);
  updateThemeIcon(next);
}
function updateThemeIcon(theme) {
  const button = $('theme-toggle');
  if (button) button.textContent = theme === 'dark' ? '☀️' : '🌙';
}

function setMobileNav(open) {
  document.body.classList.toggle('nav-open', open);
  const button = $('menu-toggle');
  button.setAttribute('aria-expanded', String(open));
  button.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  button.textContent = open ? '✕' : '☰';
  $('nav-backdrop').hidden = !open;
}
const closeMobileNav = () => setMobileNav(false);
const toggleMobileNav = () => setMobileNav(!document.body.classList.contains('nav-open'));

/* ── The student the chat is about ──
   A lookup in the chat selects that student, so a follow-up such as "what is the status?" needs no ID.
   It is remembered per role in this browser only, so a reload keeps the conversation's subject. */
const contextKey = (role) => `chatContext:${role}`;

function restoreSelected(role) {
  try {
    const saved = JSON.parse(store.get(contextKey(role)) || 'null');
    if (saved && Number.isInteger(saved.id) && typeof saved.student_id === 'string' && typeof saved.name === 'string') {
      return { id: saved.id, student_id: saved.student_id, name: saved.name };
    }
  } catch { /* a damaged value is ignored */ }
  return null;
}

function renderSelected() {
  $('selected-student').textContent = selected
    ? `📌 ${selected.name} — ${selected.student_id}`
    : 'No student selected yet. Type a student ID or name.';
  $('clear-selected').hidden = !selected;
}

function setSelected(student) {
  selected = student ? { id: student.id, student_id: student.student_id, name: student.name } : null;
  if (workspaceRole) {
    if (selected) store.set(contextKey(workspaceRole), JSON.stringify(selected));
    else store.remove(contextKey(workspaceRole));
  }
  renderSelected();
  updateSuggestions();
}

/* Shows a student in the chat: selects them and asks for their card, as if their ID had been typed. */
function openInChat(student) {
  setSelected(student);
  showView('assistant');
  send(student.student_id);
}

/* ── Roles, navigation and views ── */
function setRole(role, { useHash = false } = {}) {
  if (!ROLE_META[role]) return;
  workspaceRole = role;
  store.set('workspaceRole', role);
  const meta = ROLE_META[role];
  $('role-badge').textContent = meta.title;
  $('role-avatar').textContent = meta.avatar;
  $('role-caption').innerHTML = `${escapeHtml(meta.caption)}<small>Local prototype · sample records</small>`;
  $('assistant-eyebrow').textContent = `${meta.eyebrow} ASSISTANT`;
  $('assistant-blurb').textContent = meta.blurb;
  $('incidents-eyebrow').textContent = meta.eyebrow;
  selected = restoreSelected(role);
  bulkSelected.clear();
  for (const key of Object.keys(badgeCounts)) badgeCounts[key] = 0;
  renderNav();
  renderSelected();
  updateSuggestions();
  loadChatHistory();
  if (role === 'coordinator') {
    load();                                    // also loads the numbers behind the sidebar badges
  }
  const items = NAV[role];
  const wanted = useHash ? location.hash.slice(1) : '';
  showView(items.some(item => item.id === wanted) ? wanted : items[0].id);
}

function renderNav() {
  const items = NAV[workspaceRole] || [];
  $('role-nav').innerHTML = items.map(item =>
    `<a class="nav" href="#${item.id}" data-view="${item.id}">${item.label}</a>`).join('');
  $('mobile-dock').innerHTML = items.map(item =>
    `<a href="#${item.id}" data-view="${item.id}">${escapeHtml(plainLabel(item.label))}</a>`).join('');
  renderBadges();
}

function scrollMessages() {
  const log = $('messages');
  log.scrollTop = log.scrollHeight;
}

function showView(viewId) {
  const items = NAV[workspaceRole] || [];
  if (!items.some(item => item.id === viewId)) viewId = items.length ? items[0].id : 'assistant';
  document.querySelectorAll('.workspace').forEach(el => { el.hidden = el.id !== `view-${viewId}`; });
  if (viewId !== currentView) window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  currentView = viewId;
  document.querySelectorAll('#role-nav .nav, #mobile-dock a').forEach(link => {
    const active = link.dataset.view === viewId;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
  const dock = $('mobile-dock');                     // the dock scrolls when it has many tabs; keep the open one in view
  const current = dock && dock.querySelector('a.active');
  if (current && dock.clientWidth) dock.scrollTo({ left: current.offsetLeft - (dock.clientWidth - current.offsetWidth) / 2, behavior: 'smooth' });
  const entry = items.find(item => item.id === viewId);
  const section = entry ? plainLabel(entry.label) : 'Home';
  $('topbar-section').textContent = section;
  document.title = `${section} · School ID`;
  history.replaceState(null, '', `#${viewId}`);
  closeMobileNav();

  if (workspaceRole === 'coordinator') {
    if (viewId === 'dashboard') loadStats({ quiet: true });
    if (viewId === 'queue') loadQueue();
    if (viewId === 'requests') loadRequests();
    if (viewId === 'feedback') loadFeedback();
    if (viewId === 'activity') loadActivity();
  }
  if (viewId === 'incidents') loadIncidents();
  if (viewId === 'assistant') {
    scrollMessages();
    if (!isMobile()) $('chat-input').focus({ preventScroll: true });   // on a phone, focusing would open the keyboard
  }
}

/* ═══════════════ Dashboard (coordinator) ═══════════════ */

/* Lower case without accents, so "munoz" finds "Muñoz". */
const fold = (value) => String(value ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

/* "10B" sorts after "2A": the year as a number, then the section. */
function sectionKey(value) {
  const match = /^(\d+)(.*)$/.exec(value);
  return match ? [Number(match[1]), match[2]] : [99, value];
}
function bySection(a, b) {
  const [yearA, sectionA] = sectionKey(a), [yearB, sectionB] = sectionKey(b);
  return yearA - yearB || sectionA.localeCompare(sectionB);
}

/* What the search box looks through, folded once per record instead of on every keystroke. */
function indexRecord(row) {
  row._hay = fold(`${row.name} ${row.student_id} ${row.year_section} ${row.address} ${row.contact_number}`);
  return row;
}

function replaceRecord(updated) {
  indexRecord(updated);
  records = records.map(r => (r.id === updated.id ? updated : r));
}

async function load() {
  try {
    const data = await api('/api/inventory');
    records = data.records.map(indexRecord);
    if (Array.isArray(data.statuses) && data.statuses.length) statuses = data.statuses;
    if (Array.isArray(data.not_yet_made)) notYetMade = data.not_yet_made;
    fillSections();
    render();
    loadStats({ quiet: true });
  } catch (error) { notify(error.message); }
}

function fillSections() {
  const select = $('section-filter');
  const previous = select.value;
  const values = [...new Set(records.map(r => r.year_section))].sort(bySection);
  select.innerHTML = '<option value="">All year &amp; sections</option>' +
    values.map(value => `<option>${escapeHtml(value)}</option>`).join('');
  select.value = values.includes(previous) ? previous : '';
}

/* "Needs attention" filters. The tests run against the server's date. */
const ATTENTION = [
  { id: 'overdue', label: 'Overdue', alert: true, test: (r, today) => r.schedule_date && r.schedule_date < today && r.status !== 'Released' },
  { id: 'today', label: 'Today', test: (r, today) => r.schedule_date === today },
  { id: 'unscheduled', label: 'For release, no date', test: r => r.status === 'For release' && !r.schedule_date },
  { id: 'no-reason', label: 'Pending, no reason', test: r => r.status === 'Pending' && !(r.pending_reason || '').trim() },
];

function filterValues() {
  return {
    words: fold($('search').value).split(/\s+/).filter(Boolean),
    section: $('section-filter').value,
    kind: $('kind-filter').value,
  };
}

function matchesStatus(row, value) {
  if (!value) return true;
  return value === NOT_MADE ? notYetMade.includes(row.status) : row.status === value;
}

/* `skip` leaves one filter out, so a chip can show how many students it would give. */
function matchesFilters(row, f, today, skip = '') {
  if (skip !== 'status' && !matchesStatus(row, statusFilter)) return false;
  if (skip !== 'attention') {
    const attention = ATTENTION.find(a => a.id === attentionFilter);
    if (attention && !attention.test(row, today)) return false;
  }
  if (f.section && row.year_section !== f.section) return false;
  if (f.kind && row.request_type !== f.kind) return false;
  return f.words.every(word => row._hay.includes(word));
}

function tileHtml({ label, value, detail, alert = false, attrs = '' }) {
  const tag = attrs ? 'button' : 'div';
  const button = attrs ? ' type="button"' : '';
  return `<${tag}${button} class="tile${alert && Number(value) > 0 ? ' alert' : ''}"${attrs}><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong><small>${escapeHtml(detail)}</small></${tag}>`;
}

function renderTiles(today) {
  const count = (test) => records.filter(test).length;
  $('dashboard-tiles').innerHTML = [
    { label: 'Students', value: records.length, detail: 'On the dashboard', attrs: ' data-tile="all"' },
    { label: 'Not yet made', value: count(r => notYetMade.includes(r.status)), detail: 'Pending and Processing', attrs: ` data-tile="${NOT_MADE}"` },
    { label: 'For release', value: count(r => r.status === 'For release'), detail: 'ID made, ready to claim', attrs: ' data-tile="For release"' },
    { label: 'Released', value: count(r => r.status === 'Released'), detail: 'ID already claimed', attrs: ' data-tile="Released"' },
    { label: 'Appointments today', value: count(r => r.schedule_date === today), detail: 'Release and follow-up visits', attrs: ' data-attention-jump="today"' },
    { label: 'Overdue', value: count(r => ATTENTION[0].test(r, today)), detail: 'Past their date, not released', alert: true, attrs: ' data-attention-jump="overdue"' },
  ].map(tileHtml).join('');
}

function renderStatusChips(f, today) {
  const base = records.filter(r => matchesFilters(r, f, today, 'status'));
  const chips = [['', 'All'], [NOT_MADE, 'Not yet made'], ...statuses.map(s => [s, s])];
  $('status-chips').innerHTML = chips.map(([value, label]) => {
    const n = value ? base.filter(r => matchesStatus(r, value)).length : base.length;
    return `<button type="button" class="filter-chip ${statusClass(value || 'all')}" data-status-chip="${escapeHtml(value)}" aria-pressed="${statusFilter === value}">${escapeHtml(label)} <b>${n}</b></button>`;
  }).join('');
}

function renderAttention(f, today) {
  const base = records.filter(r => matchesFilters(r, f, today, 'attention'));
  $('attention-chips').innerHTML = ATTENTION.map(a => {
    const n = base.filter(r => a.test(r, today)).length;
    return `<button type="button" class="filter-chip${a.alert && n ? ' alert' : ''}" data-attention="${a.id}" aria-pressed="${attentionFilter === a.id}">${escapeHtml(a.label)} <b>${n}</b></button>`;
  }).join('');
}

function rowHtml(r, today) {
  const checked = bulkSelected.has(r.id);
  const when = r.schedule_date ? `${escapeHtml(r.schedule_type)} · ${escapeHtml(r.schedule_date)} ${escapeHtml(r.schedule_time)}` : '';
  const overdue = ATTENTION[0].test(r, today);
  const options = statuses.map(s => `<option${s === r.status ? ' selected' : ''}>${escapeHtml(s)}</option>`).join('');
  const none = '<span class="none">—</span>';
  return `<tr data-id="${r.id}"${checked ? ' class="checked"' : ''}>
    <td class="check-col"><input type="checkbox" class="bulk-check" data-bulk="${r.id}"${checked ? ' checked' : ''} aria-label="Select ${escapeHtml(r.name)}"></td>
    <td data-label="Student ID" class="id-cell">${escapeHtml(r.student_id)}</td>
    <td data-label="Full name" class="name-cell"><strong>${escapeHtml(r.name)}</strong>${r.request_type === 'Lost ID' ? ' <span class="tag lost">Lost ID</span>' : ''}</td>
    <td data-label="Year &amp; Section">${escapeHtml(r.year_section)}</td>
    <td data-label="Status" class="status-cell"><select class="status-select ${statusClass(r.status)}" data-status-select="${r.id}" aria-label="Status of ${escapeHtml(r.name)}">${options}</select>${
      when ? `<div class="row-meta${overdue ? ' overdue' : ''}">📅 ${when}${overdue ? ' · overdue' : ''}</div>` : ''}${
      r.status === 'Pending' && r.pending_reason ? `<div class="row-meta reason">⚠ ${escapeHtml(r.pending_reason)}</div>` : ''}</td>
    <td data-label="Address" class="addr-cell">${r.address ? escapeHtml(r.address) : none}</td>
    <td data-label="Contact number" class="contact-cell">${r.contact_number ? escapeHtml(r.contact_number) : none}</td>
    <td class="actions-col"><button type="button" class="row-more" data-row-menu="${r.id}" aria-haspopup="menu" aria-expanded="false" aria-label="Actions for ${escapeHtml(r.name)}">⋯</button></td>
  </tr>`;
}

function render() {
  closeRowMenu();
  const f = filterValues();
  const today = todayString();
  matching = records.filter(r => matchesFilters(r, f, today));
  const ids = new Set(matching.map(r => r.id));
  for (const id of [...bulkSelected]) if (!ids.has(id)) bulkSelected.delete(id);   // never act on rows you cannot see

  renderTiles(today);
  renderStatusChips(f, today);
  renderAttention(f, today);
  badgeCounts.dashboard = records.filter(r => r.status === 'Pending').length;
  renderBadges();

  const shown = matching.slice(0, visibleRows);
  $('count').textContent = `${matching.length} of ${records.length}`;
  $('records').innerHTML = shown.length
    ? shown.map(r => rowHtml(r, today)).join('')
    : `<tr class="empty-row"><td colspan="8">${emptyState(records.length
      ? 'No students match these filters.<br>Try another search or reset the filters.'
      : 'No students yet.<br>Use <b>Add student</b> or <b>Import students</b>.')}</td></tr>`;
  const more = matching.length - shown.length;
  $('table-foot').innerHTML = more > 0
    ? `<span>Showing ${shown.length} of ${matching.length}.</span> <button type="button" class="text-button" data-show-more>Show ${Math.min(PAGE_SIZE, more)} more</button>`
    : (matching.length ? `<span>${plural(matching.length, 'student')}.</span>` : '');
  syncSelectionUi();
}

/* ── Selecting students ── */
function syncSelectionUi() {
  const total = matching.length;
  const picked = bulkSelected.size;                     // always a subset of the matching students
  const all = $('select-all');
  all.checked = total > 0 && picked === total;
  all.indeterminate = picked > 0 && picked < total;
  all.disabled = total === 0;
  const button = $('select-all-btn');
  button.disabled = total === 0;
  button.textContent = total > 0 && picked === total ? '☐ Clear selection' : `☑ Select all${total ? ` (${total})` : ''}`;
  $('bulk-toolbar').classList.toggle('visible', picked > 0);
  $('bulk-count').textContent = `${picked} selected`;
}

function paintChecks() {
  document.querySelectorAll('#records .bulk-check').forEach(box => {
    box.checked = bulkSelected.has(Number(box.dataset.bulk));
    box.closest('tr').classList.toggle('checked', box.checked);
  });
}

/* Select every student that matches the filters (not only the rows drawn), or clear the selection. */
function selectAllMatching(force) {
  const allPicked = matching.length > 0 && matching.every(r => bulkSelected.has(r.id));
  const select = force ?? !allPicked;
  for (const row of matching) {
    if (select) bulkSelected.add(row.id); else bulkSelected.delete(row.id);
  }
  paintChecks();
  syncSelectionUi();
}

async function bulkUpdateStatus() {
  const status = $('bulk-status').value;
  const ids = [...bulkSelected];
  if (!status) { notify('Choose a status first.'); return; }
  if (!ids.length) { notify('Select at least one student.'); return; }
  if (ids.length >= 10 && !confirm(`Set ${ids.length} students to “${status}”?`)) return;
  const button = $('bulk-apply');
  button.disabled = true;
  let updated = 0;
  try {
    for (let i = 0; i < ids.length; i += 500) {               // the server takes at most 500 at a time
      updated += (await api('/api/inventory/bulk', postJson({ record_ids: ids.slice(i, i + 500), status }, 'PATCH'))).updated;
    }
    notify(`Updated ${plural(updated, 'student')} to “${status}”.`);
    bulkSelected.clear();
    $('bulk-status').value = '';
  } catch (error) {
    notify(updated ? `${error.message} (${plural(updated, 'student')} had already been changed.)` : error.message);
  } finally {
    button.disabled = false;
  }
  await load();
  refreshActivity();
}

function printStickers() {
  const ids = [...bulkSelected];
  const max = Number($('bulk-stickers').dataset.max) || 60;
  if (!ids.length) { notify('Select at least one student first.'); return; }
  if (ids.length > max) { notify(`A sticker sheet holds up to ${max} students (you selected ${ids.length}).`); return; }
  openTab(`/stickers?ids=${ids.join(',')}`);
}

function refreshActivity() {
  if (!$('view-activity').hidden) loadActivity();
}

/* ── Filters ── */
function resetFilters() {
  $('search').value = '';
  $('section-filter').value = '';
  $('kind-filter').value = '';
  statusFilter = '';
  attentionFilter = '';
  visibleRows = PAGE_SIZE;
  render();
}

function filterByStatus(value) {
  statusFilter = value;
  attentionFilter = '';
  visibleRows = PAGE_SIZE;
  render();
}

/* "Not yet made" (Pending and Processing) on its own, from anywhere. */
function showNotYetMade() {
  showView('dashboard');
  $('search').value = '';
  $('section-filter').value = '';
  $('kind-filter').value = '';
  filterByStatus(NOT_MADE);
}

/* Finds one student on the Dashboard: the search box gets the student's ID and every other filter is cleared. */
function showOnDashboard(studentId) {
  showView('dashboard');
  $('section-filter').value = '';
  $('kind-filter').value = '';
  $('search').value = studentId;
  filterByStatus('');
  $('students-table').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ── Changing one student ── */
async function changeStatus(select) {
  const id = Number(select.dataset.statusSelect);
  const row = records.find(r => r.id === id);
  if (!row || select.value === row.status) return;
  const previous = row.status, next = select.value;
  select.disabled = true;
  try {
    replaceRecord(await api(`/api/inventory/${id}`, postJson({ status: next }, 'PATCH')));
    notify(`${row.name}: ${previous} → ${next}.`);
    render();
    loadStats({ quiet: true });
    refreshActivity();
  } catch (error) {
    select.value = previous;                     // the change did not happen: show the old status again
    select.disabled = false;
    notify(error.message);
  }
}

async function markReleased(row) {
  try {
    replaceRecord(await api(`/api/inventory/${row.id}`, postJson({ status: 'Released' }, 'PATCH')));
    notify(`${row.name} is now marked as released.`);
    render();
    loadStats({ quiet: true });
    refreshActivity();
  } catch (error) { notify(error.message); }
}

/* A lost ID goes back to Pending (the server checks that it had been Released). */
async function reportLost(row) {
  const ok = confirm(`${row.name} lost the ID?\n\nThe status goes from Released back to Pending and the release appointment is cleared. ` +
    'Send the student to the Cashier first to pay the replacement fee.');
  if (!ok) return;
  try {
    replaceRecord(await api(`/api/inventory/${row.id}/lost`, { method: 'POST' }));
    notify(`${row.name} is back to Pending (Lost ID).`);
    render();
    loadStats({ quiet: true });
    refreshActivity();
  } catch (error) {
    notify(error.message);
    load();                                      // someone else may have changed it: show what is true now
  }
}

async function deleteRecord(row) {
  if (!confirm(`Delete ${row.name} (${row.student_id})? This cannot be undone.`)) return;
  try {
    await api(`/api/inventory/${row.id}`, { method: 'DELETE' });
    bulkSelected.delete(row.id);
    if (selected && selected.id === row.id) setSelected(null);
    notify(`Deleted ${row.name}.`);
    await load();
  } catch (error) { notify(error.message); }
}

/* ── The ⋯ menu of a row ── */
const rowMenu = $('row-menu');
let menuFor = null, menuButton = null, menuAnchor = null;

function openRowMenu(button) {
  const row = records.find(r => r.id === Number(button.dataset.rowMenu));
  if (!row) return;
  closeRowMenu();
  const items = [
    ['edit', '✏️ Edit / schedule'],
    ['chat', '💬 Ask in chat'],
    ['sticker', '🖨️ Print QR sticker'],
    ['card', '🪪 Preview ID card'],
    ['profile', '🔗 Open QR profile'],
  ];
  if (row.status === 'For release') items.push(['release', '✅ Mark as released']);
  if (row.status === 'Released') items.push(['lost', '⚠️ Lost ID (back to Pending)']);
  items.push(['delete', '🗑 Delete student', 'danger']);
  rowMenu.innerHTML = items.map(([key, label, cls]) =>
    `<button type="button" role="menuitem" data-menu-action="${key}"${cls ? ` class="${cls}"` : ''}>${label}</button>`).join('');
  rowMenu.hidden = false;
  menuFor = row.id;
  menuButton = button;
  button.setAttribute('aria-expanded', 'true');
  const rect = button.getBoundingClientRect();
  menuAnchor = rect;
  const width = rowMenu.offsetWidth, height = rowMenu.offsetHeight;
  const left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
  const below = rect.bottom + 6;
  const top = below + height > window.innerHeight - 8 ? Math.max(8, rect.top - height - 6) : below;   // flip up near the bottom
  rowMenu.style.left = `${left}px`;
  rowMenu.style.top = `${top}px`;
  rowMenu.querySelector('button').focus({ preventScroll: true });
}

function closeRowMenu({ restoreFocus = false } = {}) {
  if (rowMenu.hidden) return;
  rowMenu.hidden = true;
  rowMenu.innerHTML = '';
  if (menuButton && menuButton.isConnected) {
    menuButton.setAttribute('aria-expanded', 'false');
    if (restoreFocus) menuButton.focus({ preventScroll: true });
  }
  menuFor = null;
  menuButton = null;
  menuAnchor = null;
}

function runRowAction(action, row) {
  const actions = {
    edit: () => openDialog(row),
    chat: () => openInChat(row),
    sticker: () => openTab(`/sticker/${row.id}`),
    card: () => openTab(`/card/${row.id}`),
    profile: () => openTab(`/student/${encodeURIComponent(row.token)}`),
    release: () => markReleased(row),
    lost: () => reportLost(row),
    delete: () => deleteRecord(row),
  };
  if (actions[action]) actions[action]();
}

/* ── Add / edit dialog ── */
function setDisabled(container, disabled) {
  container.querySelectorAll('input,select,textarea').forEach(el => { el.disabled = disabled; });
}

function openDialog(row = null) {
  editing = row ? row.id : null;
  const form = $('record-form');
  form.reset();
  $('form-error').textContent = '';
  $('dialog-title').textContent = row ? `Edit ${row.name}` : 'Add student';
  $('save-record').textContent = row ? 'Save changes' : 'Save student';
  $('processing-fields').hidden = !row;
  $('schedule-hint').hidden = !row;
  $('kind-field').hidden = !!row;                       // the request type is chosen when a student is added
  setDisabled($('processing-fields'), !row);
  setDisabled($('kind-field'), !!row);
  form.elements.student_id.readOnly = !!row;            // the student ID is the key of the record, it is not edited
  if (row) {
    for (const key of ['student_id', 'name', 'year_section', 'address', 'contact_number', 'status',
      'schedule_type', 'schedule_date', 'schedule_time', 'pending_reason', 'notes']) {
      if (form.elements[key]) form.elements[key].value = row[key] ?? '';
    }
  }
  $('record-dialog').showModal();
  (row ? form.elements.name : form.elements.student_id).focus();
}

async function saveRecord(event) {
  event.preventDefault();
  const typed = {};
  for (const [key, value] of new FormData(event.target)) typed[key] = String(value).trim();   // enabled fields only
  let body = typed;
  if (editing) {
    const row = records.find(r => r.id === editing);
    body = {};
    for (const [key, value] of Object.entries(typed)) {            // send only what changed
      if (key === 'student_id' || key === 'request_type') continue;
      if (value !== String(row ? row[key] ?? '' : '')) body[key] = value;
    }
    if (!Object.keys(body).length) {
      $('record-dialog').close();
      notify('No changes to save.');
      return;
    }
  }
  const button = $('save-record');
  button.disabled = true;
  $('form-error').textContent = '';
  try {
    await api(editing ? `/api/inventory/${editing}` : '/api/inventory', postJson(body, editing ? 'PATCH' : 'POST'));
    $('record-dialog').close();
    notify(editing ? 'Changes saved.' : 'Student added.');
    await load();
    refreshActivity();
  } catch (error) {
    $('form-error').textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

/* ── Import students from a CSV file ── */
function openImport() {
  $('import-form').reset();
  $('import-error').textContent = '';
  $('import-result').innerHTML = '';
  $('import-submit').disabled = false;
  $('import-dialog').showModal();
}

function importSummary({ added, skipped, total, problems }) {
  const head = added
    ? `<p class="import-summary ok">✅ Added <b>${added}</b> ${added === 1 ? 'student' : 'students'}${skipped ? `, skipped ${skipped}` : ''} (${plural(total, 'row')} in the file).</p>`
    : `<p class="import-summary none">No students were added${skipped ? `: ${skipped === 1 ? 'the row was' : `all ${skipped} rows were`} skipped` : ''}.</p>`;
  if (!problems.length) return head;
  const items = problems.map(p =>
    `<li><b>Line ${Number(p.line)}</b>${p.student_id ? ` · ${escapeHtml(p.student_id)}` : ''} — ${escapeHtml(p.error)}</li>`).join('');
  const rest = skipped > problems.length ? `<p class="subtle">…and ${skipped - problems.length} more.</p>` : '';
  return `${head}<ul class="problem-list">${items}</ul>${rest}`;
}

async function importStudents(event) {
  event.preventDefault();
  const input = $('import-file');
  const file = input.files[0];
  const maxMb = Number(input.dataset.maxMb) || 1;
  $('import-error').textContent = '';
  $('import-result').innerHTML = '';
  if (!file) { $('import-error').textContent = 'Choose a CSV file first.'; return; }
  if (file.size > maxMb * 1024 * 1024) {
    $('import-error').textContent = `That file is ${(file.size / 1048576).toFixed(1)} MB. The limit is ${maxMb} MB.`;
    return;
  }
  const body = new FormData();
  body.append('file', file);
  $('import-submit').disabled = true;
  try {
    const result = await api('/api/inventory/import', { method: 'POST', body });   // the browser sets the multipart header
    $('import-result').innerHTML = importSummary(result);
    input.value = '';
    if (result.added) {
      notify(`Added ${plural(result.added, 'student')}.`);
      await load();
      refreshActivity();
    }
  } catch (error) {
    $('import-error').textContent = error.message;
  } finally {
    $('import-submit').disabled = false;
  }
}

function exportCSV() {
  download('/api/inventory/export');
  notify('Downloading the student list…');
}

/* ── Numbers behind the sidebar badges ── */
async function loadStats({ quiet = false } = {}) {
  if (workspaceRole !== 'coordinator') return;
  try {
    stats = await api('/api/stats');
    const dateChanged = serverToday !== stats.today;
    serverToday = stats.today;
    badgeCounts.requests = stats.open_requests;
    badgeCounts.feedback = stats.new_feedback;
    badgeCounts.incidents = stats.open_incidents;
    renderBadges();
    if (dateChanged && records.length) render();   // the attention filters depend on today's date
  } catch (error) {
    if (!quiet) notify(error.message);
  }
}

/* ═══════════════ Assistant (everyone): a full-page chat ═══════════════ */

function showTyping() {
  const indicator = document.createElement('div');
  indicator.className = 'typing-indicator';
  indicator.id = 'typing';
  indicator.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
  $('messages').appendChild(indicator);
  scrollMessages();
}
function hideTyping() {
  $('typing')?.remove();
}

/* The ANN's score is how closely the words matched a known topic, not a probability that the
   answer is right, so people see a plain label and the number only as a tooltip. Student lookups, gate
   commands and the records list come from the database and carry no score at all. */
const QUIET_INTENTS = new Set(['out_of_scope', 'greeting', 'thanks_goodbye']);
/* Answers that are a record or a list from the database are drawn as a card. */
const CARD_INTENTS = new Set(['student_lookup', 'student_not_found', 'gate_check_in', 'gate_check_out',
  'gate_not_logged', 'gate_log', 'records_list']);

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

/* meta: confidence, intent, actions, timestamp, messageId, sessionId, suggestions, suggestionsTitle,
   followUps, rating, retryText, student (the student this answer is about: the buttons act on them) */
function addMessage(text, kind, meta = {}) {
  const node = document.createElement('div');
  node.className = `message ${kind}${kind === 'bot' && CARD_INTENTS.has(meta.intent) ? ' card' : ''}`;
  let html = renderMarkdown(text);
  html += `<div class="message-time">${meta.timestamp ? formatTime(meta.timestamp) : formatNow()}</div>`;
  if (kind === 'bot' && meta.confidence > 0 && !QUIET_INTENTS.has(meta.intent)) {
    const [cls, label] = matchLabel(meta.confidence);
    const tip = `Match score ${Number(meta.confidence).toFixed(2)}. This shows how closely your words matched a known topic. It does not guarantee the answer is right.`;
    html += `<span class="confidence-badge ${cls}" title="${escapeHtml(tip)}">${label}</span>`;
  }
  const actions = kind === 'bot' && Array.isArray(meta.actions) ? meta.actions : [];
  if (actions.length) {
    html += '<div class="action-chips">' + actions.map((a, i) =>
      `<button type="button" class="action-chip" data-action-index="${i}">${escapeHtml(a.label)}</button>`).join('') + '</div>';
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
  scrollMessages();
  node.querySelectorAll('[data-action-index]').forEach(button => {
    button.onclick = () => handleAction(actions[Number(button.dataset.actionIndex)], { button, student: meta.student });
  });
  node.querySelectorAll('.reply-chip').forEach(button => {
    button.onclick = () => send(button.dataset.question);
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
  node.querySelectorAll('[data-rate]').forEach(button => {
    const on = button.dataset.rate === rating;
    button.classList.toggle('active', on);
    button.setAttribute('aria-pressed', String(on));
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
  node.querySelectorAll('[data-rate]').forEach(button => { button.disabled = true; });
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
    loadStats({ quiet: true });                      // keeps the coordinator's "needs review" badge current
  } catch (error) {
    notify(error.message);
  } finally {
    delete node.dataset.busy;
    node.querySelectorAll('[data-rate]').forEach(button => { button.disabled = false; });
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

/* ── The buttons under an answer ──
   `student` is the student the answer was about (or the selected one), so a button keeps acting on that
   student even after the chat has moved on to someone else. */
function handleAction(action, { button = null, student = null } = {}) {
  if (!action) return;
  const target = student || selected;
  const row = target ? records.find(r => r.id === target.id) : null;      // the full record, coordinator only
  const needStudent = () => notify('Type a student ID or name first.');
  const handlers = {
    view_record: () => (target ? showOnDashboard(target.student_id) : needStudent()),
    update_record: () => (row ? openDialog(row) : notify('Open this student on the Dashboard to update them.')),
    print_sticker: () => (target ? openTab(`/sticker/${target.id}`) : needStudent()),
    preview_card: () => (target ? openTab(`/card/${target.id}`) : needStudent()),
    new_record: () => openDialog(),
    open_import: () => { showView('dashboard'); openImport(); },
    reset_filters: () => { showView('dashboard'); resetFilters(); },
    export_csv: () => exportCSV(),
    select_all: () => { showView('dashboard'); selectAllMatching(true); },
    open_dashboard: () => showView('dashboard'),
    show_not_made: () => showNotYetMade(),
    open_queue: () => showView('queue'),
    open_requests: () => showView('requests'),
    open_feedback: () => showView('feedback'),
    open_incidents: () => showView('incidents'),
    open_verify: () => { showView('verify'); $('verify-query').focus(); },
    open_incident: () => openIncident(action.student_id || ''),
    open_student_request: () => openStudentRequest(student),
    ask_office: () => send('Where is the Property Management Office and what are the hours?'),
    ask_human: () => send('I need to talk to a person at the office'),
    send: () => {
      if (!action.text) return;
      if (button) {                                  // a double tap must not log the gate twice
        button.disabled = true;
        setTimeout(() => { button.disabled = false; }, 1500);
      }
      send(action.text);
    },
  };
  (handlers[action.type] || (() => notify('That action is not available in this role.')))();
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
    await new Promise(resolve => setTimeout(resolve, 200));
    const response = await api('/api/chat', postJson({
      message: text,
      record_id: selected ? selected.id : null,
      session_id: session,
      workspace_role: workspaceRole || 'coordinator',
    }));
    hideTyping();
    if (session !== sessionId()) return;        // the role was switched meanwhile: that chat is not on screen
    if (response.selected_missing) {            // the selected student was deleted since
      setSelected(null);
      notify('The selected student is no longer on the list.');
    }
    if (response.student) setSelected(response.student);
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
      student: response.student || selected,
    });
  } catch (error) {
    hideTyping();
    if (session === sessionId()) addMessage(error.message, 'bot', { retryText: text });
  } finally {
    sending = false;
    button.disabled = false;
    if (!isMobile()) $('chat-input').focus({ preventScroll: true });
  }
}

/* The quick buttons above the message box. A button is either a question to send or an action. */
function suggestionList() {
  const ask = (q, label) => ({ q, label });
  const act = (label, action) => ({ label, action });
  const common = [ask('Where is the Property Management Office?', 'Office hours')];
  if (workspaceRole === 'guard') {
    return selected ? [
      ask(`check in ${selected.student_id}`, '✅ Check in'),
      ask(`check out ${selected.student_id}`, '⬅️ Check out'),
      act('⚠️ File incident', { type: 'open_incident', student_id: selected.student_id }),
      ask('gate log', '📜 Gate log'),
      ask('records', '📂 IDs not yet made'),
    ] : [
      ask('records', '📂 IDs not yet made'),
      ask('gate log', '📜 Gate log'),
      ask('I lost my school ID', 'A student lost the ID'),
      ...common,
    ];
  }
  if (workspaceRole === 'student') {
    return selected ? [
      ask('What is the status of my student ID?', 'Status'),
      ask('When can I collect my school ID?', 'Release date'),
      ask('Why is my student ID pending?', 'Pending reason'),
      ask('What documents are needed for a new student ID?', 'Requirements'),
    ] : [
      ask('What documents are needed for a new student ID?', 'Requirements'),
      ...common,
      ask('I lost my school ID', 'Lost ID'),
      ask('What can you do?', 'Help'),
    ];
  }
  if (selected) {
    const row = records.find(r => r.id === selected.id);
    const items = [
      ask('What is the status of my student ID?', 'ID status'),
      ask('When can I collect my school ID?', 'Release schedule'),
      ask('Why is my student ID pending?', 'Pending reason'),
      ask('When is my pending ID follow-up appointment?', 'Follow-up date'),
    ];
    if (row && row.request_type === 'Lost ID') items.push(ask('When is my lost ID replacement schedule?', 'Replacement date'));
    return items;
  }
  return [
    ask('records', '📂 IDs not yet made'),
    ask('gate log', '📜 Gate log'),
    act('📤 Import students', { type: 'open_import' }),
    ask('What can you do?', 'Help'),
    ...common,
  ];
}

function updateSuggestions() {
  const container = document.querySelector('.suggestions');
  if (!container) return;
  const items = suggestionList();
  container.innerHTML = items.map((item, i) =>
    `<button type="button" data-suggestion="${i}">${escapeHtml(item.label)}</button>`).join('');
  container.querySelectorAll('[data-suggestion]').forEach(button => {
    const item = items[Number(button.dataset.suggestion)];
    button.onclick = () => (item.q ? send(item.q) : handleAction(item.action, { student: selected }));
  });
}

function welcomeMessage() {
  const meta = ROLE_META[workspaceRole];
  addMessage(meta ? meta.welcome : 'Hi! Choose a role so I can show the right tools.', 'bot', { confidence: 0 });
}

async function loadChatHistory() {
  const loadId = ++historyLoadId;               // a slower, older load must not overwrite a newer one
  $('messages').innerHTML = '';
  welcomeMessage();
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
  } catch { /* the welcome message stays */ }
}

async function newChat() {
  historyLoadId++;
  try {
    await api(`/api/chat/history?session_id=${encodeURIComponent(sessionId())}`, { method: 'DELETE' });
  } catch { /* the chat is cleared on screen either way */ }
  setSelected(null);
  $('messages').innerHTML = '';
  welcomeMessage();
}

function updateChatCount() {
  const length = $('chat-input').value.length;
  $('chat-count').hidden = length < 800;
  $('chat-count').textContent = `${length} / ${$('chat-input').maxLength}`;
  $('chat-count').classList.toggle('near', length >= 950);
}

/* ═══════════════ Appointments (coordinator) ═══════════════ */
let lastQueue = [];

async function loadQueue() {
  try {
    const data = await api('/api/queue');
    lastQueue = data.appointments;
    if (!lastQueue.length) {
      $('queue-list').innerHTML = emptyState('No appointments yet.<br>Open a student on the Dashboard and choose Edit / schedule.');
      return;
    }
    const overdue = row => row.schedule_date < data.today && row.status !== 'Released';
    const counts = {
      overdue: lastQueue.filter(overdue).length,
      today: lastQueue.filter(row => row.schedule_date === data.today).length,
      upcoming: lastQueue.filter(row => row.schedule_date > data.today).length,
    };
    $('queue-list').innerHTML =
      `<div class="pill-row"><span class="${counts.overdue ? 'alert' : ''}"><strong>${counts.overdue}</strong>overdue</span><span><strong>${counts.today}</strong>today</span><span><strong>${counts.upcoming}</strong>upcoming</span></div>` +
      lastQueue.map(row => `
      <article class="tool-row">
        <div>
          <h3>${escapeHtml(row.name)}</h3>
          <p>${escapeHtml(row.student_id)} · ${escapeHtml(row.year_section)} · <span class="status ${statusClass(row.status)}">${escapeHtml(row.status)}</span></p>
          <p class="appointment"><strong>${escapeHtml(row.schedule_type)}</strong> · ${escapeHtml(row.schedule_date)} ${escapeHtml(row.schedule_time)}</p>
        </div>
        <div class="row-actions">
          ${row.schedule_date === data.today ? '<span class="status for-release">Today</span>' : ''}
          ${overdue(row) ? '<span class="status overdue">Overdue</span>' : ''}
          <button type="button" class="text-button" data-queue-chat="${row.id}">Ask about ID</button>
        </div>
      </article>`).join('');
  } catch (error) { notify(error.message); }
}

/* ═══════════════ Student requests (coordinator) ═══════════════ */
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
          ${row.request_type === 'Lost ID' ? '<p class="reason">Lost ID: the student pays the replacement fee at the Cashier first, then brings the Cashier’s receipt, an affidavit of loss and a 1×1 photo to this office.</p>' : ''}
        </div>
        <div class="row-actions">
          ${(data.flow[row.status] || []).map(next => `<button type="button" class="${next === 'Accepted' ? 'primary' : 'text-button'}" data-request-status="${row.id}" data-status="${escapeHtml(next)}"${next === 'Accepted' && row.request_type === 'Lost ID' ? ' title="Sets this student’s ID back to Pending (Lost ID) so a replacement can be made"' : ''}>${escapeHtml(REQUEST_BUTTONS[next] || next)}</button>`).join('')}
        </div>
      </article>`).join('');
  } catch (error) { notify(error.message); }
}

/* ═══════════════ Chatbot feedback (coordinator) ═══════════════ */
const FEEDBACK_FILTERS = [
  ['review', 'Needs review', '?rating=down&status=New', s => s.new_down],
  ['down', 'All 👎', '?rating=down', s => s.down],
  ['up', '👍', '?rating=up', s => s.up],
  ['all', 'Everything', '', s => s.total],
];
const topicName = (intent) => intentLabels[intent] || String(intent || 'Unknown').replaceAll('_', ' ');

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

/* ═══════════════ Activity log (coordinator) ═══════════════ */
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

/* ═══════════════ Verify ID (guard) ═══════════════ */
let lastVerify = [];

function verifyCard(row) {
  const flags = [];
  if (row.false_statements) flags.push(`${plural(row.false_statements, 'false statement')} on record`);
  if (row.penalty_due) flags.push(`penalty due ${money(row.penalty_due)}`);
  return `<article class="tool-row">
    <div>
      <h3>${escapeHtml(row.name)}</h3>
      <p>${escapeHtml(row.student_id)} · ${escapeHtml(row.year_section)} · <span class="status ${statusClass(row.status)}">${escapeHtml(row.status)}</span> ${escapeHtml(row.status_note || '')}</p>
      <p>${escapeHtml(row.gate_note || '')}</p>
      ${flags.length ? `<p class="reason">⚠ ${escapeHtml(flags.join(' · '))}</p>` : ''}
    </div>
    <div class="row-actions">
      <button type="button" class="gate-btn" data-verify-chat="${row.id}" title="Open this student in the chat, to check them in or out">Open in chat</button>
      <button type="button" class="text-button" data-verify-incident="${escapeHtml(row.student_id)}">File incident</button>
      <a href="/student/${encodeURIComponent(row.token)}" target="_blank" rel="noopener">Open profile ↗</a>
    </div>
  </article>`;
}

async function runVerify(query, { fromScan = false } = {}) {
  try {
    const data = await api(`/api/verify?q=${encodeURIComponent(query)}`);
    lastVerify = data.matches;
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

/* ═══════════════ Incidents (guard and coordinator): No ID and Lost ID ═══════════════ */
let incidentCheckTicket = 0, incidentCheckTimer = 0;
const ID_STILL_TYPING = /^p\d{0,8}$/i;     // part of a student ID: wait for the rest before looking it up

/* What the records say about the student, shown while the guard is still filling the form in. */
function checkCardHtml(data) {
  if (!data.found) return `<p><b>Not on the list.</b> ${escapeHtml(data.message)}</p>`;
  const s = data.student;
  const claimClass = data.claim.agrees === true ? 'ok' : data.claim.agrees === false ? 'warn' : '';
  const lines = [];
  if (data.false_statements) {
    lines.push(`${plural(data.false_statements, 'false statement')} on record${data.penalty_due ? `, penalty due ${money(data.penalty_due)}` : ''}.`);
  }
  if (data.next_penalty) lines.push(`Another false statement carries the ${data.penalty_text} penalty.`);
  return `<p><b>${escapeHtml(s.name)}</b> · ${escapeHtml(s.student_id)} · ${escapeHtml(s.year_section)} · <span class="status ${statusClass(s.status)}">${escapeHtml(s.status)}</span></p>
    <p class="subtle">${escapeHtml(s.status_note)}</p>
    ${data.claim.message ? `<p class="claim ${claimClass}">${escapeHtml(data.claim.message)}</p>` : ''}
    ${lines.length ? `<p class="reason">⚠ ${escapeHtml(lines.join(' '))}</p>` : ''}`;
}

async function runIncidentCheck({ force = false } = {}) {
  const form = $('incident-form'), panel = $('incident-check');
  const studentId = form.elements.student_id.value.trim();
  const ticket = ++incidentCheckTicket;           // an older answer must not overwrite a newer one
  if (!studentId || (!force && ID_STILL_TYPING.test(studentId))) {
    panel.hidden = true;
    panel.innerHTML = '';
    return;
  }
  try {
    const data = await api(`/api/incidents/check?category=${encodeURIComponent(form.elements.category.value)}&student_id=${encodeURIComponent(studentId)}`);
    if (ticket !== incidentCheckTicket) return;
    panel.innerHTML = checkCardHtml(data);
  } catch (error) {
    if (ticket !== incidentCheckTicket) return;
    panel.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
  }
  panel.hidden = false;
}

function scheduleIncidentCheck() {
  clearTimeout(incidentCheckTimer);
  incidentCheckTimer = setTimeout(() => runIncidentCheck(), 350);
}

function openIncident(studentId = '') {
  showView('incidents');
  const form = $('incident-form');
  if (studentId) {
    form.elements.student_id.value = studentId;
    runIncidentCheck({ force: true });
  }
  form.elements.detail.focus();
}

function incidentResultHtml(incident) {
  const steps = (incident.next_steps || []).map(step => `<li>${escapeHtml(step)}</li>`).join('');
  const who = incident.student_name
    ? `<p><b>${escapeHtml(incident.student_name)}</b> · ${escapeHtml(incident.student_id)}</p>`
    : (incident.student_id ? `<p>${escapeHtml(incident.student_id)} <span class="subtle">(not on the dashboard)</span></p>` : '');
  const claim = incident.claim && incident.claim.message
    ? `<p class="claim ${incident.claim.agrees === true ? 'ok' : incident.claim.agrees === false ? 'warn' : ''}">${escapeHtml(incident.claim.message)}</p>` : '';
  const lie = incident.false_statement
    ? `<p class="reason">⚠ False statement no. ${Number(incident.false_statements)} for this student${incident.penalty ? `: penalty ${money(incident.penalty)}` : ''}.</p>` : '';
  return `<article class="tool-card result-card">
    <h3>✅ Incident #${Number(incident.id)} filed · ${escapeHtml(incident.category)}</h3>
    ${who}${claim}${lie}
    ${steps ? `<p><b>Next steps</b></p><ol>${steps}</ol>` : ''}
  </article>`;
}

function incidentRow(row) {
  const who = row.student_name ? `${row.student_name} · ${row.student_id}` : (row.student_id || 'No student ID');
  const tag = row.false_statement ? `<span class="tag danger">False statement${row.penalty ? ` · ${money(row.penalty)}` : ''}</span>` : '';
  const flagButton = workspaceRole === 'coordinator' && row.student_id
    ? (row.false_statement
      ? `<button type="button" class="text-button danger-text" data-incident-flag="${row.id}" data-flag="false" title="Not a false statement after all">Withdraw false statement</button>`
      : `<button type="button" class="text-button" data-incident-flag="${row.id}" data-flag="true">Mark as false statement</button>`)
    : '';
  return `<article class="tool-row">
    <div>
      <h3>${escapeHtml(row.category)} · ${escapeHtml(row.status)} ${tag}</h3>
      <p>${escapeHtml(who)} · ${escapeHtml(row.location || 'Unspecified')} · ${escapeHtml(row.timestamp)}</p>
      <p>${escapeHtml(row.detail)}</p>
    </div>
    <div class="row-actions">
      ${row.status === 'Open'
        ? `<button type="button" class="primary" data-incident="${row.id}" data-status="Resolved">Resolve</button>`
        : `<button type="button" class="text-button" data-incident="${row.id}" data-status="Open">Reopen</button>`}
      ${flagButton}
    </div>
  </article>`;
}

async function loadIncidents() {
  try {
    const data = await api('/api/incidents');
    $('incident-list').innerHTML = data.incidents.length
      ? '<h3 class="list-title">Latest incidents</h3>' + data.incidents.map(incidentRow).join('')
      : emptyState('No incidents filed.');
  } catch (error) { notify(error.message); }
}

async function fileIncident(event) {
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector('[type="submit"]');
  const body = {
    category: form.elements.category.value,
    student_id: form.elements.student_id.value.trim(),
    location: form.elements.location.value.trim(),
    detail: form.elements.detail.value.trim(),
    false_statement: form.elements.false_statement.checked,
  };
  button.disabled = true;
  try {
    const created = await api('/api/incidents', postJson(body));
    $('incident-result').innerHTML = incidentResultHtml(created);
    form.elements.student_id.value = '';
    form.elements.detail.value = '';
    form.elements.false_statement.checked = false;
    $('incident-check').hidden = true;
    $('incident-check').innerHTML = '';
    incidentCheckTicket++;                          // a check still on its way is no longer wanted
    notify('Incident filed.');
    loadIncidents();
    loadStats({ quiet: true });
  } catch (error) {
    notify(error.message);
  } finally {
    button.disabled = false;
  }
}

/* ═══════════════ Request help (student) ═══════════════ */
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
    html = data.requests.length
      ? '<h3 class="list-title">Your requests</h3>' + data.requests.map(row => `
        <article class="tool-row">
          <div>
            <h3>${escapeHtml(row.request_type)} <span class="subtle">#${Number(row.id)}</span></h3>
            <p>${escapeHtml(row.timestamp)} · ${escapeHtml(REQUEST_HELP[row.status] || '')}</p>
          </div>
          <div class="row-actions"><span class="status ${statusClass(row.status)}">${escapeHtml(row.status)}</span></div>
        </article>`).join('')
      : emptyState('No requests were made with that student ID yet.');
  } catch (error) {
    html = emptyState(escapeHtml(error.message));
  }
  $('request-tracker').innerHTML = html;
}

function requestResultHtml(created) {
  const steps = (created.next_steps || []).map(step => `<li>${escapeHtml(step)}</li>`).join('');
  return `<article class="tool-card result-card">
    <h3>✅ Request #${Number(created.id)} submitted</h3>
    <p>${escapeHtml(created.request_type)} · ${escapeHtml(created.student_id)} · <span class="status ${statusClass(created.status)}">${escapeHtml(created.status)}</span></p>
    ${steps ? `<p><b>What to do next</b></p><ol>${steps}</ol>` : '<p class="subtle">The office will look at it. You can follow it below.</p>'}
  </article>`;
}

/* Opens the form for one student, from the “Request help” button on that student’s own answer. */
function openStudentRequest(student = null) {
  showView('student-request');
  const form = $('student-request-form');
  if (student) {
    form.elements.student_id.value = student.student_id;
    form.elements.name.value = student.name;
  }
  form.elements.detail.focus();
}

async function submitStudentRequest(event) {
  event.preventDefault();
  const form = event.target;
  const body = Object.fromEntries(new FormData(form));
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    const created = await api('/api/requests', postJson(body));
    lookedUpStudent = created.student_id;
    $('request-result').innerHTML = requestResultHtml(created);
    form.elements.detail.value = '';
    notify(`Request #${created.id} submitted. It is now “${created.status}”.`, 6000);
    loadMyRequests(created.student_id);
  } catch (error) {
    notify(error.message);
  } finally {
    button.disabled = false;
  }
}

function trackMyRequests() {
  const studentId = $('student-request-form').elements.student_id.value.trim();
  if (!studentId) { notify('Type your student ID first.'); return; }
  lookedUpStudent = studentId;
  loadMyRequests(studentId);
}

/* ═══════════════ Event wiring ═══════════════ */

/* ── Dashboard ── */
$('search').addEventListener('input', () => { visibleRows = PAGE_SIZE; render(); });
for (const id of ['section-filter', 'kind-filter']) {
  $(id).addEventListener('change', () => { visibleRows = PAGE_SIZE; render(); });
}
$('reset-filters').onclick = resetFilters;
$('dashboard-tiles').addEventListener('click', event => {
  const tile = event.target.closest('[data-tile]');
  if (tile) {
    if (tile.dataset.tile === 'all') resetFilters(); else filterByStatus(tile.dataset.tile);
    return;
  }
  const jump = event.target.closest('[data-attention-jump]');
  if (jump) {                                     // a tile such as “Overdue”: show those students, whatever their status
    statusFilter = '';
    attentionFilter = jump.dataset.attentionJump;
    visibleRows = PAGE_SIZE;
    render();
  }
});
$('status-chips').addEventListener('click', event => {
  const chip = event.target.closest('[data-status-chip]');
  if (!chip) return;
  statusFilter = chip.dataset.statusChip;
  visibleRows = PAGE_SIZE;
  render();
});
$('attention-chips').addEventListener('click', event => {
  const chip = event.target.closest('[data-attention]');
  if (!chip) return;
  attentionFilter = chip.dataset.attention === attentionFilter ? '' : chip.dataset.attention;
  visibleRows = PAGE_SIZE;
  render();
});
$('records').addEventListener('click', event => {
  const more = event.target.closest('[data-row-menu]');
  if (!more) return;
  if (!rowMenu.hidden && menuFor === Number(more.dataset.rowMenu)) closeRowMenu(); else openRowMenu(more);
});
$('records').addEventListener('change', event => {
  const box = event.target.closest('.bulk-check');
  if (box) {
    const id = Number(box.dataset.bulk);
    if (box.checked) bulkSelected.add(id); else bulkSelected.delete(id);
    box.closest('tr').classList.toggle('checked', box.checked);
    syncSelectionUi();
    return;
  }
  const select = event.target.closest('[data-status-select]');
  if (select) changeStatus(select);
});
$('select-all').addEventListener('change', event => selectAllMatching(event.target.checked));
$('select-all-btn').onclick = () => selectAllMatching();
$('bulk-clear').onclick = () => selectAllMatching(false);
$('bulk-apply').onclick = bulkUpdateStatus;
$('bulk-stickers').onclick = printStickers;
$('table-foot').addEventListener('click', event => {
  if (event.target.closest('[data-show-more]')) { visibleRows += PAGE_SIZE; render(); }
});
$('new-record').onclick = () => openDialog();
$('export-btn').onclick = exportCSV;
$('import-btn').onclick = openImport;

rowMenu.addEventListener('click', event => {
  const item = event.target.closest('[data-menu-action]');
  if (!item) return;
  const row = records.find(r => r.id === menuFor);
  closeRowMenu();
  if (row) runRowAction(item.dataset.menuAction, row);
});
rowMenu.addEventListener('keydown', event => {
  const items = [...rowMenu.querySelectorAll('button')];
  const index = items.indexOf(document.activeElement);
  if (event.key === 'ArrowDown') { event.preventDefault(); items[(index + 1) % items.length].focus(); }
  else if (event.key === 'ArrowUp') { event.preventDefault(); items[(index - 1 + items.length) % items.length].focus(); }
  else if (event.key === 'Escape') { event.preventDefault(); closeRowMenu({ restoreFocus: true }); }
  else if (event.key === 'Tab') closeRowMenu();
});
/* The menu is fixed on screen, so it closes when the page or the table scrolls the ⋯ button away from it.
   A scroll event that moves nothing (some browsers send one late) leaves it open. */
function closeIfAnchorMoved() {
  if (rowMenu.hidden) return;
  if (!menuButton || !menuButton.isConnected || !menuAnchor) { closeRowMenu(); return; }
  const now = menuButton.getBoundingClientRect();
  if (Math.abs(now.top - menuAnchor.top) > 1 || Math.abs(now.left - menuAnchor.left) > 1) closeRowMenu();
}
window.addEventListener('scroll', closeIfAnchorMoved, true);       // capture: the table scrolls inside its own box
window.addEventListener('resize', () => closeRowMenu());

/* ── Dialogs ── */
$('record-form').onsubmit = saveRecord;
$('close-dialog').onclick = () => $('record-dialog').close();
$('import-form').onsubmit = importStudents;
$('import-close').onclick = () => $('import-dialog').close();
$('import-done').onclick = () => $('import-dialog').close();
$('scan-btn').onclick = openScanner;
$('scan-close').onclick = () => $('scan-dialog').close();
$('scan-dialog').addEventListener('close', stopScanner);

/* ── Assistant ── */
$('chat-input').addEventListener('input', updateChatCount);
$('chat-form').onsubmit = event => {
  event.preventDefault();
  const text = $('chat-input').value.trim();
  if (text && !sending) { $('chat-input').value = ''; updateChatCount(); send(text); }
};
$('new-chat').onclick = newChat;
$('clear-selected').onclick = () => setSelected(null);
/* Thumbs, the feedback panel and the Try-again chip live inside the messages, so one listener on the log handles them. */
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

/* ── Chrome: theme, navigation, role ── */
$('feedback-export').onclick = () => {
  download('/api/feedback/export');
  notify('Downloading feedback CSV…');
};
$('theme-toggle').onclick = toggleTheme;
$('menu-toggle').onclick = toggleMobileNav;
$('nav-backdrop').onclick = closeMobileNav;
$('switch-role').onclick = () => $('role-dialog').showModal();
$('role-form').addEventListener('submit', event => {
  const role = event.submitter && event.submitter.value;
  if (role) setRole(role);
});
$('role-dialog').addEventListener('cancel', event => {
  if (!workspaceRole) event.preventDefault();       // the first visit needs a role
});

/* ── Everything that is drawn later: one listener, buttons found by their data- attributes ── */
document.addEventListener('click', event => {
  if (!event.target.closest('#row-menu') && !event.target.closest('[data-row-menu]')) closeRowMenu();

  const nav = event.target.closest('[data-view]');
  if (nav) {
    event.preventDefault();
    showView(nav.dataset.view);
    return;
  }
  const queueChat = event.target.closest('[data-queue-chat]');
  if (queueChat) {
    const row = lastQueue.find(r => r.id === Number(queueChat.dataset.queueChat));
    if (row) openInChat(row);
  }
  const requestChip = event.target.closest('[data-request-filter]');
  if (requestChip) {
    requestFilter = requestChip.dataset.requestFilter;
    loadRequests();
  }
  const requestButton = event.target.closest('[data-request-status]');
  if (requestButton) {
    requestButton.disabled = true;
    api(`/api/requests/${requestButton.dataset.requestStatus}`, postJson({ status: requestButton.dataset.status }, 'PATCH'))
      .then(() => {
        notify(`Request marked ${requestButton.dataset.status}.`);
        loadRequests();
        load();                                  // accepting a lost ID changes that student's status
        refreshActivity();
      })
      .catch(error => { notify(error.message); loadRequests(); });   // reload: someone else may have moved it already
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
      .then(() => {
        notify(feedbackStatus.dataset.status === 'Reviewed' ? 'Marked as reviewed.' : 'Moved back to needs review.');
        loadFeedback();
        loadStats({ quiet: true });
        refreshActivity();
      })
      .catch(error => { notify(error.message); feedbackStatus.disabled = false; });
  }
  const verifyChat = event.target.closest('[data-verify-chat]');
  if (verifyChat) {
    const row = lastVerify.find(r => r.id === Number(verifyChat.dataset.verifyChat));
    if (row) openInChat(row);
  }
  const verifyIncident = event.target.closest('[data-verify-incident]');
  if (verifyIncident) openIncident(verifyIncident.dataset.verifyIncident);

  const incidentButton = event.target.closest('[data-incident]');
  if (incidentButton) {
    incidentButton.disabled = true;
    api(`/api/incidents/${incidentButton.dataset.incident}`, postJson({ status: incidentButton.dataset.status }, 'PATCH'))
      .then(() => {
        notify(`Incident ${incidentButton.dataset.status.toLowerCase()}.`);
        loadIncidents();
        loadStats({ quiet: true });
      })
      .catch(error => { notify(error.message); incidentButton.disabled = false; });
  }
  const flagButton = event.target.closest('[data-incident-flag]');
  if (flagButton) {
    const withdraw = flagButton.dataset.flag === 'false';
    if (withdraw && !confirm('Withdraw this false statement? The penalty is recalculated for the student’s other incidents.')) return;
    flagButton.disabled = true;
    api(`/api/incidents/${flagButton.dataset.incidentFlag}`, postJson({ false_statement: !withdraw }, 'PATCH'))
      .then(() => {
        notify(withdraw ? 'False statement withdrawn.' : 'Marked as a false statement.');
        loadIncidents();
        refreshActivity();
      })
      .catch(error => { notify(error.message); flagButton.disabled = false; });
  }
});

document.addEventListener('change', event => {
  const topic = event.target.closest('[data-feedback-topic]');
  if (!topic) return;
  topic.disabled = true;
  reviewFeedback(topic.dataset.feedbackTopic, { correct_intent: topic.value })
    .then(() => notify(topic.value ? `Saved. The right topic is “${topicName(topic.value)}”.` : 'Topic cleared.'))
    .catch(error => notify(error.message))
    .finally(() => { topic.disabled = false; });
});

/* ── Guard and student forms ── */
$('verify-form').onsubmit = event => {
  event.preventDefault();
  runVerify($('verify-query').value.trim());
};
$('incident-form').onsubmit = fileIncident;
$('incident-form').elements.student_id.addEventListener('input', scheduleIncidentCheck);
$('incident-form').elements.student_id.addEventListener('change', () => runIncidentCheck({ force: true }));
$('incident-form').elements.category.addEventListener('change', () => runIncidentCheck());
$('student-request-form').onsubmit = submitStudentRequest;
$('track-btn').onclick = trackMyRequests;

/* ── Keyboard shortcuts ── */
document.addEventListener('keydown', event => {
  const ctrl = event.ctrlKey || event.metaKey;
  const key = event.key.toLowerCase();
  if (ctrl && key === 'k') {
    event.preventDefault();
    if (workspaceRole === 'coordinator') { showView('dashboard'); $('search').focus(); $('search').select(); }
    else { showView('assistant'); $('chat-input').focus(); }
  }
  if (ctrl && key === 'n') {
    event.preventDefault();
    if (workspaceRole === 'coordinator') { showView('dashboard'); openDialog(); }
    else if (workspaceRole === 'student') showView('student-request');
    else openIncident();
  }
  if (ctrl && key === 'e' && workspaceRole === 'coordinator') {
    event.preventDefault();
    exportCSV();
  }
  if (event.key === 'Escape') {
    closeRowMenu();
    closeMobileNav();
  }
});

/* New requests and feedback arrive from other people's browsers, so the coordinator's badges refresh
   every minute and whenever the tab becomes visible again. */
setInterval(() => { if (!document.hidden) loadStats({ quiet: true }); }, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadStats({ quiet: true }); });

window.addEventListener('hashchange', () => {
  const view = location.hash.replace('#', '');
  if (view && workspaceRole) showView(view);
});

/* ── Start ── */
initTheme();
if (workspaceRole && ROLE_META[workspaceRole]) {
  setRole(workspaceRole, { useHash: true });
} else {
  workspaceRole = '';
  renderNav();
  renderSelected();
  updateSuggestions();
  showView('assistant');
  loadChatHistory();
  $('role-dialog').showModal();
}
