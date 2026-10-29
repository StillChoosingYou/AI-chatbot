'use strict';
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const view = $('view');
const TABS = [['overview', 'Overview'], ['portal', 'Student portal'], ['access', 'Access & PINs'], ['insights', 'Chatbot insights'],
  ['news', 'Announcements'], ['data', 'Data & backup'], ['security', 'Security log'], ['system', 'System']];
let current = 'overview';

function toast(text) {
  const t = $('toast'); t.textContent = text; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 3200);
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  let body = {};
  if ((response.headers.get('content-type') || '').includes('json')) { try { body = await response.json(); } catch { body = {}; } }
  if (response.status === 401) { location.assign('/admin'); throw new Error('Signed out'); }
  if (!response.ok) throw new Error(body.error || 'Something went wrong.');
  return body;
}
const send = (method, data) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
const stat = (label, value, note = '') => `<div class="a-card a-stat"><small>${esc(label)}</small><strong>${esc(value)}</strong><span>${esc(note)}</span></div>`;
const badge = (on, yes, no) => `<span class="a-badge ${on ? 'on' : 'off'}">${esc(on ? yes : no)}</span>`;
const copy = async (text) => { try { await navigator.clipboard.writeText(text); toast('Copied'); } catch { toast('Copy failed. Select the text and copy it.'); } };

function renderTabs() {
  $('tabs').innerHTML = TABS.map(([id, label]) => `<button type="button" role="tab" data-tab="${id}" aria-selected="${id === current}">${esc(label)}</button>`).join('');
}
async function show(tab) {
  current = tab; renderTabs();
  history.replaceState(null, '', `#${tab}`);
  view.innerHTML = '<p class="lead">Loading…</p>';
  try { await views[tab](); } catch (error) { view.innerHTML = `<div class="a-warn">${esc(error.message)}</div>`; }
}

const views = {
  async overview() {
    const o = await api('/api/admin/overview');
    const fallbackRate = o.answers ? Math.round((o.fallbacks / o.answers) * 100) : 0;
    view.innerHTML = `<h1>Overview</h1><p class="lead">The whole system at a glance.</p>
      <div class="a-grid">
        ${stat('Students', o.students, `${o.statuses['For release'] || 0} for release · ${o.statuses['Pending'] || 0} pending`)}
        ${stat('Open requests', o.open_requests, 'waiting for the coordinator')}
        ${stat('Open incidents', o.open_incidents, 'No ID, Lost ID, and so on')}
        ${stat('Chatbot answers', o.answers, `${fallbackRate}% could not be placed`)}
        ${stat('Feedback', `${o.thumbs_up} 👍 / ${o.thumbs_down} 👎`, 'from students and staff')}
        ${stat('Students with a PIN', `${o.with_pin}/${o.students}`, o.locked ? `${o.locked} locked` : 'none locked')}
      </div>
      <div class="a-two">
        <div class="a-card"><h2>Student portal</h2>
          <p>${badge(o.portal_open, 'Open', 'Closed')} ${badge(o.student_login, 'Sign-in required (ID + PIN)', 'No sign-in (anyone can look up)')}</p>
          <div class="a-row"><span class="a-link">${esc(o.links.student)}</span><button type="button" data-copy="${esc(o.links.student)}">Copy</button></div>
          <p>Hand this link to students. Manage it in the <a href="#portal" data-go="portal">Student portal</a> tab.</p></div>
        <div class="a-card"><h2>Needs your attention</h2>
          ${[o.without_pin && o.student_login ? `<p>⚠️ ${o.without_pin} student(s) have no PIN, so they cannot sign in. <a href="#access" data-go="access">Issue PINs</a></p>` : '',
             o.locked ? `<p>🔒 ${o.locked} student account(s) are locked. <a href="#access" data-go="access">Unlock</a></p>` : '',
             !o.student_login ? '<p>🔓 Student sign-in is off: anyone who types a student ID sees that student\'s status. Set STUDENT_LOGIN=1 after issuing PINs.</p>' : '',
             fallbackRate >= 30 ? `<p>🧠 ${fallbackRate}% of answers were out of scope. <a href="#insights" data-go="insights">See the missed questions</a></p>` : ''].join('') || '<p>Nothing needs attention.</p>'}
        </div>
      </div>`;
  },

  async portal() {
    const l = await api('/api/admin/links');
    const row = (label, url, which) => `<div class="a-card"><h2>${label}</h2>
      <div class="a-row"><span class="a-link">${esc(url)}</span><button type="button" data-copy="${esc(url)}">Copy</button><a href="${esc(url)}" target="_blank" rel="noopener"><button type="button">Open</button></a></div>
      <img class="a-qr" src="/api/admin/qr.png?which=${which}" alt="QR code for ${esc(label)}" width="170" height="170">
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
    $('save-url').onclick = async () => {
      try { await api('/api/admin/public-url', send('PUT', { url: $('public-url').value })); toast('Address saved'); show('portal'); } catch (e) { toast(e.message); }
    };
    $('toggle-portal').onclick = async () => {
      if (l.portal_open && !confirm('Close the student portal now? Students will see a "closed" page.')) return;
      await api('/api/admin/portal', send('POST', { open: !l.portal_open })); toast(l.portal_open ? 'Student portal closed' : 'Student portal open'); show('portal');
    };
  },

  async access() {
    const data = await api('/api/admin/pins');
    const list = data.students;
    const missing = list.filter((s) => !s.has_pin).length, locked = list.filter((s) => s.locked).length;
    view.innerHTML = `<h1>Access & PINs</h1><p class="lead">Student sign-in is Student ID + a 6-digit PIN. PINs are shown once and stored only as a hash.</p>
      <div class="a-grid">${stat('Students', list.length)}${stat('Without a PIN', missing)}${stat('Locked now', locked)}</div>
      <div class="a-card"><h2>Issue PINs in bulk</h2>
        <p>Make PINs for everyone who has none (safe), or reset everyone's PIN (every old PIN stops working). The new PINs appear below once: print them as slips or download the CSV, then hand each one over in person.</p>
        <div class="a-row"><button type="button" class="primary" id="pins-missing" ${missing ? '' : 'disabled'}>Issue PINs for ${missing} student(s) without one</button>
        <button type="button" class="danger" id="pins-all">Reset every student's PIN</button></div>
        <div id="pin-result"></div></div>
      <div class="a-card" style="margin-top:14px"><h2>Accounts</h2>
        <div class="a-row"><input id="pin-search" type="search" placeholder="Search student ID or name" style="flex:1 1 240px;padding:8px 10px"></div>
        <div class="a-scroll"><table class="a-table"><thead><tr><th>Student ID</th><th>Name</th><th>PIN</th><th>Status</th><th></th></tr></thead><tbody id="pin-rows"></tbody></table></div></div>`;
    const draw = () => {
      const q = $('pin-search').value.trim().toLowerCase();
      $('pin-rows').innerHTML = list.filter((s) => !q || s.student_id.toLowerCase().includes(q) || s.name.toLowerCase().includes(q)).map((s) =>
        `<tr><td>${esc(s.student_id)}</td><td>${esc(s.name)}</td><td>${badge(s.has_pin, 'Issued', 'None')}</td>
         <td>${s.locked ? `<span class="a-badge warn">Locked ${s.locked_minutes} min</span>` : s.failures ? `${s.failures} wrong try(s)` : ''}</td>
         <td>${s.locked || s.failures ? `<button type="button" data-unlock="${esc(s.student_id)}">Unlock</button>` : ''}</td></tr>`).join('') || '<tr><td colspan="5">No match.</td></tr>';
    };
    draw(); $('pin-search').oninput = draw;
    $('pin-rows').onclick = async (e) => { const b = e.target.closest('[data-unlock]'); if (!b) return; await api('/api/admin/pins/unlock', send('POST', { student_id: b.dataset.unlock })); toast('Unlocked'); show('access'); };
    const issue = async (scope) => {
      const result = await api('/api/admin/pins/bulk', send('POST', { scope }));
      const rows = result.issued;
      $('pin-result').innerHTML = `<div class="a-warn">${rows.length} PIN(s) issued. This is the only time they are shown. Print or download now.</div>
        <div class="a-row no-print"><button type="button" class="primary" id="print-pins">Print slips</button><button type="button" id="csv-pins">Download CSV</button></div>
        <div class="a-pin-sheet">${rows.map((r) => `<div class="a-pin-slip">${esc(r.name)}<br>${esc(r.student_id)} · ${esc(r.year_section)}<b>${esc(r.pin)}</b>Sign in at the student link with your Student ID and this PIN.</div>`).join('')}</div>`;
      $('print-pins').onclick = () => window.print();
      $('csv-pins').onclick = () => {
        const csv = ['student_id,name,year_section,pin', ...rows.map((r) => [r.student_id, `"${r.name.replace(/"/g, '""')}"`, r.year_section, r.pin].join(','))].join('\n');
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv' })); a.download = 'student_pins.csv'; a.click();
      };
      toast(`${rows.length} PIN(s) issued`);
    };
    $('pins-missing').onclick = () => issue('missing');
    $('pins-all').onclick = () => { if (confirm('Reset EVERY student\'s PIN? All current PINs stop working.')) issue('all'); };
  },

  async insights() {
    const d = await api('/api/admin/insights');
    const max = Math.max(1, ...d.intents.map((i) => i.count)), maxDay = Math.max(1, ...d.per_day.map((i) => i.count));
    view.innerHTML = `<h1>Chatbot insights</h1><p class="lead">What students ask, and what the bot could not place. The missed questions are your next dataset.</p>
      <div class="a-two">
        <div class="a-card"><h2>Answers by intent</h2>${d.intents.map((i) => `<div style="margin:7px 0"><div style="display:flex;justify-content:space-between;font-size:12px"><span>${esc(i.intent)}</span><span>${i.count}${i.avg_confidence ? ` · avg ${i.avg_confidence}` : ''}</span></div><div class="a-bar-wrap"><div class="a-bar" style="width:${(i.count / max) * 100}%"></div></div></div>`).join('') || '<p>No chat yet.</p>'}</div>
        <div class="a-card"><h2>Questions per day (14 days)</h2>${d.per_day.map((i) => `<div style="margin:7px 0"><div style="display:flex;justify-content:space-between;font-size:12px"><span>${esc(i.day)}</span><span>${i.count}</span></div><div class="a-bar-wrap"><div class="a-bar" style="width:${(i.count / maxDay) * 100}%"></div></div></div>`).join('') || '<p>No questions yet.</p>'}
          <h2 style="margin-top:16px">Why 👎</h2>${d.thumbs_down_reasons.map((r) => `<p style="margin:2px 0">${esc(r.reason)}: <b>${r.count}</b></p>`).join('') || '<p>None.</p>'}</div>
      </div>
      <div class="a-card" style="margin-top:14px"><h2>Questions the bot missed</h2>
        <p>Out of scope or under 50% sure. Download them, write the right intent and a good answer, and add them to the dataset.</p>
        <div class="a-row"><a href="/api/admin/missed.csv"><button type="button" class="primary">Download missed_questions.csv</button></a></div>
        <div class="a-scroll"><table class="a-table"><thead><tr><th>Question</th><th>Bot said</th><th>Sure</th><th>Role</th><th>When</th></tr></thead><tbody>
        ${d.missed.map((m) => `<tr><td>${esc(m.question)}</td><td>${esc(m.intent)}</td><td>${m.confidence}</td><td>${esc(m.role)}</td><td>${esc(m.time)}</td></tr>`).join('') || '<tr><td colspan="5">Nothing missed yet.</td></tr>'}</tbody></table></div></div>`;
  },

  async news() {
    const d = await api('/api/admin/news');
    let items = d.news.map((n) => ({ ...n }));
    const draw = () => {
      view.innerHTML = `<h1>Announcements</h1><p class="lead">The PGPC NEWS ticker on both workspaces. ${d.from_database ? 'Saved here.' : 'Showing data/pgpc_news.json until you save.'} Up to ${d.max}.</p>
        <div class="a-card"><div id="news-list">${items.map((n, i) => `<div class="a-news"><input data-i="${i}" data-k="icon" value="${esc(n.icon)}" maxlength="4" aria-label="Icon"><input data-i="${i}" data-k="text" value="${esc(n.text)}" maxlength="200" aria-label="Announcement"><button type="button" data-del="${i}">Remove</button></div>`).join('') || '<p>No announcements: the ticker is hidden.</p>'}</div>
        <div class="a-row"><button type="button" id="news-add" ${items.length >= d.max ? 'disabled' : ''}>+ Add announcement</button><button type="button" class="primary" id="news-save">Save to the ticker</button><button type="button" id="news-reset">Back to the file</button></div></div>`;
      $('news-list').oninput = (e) => { const i = e.target.dataset.i; if (i !== undefined) items[i][e.target.dataset.k] = e.target.value; };
      $('news-list').onclick = (e) => { const b = e.target.closest('[data-del]'); if (b) { items.splice(+b.dataset.del, 1); draw(); } };
      $('news-add').onclick = () => { items.push({ icon: '📣', text: '' }); draw(); };
      $('news-save').onclick = async () => { const r = await api('/api/admin/news', send('PUT', { news: items.filter((n) => n.text.trim()) })); items = r.news; toast('Ticker saved'); draw(); };
      $('news-reset').onclick = async () => { if (confirm('Drop the saved announcements and use data/pgpc_news.json again?')) { await api('/api/admin/news', { method: 'DELETE' }); toast('Back to the file'); show('news'); } };
    };
    draw();
  },

  async data() {
    view.innerHTML = `<h1>Data & backup</h1><p class="lead">Take your data out, and keep chat history from growing forever.</p>
      <div class="a-two">
        <div class="a-card"><h2>Full backup</h2><p>Every school table as one JSON file: students, requests, incidents, activity and feedback. PIN hashes are never included.</p>
          <div class="a-row"><a href="/api/admin/backup"><button type="button" class="primary">Download backup</button></a><a href="/api/admin/backup?chat=1"><button type="button">Backup with chat history</button></a></div></div>
        <div class="a-card"><h2>Delete old chat history</h2><p>Chat messages older than a number of days (the Data Privacy Act asks for a stated retention period). Count first, then delete.</p>
          <div class="a-row"><label>Older than <input id="purge-days" type="number" min="7" max="3650" value="90" style="width:80px;padding:7px"> days</label><button type="button" id="purge-count">Count</button><button type="button" class="danger" id="purge-go" disabled>Delete them</button></div><p id="purge-note"></p></div>
      </div>`;
    const days = () => parseInt($('purge-days').value, 10);
    $('purge-days').oninput = () => { $('purge-go').disabled = true; };
    $('purge-count').onclick = async () => {
      const r = await api('/api/admin/purge', send('POST', { days: days(), dry_run: true }));
      $('purge-note').textContent = `${r.count} message(s) are older than ${r.cutoff}.`; $('purge-go').disabled = r.count === 0;
    };
    $('purge-go').onclick = async () => {
      if (!confirm('Delete these chat messages for good? This cannot be undone. Download a backup first if unsure.')) return;
      const r = await api('/api/admin/purge', send('POST', { days: days(), dry_run: false }));
      $('purge-note').textContent = `${r.count} message(s) deleted.`; $('purge-go').disabled = true; toast('Chat history purged');
    };
  },

  async security() {
    const d = await api('/api/admin/security');
    view.innerHTML = `<h1>Security log</h1><p class="lead">Sign-ins, failed sign-ins, PIN changes and admin actions. ${d.failed_today ? `<b>${d.failed_today} failed sign-in(s) today.</b>` : 'No failed sign-ins today.'}</p>
      <div class="a-card"><div class="a-scroll"><table class="a-table"><thead><tr><th>When</th><th>Who</th><th>What</th><th>Detail</th></tr></thead><tbody>
      ${d.events.map((e) => `<tr><td>${esc(e.time)}</td><td>${esc(e.role)}</td><td>${esc(e.action)}</td><td>${esc(e.detail)}</td></tr>`).join('') || '<tr><td colspan="4">No events yet.</td></tr>'}</tbody></table></div></div>`;
  },

  async system() {
    const s = await api('/api/admin/system');
    const kv = (o) => Object.entries(o).map(([k, v]) => `<tr><td>${esc(k.replace(/_/g, ' '))}</td><td>${typeof v === 'boolean' ? badge(v, 'yes', 'no') : esc(v)}</td></tr>`).join('');
    view.innerHTML = `<h1>System</h1><p class="lead">Checks and configuration. Passwords and keys are never shown.</p>
      <div class="a-two">
        <div class="a-card"><h2>Database</h2><table class="a-table"><tbody>${kv({ ok: s.database.ok, version: s.database.version, latency_ms: s.database.latency_ms })}</tbody></table></div>
        <div class="a-card"><h2>Model</h2><table class="a-table"><tbody>${kv({ confidence_threshold: s.model.threshold, intents: s.model.intents.length })}</tbody></table><p>${s.model.intents.map(esc).join(', ')}</p></div>
        <div class="a-card"><h2>Versions</h2><table class="a-table"><tbody>${kv({ ...s.versions, build: s.build })}</tbody></table></div>
        <div class="a-card"><h2>Configuration</h2><table class="a-table"><tbody>${kv(s.config)}</tbody></table></div>
      </div>`;
  },
};

document.addEventListener('click', (e) => {
  const tab = e.target.closest('[data-tab]'); if (tab) show(tab.dataset.tab);
  const go = e.target.closest('[data-go]'); if (go) { e.preventDefault(); show(go.dataset.go); }
  const cp = e.target.closest('[data-copy]'); if (cp) copy(cp.dataset.copy);
});
$('theme').onclick = () => {
  const dark = document.documentElement.dataset.theme !== 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  try { localStorage.setItem('theme', dark ? 'dark' : 'light'); } catch { /* not kept */ }
};
try { const saved = localStorage.getItem('theme'); document.documentElement.dataset.theme = saved || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'); } catch { /* default */ }
show(TABS.some(([id]) => id === location.hash.slice(1)) ? location.hash.slice(1) : 'overview');
