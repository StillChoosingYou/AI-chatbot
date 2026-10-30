/* School ID property management – role-based front end.
   One page, two workspaces. The Assistant is a full-page chat that works the same way for the
   coordinator and the student; the coordinator also gets the Dashboard of every student. */
'use strict';

/* Every animation is played by static/fx.js (the Motion library). If that file did not load, each call does nothing. */
const fx = window.fx || new Proxy({}, { get: () => () => Promise.resolve() });

/* ── Browser storage that never throws (private windows and blocked site data make it fail) ── */
const store = {
  get(key) { try { return localStorage.getItem(key) || ''; } catch { return ''; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* the page still works without it */ } },
  remove(key) { try { localStorage.removeItem(key); } catch { /* ignore */ } },
};

/* crypto.randomUUID exists only on https:// pages and on localhost. A phone may open this app over
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
let stats = null, serverToday = '', sending = false, historyLoadId = 0;
let tickerKey = '', tickerUnit = '', tickerFit = '';   // what the Dashboard ticker shows, and how it is laid out
let intentLabels = {}, knownIntents = [];
const badgeCounts = { dashboard: 0, requests: 0, feedback: 0, incidents: 0 };
/* The role comes from the address: /coordinator or /student. On '/' there is none, and the chooser shows. */
let workspaceRole = document.body.dataset.pageRole || '';
let currentView = '';                              // the workspace page on screen, so a change of page starts at the top
const baseSession = store.get('chatSessionId') || (() => {
  const id = uuid();
  store.set('chatSessionId', id);
  return id;
})();

// Line icons (24x24, stroke = currentColor) so every nav item looks like it belongs to one set
const NAV_ICONS = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  assistant: '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.5-4.6A8 8 0 1 1 21 12z"/><path d="M9 11h6M9 14.5h4"/>',
  queue: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/>',
  requests: '<path d="M4 13l2.5-7.5A2 2 0 0 1 8.4 4h7.2a2 2 0 0 1 1.9 1.5L20 13"/><path d="M4 13v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5h-5l-1.5 2.5h-5L9 13z"/>',
  incidents: '<path d="M12 3l10 17H2L12 3z"/><path d="M12 10v4.5M12 17.5v.01"/>',
  feedback: '<path d="M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3zM7 11l4-8a2.5 2.5 0 0 1 2.5 2.5V9H19a2 2 0 0 1 2 2.3l-1.2 6.6A2 2 0 0 1 17.8 19.5H7"/>',
  activity: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
};
const navIcon = (id) => NAV_ICONS[id] ? `<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true">${NAV_ICONS[id]}</svg>` : '';
const NAV = {
  coordinator: [
    { id: 'dashboard', label: 'Dashboard' },
    { id: 'assistant', label: 'Assistant' },
    { id: 'queue', label: 'Appointments' },
    { id: 'requests', label: 'Requests' },
    { id: 'incidents', label: 'Incidents' },
    { id: 'feedback', label: 'Feedback' },
    { id: 'activity', label: 'Activity' },
  ],
  student: [
    { id: 'assistant', label: 'Assistant' },          // everything a student does happens in the chat
    { id: 'settings', label: 'Settings' },            // PIN, appearance and the student's own data
  ],
};

const ROLE_META = {
  coordinator: {
    title: 'Coordinator', avatar: 'PM', caption: 'Property Management', eyebrow: 'COORDINATOR',
    blurb: 'Type a student ID or name and I will show that student’s record, with address and contact number.',
    welcome: 'Hi! I’m an automated assistant for school IDs. Type a student ID or name and I will show that student’s record, with address and contact number. Type “records” to list the IDs that are not made yet.',
  },
  student: {
    title: 'Student', avatar: 'ST', caption: 'Self-service', eyebrow: 'STUDENT',
    blurb: 'Type a student ID or name and I will show that student’s ID status. Need the office’s help? Ask me, then tap Request help.',
    welcome: 'Hi! I’m an automated assistant for school IDs. Type a student ID or name and I will show that student’s ID status. You can also ask about requirements, office hours or a lost ID. When the office needs to step in, I will show a Request help button right here.',
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
/* An error under a form: it fades in and the form shakes, so it is noticed. */
function showError(id, message) {
  const line = $(id);
  line.textContent = message;
  fx.reveal(line);
  fx.shake(line.closest('form'));
}
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
  if (response.status === 401 && body.sign_in) { location.assign(body.sign_in); throw new Error(body.error); }
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

/* Sidebar badges: how many things are waiting on each page. A number that changed bounces. */
const shownBadges = {};
function renderBadges() {
  document.querySelectorAll('#role-nav .nav').forEach(nav => {
    nav.querySelector('.notification-badge')?.remove();
    const view = nav.dataset.view;
    const count = badgeCounts[view] || 0;
    if (count > 0) {
      const pulse = view === 'dashboard' ? ' pulse' : '';
      nav.insertAdjacentHTML('beforeend', `<span class="notification-badge${pulse}" title="${count} waiting">${count}</span>`);
      if (shownBadges[view] !== count) fx.pop(nav.querySelector('.notification-badge'), { scale: 1.5 });
    }
    shownBadges[view] = count;
  });
}

let toastTimer = null, toastTurn = 0;
function notify(message, duration = 3500) {
  clearTimeout(toastTimer);
  const toast = $('toast');
  const turn = ++toastTurn;                          // a newer message must not be wiped by the older one's exit
  toast.textContent = message;
  fx.toast(toast);
  toastTimer = setTimeout(() => {
    fx.hide(toast).then(() => { if (turn === toastTurn) toast.textContent = ''; });
  }, duration);
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
  fx.turn($('theme-toggle'));
}
function updateThemeIcon(theme) {
  const button = $('theme-toggle');
  if (button) button.textContent = theme === 'dark' ? '☀️' : '🌙';
}

function setMobileNav(open) {
  const changed = document.body.classList.contains('nav-open') !== open;
  document.body.classList.toggle('nav-open', open);
  const button = $('menu-toggle');
  button.setAttribute('aria-expanded', String(open));
  button.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
  button.textContent = open ? '✕' : '☰';
  $('nav-backdrop').hidden = !open;
  if (changed) {
    fx.turn(button, open ? 90 : -90);
    if (open) {
      fx.fade($('nav-backdrop'));
      fx.enter($('role-nav').children, { x: -14, y: 0, delay: 0.1, step: 0.04, duration: 0.3 });
    }
  }
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

/* With student sign-in on, the server renders who is signed in; the chat is about that student and no one else. */
function signedStudent() {
  try {
    const me = JSON.parse(document.body.dataset.student || 'null');
    if (me && Number.isInteger(me.id) && typeof me.student_id === 'string') return { id: me.id, student_id: me.student_id, name: me.name };
  } catch { /* no signed-in student */ }
  return null;
}

function renderSelected() {
  const banner = $('selected-student');
  const text = selected ? `📌 ${selected.name} — ${selected.student_id}` : 'No student selected yet. Type a student ID or name.';
  const changed = banner.textContent !== text;
  banner.textContent = text;
  $('clear-selected').hidden = !selected || !!signedStudent();
  if (changed) fx.swap(banner);
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
  fx.reset();                                    // another workspace: the lists draw themselves from the start
  workspaceRole = role;
  const meta = ROLE_META[role];
  $('role-badge').textContent = meta.title;
  $('role-avatar').textContent = meta.avatar;
  fx.swap($('role-badge'));
  fx.pop($('role-avatar'));
  $('role-caption').innerHTML = `${escapeHtml(meta.caption)}<small>Local prototype · sample records</small>`;
  $('assistant-eyebrow').textContent = `${meta.eyebrow} ASSISTANT`;
  $('assistant-blurb').textContent = meta.blurb;
  $('incidents-eyebrow').textContent = meta.eyebrow;
  selected = restoreSelected(role);
  const me = role === 'student' ? signedStudent() : null;
  if (me) {
    selected = me;
    $('assistant-blurb').textContent = 'Ask about your ID status, requirements, office hours or a lost ID. Need the office’s help? Ask me, then tap Request help.';
    $('chat-input').placeholder = 'Type “my status” or ask a question…';
    renderCaption(me);
    setupSettings();
    document.querySelector('#switch-role small').textContent = 'Sign out';
  }
  const sw = $('switch-role');                                        // nobody but the admin chooses a role now
  if (document.body.dataset.admin) sw.querySelector('small').textContent = 'Back to Admin panel';
  else if (role === 'coordinator') sw.querySelector('small').textContent = 'Sign out';
  sw.hidden = !(document.body.dataset.admin || role === 'coordinator' || me);
  bulkSelected.clear();
  for (const key of Object.keys(badgeCounts)) badgeCounts[key] = 0;
  renderNav();
  renderSelected();
  updateSuggestions();
  loadChatHistory();
  placeTicker(role);
  loadNews();                                  // every role sees the school's news
  if (role === 'coordinator') {
    load();                                    // also loads the numbers behind the sidebar badges
  }
  const items = NAV[role];
  const wanted = useHash ? location.hash.slice(1) : '';
  showView(items.some(item => item.id === wanted) ? wanted : items[0].id, { replay: true });
}

function renderCaption(me, status = '') {
  $('role-caption').innerHTML = `${escapeHtml(me.name)}<small>${escapeHtml(me.student_id)} · ${escapeHtml(status || 'signed in')}</small>`;
}

function renderNav() {
  const items = NAV[workspaceRole] || [];
  $('role-nav').innerHTML = items.map(item =>
    `<a class="nav" href="#${item.id}" data-view="${item.id}">${navIcon(item.id)}<span>${escapeHtml(item.label)}</span></a>`).join('');
  fx.enter($('role-nav').children, { x: -14, y: 0, step: 0.05, duration: 0.3 });
  $('mobile-dock').innerHTML = items.map(item =>
    `<a href="#${item.id}" data-view="${item.id}">${navIcon(item.id)}<span>${escapeHtml(plainLabel(item.label))}</span></a>`).join('');
  const single = items.length < 2;                 // a role with one page (the student's chat) needs no row of tabs
  $('mobile-dock').hidden = single;
  document.body.classList.toggle('single-view', single);
  renderBadges();
}

function scrollMessages() {
  const log = $('messages');
  log.scrollTop = log.scrollHeight;
}

function showView(viewId, { replay = false } = {}) {
  const items = NAV[workspaceRole] || [];
  if (!items.some(item => item.id === viewId)) viewId = items.length ? items[0].id : 'assistant';
  const opened = replay || viewId !== currentView;           // a real change of page, or a new role: the page animates in
  document.querySelectorAll('.workspace').forEach(el => { el.hidden = el.id !== `view-${viewId}`; });
  if (viewId !== currentView) window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  currentView = viewId;
  document.querySelectorAll('#role-nav .nav, #mobile-dock a').forEach(link => {
    const active = link.dataset.view === viewId;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
  moveNavInk();
  const dock = $('mobile-dock');                     // the dock scrolls when it has many tabs; keep the open one in view
  const current = dock && dock.querySelector('a.active');
  if (current && dock.clientWidth) dock.scrollTo({ left: current.offsetLeft - (dock.clientWidth - current.offsetWidth) / 2, behavior: 'smooth' });
  const entry = items.find(item => item.id === viewId);
  const section = entry ? plainLabel(entry.label) : 'Home';
  $('topbar-section').textContent = section;
  document.title = `${section} · School ID`;
  history.replaceState(null, '', `#${viewId}`);
  closeMobileNav();
  if (opened) { fx.view($(`view-${viewId}`)); fx.swap($('topbar-section')); }

  if (workspaceRole === 'coordinator') {
    if (viewId === 'dashboard') { fitTicker(); loadStats({ quiet: true, resync: true }); }
    if (viewId === 'queue') loadQueue();
    if (viewId === 'requests') loadRequests();
    if (viewId === 'feedback') loadFeedback();
    if (viewId === 'activity') loadActivity();
  }
  if (viewId === 'incidents') loadIncidents();
  if (viewId === 'assistant') {
    fitTicker();
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

/* `reuse`: when a load is already on its way, wait for that one instead of asking again (two loads would draw the
   Dashboard twice, and the second drawing cuts the first one's animations short). Anything that follows a change
   asks again, so it never gets numbers from before the change. */
let loading = null;
function load({ reuse = false } = {}) {
  if (reuse && loading) return loading;
  const mine = (async () => {
    try {
      const data = await api('/api/inventory');
      records = data.records.map(indexRecord);
      if (Array.isArray(data.statuses) && data.statuses.length) statuses = data.statuses;
      if (Array.isArray(data.not_yet_made)) notYetMade = data.not_yet_made;
      fillSections();
      render();
      loadStats({ quiet: true });
    } catch (error) { notify(error.message); }
  })();
  loading = mine;
  mine.finally(() => { if (loading === mine) loading = null; });
  return mine;
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
  fx.tiles($('dashboard-tiles'));
}

function renderStatusChips(f, today) {
  const base = records.filter(r => matchesFilters(r, f, today, 'status'));
  const chips = [['', 'All'], [NOT_MADE, 'Not yet made'], ...statuses.map(s => [s, s])];
  $('status-chips').innerHTML = chips.map(([value, label]) => {
    const n = value ? base.filter(r => matchesStatus(r, value)).length : base.length;
    return `<button type="button" class="filter-chip ${statusClass(value || 'all')}" data-status-chip="${escapeHtml(value)}" aria-pressed="${statusFilter === value}">${escapeHtml(label)} <b>${n}</b></button>`;
  }).join('');
  fx.chips($('status-chips'));
}

function renderAttention(f, today) {
  const base = records.filter(r => matchesFilters(r, f, today, 'attention'));
  $('attention-chips').innerHTML = ATTENTION.map(a => {
    const n = base.filter(r => a.test(r, today)).length;
    return `<button type="button" class="filter-chip${a.alert && n ? ' alert' : ''}" data-attention="${a.id}" aria-pressed="${attentionFilter === a.id}">${escapeHtml(a.label)} <b>${n}</b></button>`;
  }).join('');
  fx.chips($('attention-chips'));
}

function rowHtml(r, today) {
  const checked = bulkSelected.has(r.id);
  const when = r.schedule_date ? `${escapeHtml(r.schedule_type)} · ${escapeHtml(r.schedule_date)} ${escapeHtml(r.schedule_time)}` : '';
  const overdue = ATTENTION[0].test(r, today);
  const options = statuses.map(s => `<option${s === r.status ? ' selected' : ''}>${escapeHtml(s)}</option>`).join('');
  const none = '<span class="none">—</span>';
  const sig = escapeHtml([r.status, r.schedule_type, r.schedule_date, r.schedule_time, r.pending_reason, r.name, r.year_section,
    r.address, r.contact_number, r.request_type].join('|'));          // what the row shows: a change of it makes the row glow
  return `<tr data-id="${r.id}" data-sig="${sig}"${checked ? ' class="checked"' : ''}>
    <td class="check-col"><input type="checkbox" class="bulk-check" data-bulk="${r.id}"${checked ? ' checked' : ''} aria-label="Select ${escapeHtml(r.name)}"></td>
    <td data-label="QR" class="qr-col"><img class="row-qr" src="/qr/${r.id}.png" width="48" height="48" loading="lazy" alt="QR code of ${escapeHtml(r.name)}"></td>
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
  renderTicker();
  badgeCounts.dashboard = records.filter(r => r.status === 'Pending').length;
  renderBadges();

  const shown = matching.slice(0, visibleRows);
  $('count').textContent = `${matching.length} of ${records.length}`;
  $('records').innerHTML = shown.length
    ? shown.map(r => rowHtml(r, today)).join('')
    : `<tr class="empty-row"><td colspan="9">${emptyState(records.length
      ? 'No students match these filters.<br>Try another search or reset the filters.'
      : 'No students yet.<br>Use <b>Add student</b> or <b>Import students</b>.')}</td></tr>`;
  fx.rows($('records'));
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
  const bar = $('bulk-toolbar'), wasVisible = bar.classList.contains('visible');
  bar.classList.toggle('visible', picked > 0);
  if (picked > 0 && !wasVisible) fx.reveal(bar);
  const label = `${picked} selected`;
  if (picked > 0 && $('bulk-count').textContent !== label) fx.pop($('bulk-count'), { scale: 1.25 });
  $('bulk-count').textContent = label;
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
  if (ids.length >= 10 && !(await ux.confirm({ title: 'Change many students?', text: `Set ${ids.length} students to “${status}”?`, ok: 'Set status' }))) return;
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
/* A status change comes with an Undo bar for a few seconds: one click puts the old status back. */
function undoable(message, id, previous) {
  ux.undo({
    text: message,
    onUndo: async () => {
      try {
        replaceRecord(await api(`/api/inventory/${id}`, postJson({ status: previous }, 'PATCH')));
        notify(`Put back to ${previous}.`);
        render(); loadStats({ quiet: true }); refreshActivity();
      } catch (error) { notify(error.message); load(); }
    },
  });
}

async function changeStatus(select) {
  const id = Number(select.dataset.statusSelect);
  const row = records.find(r => r.id === id);
  if (!row || select.value === row.status) return;
  const previous = row.status, next = select.value;
  select.disabled = true;
  try {
    replaceRecord(await api(`/api/inventory/${id}`, postJson({ status: next }, 'PATCH')));
    undoable(`${row.name}: ${previous} → ${next}.`, id, previous);
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
    const before = row.status;
    replaceRecord(await api(`/api/inventory/${row.id}`, postJson({ status: 'Released' }, 'PATCH')));
    undoable(`${row.name} is now marked as released.`, row.id, before);
    render();
    loadStats({ quiet: true });
    refreshActivity();
  } catch (error) { notify(error.message); }
}

/* A new sign-in PIN for one student. It is shown once; the server keeps only a hash. */
async function issuePin(row) {
  if (!(await ux.confirm({ title: 'Issue a new PIN?', text: `${row.name} will sign in with the new PIN. Any PIN they had stops working.`, ok: 'Issue PIN' }))) return;
  try {
    const result = await api(`/api/inventory/${row.id}/pin`, { method: 'POST' });
    refreshActivity();
    await ux.reveal({ title: 'New sign-in PIN', subject: `${result.name} · ${result.student_id}`, secret: result.pin,
                      note: 'Give it to the student in person. It is shown only this once and the server keeps only a hash.' });
  } catch (error) { notify(error.message); }
}

/* A lost ID goes back to Pending (the server checks that it had been Released). */
async function reportLost(row) {
  const ok = await ux.confirm({ title: `${row.name} lost the ID?`, ok: 'Report lost ID',
    text: 'The status goes from Released back to Pending and the release appointment is cleared.\nSend the student to the Cashier first to pay the replacement fee.' });
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
  if (!(await ux.confirm({ title: `Delete ${row.name}?`, text: `${row.student_id} and their record are removed. This cannot be undone.`, ok: 'Delete', danger: true }))) return;
  try {
    await api(`/api/inventory/${row.id}`, { method: 'DELETE' });
    bulkSelected.delete(row.id);
    if (selected && selected.id === row.id) setSelected(null);
    notify(`Deleted ${row.name}.`);
    await fx.leave($('records').querySelector(`tr[data-id="${row.id}"]`));
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
    ['pin', '🔑 Issue sign-in PIN'],
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
  fx.menu(rowMenu, top < below);
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
    pin: () => issuePin(row),
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
  fx.dialog($('record-dialog'));
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
    showError('form-error', error.message);
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
  fx.dialog($('import-dialog'));
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
  if (!file) { showError('import-error', 'Choose a CSV file first.'); return; }
  if (file.size > maxMb * 1024 * 1024) {
    showError('import-error', `That file is ${(file.size / 1048576).toFixed(1)} MB. The limit is ${maxMb} MB.`);
    return;
  }
  const body = new FormData();
  body.append('file', file);
  $('import-submit').disabled = true;
  try {
    const result = await api('/api/inventory/import', { method: 'POST', body });   // the browser sets the multipart header
    $('import-result').innerHTML = importSummary(result);
    fx.fill($('import-result'));
    input.value = '';
    if (result.added) {
      notify(`Added ${plural(result.added, 'student')}.`);
      await load();
      refreshActivity();
    }
  } catch (error) {
    showError('import-error', error.message);
  } finally {
    $('import-submit').disabled = false;
  }
}

function exportCSV() {
  download('/api/inventory/export');
  notify('Downloading the student list…');
}

/* ── Numbers behind the sidebar badges ── */
/* `resync`: when these numbers say the student list changed (someone else added a student or moved an ID), the
   Dashboard is loaded again. load() asks for the numbers without `resync`, so the two cannot loop. */
async function loadStats({ quiet = false, resync = false } = {}) {
  if (workspaceRole !== 'coordinator') return;
  try {
    stats = await api('/api/stats');
    const dateChanged = todayString() !== stats.today;       // the date the last drawing used is not the server's
    serverToday = stats.today;
    badgeCounts.requests = stats.open_requests;
    badgeCounts.feedback = stats.new_feedback;
    badgeCounts.incidents = stats.open_incidents;
    renderBadges();
    if (resync && dashboardIsStale()) { await load({ reuse: true }); return; }
    if (dateChanged && records.length) render();   // the attention filters depend on today's date
    else renderTicker();
  } catch (error) {
    if (!quiet) notify(error.message);
  }
}

function dashboardIsStale() {
  if (!stats || (!records.length && !stats.total)) return false;
  return stats.total !== records.length ||
    statuses.some(status => (stats.by_status[status] || 0) !== records.filter(r => r.status === status).length);
}

/* ═══════════════ Ticker (Dashboard) ═══════════════ */
/* A strip under the heading that scrolls where the IDs are, what needs attention and the latest activity. It is drawn
   again only when something in it changed, so typing in the search box never restarts the scroll. */
const TICKER_PX_PER_SECOND = 55;
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/* The ticker carries the school's announcements, from data/pgpc_news.json through /api/news. */
let newsItems = [];
async function loadNews() {
  try {
    newsItems = (await api('/api/news')).news || [];
  } catch { newsItems = []; }
  renderTicker();
}

function tickerItems() {
  return newsItems.map(item => ({ icon: item.icon, text: item.text }));
}

const tickerText = (item) => item.text || `${item.label} ${item.value}`;

function tickerItemHtml(item) {
  const lead = item.dot ? `<i class="dot ${escapeHtml(item.dot)}" aria-hidden="true"></i>` : `<span aria-hidden="true">${item.icon}</span>`;
  const body = item.text ? escapeHtml(item.text) : `${escapeHtml(item.label)} <b>${escapeHtml(item.value)}</b>`;
  return `<span class="ticker-item${item.alert ? ' alert' : ''}${item.muted ? ' muted' : ''}">${lead}${body}</span>`;
}

/* The strip sits under the Dashboard heading for the coordinator and under the Assistant heading for everyone else. */
function placeTicker(role) {
  const ticker = $('ticker');
  if (role === 'coordinator') $('dashboard-tiles').before(ticker);
  else $('view-assistant').querySelector('.heading').after(ticker);
  tickerFit = '';                                      // measured again in its new place
}

function renderTicker() {
  if (!newsItems.length) return;
  const items = tickerItems();
  const key = JSON.stringify(items);
  if (key === tickerKey) return;                       // nothing changed: the scroll carries on
  tickerKey = key;
  tickerUnit = items.map(tickerItemHtml).join('');
  $('ticker-summary').textContent = items.map(tickerText).join('. ');
  const first = $('ticker').hidden;
  $('ticker').hidden = false;
  fitTicker();
  if (first) fx.reveal($('ticker')); else fx.fade($('ticker-window'), { from: 0.3, duration: 0.5 });
}

/* The loop shows two copies side by side. One copy has to be wider than the strip or the loop would show a gap,
   so it is repeated to fill it. Called again when the strip changes size or the Dashboard is opened. */
function fitTicker() {
  const view = $('ticker-window');
  if (!tickerUnit || !view || !view.clientWidth) return;     // hidden for now: it is fitted when the Dashboard opens
  const still = reducedMotion();
  const probe = Object.assign(document.createElement('div'), { className: 'ticker-group', innerHTML: tickerUnit });
  probe.style.cssText = 'position:absolute;visibility:hidden;width:max-content';
  view.appendChild(probe);
  const unitWidth = probe.scrollWidth;
  probe.remove();
  const repeats = still || !unitWidth ? 1 : Math.max(1, Math.ceil(view.clientWidth * 1.15 / unitWidth));
  const fit = `${tickerKey}|${repeats}|${still}`;
  if (fit === tickerFit) return;
  tickerFit = fit;
  const group = `<div class="ticker-group">${tickerUnit.repeat(repeats)}</div>`;
  $('ticker-track').innerHTML = still ? group : group + group;
  $('ticker-track').style.setProperty('--ticker-seconds', `${Math.max(20, Math.round(unitWidth * repeats / TICKER_PX_PER_SECOND))}s`);
}

function setTickerPaused(paused) {
  $('ticker').classList.toggle('paused', paused);
  const button = $('ticker-toggle');
  button.setAttribute('aria-pressed', String(paused));
  button.setAttribute('aria-label', paused ? 'Play the ticker' : 'Pause the ticker');
  button.title = paused ? 'Play the ticker' : 'Pause the ticker';
  button.textContent = paused ? '▶' : '⏸';
  if (paused) store.set('tickerPaused', '1'); else store.remove('tickerPaused');
}

/* ═══════════════ Assistant (everyone): a full-page chat ═══════════════ */

function showTyping() {
  const indicator = document.createElement('div');
  indicator.className = 'typing-indicator';
  indicator.id = 'typing';
  indicator.innerHTML = '<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>';
  $('messages').appendChild(indicator);
  fx.typing(indicator);
  scrollMessages();
}
function hideTyping() {
  const indicator = $('typing');
  if (!indicator) return;
  fx.untyping(indicator);
  indicator.remove();
}

/* The ANN's score is how closely the words matched a known topic, not a probability that the
   answer is right, so people see a plain label and the number only as a tooltip. Student lookups
   and the records list come from the database and carry no score at all. */
const QUIET_INTENTS = new Set(['out_of_scope', 'greeting', 'thanks_goodbye']);
/* Answers that are a record or a list from the database are drawn as a card. */
/* Confirmations glow once when they arrive. */
const GLOW_INTENTS = new Set(['incident_filed', 'request_submitted']);
const CARD_INTENTS = new Set(['student_lookup', 'student_not_found',
  'records_list', 'my_requests', 'request_submitted', 'incidents_list', 'incident_filed',
  'incident_status']);

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
  node.dataset.raw = text;
  node.dataset.time = meta.timestamp ? formatTime(meta.timestamp) : formatNow();
  if (kind === 'bot') html += '<button type="button" class="msg-copy" data-copy aria-label="Copy this answer" title="Copy this answer">⧉</button>';
  node.innerHTML = html;
  if (kind === 'bot' && meta.messageId) {
    node.dataset.messageId = meta.messageId;
    node.dataset.session = meta.sessionId || sessionId();
    node.appendChild($('feedback-template').content.firstElementChild.cloneNode(true));
    setRating(node, meta.rating || '');
  }
  $('messages').appendChild(node);
  scrollMessages();
  if (meta.fresh !== false) fx.message(node, { kind, glow: kind === 'bot' && GLOW_INTENTS.has(meta.intent) });
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
  if (noteEl) {
    noteEl.textContent = note;
    if (note) fx.swap(noteEl);
  }
}

function feedbackIds(node) {
  return { message_id: Number(node.dataset.messageId), session_id: node.dataset.session };
}

/* The details form is wider than a normal reply, so the bubble stretches while it is open. */
function togglePanel(node, open) {
  const panel = node && node.querySelector('.feedback-panel');
  if (!panel) return;
  const opening = open && panel.hidden;
  panel.hidden = !open;
  node.classList.toggle('rating-open', open);
  if (opening) fx.reveal(panel);
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
      fx.pop(node.querySelector(`[data-rate="${rating}"]`), { scale: 0.8 });
    } else {
      await api('/api/chat/feedback', postJson({ ...ids, rating }));
      setRating(node, rating, rating === 'up' ? 'Thanks for the feedback.' : '');
      fx.pop(node.querySelector(`[data-rate="${rating}"]`), { scale: 1.5 });
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
    print_sticker: () => (target ? openTab(`${workspaceRole === 'student' ? '/me' : ''}/sticker/${target.id}`) : needStudent()),
    preview_card: () => (target ? openTab(`${workspaceRole === 'student' ? '/me' : ''}/card/${target.id}`) : needStudent()),
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
    open_incident: () => openIncident(action, student),
    open_student_request: () => openStudentRequest(action, student),
    ask_office: () => send('Where is the Property Management Office and what are the hours?'),
    ask_human: () => send('I need to talk to a person at the office'),
    send: () => {
      if (!action.text) return;
      if (button) {                                  // a double tap must not send twice
        button.disabled = true;
        setTimeout(() => { button.disabled = false; }, 1500);
      }
      send(action.text);
    },
  };
  (handlers[action.type] || (() => notify('That action is not available in this role.')))();
}

/* What addMessage needs to draw a bot answer, from the server's reply to /api/chat or /api/chat/request. */
function replyMeta(response, session) {
  return {
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
  };
}

async function send(text, { retry = false } = {}) {
  if (sending) { notify('One moment, I am still answering.'); return; }
  sending = true;
  const session = sessionId();                  // the answer belongs to the chat that asked
  const button = $('chat-form').querySelector('button');
  if (!retry) addMessage(text, 'user');
  button.disabled = true;
  fx.nudge(button);
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
    addMessage(response.reply, 'bot', replyMeta(response, session));
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
  if (workspaceRole === 'student') {
    const help = act('📝 Request help', { type: 'open_student_request' });
    return selected ? [
      ask('What is the status of my student ID?', 'Status'),
      ask('When can I collect my school ID?', 'Release date'),
      ask('Why is my student ID pending?', 'Pending reason'),
      help,
      ask(`my requests ${selected.student_id}`, '📋 My requests'),
      ask('What documents are needed for a new student ID?', 'Requirements'),
    ] : [
      ask('What documents are needed for a new student ID?', 'Requirements'),
      ...common,
      ask('I lost my school ID', 'Lost ID'),
      help,
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
  fx.refill(container);
  container.querySelectorAll('[data-suggestion]').forEach(button => {
    const item = items[Number(button.dataset.suggestion)];
    button.onclick = () => (item.q ? send(item.q) : handleAction(item.action, { student: selected }));
  });
}

function welcomeMessage() {
  const meta = ROLE_META[workspaceRole];
  const me = workspaceRole === 'student' ? signedStudent() : null;
  addMessage(me ? `Hi ${me.name.split(' ')[0]}! You are signed in. Type “my status” to see your ID, or ask about requirements, office hours, a lost ID or how an ID is made. For anything the office must do, I will show a Request help button.`
    : meta ? meta.welcome : 'Hi! Choose a role so I can show the right tools.', 'bot', { confidence: 0 });
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
        fresh: false,                               // an old conversation does not fly in message by message
      }));
      fx.enter($('messages').querySelectorAll('.message:nth-last-child(-n+6)'), { y: 10, step: 0.05, duration: 0.3 });
    }
  } catch { /* the welcome message stays */ }
}

async function newChat() {
  historyLoadId++;
  try {
    await api(`/api/chat/history?session_id=${encodeURIComponent(sessionId())}`, { method: 'DELETE' });
  } catch { /* the chat is cleared on screen either way */ }
  setSelected(null);
  const old = Array.from($('messages').children);           // only what was on screen is swept away
  await Promise.all(old.slice(-8).map(node => fx.leave(node)));
  old.forEach(node => node.remove());
  welcomeMessage();
}

function updateChatCount() {
  const length = $('chat-input').value.length;
  const counter = $('chat-count');
  const wasHidden = counter.hidden, wasNear = counter.classList.contains('near');
  counter.hidden = length < 800;
  counter.textContent = `${length} / ${$('chat-input').maxLength}`;
  counter.classList.toggle('near', length >= 950);
  if (wasHidden && !counter.hidden) fx.reveal(counter);
  if (!wasNear && counter.classList.contains('near')) fx.shake(counter);
}

/* ═══════════════ Appointments (coordinator) ═══════════════ */
let lastQueue = [];

async function loadQueue() {
  try {
    const data = await api('/api/queue');
    lastQueue = data.appointments;
    if (!lastQueue.length) {
      $('queue-list').innerHTML = emptyState('No appointments yet.<br>Open a student on the Dashboard and choose Edit / schedule.');
      fx.sync($('queue-list'));
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
      <article class="tool-row" data-key="q${row.id}">
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
    fx.sync($('queue-list'));
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
    fx.chips($('request-chips'));
    if (!data.requests.length) {
      $('request-inbox').innerHTML = emptyState(requestFilter ? `No requests are ${escapeHtml(requestFilter.toLowerCase())} right now.` : 'No student requests yet.');
      fx.sync($('request-inbox'));
      return;
    }
    $('request-inbox').innerHTML = data.requests.map(row => `
      <article class="tool-row" data-key="r${row.id}">
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
    fx.sync($('request-inbox'));
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
  fx.tiles($('feedback-tiles'));
  const struggled = summary.by_intent.filter(row => row.down > 0);
  $('feedback-topics').innerHTML = struggled.length
    ? struggled.map(row => `<div class="bar-row"><span>${escapeHtml(topicName(row.intent))}</span><span class="bar-track" aria-hidden="true"><span class="bar-fill down" style="width:${Math.round(100 * row.down / Math.max(1, struggled[0].down))}%"></span></span><b>👎 ${row.down} · 👍 ${row.up}</b></div>`).join('')
    : '<p class="subtle">No 👎 ratings yet.</p>';
  $('feedback-reasons').innerHTML = barsHtml(Object.entries(summary.by_reason), 'down');
  fx.bars($('feedback-topics'));
  fx.bars($('feedback-reasons'));
  $('feedback-chips').innerHTML = FEEDBACK_FILTERS.map(([id, label, , count]) =>
    `<button type="button" class="filter-chip" data-feedback-filter="${id}" aria-pressed="${feedbackFilter === id}">${escapeHtml(label)} <b>${count(summary)}</b></button>`
  ).join('');
  fx.chips($('feedback-chips'));
  if (!items.length) {
    $('feedback-list').innerHTML = emptyState(feedbackFilter === 'review'
      ? 'Nothing waiting for review. When someone rates an answer 👎 it shows up here.'
      : 'No feedback in this view yet.');
    fx.sync($('feedback-list'));
    return;
  }
  const options = (current) => '<option value="">Not set</option>' + knownIntents.map(intent =>
    `<option value="${escapeHtml(intent)}"${intent === current ? ' selected' : ''}>${escapeHtml(topicName(intent))}</option>`).join('');
  $('feedback-list').innerHTML = items.map(item => {
    const down = item.rating === 'down';
    return `<article class="tool-row feedback-item" data-key="f${item.id}">
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
  fx.sync($('feedback-list'));
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
        <article class="tool-row" data-key="${escapeHtml(`${row.timestamp}|${row.action}|${row.detail}`)}">
          <div>
            <h3>${escapeHtml(row.action)}</h3>
            <p>${escapeHtml(row.actor_role)} · ${escapeHtml(row.detail)} · ${escapeHtml(row.timestamp)}</p>
          </div>
        </article>`).join('')
      : emptyState('No activity yet. Changes will appear here.');
    fx.sync($('activity-list'));
  } catch (error) { notify(error.message); }
}

/* ═══════════════ QR scanner (coordinator) ═══════════════ */

/* ── QR scanner (camera) ──
   The decoder (jsQR, vendored in static/vendor) is loaded only when the scanner opens, so it works in
   any browser with camera access. getUserMedia needs a secure page: https:// or localhost. */
let scanStream = null, scanTimer = 0, jsQrLoading = null, stopScanLine = () => {};
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
  stopScanLine();
  stopScanLine = () => {};
  $('scan-frame').classList.remove('scanning');
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

async function findOnDashboardByScan(token) {
  try {
    const data = await api(`/api/verify?q=${encodeURIComponent(token)}`);
    const match = data.matches.length === 1 ? data.matches[0] : null;
    if (!match) { notify('That QR code does not match any student on the list.'); return; }
    showOnDashboard(match.student_id);
    notify(`Scanned ${match.name}.`);
  } catch (error) { notify(error.message); }
}

function finishScan(token) {
  $('scan-dialog').close();
  findOnDashboardByScan(token);
}

async function openScanner() {
  const dialog = $('scan-dialog'), video = $('scan-video'), status = $('scan-status');
  video.hidden = true;
  status.textContent = 'Starting the camera…';
  dialog.showModal();
  fx.dialog(dialog);
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
    $('scan-frame').classList.add('scanning');
    stopScanLine = fx.scanner($('scan-line'), $('scan-frame'));
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

/* ═══════════════ Incidents (coordinator): No ID and Lost ID ═══════════════ */
let incidentCheckTicket = 0, incidentCheckTimer = 0;
const ID_STILL_TYPING = /^p\d{0,8}$/i;     // part of a student ID: wait for the rest before looking it up

/* What the records say about the student, shown while the form is still being filled in. */
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
    fx.forget(panel);
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
  fx.update(panel).then(moved => {                    // a student whose answer disagrees with the records gets a nudge
    const warning = moved && panel.querySelector('.claim.warn');
    if (warning) fx.shake(warning);
  });
}

function scheduleIncidentCheck() {
  clearTimeout(incidentCheckTimer);
  incidentCheckTimer = setTimeout(() => runIncidentCheck(), 350);
}

const incidentCategories = () => [...$('incident-form').elements.category.options].map(o => o.value);

/* The File incident button opens the form over the chat, whichever page it was pressed on. `action` may name the
   student and the category the answer was about; `student` is the one the chat is on. */
function openIncident(action = {}, student = null) {
  if (workspaceRole === 'student') return;
  if (currentView !== 'assistant') showView('assistant');
  const form = $('incident-form');
  const who = action.student_id || (student && student.student_id) || (selected && selected.student_id) || '';
  form.reset();
  $('incident-error').textContent = '';
  $('incident-check').hidden = true;
  $('incident-check').innerHTML = '';
  fx.forget($('incident-check'));
  incidentCheckTicket++;                            // a check still on its way is no longer wanted
  form.elements.student_id.value = who;
  if (incidentCategories().includes(action.category)) form.elements.category.value = action.category;
  $('incident-dialog').showModal();
  fx.dialog($('incident-dialog'));
  if (who) runIncidentCheck({ force: true });
  form.elements[who ? 'detail' : 'student_id'].focus();
}

function incidentRow(row) {
  const who = row.student_name ? `${row.student_name} · ${row.student_id}` : (row.student_id || 'No student ID');
  const tag = row.false_statement ? `<span class="tag danger">False statement${row.penalty ? ` · ${money(row.penalty)}` : ''}</span>` : '';
  const flagButton = workspaceRole === 'coordinator' && row.student_id
    ? (row.false_statement
      ? `<button type="button" class="text-button danger-text" data-incident-flag="${row.id}" data-flag="false" title="Not a false statement after all">Withdraw false statement</button>`
      : `<button type="button" class="text-button" data-incident-flag="${row.id}" data-flag="true">Mark as false statement</button>`)
    : '';
  return `<article class="tool-row" data-key="i${row.id}">
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
    fx.sync($('incident-list'));
  } catch (error) { notify(error.message); }
}

/* Sending the form puts the confirmation in the chat: the coordinator never leaves the conversation. */
async function fileIncident(event) {
  event.preventDefault();
  const form = event.target;
  const button = $('incident-submit');
  const session = sessionId();
  const body = {
    category: form.elements.category.value,
    student_id: form.elements.student_id.value.trim(),
    location: form.elements.location.value.trim(),
    detail: form.elements.detail.value.trim(),
    false_statement: form.elements.false_statement.checked,
    session_id: session,
    workspace_role: workspaceRole,
  };
  button.disabled = true;
  $('incident-error').textContent = '';
  try {
    const response = await api('/api/chat/incident', postJson(body));
    incidentCheckTicket++;
    $('incident-dialog').close();
    if (session !== sessionId()) return;            // the role was switched meanwhile: that chat is not on screen
    if (response.student) setSelected(response.student);
    addMessage(`⚠️ File incident: ${response.incident.category}`, 'user');
    addMessage(response.reply, 'bot', replyMeta(response, session));
    notify(`Incident #${response.incident.id} filed.`, 6000);
    loadStats({ quiet: true });
  } catch (error) {
    showError('incident-error', error.message);          // the form stays open, with what was typed
  } finally {
    button.disabled = false;
  }
}

/* ═══════════════ Request help (student): a pop-up opened from the chat ═══════════════ */
/* The “Request help” button under an answer opens this form. Sending it puts the confirmation in the chat, and
   “my requests” in the chat follows the request, so a student never leaves the conversation. */

const requestTypes = () => [...$('student-request-form').elements.request_type.options].map(o => o.value).filter(Boolean);

function syncRequestNotice() {
  const notice = $('request-notice');
  const show = $('student-request-form').elements.request_type.value === 'Lost ID';
  const opening = show && notice.hidden;
  notice.hidden = !show;
  if (opening) fx.reveal(notice);
}

/* `action` may name the student and the kind of help the answer was about; `student` is the one the chat is on. */
function openStudentRequest(action = {}, student = null) {
  if (workspaceRole !== 'student') return;
  const form = $('student-request-form');
  const who = action.student_id ? { student_id: action.student_id, name: action.name || '' } : (student || selected);
  form.reset();
  $('request-error').textContent = '';
  form.elements.student_id.value = who ? who.student_id : '';
  form.elements.name.value = who ? who.name : '';
  form.elements.request_type.value = requestTypes().includes(action.request_type) ? action.request_type : '';
  syncRequestNotice();
  $('request-dialog').showModal();
  fx.dialog($('request-dialog'));
  form.elements[['student_id', 'request_type'].find(name => !form.elements[name].value) || 'detail'].focus();
}

async function submitStudentRequest(event) {
  event.preventDefault();
  const body = Object.fromEntries([...new FormData(event.target)].map(([key, value]) => [key, String(value).trim()]));
  const button = $('request-submit');
  const session = sessionId();
  button.disabled = true;
  $('request-error').textContent = '';
  try {
    const response = await api('/api/chat/request', postJson({ ...body, session_id: session, workspace_role: workspaceRole }));
    $('request-dialog').close();
    if (session !== sessionId()) return;               // the role was switched meanwhile: that chat is not on screen
    if (response.student) setSelected(response.student);
    addMessage(`📝 Request help: ${response.request.request_type}`, 'user');
    addMessage(response.reply, 'bot', replyMeta(response, session));
    notify(`Request #${response.request.id} sent. It is now “${response.request.status}”.`, 6000);
  } catch (error) {
    showError('request-error', error.message);          // the form stays open, with what was typed
  } finally {
    button.disabled = false;
  }
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
    fx.pop(box, { scale: 1.3 });
    return;
  }
  const select = event.target.closest('[data-status-select]');
  if (select) changeStatus(select);
});
$('select-all').addEventListener('change', event => { selectAllMatching(event.target.checked); fx.pop(event.target, { scale: 1.3 }); });
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
$('dash-scan-btn').onclick = openScanner;
$('scan-close').onclick = () => $('scan-dialog').close();
$('scan-dialog').addEventListener('close', stopScanner);

/* ── Assistant ── */
$('chat-input').addEventListener('input', updateChatCount);
$('chat-form').onsubmit = event => {
  event.preventDefault();
  const text = $('chat-input').value.trim();
  if (text && !sending) { rememberSent(text); $('chat-input').value = ''; updateChatCount(); send(text); }
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
function openRoleDialog() {
  $('role-dialog').showModal();
  fx.dialog($('role-dialog'));
}
$('switch-role').onclick = () => {                              // the home page is the admin sign-in: only the admin can change role
  if (document.body.dataset.admin) location.assign('/');                       // back to the admin panel, still signed in
  else if (workspaceRole === 'coordinator') fetch('/coordinator/logout', { method: 'POST' }).then(() => location.assign('/coordinator'), () => location.assign('/coordinator'));
  else if (signedStudent()) fetch('/student/logout', { method: 'POST' }).then(() => location.assign('/student'), () => location.assign('/student'));
};
$('role-form').addEventListener('submit', event => {
  event.preventDefault();
  const role = event.submitter && event.submitter.value;
  if (ROLE_META[role]) location.assign(`/${role}`);
});
$('role-dialog').addEventListener('cancel', event => {
  if (!workspaceRole) event.preventDefault();       // the first visit needs a role
});

/* ── Everything that is drawn later: one listener, buttons found by their data- attributes ── */
document.addEventListener('click', async event => {
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
      .then(async () => {
        if (requestFilter && requestFilter !== requestButton.dataset.status) await fx.leave(requestButton.closest('.tool-row'));
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
      .then(async () => {
        if (feedbackFilter === 'review' && feedbackStatus.dataset.status === 'Reviewed') await fx.leave(feedbackStatus.closest('.tool-row'));
        notify(feedbackStatus.dataset.status === 'Reviewed' ? 'Marked as reviewed.' : 'Moved back to needs review.');
        loadFeedback();
        loadStats({ quiet: true });
        refreshActivity();
      })
      .catch(error => { notify(error.message); feedbackStatus.disabled = false; });
  }
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
    if (withdraw && !(await ux.confirm({ title: 'Withdraw this false statement?', text: 'The penalty is recalculated for the student’s other incidents.', ok: 'Withdraw' }))) return;
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

/* ── Incident form ── */
$('incident-form').onsubmit = fileIncident;
$('incident-form').elements.student_id.addEventListener('input', scheduleIncidentCheck);
$('incident-form').elements.student_id.addEventListener('change', () => runIncidentCheck({ force: true }));
$('incident-form').elements.category.addEventListener('change', () => runIncidentCheck());
$('incident-close').onclick = $('incident-cancel').onclick = () => $('incident-dialog').close();
$('incident-open').onclick = () => openIncident();
$('student-request-form').onsubmit = submitStudentRequest;
$('student-request-form').elements.request_type.addEventListener('change', syncRequestNotice);
$('request-close').onclick = $('request-cancel').onclick = () => $('request-dialog').close();
$('ticker-toggle').onclick = () => { setTickerPaused(!$('ticker').classList.contains('paused')); fx.turn($('ticker-toggle'), 180); };

/* ── Keyboard shortcuts ── */
document.addEventListener('keydown', event => {
  const ctrl = event.ctrlKey || event.metaKey;
  const key = event.key.toLowerCase();
  if (ctrl && key === 'n') {
    event.preventDefault();
    if (workspaceRole === 'coordinator') { showView('dashboard'); openDialog(); }
    else if (workspaceRole === 'student') openStudentRequest();
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

/* ═══════════════ Live updates (all three workspaces) ═══════════════
   Every change anywhere (a student signing in, the coordinator moving an ID, the admin issuing a PIN) moves one
   counter on the server. Each page asks for that counter every few seconds (never while the tab is hidden) and, when it
   moved, loads again only what is on screen. A page with an open form or a focused box waits for its next turn. */
const LIVE_MS = 4000;
let liveRev = null, liveBusy = false, liveMe = null, liveClosed = false;

function setLiveChip(on) {
  const chip = $('live-chip');
  if (!chip) return;
  chip.classList.toggle('off', !on);
  chip.querySelector('span').textContent = on ? 'Live' : 'Reconnecting…';
}

function pageIsBusy() {
  const active = document.activeElement;
  return !!document.querySelector('dialog[open]') || !!(active && active.matches && active.matches('input, textarea, select') && active.closest('.workspace:not([hidden])') && active.id !== 'search');
}

async function coordinatorLive() {
  if (pageIsBusy()) { liveRev = null; return; }              // asked again next time, so nothing is missed
  await loadStats({ quiet: true });
  const reload = { dashboard: () => load({ reuse: true }), queue: loadQueue, requests: loadRequests,
                   incidents: loadIncidents, feedback: loadFeedback, activity: loadActivity }[currentView];
  if (reload) await reload();
}

/* The student's own status, kept current. A change by the office is announced once in the chat and as a toast. */
function studentLive(me) {
  const key = JSON.stringify(me);
  if (key === JSON.stringify(liveMe)) return;
  const before = liveMe;
  liveMe = me;
  renderProfile(me);
  renderCaption(me, me.status);
  if (before && (before.status !== me.status || before.schedule_date !== me.schedule_date || before.schedule_time !== me.schedule_time
                 || before.pending_reason !== me.pending_reason || before.request_type !== me.request_type)) {
    const when = me.schedule_date ? ` Release schedule: ${me.schedule_date}${me.schedule_time ? ' at ' + me.schedule_time : ''}.` : '';
    const reason = me.status === 'Pending' && me.pending_reason ? ` Reason: ${me.pending_reason}.` : '';
    const text = `Update from the office: your ID is now “${me.status}”.${when}${reason}`;
    addMessage(text, 'bot', { confidence: 0 });
    notify(`Your ID status is now “${me.status}”.`, 6000);
  }
}

async function pollLive() {
  if (document.hidden || liveBusy || !workspaceRole || workspaceRole === 'student' && !signedStudent()) return;
  liveBusy = true;
  try {
    const response = await fetch(`/api/live?role=${workspaceRole}`, { cache: 'no-store' });
    if (response.status === 401) {
      const body = await response.json().catch(() => ({}));
      location.assign(body.sign_in || `/${workspaceRole}`);
      return;
    }
    if (!response.ok) throw new Error('live');
    const body = await response.json();
    setLiveChip(true);
    if (workspaceRole === 'student') {
      if (body.portal_open === false && !liveClosed) { liveClosed = true; location.reload(); return; }   // the admin closed the portal
      if (body.me) studentLive(body.me);
      liveRev = body.rev;
    } else {
      const changed = liveRev !== null && body.rev !== liveRev;
      liveRev = body.rev;
      if (changed) await coordinatorLive();
    }
  } catch { setLiveChip(false); }
  finally { liveBusy = false; }
}
setInterval(pollLive, LIVE_MS);
document.addEventListener('visibilitychange', () => { if (!document.hidden) pollLive(); });

/* ═══════════════ Student settings ═══════════════ */
function renderProfile(me) {
  const rows = [
    ['Name', me.name], ['Student ID', me.student_id], ['Course', me.course || '—'], ['Year & section', me.year_section],
    ['ID status', me.status], ['Request', me.request_type],
    ...(me.status === 'Pending' && me.pending_reason ? [['Reason', me.pending_reason]] : []),
    ...(me.schedule_date ? [['Release schedule', `${me.schedule_date}${me.schedule_time ? ' ' + me.schedule_time : ''}`]] : []),
    ['Signed in at', me.last_signin || '—'], ['Times signed in', String(me.signin_count)],
    ['PIN', me.pin_changed ? `Changed by you on ${me.pin_changed}` : 'Issued by the office'],
  ];
  const box = $('set-profile');
  const html = rows.map(([k, v]) => `<div><dt>${escapeHtml(k)}</dt><dd>${k === 'ID status' ? `<span class="status ${statusClass(v)}">${escapeHtml(v)}</span>` : escapeHtml(v)}</dd></div>`).join('');
  if (box.dataset.html !== html) { box.innerHTML = html; box.dataset.html = html; if (box.dataset.shown) fx.flash && fx.flash(box); box.dataset.shown = '1'; }
}

function applyPrefs() {
  const html = document.documentElement;
  html.dataset.text = store.get('textSize') || 'normal';
  document.body.classList.toggle('no-ticker', store.get('tickerHidden') === '1');
}

function setupSettings() {
  if (setupSettings.done) return;
  setupSettings.done = true;
  const mark = (group, value) => $(group).querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.v === value)));
  const themeValue = () => store.get('theme') || 'auto';
  mark('seg-theme', themeValue()); mark('seg-text', store.get('textSize') || 'normal');
  $('set-motion').checked = store.get('motion') === 'off';
  $('set-ticker').checked = store.get('tickerHidden') !== '1';

  $('seg-theme').onclick = (e) => {
    const b = e.target.closest('button'); if (!b) return;
    if (b.dataset.v === 'auto') store.remove('theme'); else store.set('theme', b.dataset.v);
    initTheme(); mark('seg-theme', b.dataset.v);
  };
  $('seg-text').onclick = (e) => {
    const b = e.target.closest('button'); if (!b) return;
    store.set('textSize', b.dataset.v); applyPrefs(); mark('seg-text', b.dataset.v);
  };
  $('set-motion').onchange = (e) => {
    if (e.target.checked) store.set('motion', 'off'); else store.remove('motion');
    window.dispatchEvent(new StorageEvent('storage', { key: 'motion' }));            // fx.js listens for this key
    document.documentElement.classList.toggle('motion-off', e.target.checked);
  };
  document.documentElement.classList.toggle('motion-off', store.get('motion') === 'off');
  $('set-ticker').onchange = (e) => { if (e.target.checked) store.remove('tickerHidden'); else store.set('tickerHidden', '1'); applyPrefs(); };

  $('set-clear').onclick = async () => { await newChat(); showView('assistant'); notify('Your conversation was cleared.'); };
  $('set-export').onclick = () => download('/api/student/export');
  $('set-signout').onclick = () => fetch('/student/logout', { method: 'POST' }).then(() => location.assign('/student'), () => location.assign('/student'));

  /* change PIN */
  const fields = ['pin-current', 'pin-new', 'pin-confirm'].map($);
  const [cur, nw, again] = fields;
  const problem = (pin) => (!/^\d{6}$/.test(pin) ? 'len' : new Set(pin).size === 1 || '123456789012'.includes(pin) || '098765432109'.includes(pin) ? 'mix' : '');
  const showError = (text, field) => {
    const box = $('pin-error'); box.textContent = text; box.hidden = !text;
    fields.forEach(f => f.classList.toggle('bad', !!text && (!field || f.id === `pin-${field}`)));
    if (text) { fx.shake($('pin-form')); }
  };
  const rules = () => {
    const v = nw.value;
    const ok = { len: /^\d{6}$/.test(v), mix: /^\d{6}$/.test(v) && problem(v) === '', same: v.length > 0 && v === again.value };
    document.querySelectorAll('#pin-rules li').forEach(li => li.classList.toggle('ok', ok[li.dataset.rule]));
    return ok.len && ok.mix && ok.same && /^\d{6}$/.test(cur.value);
  };
  fields.forEach(f => f.addEventListener('input', () => { f.value = f.value.replace(/\D/g, '').slice(0, 6); showError(''); rules(); }));
  $('pin-show').onchange = (e) => fields.forEach(f => { f.type = e.target.checked ? 'text' : 'password'; });
  $('pin-form').onsubmit = async (event) => {
    event.preventDefault();
    if (!rules()) { showError('Fill in the current PIN and a new 6-digit PIN, typed twice.'); return; }
    const button = $('pin-save');
    button.disabled = true;
    try {
      await api('/api/student/pin', postJson({ current: cur.value, new: nw.value, confirm: again.value }));
      fields.forEach(f => { f.value = ''; }); rules(); showError('');
      notify('Your PIN was changed. Your other devices are signed out.', 5000);
      liveMe = null; pollLive();
    } catch (error) { showError(error.message); } finally { button.disabled = false; }
  };
}

window.addEventListener('hashchange', () => {
  const view = location.hash.replace('#', '');
  if (view && workspaceRole) showView(view);
});

/* ═══════════════ Interface tools: nav indicator, chat tools, command palette ═══════════════ */
/* One gold marker slides to the page you opened instead of jumping. */
function moveNavInk() {
  const nav = $('role-nav');
  const on = nav && nav.querySelector('.nav.active');
  let ink = nav && nav.querySelector('.nav-ink');
  if (!on) { if (ink) ink.style.opacity = '0'; nav && nav.classList.remove('has-ink'); return; }
  if (!ink) {
    ink = document.createElement('span');
    ink.className = 'nav-ink'; ink.setAttribute('aria-hidden', 'true');
    nav.prepend(ink);
  }
  nav.classList.add('has-ink');
  ink.style.height = `${on.offsetHeight}px`;
  ink.style.transform = `translateY(${on.offsetTop}px)`;
  ink.style.opacity = '1';
}
window.addEventListener('resize', moveNavInk);
if (document.fonts) document.fonts.ready.then(moveNavInk);

/* The messages you sent, so ↑ brings one back (kept in memory only: a shared computer forgets it on reload). */
const sentTexts = [];
let sentAt = -1;
function rememberSent(text) {
  if (sentTexts[sentTexts.length - 1] !== text) sentTexts.push(text);
  if (sentTexts.length > 30) sentTexts.shift();
  sentAt = -1;
}
$('chat-input').addEventListener('keydown', event => {
  if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
  const input = event.target;
  if (input.value && sentAt === -1) return;                        // typing something new: the arrows are left alone
  if (!sentTexts.length) return;
  event.preventDefault();
  sentAt = event.key === 'ArrowUp' ? Math.max(0, (sentAt === -1 ? sentTexts.length : sentAt) - 1)
                                   : (sentAt === -1 ? -1 : sentAt + 1 >= sentTexts.length ? -1 : sentAt + 1);
  input.value = sentAt === -1 ? '' : sentTexts[sentAt];
  updateChatCount();
});

/* Copy an answer, jump back to the latest message, save the conversation as a text file. */
$('messages').addEventListener('click', async event => {
  const button = event.target.closest('.msg-copy');
  if (!button) return;
  const node = button.closest('.message');
  try { await navigator.clipboard.writeText(node.dataset.raw || node.innerText); button.textContent = '✓'; notify('Copied'); }
  catch { notify('Copy failed. Select the text and copy it.'); }
  setTimeout(() => { button.textContent = '⧉'; }, 1400);
});

const jump = document.createElement('button');
jump.type = 'button'; jump.id = 'jump-latest'; jump.className = 'jump-latest'; jump.hidden = true; jump.textContent = '↓ Latest';
$('messages').after(jump);
const syncJump = () => {
  const log = $('messages');
  jump.hidden = log.scrollHeight - log.scrollTop - log.clientHeight < 140;
};
$('messages').addEventListener('scroll', syncJump, { passive: true });
new MutationObserver(syncJump).observe($('messages'), { childList: true });
jump.onclick = () => $('messages').scrollTo({ top: $('messages').scrollHeight, behavior: 'smooth' });

function saveChat() {
  const nodes = [...$('messages').querySelectorAll('.message[data-raw]')];
  if (nodes.length < 2) { notify('There is no conversation to save yet.'); return; }
  const who = signedStudent() ? signedStudent().name : workspaceRole === 'coordinator' ? 'Coordinator' : 'Student';
  const lines = nodes.map(n => `[${n.dataset.time}] ${n.classList.contains('user') ? who : 'Assistant'}: ${n.dataset.raw}`);
  const blob = new Blob([`School ID assistant · ${new Date().toLocaleString()}\n\n${lines.join('\n\n')}\n`], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  clickLink({ href: url, download: `chat-${new Date().toISOString().slice(0, 10)}.txt` });
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
const saveButton = document.createElement('button');
saveButton.type = 'button'; saveButton.id = 'save-chat'; saveButton.className = 'export-btn'; saveButton.title = 'Save this conversation as a text file';
saveButton.textContent = '⬇ Save chat';
saveButton.onclick = saveChat;
$('new-chat').before(saveButton);

/* The command palette (Ctrl or ⌘ + K). Each role gets the commands of its own pages. */
function roleCommands(query) {
  const q = fold(query);
  const out = [];
  (NAV[workspaceRole] || []).forEach(item => out.push({
    group: 'Go to', icon: '↗', label: plainLabel(item.label), keywords: 'open page view', run: () => showView(item.id),
  }));
  if (workspaceRole === 'coordinator') {
    out.push(
      { group: 'Actions', icon: '＋', label: 'New student', hint: 'Ctrl+N', keywords: 'add create record', run: () => { showView('dashboard'); openDialog(); } },
      { group: 'Actions', icon: '⇪', label: 'Import students from CSV', keywords: 'upload bulk', run: () => { showView('dashboard'); openImport(); } },
      { group: 'Actions', icon: '⇩', label: 'Export the student list (CSV)', hint: 'Ctrl+E', keywords: 'download', run: exportCSV },
      { group: 'Actions', icon: '⚑', label: 'Report an incident', keywords: 'lost id no id', run: () => openIncident() },
    );
    if (q.length >= 1) {
      out.push({ group: 'Dashboard', icon: '⌕', label: `Search the Dashboard for “${query.trim()}”`, always: true, rank: 2,
                 run: () => { showView('dashboard'); $('search').value = query.trim(); render(); $('search').focus(); } });
    }
    if (q.length >= 2) {
      records.filter(r => fold(`${r.name} ${r.student_id}`).includes(q)).slice(0, 6).forEach(r => out.push({
        group: 'Students', icon: '👤', label: r.name, hint: `${r.student_id} · ${r.status}`, always: true, run: () => openInChat(r),
      }));
    }
  } else {
    out.push(
      { group: 'Actions', icon: '✎', label: 'Request help from the office', keywords: 'ask office request', run: () => openStudentRequest() },
      { group: 'Actions', icon: '🔑', label: 'Change my PIN', keywords: 'password security', run: () => { showView('settings'); setTimeout(() => $('pin-current').focus(), 350); } },
      { group: 'Actions', icon: '⇩', label: 'Download my data', keywords: 'export privacy', run: () => download('/api/student/export') },
    );
  }
  out.push(
    { group: 'Actions', icon: '＋', label: 'Start a new chat', keywords: 'clear conversation', run: () => { showView('assistant'); newChat(); } },
    { group: 'Actions', icon: '⬇', label: 'Save this chat as a text file', keywords: 'download transcript', run: saveChat },
    { group: 'Actions', icon: '◐', label: 'Switch light / dark theme', keywords: 'appearance mode', run: toggleTheme },
    { group: 'Help', icon: '?', label: 'Keyboard shortcuts', hint: '?', keywords: 'keys help', run: () => ux.help.open() },
  );
  if (workspaceRole === 'coordinator' || signedStudent()) {
    out.push({ group: 'Account', icon: '⎋', label: 'Sign out', keywords: 'log out', run: () => $('switch-role').click() });
  }
  return out;
}
ux.palette.register(roleCommands);
ux.help.register(() => [
  ['Ctrl+K', 'Command palette: pages, actions and students'],
  ['?', 'This list'],
  ['↑ / ↓', 'In the chat box: bring back the messages you sent'],
  ...(workspaceRole === 'coordinator' ? [['Ctrl+N', 'New student'], ['Ctrl+E', 'Export the student list']] : []),
  ['Esc', 'Close a menu or dialog'],
]);

/* ── Start ── */
applyPrefs();
initTheme();
setTickerPaused(!!store.get('tickerPaused'));
let tickerResize = 0;
window.addEventListener('resize', () => { clearTimeout(tickerResize); tickerResize = setTimeout(fitTicker, 200); });
if (document.fonts) document.fonts.ready.then(fitTicker);        // the strip is measured again once the fonts are in
if (workspaceRole && ROLE_META[workspaceRole]) {
  setRole(workspaceRole, { useHash: true });
} else {
  workspaceRole = '';
  renderNav();
  renderSelected();
  updateSuggestions();
  showView('assistant');
  loadChatHistory();
  openRoleDialog();
}
