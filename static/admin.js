'use strict';
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const view = $('view');
const TABS = [['overview', 'Overview'], ['portal', 'Student portal'], ['access', 'Access & PINs'], ['insights', 'Chatbot insights'],
  ['news', 'Announcements'], ['data', 'Data & backup'], ['security', 'Security log'], ['system', 'System']];
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
function toast(text, kind = 'ok') {
  const t = $('toast');
  t.className = `toast ${kind}`;
  t.innerHTML = `<span class="toast-ico">${kind === 'err' ? '!' : '✓'}</span><span></span>`;
  t.lastChild.textContent = text;
  t.hidden = false;
  void t.offsetWidth;                // restart the entrance
  t.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    t.classList.remove('show');
    toast.hide = setTimeout(() => { t.hidden = true; }, 260);
  }, kind === 'err' ? 5000 : 3000);
  clearTimeout(toast.hide);
}

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
      fallbackRate >= 30 ? `<p>🧠 ${fallbackRate}% of answers were out of scope. <a href="#insights" data-go="insights">See the missed questions</a></p>` : ''].join('') || '<p class="all-clear">✅ Nothing needs attention.</p>';
    return () => {
      const live = $('ov-live');
      if (live && live.dataset.ready) {                            // refresh in place: numbers roll to the new value
        const values = { students: o.students, requests: o.open_requests, incidents: o.open_incidents, answers: o.answers, signedin: o.signed_in_today };
        Object.entries(values).forEach(([key, v]) => {
          const el = view.querySelector(`[data-key="${key}"] strong`); if (el) { el.dataset.count = v; countUp(el, v, Number(el.dataset.value || 0)); }
        });
        $('ov-attention').innerHTML = attention();
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
          <div class="a-card"><h2>Student portal</h2>
            <p>${badge(o.portal_open, 'Open', 'Closed')} ${badge(o.student_login, 'Sign-in required (ID + PIN)', 'No sign-in (anyone can look up)')}</p>
            <div class="a-row"><span class="a-link">${esc(o.links.student)}</span><button type="button" data-copy="${esc(o.links.student)}">Copy</button></div>
            <p>Hand this link to students. Manage it in the <a href="#portal" data-go="portal">Student portal</a> tab.</p></div>
          <div class="a-card a-quality"><div class="ring" id="ov-ring" style="--p:${understood}"><b id="ov-ring-n">${understood}%</b></div>
            <div><h2>Answer quality</h2><p>${o.answers ? `The bot placed ${understood}% of ${o.answers.toLocaleString()} question(s) into a topic.` : 'No questions yet.'}</p></div></div>
          <div class="a-card"><h2>Needs your attention</h2><div id="ov-attention">${attention()}</div></div>
          <div class="a-card a-wide"><h2>Live activity</h2><div id="ov-events">${feed(events)}</div>
            <p><a href="#security" data-go="security">Open the whole Security log</a></p></div>
        </div>`;
      $('ov-refresh').onclick = (e) => refreshOverview(e.currentTarget, true);
    };
  },

  async portal() {
    const l = await api('/api/admin/links');
    return () => {
      const row = (label, url, which) => `<div class="a-card"><h2>${label}</h2>
        <div class="a-row"><span class="a-link">${esc(url)}</span><button type="button" data-copy="${esc(url)}">Copy</button><a href="${esc(url)}" target="_blank" rel="noopener"><button type="button">Open</button></a></div>
        <div class="a-qr-box"><img class="a-qr" src="/api/admin/qr.png?which=${which}" alt="QR code for ${esc(label)}" width="170" height="170" onload="this.classList.add('in')"></div>
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
    const data = await api('/api/admin/pins');
    const list = data.students;
    const missing = list.filter((s) => !s.has_pin).length, locked = list.filter((s) => s.locked).length;
    return () => {
      view.innerHTML = `<h1>Access & PINs</h1><p class="lead">Student sign-in is Student ID + a 6-digit PIN. PINs are shown once and stored only as a hash.</p>
        <div class="a-grid">${stat('Students', list.length)}${stat('Without a PIN', missing)}${stat('Locked now', locked)}${stat('Signed in today', data.today.signed_in)}${stat('Failed tries today', data.today.failed)}${stat('PIN changes today', data.today.pin_changes)}</div>
        <div class="a-card"><h2>Issue PINs in bulk</h2>
          <p>Make PINs for everyone who has none (safe), or reset everyone's PIN (every old PIN stops working). The new PINs appear below once: print them as slips or download the CSV, then hand each one over in person.</p>
          <div class="a-row"><button type="button" class="primary" id="pins-missing" ${missing ? '' : 'disabled'}>Issue PINs for ${missing} student(s) without one</button>
          <button type="button" class="danger" id="pins-all">Reset every student's PIN</button></div>
          <div id="pin-result"></div></div>
        <div class="a-card" style="margin-top:14px"><h2>Recent student activity</h2>
          <p>Every sign-in, sign-out, wrong PIN and PIN change is recorded here (also in the Security log).</p>
          ${data.recent.length ? `<ul class="a-feed">${data.recent.map((r) => `<li class="${/fail|locked/.test(r.action) ? 'bad' : ''}"><time>${esc(r.time)}</time><b>${esc(r.action.replace('student ', ''))}</b><span>${esc(r.detail)}</span></li>`).join('')}</ul>` : '<p>No student has signed in yet.</p>'}</div>
        <div class="a-card" style="margin-top:14px"><h2>Accounts</h2>
          <div class="a-row"><input id="pin-search" type="search" placeholder="Search student ID or name   ( / )" style="flex:1 1 240px;padding:8px 10px">
            <div class="a-chips" id="pin-chips" role="group" aria-label="Filter">
              <button type="button" class="chip on" data-f="all">All <b>${list.length}</b></button>
              <button type="button" class="chip" data-f="none">No PIN <b>${missing}</b></button>
              <button type="button" class="chip" data-f="locked">Locked <b>${locked}</b></button></div></div>
          <div class="a-scroll"><table class="a-table"><thead><tr><th>Student ID</th><th>Name</th><th>PIN</th><th>Last sign-in</th><th>Sign-ins</th><th>Status</th><th></th></tr></thead><tbody id="pin-rows"></tbody></table></div>
          <p class="a-count" id="pin-count"></p></div>`;
      let filter = 'all';
      const draw = () => {
        const q = $('pin-search').value.trim().toLowerCase();
        const rows = list.filter((s) => (!q || s.student_id.toLowerCase().includes(q) || s.name.toLowerCase().includes(q))
          && (filter === 'all' || (filter === 'none' && !s.has_pin) || (filter === 'locked' && s.locked)));
        $('pin-rows').innerHTML = rows.slice(0, 300).map((s, i) =>
          `<tr class="rise fast" style="--i:${Math.min(i, 10)}" data-row="${esc(s.student_id)}"><td>${esc(s.student_id)}</td><td>${esc(s.name)}</td><td>${badge(s.has_pin, 'Issued', 'None')}${s.pin_changed ? ' <small title="Changed by the student">own PIN</small>' : ''}</td>
           <td>${s.last_signin ? esc(s.last_signin) : '<small>never</small>'}</td><td>${s.signins || 0}</td>
           <td>${s.locked ? `<span class="a-badge warn">Locked ${s.locked_minutes} min</span>` : s.failures ? `${s.failures} wrong try(s)` : ''}</td>
           <td>${s.locked || s.failures ? `<button type="button" data-unlock="${esc(s.student_id)}">Unlock</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="7">No match.</td></tr>';
        $('pin-count').textContent = rows.length > 300 ? `Showing the first 300 of ${rows.length}. Search to narrow it down.` : `${rows.length} of ${list.length} student(s)`;
      };
      draw(); $('pin-search').oninput = draw;
      $('pin-chips').onclick = (e) => {
        const c = e.target.closest('[data-f]'); if (!c) return; filter = c.dataset.f;
        $('pin-chips').querySelectorAll('.chip').forEach((b) => b.classList.toggle('on', b === c)); draw();
      };
      $('pin-rows').onclick = (e) => {
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
          <div class="a-card"><h2>Answers by intent</h2>${d.intents.map((i, n) => `<div class="a-meter"><div class="a-meter-top"><span>${esc(i.intent)}</span><span>${i.count} · ${Math.round((i.count / total) * 100)}%${i.avg_confidence ? ` · avg ${i.avg_confidence}` : ''}</span></div><div class="a-bar-wrap"><div class="a-bar" style="--w:${(i.count / max) * 100}%;--d:${Math.min(n, 14) * 40}ms"></div></div></div>`).join('') || '<p>No chat yet.</p>'}</div>
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
        const rows = d.missed.filter((m) => !q || `${m.question} ${m.intent} ${m.role}`.toLowerCase().includes(q));
        $('miss-rows').innerHTML = rows.map((m, i) => `<tr class="rise fast" style="--i:${Math.min(i, 10)}"><td>${esc(m.question)}</td><td>${esc(m.intent)}</td><td>${m.confidence}</td><td>${esc(m.role)}</td><td>${esc(m.time)}</td></tr>`).join('') || `<tr><td colspan="5">${d.missed.length ? 'No match.' : 'Nothing missed yet.'}</td></tr>`;
        $('miss-count').textContent = d.missed.length ? `${rows.length} of ${d.missed.length}` : '';
      };
      draw(); $('miss-search').oninput = draw;
    };
  },

  async news() {
    const d = await api('/api/admin/news');
    let items = d.news.map((n) => ({ ...n }));
    return () => {
      const draw = (fresh = -1, removed = -1) => {
        view.innerHTML = `<h1>Announcements</h1><p class="lead">The PGPC NEWS ticker on both workspaces. ${d.from_database ? 'Saved here.' : 'Showing data/pgpc_news.json until you save.'} Up to ${d.max}.</p>
          <div class="a-card"><div id="news-list">${items.map((n, i) => `<div class="a-news ${i === fresh ? 'pop' : ''}"><input data-i="${i}" data-k="icon" value="${esc(n.icon)}" maxlength="4" aria-label="Icon"><input data-i="${i}" data-k="text" value="${esc(n.text)}" maxlength="200" aria-label="Announcement"><button type="button" data-del="${i}">Remove</button></div>`).join('') || '<p>No announcements: the ticker is hidden.</p>'}</div>
          <div class="a-row"><button type="button" id="news-add" ${items.length >= d.max ? 'disabled' : ''}>+ Add announcement</button><button type="button" class="primary" id="news-save">Save to the ticker</button><button type="button" id="news-reset">Back to the file</button></div></div>`;
        if (fresh >= 0) document.querySelector(`[data-i="${fresh}"][data-k="text"]`)?.focus();
        $('news-list').oninput = (e) => { const i = e.target.dataset.i; if (i !== undefined) items[i][e.target.dataset.k] = e.target.value; };
        $('news-list').onclick = async (e) => {
          const b = e.target.closest('[data-del]'); if (!b) return;
          const rowEl = b.closest('.a-news'); rowEl.classList.add('out'); await wait(220);
          items.splice(+b.dataset.del, 1); draw();
        };
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
    const kv = (o) => Object.entries(o).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td>${typeof v === 'boolean' ? badge(v, 'yes', 'no') : esc(v)}</td></tr>`).join('');
    return () => {
      view.innerHTML = `<div class="a-head"><div><h1>System</h1><p class="lead">Checks and configuration. Passwords and keys are never shown.</p></div>
        <button type="button" id="sys-again">Run the checks again</button></div>
        <div class="a-two">
          <div class="a-card"><h2>Database <span class="health ${s.database.ok ? 'good' : 'bad'}" title="${s.database.ok ? 'Healthy' : 'Not answering'}"></span></h2><table class="a-table"><tbody>${kv({ ok: s.database.ok, version: s.database.version, latency_ms: s.database.latency_ms })}</tbody></table></div>
          <div class="a-card"><h2>Model</h2><table class="a-table"><tbody>${kv({ confidence_threshold: s.model.threshold, intents: s.model.intents.length })}</tbody></table><p>${s.model.intents.map(esc).join(', ')}</p></div>
          <div class="a-card"><h2>Versions</h2><table class="a-table"><tbody>${kv({ ...s.versions, build: s.build })}</tbody></table></div>
          <div class="a-card"><h2>Configuration</h2><table class="a-table"><tbody>${kv(s.config)}</tbody></table></div>
        </div>`;
      $('sys-again').onclick = (e) => busy(e.currentTarget, async () => { await show('system'); toast('Checks done'); });
    };
  },
};

/* ---------- live: one change counter, asked every 4 seconds; the page reloads what it shows when it moves ---------- */
const LIVE_MS = 4000;
let rev = null;
const liveSafe = {                                   // a tab whose text boxes hold something is left alone
  overview: () => true,
  security: () => !$('sec-search')?.value && !$('sec-role')?.value && !$('sec-bad')?.checked,
  insights: () => !$('miss-search')?.value,
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
  } else if (!typing && /^[1-8]$/.test(e.key)) show(TABS[+e.key - 1][0]);
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
  ['Ctrl+K', 'Command palette: tabs and actions'], ['?', 'This list'], ['1 – 8', 'Open a tab'], ['/', 'Focus the search box of the tab'],
  ['← / →', 'Between tabs (while a tab is focused)'], ['Esc', 'Close a dialog'],
]);
buildTabs();
show(TABS.some(([id]) => id === location.hash.slice(1)) ? location.hash.slice(1) : 'overview');
if (!location.hash || location.hash === '#overview') { /* badges come with the overview itself */ } else api('/api/admin/overview').then(paintBadges).catch(() => {});
