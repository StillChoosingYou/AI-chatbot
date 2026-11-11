'use strict';
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const view = $('view');
const TABS = [['overview', 'Overview'], ['portal', 'Student portal'], ['access', 'Access & PINs'], ['insights', 'Chatbot insights'], ['moderation', 'Moderation'],
  ['news', 'Announcements'], ['data', 'Data & backup'], ['security', 'Security log'], ['system', 'System'], ['quality', 'Quality']];
const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;
const REFRESH_MS = 30000;
let current = 'overview';
let epoch = 0;                       // a newer tab click makes an older, slower one stop painting

/* ---------- small motion helpers ---------- */
const wait = (ms) => new Promise((r) => setTimeout(r, REDUCED ? 0 : ms));
const frame = () => new Promise((r) => requestAnimationFrame(() => r()));

function countUp(el, to, from = 0) {
  const target = Number(to);
  if (!Number.isFinite(target)) { el.textContent = to; return; }
  if (REDUCED || from === target) { el.textContent = target.toLocaleString(); el.dataset.value = target; return; }
  const start = performance.now(), span = 750;
  const tick = (now) => {
    const t = Math.min(1, (now - start) / span), eased = 1 - Math.pow(1 - t, 3);
    el.textContent = Math.round(from + (target - from) * eased).toLocaleString();
    if (t < 1) requestAnimationFrame(tick); else el.dataset.value = target;
  };
  requestAnimationFrame(tick);
}
function runCounters(root = view) {
  root.querySelectorAll('[data-count]').forEach((el) => countUp(el, el.dataset.count, Number(el.dataset.value || 0)));
}
/* cards, headings and rows rise in one after the other */
function stagger(root = view) {
  let i = 0;
  root.querySelectorAll('h1, .lead, .a-card, .a-warn, .a-news, .a-pin-slip, .a-table tbody tr').forEach((el) => {
    el.style.setProperty('--i', Math.min(i++, 16));
    el.classList.add('rise');
  });
}

/* ---------- top progress bar ---------- */
const progress = document.createElement('div');
progress.className = 'a-progress'; progress.setAttribute('aria-hidden', 'true');
document.body.appendChild(progress);
let inflight = 0;
let silent = false;                  // live refreshes do not flash the progress bar
function progressStart() {
  if (silent) return;
  inflight++; progress.classList.remove('done'); progress.classList.add('run');
}
function progressStop() {
  if (silent) return;
  inflight = Math.max(0, inflight - 1);
  if (inflight) return;
  progress.classList.add('done');
  setTimeout(() => { if (!inflight) progress.classList.remove('run', 'done'); }, 380);
}

/* ---------- toast ---------- */
function toast(text, kind = 'ok') { return ux.toast(text, { kind }); }

/* ---------- confirm dialog (replaces the browser's confirm box) ---------- */
function ask({ title, text, ok = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    const back = document.createElement('div');
    back.className = 'a-modal-back';
    back.innerHTML = `<div class="a-modal" role="alertdialog" aria-modal="true" aria-labelledby="m-title" aria-describedby="m-text">
      <h2 id="m-title"></h2><p id="m-text"></p>
      <div class="a-row a-modal-actions"><button type="button" data-no>Cancel</button><button type="button" data-yes class="${danger ? 'danger' : 'primary'}"></button></div></div>`;
    back.querySelector('#m-title').textContent = title;
    back.querySelector('#m-text').textContent = text;
    back.querySelector('[data-yes]').textContent = ok;
    const previous = document.activeElement;
    const close = (answer) => {
      document.removeEventListener('keydown', onKey, true);
      back.classList.remove('show');
      setTimeout(() => { back.remove(); if (previous && previous.focus) previous.focus(); resolve(answer); }, REDUCED ? 0 : 200);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(false); }
      if (e.key === 'Tab') {                                   // keep focus inside the dialog
        const b = [...back.querySelectorAll('button')];
        const at = b.indexOf(document.activeElement);
        e.preventDefault(); b[(at + (e.shiftKey ? b.length - 1 : 1)) % b.length].focus();
      }
    };
    back.addEventListener('click', (e) => { if (e.target === back) close(false); if (e.target.closest('[data-no]')) close(false); if (e.target.closest('[data-yes]')) close(true); });
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(back);
    void back.offsetWidth; back.classList.add('show');
    back.querySelector(danger ? '[data-no]' : '[data-yes]').focus();
  });
}

/* ---------- a flagged chat message, in full (words stay masked: only the first letter of a rude word is kept) ---------- */
function showMessage(r, onDecide) {
  const back = document.createElement('div');
  back.className = 'a-modal-back';
  const open = r.status === 'new';
  back.innerHTML = `<div class="a-modal a-msg-modal" role="dialog" aria-modal="true" aria-labelledby="mm-title">
    <h2 id="mm-title">Flagged message</h2>
    <dl class="a-msg-meta"><div><dt>From</dt><dd></dd></div><div><dt>When</dt><dd></dd></div><div><dt>Kind</dt><dd></dd></div><div><dt>Result</dt><dd></dd></div></dl>
    <div class="a-msg-body" tabindex="0"></div>
    <p class="a-msg-note">Rude words are masked when the message is saved, so the administrator reads the whole message without keeping the offensive words.</p>
    <div class="a-row a-modal-actions"><button type="button" data-close>Close</button>${open ? '<button type="button" data-d="dismiss">False alarm</button><button type="button" data-d="confirm" class="primary">Confirm</button>' : ''}</div></div>`;
  const dd = back.querySelectorAll('dd');
  dd[0].textContent = r.subject_label; dd[1].textContent = r.timestamp;
  dd[2].textContent = `${r.categories.replace(/,/g, ', ')} · ${['', 'notice', 'strike', 'severe'][r.severity]}${r.source === 'model' ? ' · model' : ''}`;
  dd[3].textContent = `${r.action}${r.strikes ? ` (strike ${r.strikes})` : ''}${r.status !== 'new' ? ` · ${r.status}` : ''}`;
  back.querySelector('.a-msg-body').textContent = r.excerpt;
  const previous = document.activeElement;
  const close = () => {
    document.removeEventListener('keydown', onKey, true);
    back.classList.remove('show');
    setTimeout(() => { back.remove(); if (previous && previous.focus) previous.focus(); }, REDUCED ? 0 : 200);
  };
  const onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } };
  back.addEventListener('click', (e) => {
    if (e.target === back || e.target.closest('[data-close]')) close();
    const d = e.target.closest('[data-d]'); if (d) { close(); onDecide(d.dataset.d); }
  });
  document.addEventListener('keydown', onKey, true);
  document.body.appendChild(back);
  void back.offsetWidth; back.classList.add('show');
  back.querySelector('[data-close]').focus();
}

/* ---------- requests ---------- */
async function api(url, options = {}) {
  progressStart();
  try {
    const response = await fetch(url, options);
    let body = {};
    if ((response.headers.get('content-type') || '').includes('json')) { try { body = await response.json(); } catch { body = {}; } }
    if (response.status === 401) { location.assign('/admin'); throw new Error('Signed out'); }
    if (!response.ok) throw new Error(body.error || 'Something went wrong.');
    return body;
  } finally { progressStop(); }
}
const send = (method, data) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });

/* a button that does something slow: spinner, no double click, errors become a toast */
async function busy(button, job) {
  if (button) { if (button.disabled) return; button.disabled = true; button.classList.add('busy'); }
  try { return await job(); }
  catch (error) { if (error.message !== 'Signed out') toast(error.message, 'err'); }
  finally { if (button && button.isConnected) { button.classList.remove('busy'); button.disabled = false; } }
}

const stat = (label, value, note = '', key = '') => {
  const number = typeof value === 'number';
  return `<div class="a-card a-stat"${key ? ` data-key="${key}"` : ''}><small>${esc(label)}</small><strong${number ? ` data-count="${value}"` : ''}>${number ? 0 : esc(value)}</strong><span>${esc(note)}</span></div>`;
};
const badge = (on, yes, no) => `<span class="a-badge ${on ? 'on' : 'off'}">${esc(on ? yes : no)}</span>`;
async function copy(text, button) {
  try {
    await navigator.clipboard.writeText(text); toast('Copied');
    if (button) { const old = button.textContent; button.textContent = 'Copied ✓'; button.classList.add('flash'); setTimeout(() => { button.textContent = old; button.classList.remove('flash'); }, 1400); }
  } catch { toast('Copy failed. Select the text and copy it.', 'err'); }
}
const clock = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

/* ---------- tabs (built once, so the gold underline can slide) ---------- */
function buildTabs() {
  $('tabs').innerHTML = TABS.map(([id, label], n) =>
    `<button type="button" role="tab" data-tab="${id}" id="tab-${id}" aria-selected="false" title="${esc(label)} (press ${n + 1})">${esc(label)}<i class="tab-dot" hidden></i></button>`).join('') + '<span class="tab-ink" aria-hidden="true"></span>';
}
function markTab() {
  document.querySelectorAll('#tabs [data-tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === current)));
  const on = $('tab-' + current), ink = document.querySelector('.tab-ink');
  if (on && ink) {
    ink.style.width = `${on.offsetWidth}px`; ink.style.transform = `translateX(${on.offsetLeft}px)`;
    on.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: REDUCED ? 'auto' : 'smooth' });
  }
}
window.addEventListener('resize', markTab);
function tabDot(id, on, label = '') {
  const dot = $('tab-' + id)?.querySelector('.tab-dot'); if (!dot) return;
  dot.hidden = !on; dot.textContent = label;
  dot.classList.toggle('num', Boolean(label));
}
function paintBadges(o) {
  const needs = (o.student_login ? o.without_pin : 0) + o.locked;
  tabDot('access', needs > 0, needs > 99 ? '99+' : String(needs));
  const rate = o.answers ? Math.round((o.fallbacks / o.answers) * 100) : 0;
  tabDot('insights', rate >= 30, '!');
  const flagged = (o.moderation_new || 0) + (o.moderation_suspended || 0);
  tabDot('moderation', flagged > 0, flagged > 99 ? '99+' : String(flagged));
}

async function show(tab, { quiet = false } = {}) {
  const mine = ++epoch;
  current = tab; markTab();
  history.replaceState(null, '', `#${tab}`);
  if (!quiet) {
    view.classList.add('leaving');
    await wait(130);
    if (mine !== epoch) return;
    view.innerHTML = `<div class="skel-title"></div><div class="skel-line"></div><div class="a-grid">${'<div class="a-card skel"></div>'.repeat(6)}</div>`;
    view.classList.remove('leaving');
  }
  try {
    const paint = await views[tab]();               // each view fetches, then returns a function that draws
    if (mine !== epoch) return;
    if (quiet) {
      view.classList.add('still'); paint();
      view.querySelectorAll('[data-count]').forEach((el) => { if (!el.dataset.value) { el.dataset.value = el.dataset.count; el.textContent = Number(el.dataset.count).toLocaleString(); } });
      setTimeout(() => view.classList.remove('still'), 80); return; }
    view.classList.add('leaving'); await wait(90);
    if (mine !== epoch) return;
    paint(); stagger(); runCounters();
    view.classList.remove('leaving'); view.scrollTo?.(0, 0); window.scrollTo({ top: 0, behavior: 'auto' });
  } catch (error) {
    if (mine !== epoch || error.message === 'Signed out') return;
    view.classList.remove('leaving');
    view.innerHTML = `<div class="a-warn rise">${esc(error.message)} <button type="button" data-retry>Try again</button></div>`;
  }
}

/* the newest events; one that was not on screen a moment ago lights up once */
const seenEvents = new Set();
function feed(events) {
  const first = seenEvents.size === 0;
  const html = events.length ? `<ul class="a-feed short">${events.map((e) => {
    const key = `${e.time}|${e.action}|${e.detail}`;
    const fresh = !first && !seenEvents.has(key);
    seenEvents.add(key);
    return `<li class="${/fail|locked/.test(e.action) ? 'bad' : ''}${fresh ? ' fresh' : ''}"><time>${esc(e.time)}</time><b>${esc(e.action)}</b><span>${esc(e.role)} · ${esc(e.detail)}</span></li>`;
  }).join('')}</ul>` : '<p>Nothing recorded yet.</p>';
  return html;
}

/* ---------- views: each returns a function that paints once the data is here ---------- */
const views = {
  async overview() {
    const [o, sec] = await Promise.all([api('/api/admin/overview'), api('/api/admin/security')]);
    const events = sec.events.slice(0, 7);
    paintBadges(o);
    const fallbackRate = o.answers ? Math.round((o.fallbacks / o.answers) * 100) : 0;
    const understood = 100 - fallbackRate;
    const attention = () => [o.without_pin && o.student_login ? `<p>⚠️ ${o.without_pin} student(s) have no PIN, so they cannot sign in. <a href="#access" data-go="access">Issue PINs</a></p>` : '',
      o.locked ? `<p>🔒 ${o.locked} student account(s) are locked. <a href="#access" data-go="access">Unlock</a></p>` : '',
      !o.student_login ? '<p>🔓 Student sign-in is off: anyone who types a student ID sees that student\'s status. Set STUDENT_LOGIN=1 after issuing PINs.</p>' : '',
      o.moderation_suspended ? `<p>🚫 ${o.moderation_suspended} chat suspension(s) wait for your review. <a href="#moderation" data-go="moderation">Review</a></p>` : '',
      o.moderation_new ? `<p>🛡️ ${o.moderation_new} flagged chat message(s) to review. <a href="#moderation" data-go="moderation">Open moderation</a></p>` : '',
      fallbackRate >= 30 ? `<p>🧠 ${fallbackRate}% of answers were out of scope. <a href="#insights" data-go="insights">See the missed questions</a></p>` : ''].join('') || '<p class="all-clear">✅ Nothing needs attention.</p>';
    const portalCard = () => `<h2>Student portal</h2>
        <p>${badge(o.portal_open, 'Open', 'Closed')} ${badge(o.student_login, 'Sign-in required (ID + PIN)', 'No sign-in (anyone can look up)')}</p>
        <div class="a-row"><span class="a-link">${esc(o.links.student)}</span><button type="button" data-copy="${esc(o.links.student)}">Copy</button></div>
        <div class="a-row"><button type="button" class="${o.portal_open ? 'danger' : 'primary'}" id="ov-portal-switch">${o.portal_open ? 'Close the student portal' : 'Open the student portal'}</button></div>
        <p>Hand this link to students. The link, QR code and address are in the <a href="#portal" data-go="portal">Student portal</a> tab.</p>`;
    const bindPortal = () => {
      const button = $('ov-portal-switch'); if (!button) return;
      button.onclick = (e) => busy(e.currentTarget, async () => {
        if (o.portal_open && !(await ask({ title: 'Close the student portal?', text: 'Students will see a "closed" page and their chat stops until you open it again.', ok: 'Close it', danger: true }))) return;
        await api('/api/admin/portal', send('POST', { open: !o.portal_open }));
        toast(o.portal_open ? 'Student portal closed' : 'Student portal open'); show('overview');
      });
    };
    return () => {
      const live = $('ov-live');
      if (live && live.dataset.ready) {                            // refresh in place: numbers roll to the new value
        const values = { students: o.students, requests: o.open_requests, incidents: o.open_incidents, answers: o.answers, signedin: o.signed_in_today };
        Object.entries(values).forEach(([key, v]) => {
          const el = view.querySelector(`[data-key="${key}"] strong`); if (el) { el.dataset.count = v; countUp(el, v, Number(el.dataset.value || 0)); }
        });
        $('ov-attention').innerHTML = attention();
        $('ov-portal').innerHTML = portalCard(); bindPortal();
        $('ov-events').innerHTML = feed(events);
        $('ov-ring').style.setProperty('--p', understood); $('ov-ring-n').textContent = `${understood}%`;
        live.querySelector('time').textContent = clock();
        return;
      }
      view.innerHTML = `<div class="a-head"><div><h1>Overview</h1><p class="lead">The whole system at a glance.</p></div>
        <div class="a-live" id="ov-live" data-ready="1"><i class="live-dot"></i> Live · updated <time>${clock()}</time> <button type="button" id="ov-refresh" class="icon-btn" aria-label="Refresh now" title="Refresh now">↻</button></div></div>
        <div class="a-grid">
          ${stat('Students', o.students, `${o.statuses['For release'] || 0} for release · ${o.statuses['Pending'] || 0} pending`, 'students')}
          ${stat('Open requests', o.open_requests, 'waiting for the coordinator', 'requests')}
          ${stat('Open incidents', o.open_incidents, 'No ID, Lost ID, and so on', 'incidents')}
          ${stat('Chatbot answers', o.answers, `${fallbackRate}% could not be placed`, 'answers')}
          ${stat('Feedback', `${o.thumbs_up} 👍 / ${o.thumbs_down} 👎`, 'from students and staff')}
          ${stat('Students with a PIN', `${o.with_pin}/${o.students}`, o.locked ? `${o.locked} locked` : 'none locked')}
          ${stat('Signed in today', o.signed_in_today, 'students using the assistant', 'signedin')}
        </div>
        <div class="a-two">
          <div class="a-card" id="ov-portal">${portalCard()}</div>
          <div class="a-card a-quality"><div class="ring" id="ov-ring" style="--p:${understood}"><b id="ov-ring-n">${understood}%</b></div>
            <div><h2>Answer quality</h2><p>${o.answers ? `The bot placed ${understood}% of ${o.answers.toLocaleString()} question(s) into a topic.` : 'No questions yet.'}</p></div></div>
          <div class="a-card"><h2>Needs your attention</h2><div id="ov-attention">${attention()}</div></div>
          <div class="a-card a-wide"><h2>Live activity</h2><div id="ov-events">${feed(events)}</div>
            <p><a href="#security" data-go="security">Open the whole Security log</a></p></div>
        </div>`;
      $('ov-refresh').onclick = (e) => refreshOverview(e.currentTarget, true);
      bindPortal();
    };
  },

  async portal() {
    const l = await api('/api/admin/links');
    return () => {
      const row = (label, url, which) => `<div class="a-card"><h2>${label}</h2>
        <div class="a-row"><span class="a-link">${esc(url)}</span><button type="button" data-copy="${esc(url)}">Copy</button><a href="${esc(url)}" target="_blank" rel="noopener"><button type="button">Open</button></a></div>
        <div class="a-qr-box"><img class="a-qr" src="/api/admin/qr.png?which=${which}" alt="QR code for ${esc(label)}" width="170" height="170"></div>
        <p class="no-print">Right-click the QR to save it, or print this page to put it on a poster.</p></div>`;
      view.innerHTML = `<h1>Student portal</h1><p class="lead">The link students use. It comes from here.</p>
        <div class="a-two">${row('Student link', l.student, 'student')}${row('Coordinator link', l.coordinator, 'coordinator')}</div>
        <div class="a-card" style="margin-top:14px"><h2>Website address</h2>
          <p>The links and QR codes above are built from this address. Leave it empty to use the automatic one (the live site, even when you open this panel on your own computer).</p>
          <div class="a-row"><input id="public-url" value="${esc(l.public_url)}" placeholder="https://pgpc-ai-chatbot.vercel.app" style="flex:1 1 320px"><button type="button" class="primary" id="save-url">Save address</button></div></div>
        <div class="a-card" style="margin-top:14px"><h2>Portal switch</h2>
          <p>Status: ${badge(l.portal_open, 'Open', 'Closed')} · Student sign-in: ${badge(l.student_login, 'On', 'Off')}</p>
          <p>Closing the portal shows students a "closed" page and stops their chat (maintenance, an exam day). Sticker scan pages and the coordinator workspace keep working.</p>
          <div class="a-row"><button type="button" class="${l.portal_open ? 'danger' : 'primary'}" id="toggle-portal">${l.portal_open ? 'Close the student portal' : 'Open the student portal'}</button></div>
          ${l.separate_hosts ? '' : '<div class="a-warn">Student and coordinator are on the same address. For two separate addresses, add two domains in Vercel and set STUDENT_HOST and COORDINATOR_HOST (see the README).</div>'}</div>`;
      view.querySelectorAll('.a-qr').forEach((img) => { if (img.complete) img.classList.add('in'); else img.addEventListener('load', () => img.classList.add('in'), { once: true }); });
      $('save-url').onclick = (e) => busy(e.currentTarget, async () => {
        await api('/api/admin/public-url', send('PUT', { url: $('public-url').value })); toast('Address saved'); show('portal');
      });
      $('toggle-portal').onclick = (e) => busy(e.currentTarget, async () => {
        if (l.portal_open && !(await ask({ title: 'Close the student portal?', text: 'Students will see a "closed" page and their chat stops until you open it again.', ok: 'Close it', danger: true }))) return;
        await api('/api/admin/portal', send('POST', { open: !l.portal_open })); toast(l.portal_open ? 'Student portal closed' : 'Student portal open'); show('portal');
      });
    };
  },

  async access() {
    const [data, cpin] = await Promise.all([api('/api/admin/pins'), api('/api/admin/coordinator-pin')]);
    const list = data.students;
    const missing = list.filter((s) => !s.has_pin).length, locked = list.filter((s) => s.locked).length;
    const years = [...new Set([1, 2, 3, 4, ...list.map((s) => s.year_level)].filter(Boolean))].sort((a, b) => a - b);   // the usual years and sections are always offered
    const sections = [...new Set(['A', 'B', 'C', 'D', 'E', ...list.map((s) => String(s.section || '').toUpperCase())].filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), undefined, { numeric: true }));
    return () => {
      view.innerHTML = `<h1>Access & PINs</h1><p class="lead">Student sign-in is Student ID + a 6-digit PIN. The PIN the office issued is shown in the Accounts table (kept encrypted). Once a student changes it to their own PIN, it becomes private and shows <i>Private</i>: use <b>Reset PIN</b> to give a new one.</p>
        <div class="a-grid">${stat('Students', list.length)}${stat('Without a PIN', missing)}${stat('Locked now', locked)}${stat('Signed in today', data.today.signed_in)}${stat('Failed tries today', data.today.failed)}${stat('PIN changes today', data.today.pin_changes)}</div>
        <div class="a-card" id="cpin-card"><h2>Coordinator sign-in PIN</h2>
          <p>The coordinator signs in at <b>/coordinator</b> with the username <b>${esc(cpin.username)}</b> and this PIN. ${cpin.source === 'admin' ? `Set from this panel${cpin.changed ? ' on ' + esc(cpin.changed) : ''}.` : cpin.source === 'environment' ? 'Right now it is the password in the server settings. Set a PIN here and it takes over, with no redeploy.' : 'No coordinator password is set yet: set a PIN here.'} A new PIN is shown once and signs the coordinator out. Hand it over in person.</p>
          <div class="a-row"><input id="cpin-input" type="text" inputmode="text" autocomplete="off" maxlength="64" placeholder="New PIN (empty = random 6 digits)" style="flex:1 1 220px;padding:8px 10px">
          <button type="button" class="primary" id="cpin-change">Change coordinator PIN</button>
          ${cpin.source === 'admin' ? '<button type="button" id="cpin-reset" title="Go back to the password in the server settings">Use server-settings password</button>' : ''}</div></div>
        <div class="a-card" style="margin-top:14px"><h2>Issue PINs in bulk</h2>
          <p>Make PINs for everyone who has none (safe), or reset everyone's PIN (every old PIN stops working). The new PINs appear below once: print them as slips or download the CSV, then hand each one over in person.</p>
          <div class="a-row"><button type="button" class="primary" id="pins-missing" ${missing ? '' : 'disabled'}>Issue PINs for ${missing} student(s) without one</button>
          <button type="button" class="danger" id="pins-all">Reset every student's PIN</button></div>
          <div id="pin-result"></div></div>
        <div class="a-card" style="margin-top:14px"><h2>Recent student activity</h2>
          <p>Every sign-in, sign-out, wrong PIN and PIN change is recorded here (also in the Security log).</p>
          ${data.recent.length ? `<ul class="a-feed">${data.recent.map((r) => `<li class="${/fail|locked/.test(r.action) ? 'bad' : ''}"><time>${esc(r.time)}</time><b>${esc(r.action.replace('student ', ''))}</b><span>${esc(r.detail)}</span></li>`).join('')}</ul>` : '<p>No student has signed in yet.</p>'}</div>
        <div class="a-card" id="accounts-card" style="margin-top:14px">
          <div class="a-head"><h2>Accounts</h2><button type="button" class="no-print" id="accounts-print" title="Print the accounts table (or save it as a PDF)">Print</button></div>
          <p class="print-only" id="accounts-stamp"></p>
          <div class="a-row no-print"><input id="pin-search" type="search" placeholder="Search student ID or name   ( / )" style="flex:1 1 240px;padding:8px 10px">
            <select id="pin-year" aria-label="Filter by year level"><option value="">All year levels</option>${years.map((y) => `<option value="${y}">Year ${y}</option>`).join('')}</select>
            <select id="pin-section" aria-label="Filter by section"><option value="">All sections</option>${sections.map((s) => `<option value="${esc(s)}">Section ${esc(s)}</option>`).join('')}</select>
            <div class="a-chips" id="pin-chips" role="group" aria-label="Filter">
              <button type="button" class="chip on" data-f="all">All <b>${list.length}</b></button>
              <button type="button" class="chip" data-f="none">No PIN <b>${missing}</b></button>
              <button type="button" class="chip" data-f="locked">Locked <b>${locked}</b></button></div></div>
          <div class="a-scroll"><table class="a-table"><thead><tr><th>Student ID</th><th>Name</th><th>Year &amp; section</th><th>PIN <button type="button" class="pin-eye" id="pin-eye" aria-pressed="true" title="Hide or show PINs">Hide</button></th><th>Last sign-in</th><th>Sign-ins</th><th>Status</th><th></th></tr></thead><tbody id="pin-rows"></tbody></table></div>
          <p class="a-count" id="pin-count"></p></div>`;
      let filter = 'all', shown = true;
      const pinCell = (s) => s.pin ? `<code class="pin-code" data-pin="${esc(s.pin)}">${shown ? esc(s.pin) : '••••••'}</code>`
        : !s.has_pin ? badge(false, 'Issued', 'None')
        : s.pin_changed ? '<span class="a-badge" title="The student chose their own PIN, so nobody can read it">Private (own PIN)</span>'
        : '<small title="Issued before PINs could be shown. Reset to see it">Reset to view</small>';
      const draw = () => {
        const q = $('pin-search').value.trim().toLowerCase();
        const year = $('pin-year').value, section = $('pin-section').value;
        const rows = list.filter((s) => (!q || s.student_id.toLowerCase().includes(q) || s.name.toLowerCase().includes(q))
          && (!year || String(s.year_level) === year) && (!section || s.section === section)
          && (filter === 'all' || (filter === 'none' && !s.has_pin) || (filter === 'locked' && s.locked)));
        $('pin-rows').innerHTML = rows.slice(0, 300).map((s, i) =>
          `<tr class="rise fast" style="--i:${Math.min(i, 10)}" data-row="${esc(s.student_id)}"><td>${esc(s.student_id)}</td><td>${esc(s.name)}</td><td>${esc(s.year_section || '')}</td><td>${pinCell(s)}</td>
           <td>${s.last_signin ? esc(s.last_signin) : '<small>never</small>'}</td><td>${s.signins || 0}</td>
           <td>${s.locked ? `<span class="a-badge warn">Locked ${s.locked_minutes} min</span>` : s.failures ? `${s.failures} wrong try(s)` : ''}</td>
           <td class="a-actions">${s.locked || s.failures ? `<button type="button" data-unlock="${esc(s.student_id)}">Unlock</button>` : ''}<button type="button" data-newpin="${esc(s.student_id)}" data-name="${esc(s.name)}" data-has="${s.has_pin ? 1 : 0}">${s.has_pin ? 'Reset PIN' : 'Issue PIN'}</button></td></tr>`).join('') || '<tr><td colspan="8">No match.</td></tr>';
        $('pin-count').textContent = rows.length > 300 ? `Showing the first 300 of ${rows.length}. Search to narrow it down.` : `${rows.length} of ${list.length} student(s)`;
      };
      draw(); $('pin-search').oninput = draw; $('pin-year').onchange = draw; $('pin-section').onchange = draw;
      // Print only the Accounts card: hide its siblings (and those of its parents) while the print dialog is open.
      $('accounts-print').onclick = () => {
        const card = $('accounts-card'), hidden = [];
        for (let el = card; el && el !== document.body; el = el.parentElement)
          Array.from(el.parentElement.children).forEach((sib) => { if (sib !== el) { sib.classList.add('print-skip'); hidden.push(sib); } });
        const picked = [$('pin-year').value && `Year ${$('pin-year').value}`, $('pin-section').value && `Section ${$('pin-section').value}`].filter(Boolean);
        $('accounts-stamp').textContent = `Student PIN list${picked.length ? ' (' + picked.join(', ') + ')' : ''}\nPrinted ${new Date().toLocaleString()}`;
        // The list on paper shows the PINs even if they are hidden on screen.
        const codes = Array.from($('pin-rows').querySelectorAll('.pin-code')), before = codes.map((c) => c.textContent);
        codes.forEach((c) => { c.textContent = c.dataset.pin; });
        const done = () => {
          hidden.forEach((s) => s.classList.remove('print-skip'));
          codes.forEach((c, i) => { c.textContent = before[i]; });
          window.removeEventListener('afterprint', done);
        };
        window.addEventListener('afterprint', done);
        window.print();
      };
      $('pin-eye').onclick = () => {
        shown = !shown; $('pin-eye').textContent = shown ? 'Hide' : 'Show'; $('pin-eye').setAttribute('aria-pressed', shown);
        $('pin-rows').querySelectorAll('.pin-code').forEach((c) => { c.textContent = shown ? c.dataset.pin : '••••••'; });
      };
      $('pin-chips').onclick = (e) => {
        const c = e.target.closest('[data-f]'); if (!c) return; filter = c.dataset.f;
        $('pin-chips').querySelectorAll('.chip').forEach((b) => b.classList.toggle('on', b === c)); draw();
      };
      $('pin-rows').onclick = (e) => {
        const np = e.target.closest('[data-newpin]');
        if (np) {
          busy(np, async () => {
            const had = np.dataset.has === '1';
            if (!(await ux.confirm({ title: had ? 'Reset this PIN?' : 'Issue a PIN?', text: `${np.dataset.name} (${np.dataset.newpin})${had ? ': the current PIN stops working and the student is signed out.' : ''}`, ok: had ? 'Reset PIN' : 'Issue PIN', danger: had }))) return;
            const r = await api('/api/admin/pins/issue', send('POST', { student_id: np.dataset.newpin }));
            show('access', { quiet: true });
            await ux.reveal({ title: 'New sign-in PIN', subject: `${r.name} · ${r.student_id}`, secret: r.pin, note: 'Give it to the student in person. It also stays visible in the Accounts table until the student changes it.' });
          });
          return;
        }
        const b = e.target.closest('[data-unlock]'); if (!b) return;
        busy(b, async () => {
          await api('/api/admin/pins/unlock', send('POST', { student_id: b.dataset.unlock }));
          b.closest('tr').classList.add('flash-row'); toast('Unlocked'); await wait(450); show('access', { quiet: true });
        });
      };
      const issue = (button, scope) => busy(button, async () => {
        const result = await api('/api/admin/pins/bulk', send('POST', { scope }));
        const rows = result.issued;
        $('pin-result').innerHTML = `<div class="a-warn">${rows.length} PIN(s) issued. This is the only time they are shown. Print or download now.</div>
          <div class="a-row no-print"><button type="button" class="primary" id="print-pins">Print slips</button><button type="button" id="csv-pins">Download CSV</button></div>
          <div class="a-pin-sheet">${rows.map((r) => `<div class="a-pin-slip">${esc(r.name)}<br>${esc(r.student_id)} · ${esc(r.year_section)}<b>${esc(r.pin)}</b>Sign in at the student link with your Student ID and this PIN.</div>`).join('')}</div>`;
        stagger($('pin-result'));
        $('pin-result').scrollIntoView({ block: 'nearest', behavior: REDUCED ? 'auto' : 'smooth' });
        $('print-pins').onclick = () => window.print();
        $('csv-pins').onclick = () => {
          const csv = ['student_id,name,year_section,pin', ...rows.map((r) => [r.student_id, `"${r.name.replace(/"/g, '""')}"`, r.year_section, r.pin].join(','))].join('\n');
          const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = 'student_pins.csv'; a.click();
        };
        toast(`${rows.length} PIN(s) issued`);
      });
      $('cpin-change').onclick = (e) => busy(e.currentTarget, async () => {
        if (!(await ux.confirm({ title: 'Change the coordinator PIN?', text: 'The old PIN stops working and the coordinator is signed out. You will see the new PIN once.', ok: 'Change PIN', danger: true }))) return;
        const r = await api('/api/admin/coordinator-pin', send('POST', { pin: $('cpin-input').value }));
        show('access', { quiet: true });
        await ux.reveal({ title: 'New coordinator PIN', subject: `Username · ${r.username}`, secret: r.pin, note: 'Give it to the coordinator in person. It cannot be shown again; change it here if it is lost.' });
      });
      if ($('cpin-reset')) $('cpin-reset').onclick = (e) => busy(e.currentTarget, async () => {
        if (!(await ux.confirm({ title: 'Use the server-settings password?', text: 'The PIN set here stops working. The coordinator signs in with COORDINATOR_PASSWORD again.', ok: 'Switch back', danger: true }))) return;
        await api('/api/admin/coordinator-pin', { method: 'DELETE' }); toast('Back to the server-settings password'); show('access', { quiet: true });
      });
      $('pins-missing').onclick = (e) => issue(e.currentTarget, 'missing');
      $('pins-all').onclick = async (e) => {
        const b = e.currentTarget;
        if (await ask({ title: 'Reset every PIN?', text: 'All current student PINs stop working right away. You will see the new ones once.', ok: 'Reset all', danger: true })) issue(b, 'all');
      };
    };
  },

  async insights() {
    const d = await api('/api/admin/insights');
    const max = Math.max(1, ...d.intents.map((i) => i.count)), maxDay = Math.max(1, ...d.per_day.map((i) => i.count));
    const total = d.intents.reduce((n, i) => n + i.count, 0) || 1;
    return () => {
      view.innerHTML = `<h1>Chatbot insights</h1><p class="lead">What students ask, and what the bot could not place. The missed questions are your next dataset.</p>
        <div class="a-two">
          <div class="a-card"><h2>Answers by intent</h2>${d.intents.map((i, n) => `<div class="a-meter"><div class="a-meter-top"><span title="${esc(i.intent)}">${esc(i.label || i.intent)}</span><span>${i.count} · ${Math.round((i.count / total) * 100)}%${i.avg_confidence ? ` · avg ${i.avg_confidence}` : ''}</span></div><div class="a-bar-wrap"><div class="a-bar" style="--w:${(i.count / max) * 100}%;--d:${Math.min(n, 14) * 40}ms"></div></div></div>`).join('') || '<p>No chat yet.</p>'}</div>
          <div class="a-card"><h2>Questions per day (14 days)</h2>
            ${d.per_day.length ? `<div class="a-cols">${d.per_day.map((i, n) => `<div class="a-col" title="${esc(i.day)}: ${i.count}"><span class="a-col-n">${i.count}</span><div class="a-col-bar" style="--h:${(i.count / maxDay) * 100}%;--d:${n * 35}ms"></div><small>${esc(String(i.day).slice(-5))}</small></div>`).join('')}</div>` : '<p>No questions yet.</p>'}
            <h2 style="margin-top:16px">Why 👎</h2>${d.thumbs_down_reasons.map((r) => `<p style="margin:2px 0">${esc(r.reason)}: <b>${r.count}</b></p>`).join('') || '<p>None.</p>'}</div>
        </div>
        <div class="a-card" style="margin-top:14px"><h2>Questions the bot missed</h2>
          <p>Out of scope or under 50% sure. Download them, write the right intent and a good answer, and add them to the dataset.</p>
          <div class="a-row"><a href="/api/admin/missed.csv"><button type="button" class="primary">Download missed_questions.csv</button></a>
            <input id="miss-search" type="search" placeholder="Filter the questions   ( / )" style="flex:1 1 220px;padding:8px 10px"></div>
          <div class="a-scroll"><table class="a-table"><thead><tr><th>Question</th><th>Bot said</th><th>Sure</th><th>Role</th><th>When</th></tr></thead><tbody id="miss-rows"></tbody></table></div>
          <p class="a-count" id="miss-count"></p></div>`;
      const draw = () => {
        const q = $('miss-search').value.trim().toLowerCase();
        const rows = d.missed.filter((m) => !q || `${m.question} ${m.intent} ${m.label || ''} ${m.role}`.toLowerCase().includes(q));
        $('miss-rows').innerHTML = rows.map((m, i) => `<tr class="rise fast" style="--i:${Math.min(i, 10)}"><td>${esc(m.question)}</td><td title="${esc(m.intent)}">${esc(m.label || m.intent)}</td><td>${m.confidence}</td><td>${esc(m.role)}</td><td>${esc(m.time)}</td></tr>`).join('') || `<tr><td colspan="5">${d.missed.length ? 'No match.' : 'Nothing missed yet.'}</td></tr>`;
        $('miss-count').textContent = d.missed.length ? `${rows.length} of ${d.missed.length}` : '';
      };
      draw(); $('miss-search').oninput = draw;
    };
  },

  async moderation() {
    const d = await api(`/api/admin/moderation?status=${moderation.filter}`);
    const SEV = ['', 'notice', 'strike', 'severe'];
    const left = (s) => { const m = Math.ceil(s / 60); return m >= 120 ? `${Math.round(m / 60)} h` : `${m} min`; };
    const standing = (u) => u.state === 'suspended' ? `<span class="a-badge off">Suspended${u.review === 'upheld' ? ' · upheld' : ' · awaiting review'}</span>`
      : u.state === 'restricted' ? `<span class="a-badge off">Paused ${left(u.pause_left)}</span>` : '<span class="a-badge on">Can chat</span>';
    const st = d.stats, top = Math.max(1, ...st.by_day.map((x) => x.count));
    return () => {
      const chips = [['new', 'Needs review'], ['urgent', 'Threats'], ['review', 'Model only (chat not interrupted)'], ['all', 'Everything']];
      view.innerHTML = `<h1>Chat moderation</h1><p class="lead">Messages the chat flagged for offensive words, insults, threats or harassment. Rude words are masked. A first strike is a warning, then the chat pauses (5 min, 30 min, 24 h) and the fifth strike suspends the person until you review it. Dismiss a false alarm and the strike is given back.</p>
        <div class="a-grid">${stat('Need review', st.needs_attention, 'strikes not yet reviewed')}${stat('Threats', st.urgent, 'severe, not yet reviewed')}${stat('Paused now', st.restricted_now, 'temporary pause')}${stat('Suspended', st.suspended_pending + st.suspended_upheld, `${st.suspended_pending} awaiting review`)}${stat('Last 7 days', st.last_7_days, 'flagged messages')}</div>
        <div class="a-two" style="margin-top:14px">
          <div class="a-card"><h2>Flagged per day (14 days)</h2><div class="a-cols">${st.by_day.map((i, n) => `<div class="a-col" title="${esc(i.day)}: ${i.count}"><span class="a-col-n">${i.count}</span><div class="a-col-bar" style="--h:${(i.count / top) * 100}%;--d:${n * 35}ms"></div><small>${esc(i.day.slice(-5))}</small></div>`).join('')}</div></div>
          <div class="a-card"><h2>By kind</h2>${Object.entries(st.by_category).map(([k, v]) => `<p style="margin:2px 0">${esc(k)}: <b>${v}</b></p>`).join('') || '<p>Nothing flagged yet.</p>'}
            <p style="margin-top:12px"><small>${d.model.loaded ? 'Rules and trained model are both on.' : `Rules only: ${esc(d.model.reason || 'model not loaded')}`}</small></p></div></div>
        <div class="a-card" style="margin-top:14px"><h2>Reports</h2><div class="a-chips" id="mod-chips">${chips.map(([id, label]) => `<button type="button" class="chip ${moderation.filter === id ? 'on' : ''}" data-f="${id}">${label}</button>`).join('')}</div>
          <div class="a-scroll"><table class="a-table"><thead><tr><th>Message</th><th>Who</th><th>Kind</th><th>Result</th><th>When</th><th></th></tr></thead><tbody>${d.reports.map((r, i) => `<tr class="rise fast ${r.severity >= 3 ? 'bad' : ''}" style="--i:${Math.min(i, 10)}"><td><button type="button" class="a-msg-btn" data-m="${i}" title="Read the full message" aria-label="Read the full message from ${esc(r.subject_label)}">💬</button></td><td>${esc(r.subject_label)}</td><td>${esc(r.categories)} · ${SEV[r.severity]}${r.source === 'model' ? ' · model' : ''}</td><td>${esc(r.action)}${r.strikes ? ` (strike ${r.strikes})` : ''}${r.status !== 'new' ? ` · <b>${esc(r.status)}</b>` : ''}</td><td>${esc(r.timestamp)}</td><td>${r.status === 'new' ? `<button type="button" data-r="${r.id}" data-a="confirm">Confirm</button> <button type="button" data-r="${r.id}" data-a="dismiss">False alarm</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="6">Nothing here. ✅</td></tr>'}</tbody></table></div></div>
        <div class="a-card" style="margin-top:14px"><h2>People with a record</h2>
          <div class="a-scroll"><table class="a-table"><thead><tr><th>Person</th><th>Standing</th><th>Strikes (30 days)</th><th>Reports</th><th>Last</th><th></th></tr></thead><tbody>${d.users.map((u) => `<tr><td>${esc(u.subject_label)}</td><td>${standing(u)}</td><td>${u.strikes}</td><td>${u.total}</td><td>${esc(u.last || '')}</td><td>${u.subject === 'coordinator' ? '' : `${u.state !== 'ok' ? `<button type="button" data-u="${esc(u.subject)}" data-a="lift">Lift</button> ` : ''}${u.state === 'suspended' && u.review !== 'upheld' ? `<button type="button" data-u="${esc(u.subject)}" data-a="uphold">Uphold</button> ` : ''}${u.state !== 'suspended' ? `<button type="button" data-u="${esc(u.subject)}" data-a="suspend">Suspend</button> ` : ''}<button type="button" data-u="${esc(u.subject)}" data-a="reset">Clear strikes</button>`}</td></tr>`).join('') || '<tr><td colspan="6">Nobody has been flagged.</td></tr>'}</tbody></table></div></div>`;
      $('mod-chips').onclick = (e) => { const b = e.target.closest('[data-f]'); if (b) { moderation.filter = b.dataset.f; show('moderation'); } };
      view.querySelectorAll('button[data-m]').forEach((b) => { b.onclick = () => showMessage(d.reports[+b.dataset.m], (action) => busy(null, async () => {
        const r = d.reports[+b.dataset.m];
        await api(`/api/admin/moderation/report/${r.id}`, send('POST', { action }));
        toast(action === 'dismiss' ? 'Dismissed: the strike is given back' : 'Confirmed'); await show('moderation', { quiet: true }); })); });
      view.querySelectorAll('button[data-r]').forEach((b) => { b.onclick = () => busy(b, async () => {
        await api(`/api/admin/moderation/report/${b.dataset.r}`, send('POST', { action: b.dataset.a }));
        toast(b.dataset.a === 'dismiss' ? 'Dismissed: the strike is given back' : 'Confirmed'); await show('moderation', { quiet: true }); }); });
      view.querySelectorAll('button[data-u]').forEach((b) => { b.onclick = () => busy(b, async () => {
        const danger = b.dataset.a === 'suspend';
        if (danger && !(await ask({ title: 'Suspend this person?', text: 'They cannot use the chat until you lift the suspension.', ok: 'Suspend', danger: true }))) return;
        await api('/api/admin/moderation/user', send('POST', { subject: b.dataset.u, action: b.dataset.a }));
        toast('Saved'); await show('moderation', { quiet: true }); }); });
    };
  },

  async quality() {
    const d = await api(`/api/admin/quality${quality.deep ? '?deep=1' : ''}`); quality.deep = false;
    return () => {
      const tone = (p) => (p === null ? 'na' : p >= 0.9 ? 'ok' : p >= 0.5 ? 'part' : 'no');
      const mark = (p) => (p === null ? '–' : p >= 0.9 ? '✓' : p >= 0.5 ? '◐' : '✗');
      view.innerHTML = `<h1>Quality scorecard</h1><p class="lead">The system measured against the eight quality characteristics of ${esc(d.standard)}. Every check says how it is known: <b>measured</b> now, <b>inspected</b> in the files, <b>declared</b> by design, or a <b>gap</b> that still scores zero.</p>
        <div class="a-card q-top"><div class="q-ring" style="--p:${d.overall || 0}"><b>${d.overall ?? '–'}</b><small>${esc(d.grade)}</small></div>
          <div class="q-sum">${d.characteristics.map((c) => `<a href="#q-${c.id}" class="q-row" data-q="${c.id}"><span>${esc(c.name)}</span><span class="q-meter"><i style="width:${c.score || 0}%"></i></span><b>${c.score ?? '–'}</b></a>`).join('')}</div>
          <div class="q-actions"><button type="button" class="primary" id="q-deep">Measure again (deeper)</button><a class="q-link" href="/admin/quality-report" target="_blank" rel="noopener">Printable report (PDF) ↗</a>
            <small>Model ${d.benchmark.inference_p95} ms · database ${d.benchmark.db_p95} ms (95th percentile)<br>Server up ${Math.round(d.live.uptime_seconds / 60)} min · ${d.live.requests} requests · ${d.live.errors} errors</small></div></div>
        <div class="q-grid">${d.characteristics.map((c) => `<div class="a-card q-card" id="q-${c.id}"><div class="q-head"><h2>${esc(c.name)}</h2><b class="q-score ${c.score >= 90 ? 'ok' : c.score >= 60 ? 'part' : 'no'}">${c.score ?? '–'}</b></div><p class="q-ask">${esc(c.question)}</p>
          <ul class="q-checks">${c.checks.map((k) => `<li class="${tone(k.points)}"><span class="q-mark">${mark(k.points)}</span><span><b>${esc(k.name)}</b><br><small>${esc(k.detail)}</small></span><em class="a-badge">${esc(k.kind)}</em></li>`).join('')}</ul></div>`).join('')}</div>`;
      $('q-deep').onclick = (e) => busy(e.currentTarget, async () => { quality.deep = true; await show('quality'); toast('Measured again'); });
      view.querySelectorAll('[data-q]').forEach((a) => { a.onclick = (e) => { e.preventDefault(); $('q-' + a.dataset.q).scrollIntoView({ behavior: 'smooth', block: 'start' }); }; });
    };
  },

  async news() {
    const d = await api('/api/admin/news');
    let items = d.news.map((n) => ({ ...n }));
    return () => {
      const draw = (fresh = -1, removed = -1) => {
        view.innerHTML = `<h1>Announcements</h1><p class="lead">The PGPC NEWS ticker on both workspaces. ${d.from_database ? 'Saved here.' : 'Showing data/pgpc_news.json until you save.'} Up to ${d.max} of your own.</p>
          <div class="a-card a-auto"><h2>Automatic monthly announcements</h2>
            <p>The ticker adds this month's events and a look at next month's (for example Halloween now, Christmas next), by itself, every month. They come after your own announcements. ${d.auto_forced_off ? '<b>Switched off for this server (AUTO_NEWS=0).</b>' : ''}</p>
            <label class="set-check"><input type="checkbox" id="news-auto" ${d.auto_on ? 'checked' : ''} ${d.auto_forced_off ? 'disabled' : ''}> Show them on the ticker</label>
            <ul class="a-feed a-auto-list ${d.auto_on ? '' : 'off'}">${d.auto.map((a) => `<li><b>${esc(a.icon)}</b><span>${esc(a.text)}</span><em class="a-badge">auto</em></li>`).join('') || '<li>Nothing for this month.</li>'}</ul></div>
          <div class="a-card" style="margin-top:14px"><h2>Your announcements</h2><div id="news-list">${items.map((n, i) => `<div class="a-news ${i === fresh ? 'pop' : ''}"><input data-i="${i}" data-k="icon" value="${esc(n.icon)}" maxlength="4" aria-label="Icon"><input data-i="${i}" data-k="text" value="${esc(n.text)}" maxlength="200" aria-label="Announcement"><button type="button" data-del="${i}">Remove</button></div>`).join('') || '<p>No announcements: the ticker is hidden.</p>'}</div>
          <div class="a-row"><button type="button" id="news-add" ${items.length >= d.max ? 'disabled' : ''}>+ Add announcement</button><button type="button" class="primary" id="news-save">Save to the ticker</button><button type="button" id="news-reset">Back to the file</button></div></div>`;
        if (fresh >= 0) document.querySelector(`[data-i="${fresh}"][data-k="text"]`)?.focus();
        $('news-list').oninput = (e) => { const i = e.target.dataset.i; if (i !== undefined) items[i][e.target.dataset.k] = e.target.value; };
        $('news-list').onclick = async (e) => {
          const b = e.target.closest('[data-del]'); if (!b) return;
          const rowEl = b.closest('.a-news'); rowEl.classList.add('out'); await wait(220);
          items.splice(+b.dataset.del, 1); draw();
        };
        $('news-auto').onchange = (e) => busy(e.currentTarget, async () => {
          const r = await api('/api/admin/news/auto', send('PUT', { on: e.currentTarget.checked }));
          d.auto_on = r.auto_on; d.auto = r.auto; toast(r.auto_on ? 'Automatic announcements on' : 'Automatic announcements off'); draw();
        });
        $('news-add').onclick = () => { items.push({ icon: '📣', text: '' }); draw(items.length - 1); };
        $('news-save').onclick = (e) => busy(e.currentTarget, async () => { const r = await api('/api/admin/news', send('PUT', { news: items.filter((n) => n.text.trim()) })); items = r.news; toast('Ticker saved'); draw(); });
        $('news-reset').onclick = (e) => busy(e.currentTarget, async () => {
          if (!(await ask({ title: 'Use the file again?', text: 'The announcements you saved here are dropped and data/pgpc_news.json is used again.', ok: 'Drop them', danger: true }))) return;
          await api('/api/admin/news', { method: 'DELETE' }); toast('Back to the file'); show('news');
        });
      };
      draw(); stagger();
    };
  },

  async data() {
    return () => {
      view.innerHTML = `<h1>Data & backup</h1><p class="lead">Take your data out, and keep chat history from growing forever.</p>
        <div class="a-two">
          <div class="a-card"><h2>Full backup</h2><p>Every school table as one JSON file: students, requests, incidents, activity and feedback. PIN hashes are never included.</p>
            <div class="a-row"><a href="/api/admin/backup"><button type="button" class="primary">Download backup</button></a><a href="/api/admin/backup?chat=1"><button type="button">Backup with chat history</button></a></div></div>
          <div class="a-card"><h2>Delete old chat history</h2><p>Chat messages older than a number of days (the Data Privacy Act asks for a stated retention period). Count first, then delete.</p>
            <div class="a-row"><label>Older than <input id="purge-days" type="number" min="7" max="3650" value="90" style="width:80px;padding:7px"> days</label><button type="button" id="purge-count">Count</button><button type="button" class="danger" id="purge-go" disabled>Delete them</button></div><p id="purge-note" aria-live="polite"></p></div>
        </div>`;
      const days = () => parseInt($('purge-days').value, 10);
      const note = (text) => { const n = $('purge-note'); n.textContent = text; n.classList.remove('pop'); void n.offsetWidth; n.classList.add('pop'); };
      $('purge-days').oninput = () => { $('purge-go').disabled = true; };
      $('purge-count').onclick = (e) => busy(e.currentTarget, async () => {
        const r = await api('/api/admin/purge', send('POST', { days: days(), dry_run: true }));
        note(`${r.count} message(s) are older than ${r.cutoff}.`); $('purge-go').disabled = r.count === 0;
      });
      $('purge-go').onclick = (e) => busy(e.currentTarget, async () => {
        if (!(await ask({ title: 'Delete these messages?', text: 'The chat messages are deleted for good and this cannot be undone. Download a backup first if you are unsure.', ok: 'Delete for good', danger: true }))) return;
        const r = await api('/api/admin/purge', send('POST', { days: days(), dry_run: false }));
        note(`${r.count} message(s) deleted.`); toast('Chat history purged'); $('purge-go').disabled = true;
      });
    };
  },

  async security() {
    const d = await api('/api/admin/security');
    const roles = [...new Set(d.events.map((e) => e.role))];
    const bad = (e) => /fail|lock|denied|wrong|invalid|blocked/i.test(`${e.action} ${e.detail}`);
    return () => {
      view.innerHTML = `<h1>Security log</h1><p class="lead">Sign-ins, failed sign-ins, PIN changes and admin actions. ${d.failed_today ? `<b>${d.failed_today} failed sign-in(s) today.</b>` : 'No failed sign-ins today.'}</p>
        <div class="a-card"><div class="a-row"><input id="sec-search" type="search" placeholder="Search the log   ( / )" style="flex:1 1 220px;padding:8px 10px">
          <select id="sec-role" aria-label="Role"><option value="">All roles</option>${roles.map((r) => `<option>${esc(r)}</option>`).join('')}</select>
          <label class="a-check"><input type="checkbox" id="sec-bad"> Problems only</label>
          <button type="button" id="sec-csv">Download CSV</button></div>
          <div class="a-scroll"><table class="a-table"><thead><tr><th>When</th><th>Who</th><th>What</th><th>Detail</th></tr></thead><tbody id="sec-rows"></tbody></table></div>
          <p class="a-count" id="sec-count"></p></div>`;
      const draw = () => {
        const q = $('sec-search').value.trim().toLowerCase(), role = $('sec-role').value, only = $('sec-bad').checked;
        const rows = d.events.filter((e) => (!role || e.role === role) && (!only || bad(e)) && (!q || `${e.role} ${e.action} ${e.detail} ${e.time}`.toLowerCase().includes(q)));
        $('sec-rows').innerHTML = rows.slice(0, 400).map((e, i) => `<tr class="rise fast ${bad(e) ? 'bad' : ''}" style="--i:${Math.min(i, 10)}"><td>${esc(e.time)}</td><td>${esc(e.role)}</td><td>${esc(e.action)}</td><td>${esc(e.detail)}</td></tr>`).join('') || `<tr><td colspan="4">${d.events.length ? 'No match.' : 'No events yet.'}</td></tr>`;
        $('sec-count').textContent = d.events.length ? `${Math.min(rows.length, 400)} of ${d.events.length} event(s)` : '';
      };
      draw(); ['input', 'change'].forEach((ev) => view.querySelector('.a-card').addEventListener(ev, draw));
      $('sec-csv').onclick = () => {
        const cell = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
        const csv = ['when,who,what,detail', ...d.events.map((e) => [e.time, e.role, e.action, e.detail].map(cell).join(','))].join('\n');
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
        a.download = 'security_log.csv'; a.click(); toast('Security log downloaded');
      };
    };
  },

  async system() {
    const s = await api('/api/admin/system');
    const two = await api('/api/admin/totp');
    const kv = (o) => Object.entries(o).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td>${typeof v === 'boolean' ? badge(v, 'yes', 'no') : esc(v)}</td></tr>`).join('');
    return () => {
      view.innerHTML = `<div class="a-head"><div><h1>System</h1><p class="lead">Checks and configuration. Passwords and keys are never shown.</p></div>
        <button type="button" id="sys-again">Run the checks again</button></div>
        <div class="a-two">
          <div class="a-card"><h2>Database <span class="health ${s.database.ok ? 'good' : 'bad'}" title="${s.database.ok ? 'Healthy' : 'Not answering'}"></span></h2><table class="a-table"><tbody>${kv({ ok: s.database.ok, version: s.database.version, latency_ms: s.database.latency_ms })}</tbody></table></div>
          <div class="a-card"><h2>Model</h2><table class="a-table"><tbody>${kv({ confidence_threshold: s.model.threshold, intents: s.model.intents.length })}</tbody></table><p>${s.model.intents.map(esc).join(', ')}</p></div>
          <div class="a-card"><h2>Versions</h2><table class="a-table"><tbody>${kv({ ...s.versions, build: s.build })}</tbody></table></div>
          <div class="a-card"><h2>Configuration</h2><table class="a-table"><tbody>${kv(s.config)}</tbody></table></div>
        </div>
        <div class="a-card" id="two-factor" style="margin-top:14px"></div>`;
      twoFactor(two);
      $('sys-again').onclick = (e) => busy(e.currentTarget, async () => { await show('system'); toast('Checks done'); });
    };
  },
};

/* ---------- two-factor sign-in: an authenticator app's 6-digit code, on top of the password and the picture ---------- */
function recoveryBody(codes) {
  return `<p class="subtle">Each code works once. Keep them somewhere safe, not on the same phone.</p><div class="recovery-codes">${codes.map((c) => `<code>${esc(c)}</code>`).join('')}</div>`;
}
async function showRecovery(codes, title) {
  await ux.dialog({ title, ok: 'I saved them', cancel: '', body: recoveryBody(codes) });
}
async function askCode(title, text, ok, danger = false) {
  let code = '';
  const yes = await ux.dialog({ title, text, ok, danger, body: '<label class="ux-field">Code from your app<input id="two-code" inputmode="numeric" autocomplete="one-time-code" maxlength="12" placeholder="123456"></label>',
    read: (panel) => { code = panel.querySelector('#two-code').value.trim(); if (!code) { panel.querySelector('#two-code').focus(); return false; } return true; } });
  return yes ? code : null;
}
function twoFactor(info) {
  const box = $('two-factor');
  const paint = () => {
    if (info.switched_off_by_environment) {
      box.innerHTML = `<h2>Two-factor sign-in</h2><p>Switched off by the server setting <code>ADMIN_TOTP=0</code>. Remove that setting to use it again.</p>`;
      return;
    }
    if (info.enabled) {
      box.innerHTML = `<h2>Two-factor sign-in ${badge(true, 'On', 'Off')}</h2>
        <p>Signing in to the admin panel asks for the password, the picture and the 6-digit code from your authenticator app. Recovery codes left: <strong>${info.recovery_left}</strong>${info.recovery_left <= 2 ? ' · make new ones soon' : ''}.</p>
        <div class="a-row"><button type="button" id="two-new">New recovery codes</button><button type="button" class="danger" id="two-off">Turn off</button></div>`;
      $('two-new').onclick = (e) => busy(e.currentTarget, async () => {
        const code = await askCode('New recovery codes?', 'The old ones stop working. Type a current code from your app.', 'Make new codes'); if (!code) return;
        const r = await api('/api/admin/totp/recovery', send('POST', { code }));
        info.recovery_left = r.recovery.length; paint(); await showRecovery(r.recovery, 'Your new recovery codes');
      });
      $('two-off').onclick = (e) => busy(e.currentTarget, async () => {
        const code = await askCode('Turn off two-factor?', 'The admin panel will ask only for the password and the picture. Type a current code to confirm.', 'Turn off', true); if (!code) return;
        await api('/api/admin/totp/disable', send('POST', { code }));
        info.enabled = false; toast('Two-factor sign-in is off'); paint();
      });
    } else {
      box.innerHTML = `<h2>Two-factor sign-in ${badge(false, 'On', 'Off')}</h2>
        <p>Add a second lock: a 6-digit code from an authenticator app on your phone (Google Authenticator, Microsoft Authenticator, Authy). A stolen or guessed password is then not enough.</p>
        <div class="a-row"><button type="button" class="primary" id="two-on">Set it up</button></div>`;
      $('two-on').onclick = (e) => busy(e.currentTarget, async () => {
        const r = await api('/api/admin/totp/begin', send('POST', {}));
        box.innerHTML = `<h2>Set up two-factor sign-in</h2>
          <ol class="two-steps"><li>In your authenticator app choose <b>Add account</b> and scan this picture.</li><li>Cannot scan? Type this key instead: <code class="two-key">${esc(r.secret)}</code></li><li>Type the 6-digit code the app shows now.</li></ol>
          <div class="a-qr-box"><img class="a-qr" src="/api/admin/totp/qr.png?t=${Date.now()}" alt="QR code for your authenticator app" width="170" height="170"></div>
          <div class="a-row"><input id="two-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456" aria-label="6-digit code" style="max-width:150px"><button type="button" class="primary" id="two-ok">Turn it on</button><button type="button" id="two-cancel">Cancel</button></div>`;
        const img = box.querySelector('.a-qr'); if (img.complete) img.classList.add('in'); else img.addEventListener('load', () => img.classList.add('in'), { once: true });
        $('two-cancel').onclick = () => paint();
        $('two-ok').onclick = (ev) => busy(ev.currentTarget, async () => {
          const res = await api('/api/admin/totp/enable', send('POST', { code: $('two-code').value }));
          info.enabled = true; info.recovery_left = res.recovery.length; toast('Two-factor sign-in is on'); paint();
          await showRecovery(res.recovery, 'Save your recovery codes');
        });
        $('two-code').focus();
      });
    }
  };
  paint();
}

/* ---------- live: one change counter, asked every 4 seconds; the page reloads what it shows when it moves ---------- */
const LIVE_MS = 4000;
let rev = null;
const moderation = { filter: 'new' };
const quality = { deep: false };                   // set by "Measure again": one deeper measurement
const liveSafe = {                                   // a tab whose text boxes hold something is left alone
  overview: () => true,
  security: () => !$('sec-search')?.value && !$('sec-role')?.value && !$('sec-bad')?.checked,
  insights: () => !$('miss-search')?.value,
  moderation: () => true,
  access: () => !$('pin-result')?.innerHTML && !$('pin-search')?.value && document.querySelector('#pin-chips .chip.on')?.dataset.f === 'all',
  system: () => true,
};
async function refreshOverview(button, manual = false) {
  button && button.classList.add('spin');
  try {
    if (current === 'overview') await show('overview', { quiet: true });
    else paintBadges(await api('/api/admin/overview'));
    if (manual) toast('Refreshed');
  } catch (error) { if (manual && error.message !== 'Signed out') toast(error.message, 'err'); }
  finally { if (button) setTimeout(() => button.classList.remove('spin'), 500); }
}
async function liveRefresh() {
  silent = true;
  try {
    if (current === 'overview') await show('overview', { quiet: true });
    else {
      paintBadges(await api('/api/admin/overview'));
      if (liveSafe[current] && liveSafe[current]() && !document.activeElement?.matches?.('input, textarea, select')) await show(current, { quiet: true });
    }
  } catch { /* the next change tries again */ }
  finally { silent = false; }
}
function liveState(on) {
  document.querySelectorAll('.live-dot').forEach((d) => d.classList.toggle('off', !on));
}
async function pollLive() {
  if (document.hidden || document.querySelector('.a-modal-back')) return;
  try {
    const response = await fetch('/api/live?role=admin', { cache: 'no-store' });
    if (response.status === 401) { location.assign('/admin'); return; }
    const body = await response.json();
    liveState(true);
    if (rev !== null && body.rev !== rev) { rev = body.rev; await liveRefresh(); } else rev = body.rev;
  } catch { liveState(false); }
}
setInterval(pollLive, LIVE_MS);
document.addEventListener('visibilitychange', () => { if (!document.hidden) pollLive(); });

/* ---------- events ---------- */
document.addEventListener('click', (e) => {
  const tab = e.target.closest('[data-tab]'); if (tab && tab.dataset.tab !== current) show(tab.dataset.tab);
  const go = e.target.closest('[data-go]'); if (go) { e.preventDefault(); show(go.dataset.go); }
  const cp = e.target.closest('[data-copy]'); if (cp) copy(cp.dataset.copy, cp);
  if (e.target.closest('[data-retry]')) show(current);
  const b = e.target.closest('.admin-main button, .admin-actions button, .admin-tabs button');   // press ripple
  if (b && !REDUCED && !b.disabled) {
    const r = b.getBoundingClientRect(), dot = document.createElement('span');
    dot.className = 'ripple'; dot.style.left = `${e.clientX - r.left}px`; dot.style.top = `${e.clientY - r.top}px`;
    b.appendChild(dot); setTimeout(() => dot.remove(), 600);
  }
});
document.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey || document.querySelector('.a-modal-back')) return;
  const typing = /^(input|textarea|select)$/i.test(e.target.tagName);
  if (e.key === '/' && !typing) {
    const box = view.querySelector('input[type="search"]'); if (box) { e.preventDefault(); box.focus(); }
  } else if (!typing && /^[1-9]$/.test(e.key)) show(TABS[+e.key - 1][0]);
  else if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
    if (e.target.closest && e.target.closest('#tabs')) {
      const at = TABS.findIndex(([id]) => id === current), next = TABS[(at + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length][0];
      show(next); $('tab-' + next).focus();
    }
  }
});
$('theme').onclick = (e) => {
  const dark = document.documentElement.dataset.theme !== 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  e.currentTarget.classList.remove('turn'); void e.currentTarget.offsetWidth; e.currentTarget.classList.add('turn');
  try { localStorage.setItem('theme', dark ? 'dark' : 'light'); } catch { /* not kept */ }
};
try { const saved = localStorage.getItem('theme'); document.documentElement.dataset.theme = saved || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); } catch { /* default */ }
ux.palette.register((q) => [
  ...TABS.map(([id, label], n) => ({ group: 'Go to', icon: '↗', label, hint: String(n + 1), keywords: 'open tab page', run: () => show(id) })),
  { group: 'Actions', icon: '↻', label: 'Refresh now', keywords: 'reload update', run: () => refreshOverview(null, true) },
  { group: 'Actions', icon: '⇩', label: 'Download a full backup', keywords: 'export json', run: () => { location.href = '/api/admin/backup'; } },
  { group: 'Actions', icon: '⇩', label: 'Download the missed questions (CSV)', keywords: 'dataset chatbot', run: () => { location.href = '/api/admin/missed.csv'; } },
  { group: 'Actions', icon: '◐', label: 'Switch light / dark theme', keywords: 'appearance mode', run: () => $('theme').click() },
  { group: 'Actions', icon: '↗', label: 'Open the coordinator workspace', keywords: 'switch role', run: () => document.querySelector('form[action="/admin/switch"] button').click() },
  { group: 'Help', icon: '?', label: 'Keyboard shortcuts', hint: '?', keywords: 'keys help', run: () => ux.help.open() },
  { group: 'Account', icon: '⎋', label: 'Sign out', keywords: 'log out', run: () => document.querySelector('form[action="/admin/logout"] button').click() },
]);
ux.help.register(() => [
  ['Ctrl+K', 'Command palette: tabs and actions'], ['?', 'This list'], ['1 – 9', 'Open a tab'], ['/', 'Focus the search box of the tab'],
  ['← / →', 'Between tabs (while a tab is focused)'], ['Esc', 'Close a dialog'],
]);

/* ---------- tutorial for a new admin (tour.js) ---------- */
const tourSteps = () => [
  { title: 'Welcome to the Admin panel 👋', text: 'A one-minute tour of the sections you will use most. A hand will show you where to click. Press Esc any time to stop.' },
  { target: '#tabs', title: 'Your sections', text: 'Eight tabs run the whole system. Press 1 to 8, or use the ← and → keys while a tab is focused.' },
  { target: '#tab-access', mode: 'click', title: 'Try it: open Access & PINs', text: 'Students sign in with their Student ID and a 6-digit PIN. You issue the PINs here.' },
  { wait: '#pin-chips', target: '#pins-missing', title: 'Issue PINs in one go', text: 'Make a PIN for everyone who has none. The new PINs show once: print the slips or download the CSV.' },
  { target: '#pin-eye', title: 'See the issued PIN', text: 'The PIN the office issued is shown in the Accounts table. Hide or show them here. A PIN a student chose themselves stays private.' },
  { target: '#tab-news', mode: 'click', title: 'Try it: open Announcements', text: 'The PGPC NEWS ticker that every student and coordinator sees.' },
  { wait: '#news-auto', target: '#news-auto', title: 'Automatic monthly announcements', text: 'The ticker adds this month’s events and a look at next month’s by itself. Switch it off here if you prefer.' },
  { target: '#news-add', title: 'Add your own', text: 'Write an announcement with an emoji icon, then press “Save to the ticker”. Yours always come first.' },
  { target: '#tab-security', mode: 'click', title: 'Try it: open the Security log', text: 'Every sign-in, failed try, PIN change and admin action is recorded.' },
  { wait: '#view h1', target: '#view h1', title: 'Your audit trail', text: 'Check this after anything unusual. You can download it as a CSV.' },
  { target: '.admin-actions form[action="/admin/switch"] button', title: 'Work as the coordinator', text: 'Opens the coordinator sign-in. Enter the coordinator username and PIN, and you can return here from the workspace.' },
  { target: '#tour-btn', title: 'You are ready! 🎉', text: 'Replay this tutorial any time with this button. Ctrl+K opens a quick command search and ? lists the shortcuts.' },
];
$('tour-btn').onclick = () => tour.start(tourSteps(), { key: 'admin' });
ux.palette.register(() => [{ group: 'Help', icon: '🎓', label: 'Take the tutorial', keywords: 'tour guide help new', run: () => $('tour-btn').click() }]);
buildTabs();
show(TABS.some(([id]) => id === location.hash.slice(1)) ? location.hash.slice(1) : 'overview');
if (!location.hash || location.hash === '#overview') { /* badges come with the overview itself */ } else api('/api/admin/overview').then(paintBadges).catch(() => {});
tour.auto('admin', tourSteps());
