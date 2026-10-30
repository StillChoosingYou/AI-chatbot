/* Shared interface tools for the coordinator, student and admin pages:
   - ux.confirm / ux.reveal / ux.modal   styled dialogs that replace the browser's confirm() and prompt()
   - ux.palette                          Ctrl/⌘+K command palette (each page registers its own commands)
   - ux.help                             "?" lists the keyboard shortcuts of the page
   - ux.undo                             a bar with an Undo button after a change
   - the offline banner
   Everything is plain DOM, keyboard friendly and switched off by "reduce motion". */
(function () {
  'use strict';

  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const typing = (el) => !!el && /^(input|textarea|select)$/i.test(el.tagName) || !!(el && el.isContentEditable);
  const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

  /* ---------- a layer on top of the page: backdrop, focus trap, Escape, focus returns where it was ---------- */
  let layers = 0;
  function layer(panelHtml, { label = 'Dialog', cls = '', onClose } = {}) {
    const back = document.createElement('div');
    back.className = 'ux-back';
    back.innerHTML = `<div class="ux-panel ${cls}" role="dialog" aria-modal="true" aria-label="${esc(label)}">${panelHtml}</div>`;
    const panel = back.firstElementChild;
    const previous = document.activeElement;
    let closed = false;
    const close = (value) => {
      if (closed) return;
      closed = true; layers--;
      document.removeEventListener('keydown', onKey, true);
      back.classList.remove('show');
      setTimeout(() => { back.remove(); if (previous && previous.focus) previous.focus(); }, 200);
      if (onClose) onClose(value);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(undefined); }
      else if (e.key === 'Tab') {
        const items = [...panel.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null);
        if (!items.length) return;
        const at = items.indexOf(document.activeElement);
        e.preventDefault();
        items[(at + (e.shiftKey ? items.length - 1 : 1)) % items.length].focus();
      }
    };
    back.addEventListener('mousedown', (e) => { if (e.target === back) close(undefined); });
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(back);
    layers++;
    void back.offsetWidth;
    back.classList.add('show');
    return { back, panel, close };
  }

  function dialog({ title, text = '', ok = 'OK', cancel = 'Cancel', danger = false, body = '' }) {
    return new Promise((resolve) => {
      const l = layer(`<h2>${esc(title)}</h2>${text ? `<p>${esc(text)}</p>` : ''}${body}
        <div class="ux-actions">${cancel ? `<button type="button" data-no>${esc(cancel)}</button>` : ''}<button type="button" data-yes class="${danger ? 'danger' : 'primary'}">${esc(ok)}</button></div>`,
      { label: title, onClose: (v) => resolve(v === true) });
      l.panel.addEventListener('click', (e) => {
        if (e.target.closest('[data-yes]')) l.close(true);
        else if (e.target.closest('[data-no]')) l.close(false);
      });
      (l.panel.querySelector(danger ? '[data-no]' : '[data-yes]') || l.panel.querySelector('[data-yes]')).focus();
    });
  }
  const confirmBox = (o) => dialog({ ok: 'Confirm', ...o });

  /* A secret shown once (a new PIN): big digits, a copy button, nothing to select by accident. */
  function reveal({ title, note = '', secret, subject = '' }) {
    return new Promise((resolve) => {
      const l = layer(`<h2>${esc(title)}</h2>${subject ? `<p class="ux-subject">${esc(subject)}</p>` : ''}
        <div class="ux-secret" aria-live="polite">${[...String(secret)].map((c, i) => `<span style="--i:${i}">${esc(c)}</span>`).join('')}</div>
        <p>${esc(note)}</p>
        <div class="ux-actions"><button type="button" data-copy>Copy</button><button type="button" data-yes class="primary">Done</button></div>`,
      { label: title, onClose: () => resolve() });
      l.panel.addEventListener('click', async (e) => {
        if (e.target.closest('[data-yes]')) l.close();
        const copy = e.target.closest('[data-copy]');
        if (copy) {
          try { await navigator.clipboard.writeText(String(secret)); copy.textContent = 'Copied ✓'; } catch { copy.textContent = 'Select and copy it'; }
          setTimeout(() => { copy.textContent = 'Copy'; }, 1500);
        }
      });
      l.panel.querySelector('[data-yes]').focus();
    });
  }

  function modal(title, html) {
    const l = layer(`<h2>${esc(title)}</h2>${html}<div class="ux-actions"><button type="button" data-yes class="primary">Close</button></div>`, { label: title, cls: 'wide' });
    l.panel.addEventListener('click', (e) => { if (e.target.closest('[data-yes]')) l.close(); });
    l.panel.querySelector('[data-yes]').focus();
    return l;
  }

  /* ---------- command palette ---------- */
  const sources = [];
  function score(text, q) {
    const t = text.toLowerCase();
    if (!q) return 1;
    if (t.startsWith(q)) return 4;
    if (t.split(/[\s·:/-]+/).some((w) => w.startsWith(q))) return 3;
    if (t.includes(q)) return 2;
    let at = 0;                                                       // letters in order: "dsh" finds "Dashboard"
    for (const c of q) { at = t.indexOf(c, at); if (at < 0) return 0; at++; }
    return 1;
  }
  let paletteLayer = null;
  function openPalette() {
    if (paletteLayer || layers) return;
    const l = layer(`<div class="pal-box"><span class="pal-icon" aria-hidden="true">⌘</span>
        <input class="pal-input" type="text" placeholder="Type a command or a student…" aria-label="Command palette" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true" aria-controls="pal-list">
        <kbd>Esc</kbd></div><div class="pal-list" id="pal-list" role="listbox"></div>
        <div class="pal-foot"><span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> run</span><span><kbd>?</kbd> shortcuts</span></div>`,
    { label: 'Command palette', cls: 'pal', onClose: () => { paletteLayer = null; } });
    paletteLayer = l;
    const input = l.panel.querySelector('.pal-input'), list = l.panel.querySelector('.pal-list');
    let shown = [], active = 0;
    const gather = (q) => {
      const out = [];
      sources.forEach((source) => {
        let items = [];
        try { items = source(q) || []; } catch { items = []; }
        items.forEach((item) => {
          const s = item.always ? (item.rank ?? 5) : score(`${item.label} ${item.keywords || ''}`, q);
          if (s > 0) out.push({ ...item, s });
        });
      });
      return out.sort((a, b) => b.s - a.s).slice(0, 14);
    };
    const draw = () => {
      const q = input.value.trim().toLowerCase();
      shown = gather(q);
      active = Math.min(active, Math.max(0, shown.length - 1));
      let group = '';
      list.innerHTML = shown.map((item, i) => {
        const head = item.group && item.group !== group ? `<div class="pal-group">${esc(group = item.group)}</div>` : '';
        return `${head}<button type="button" class="pal-item" role="option" id="pal-${i}" data-i="${i}" aria-selected="${i === active}" style="--i:${Math.min(i, 9)}">
          <span class="pal-ico" aria-hidden="true">${esc(item.icon || '›')}</span><span class="pal-label">${esc(item.label)}</span>${item.hint ? `<small>${esc(item.hint)}</small>` : ''}</button>`;
      }).join('') || '<p class="pal-empty">Nothing matches. Try another word.</p>';
      input.setAttribute('aria-activedescendant', shown.length ? `pal-${active}` : '');
    };
    const mark = () => {
      list.querySelectorAll('.pal-item').forEach((n) => n.setAttribute('aria-selected', String(Number(n.dataset.i) === active)));
      list.querySelector(`#pal-${active}`)?.scrollIntoView({ block: 'nearest' });
      input.setAttribute('aria-activedescendant', `pal-${active}`);
    };
    const run = (i) => { const item = shown[i]; if (!item) return; l.close(); setTimeout(() => item.run(), 120); };
    input.addEventListener('input', () => { active = 0; draw(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); active = (active + 1) % Math.max(1, shown.length); mark(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); active = (active + Math.max(1, shown.length) - 1) % Math.max(1, shown.length); mark(); }
      else if (e.key === 'Enter') { e.preventDefault(); run(active); }
      else if (e.key === '?' && !input.value) { e.preventDefault(); l.close(); setTimeout(openHelp, 220); }
    });
    list.addEventListener('mousemove', (e) => { const b = e.target.closest('.pal-item'); if (b && Number(b.dataset.i) !== active) { active = Number(b.dataset.i); mark(); } });
    list.addEventListener('click', (e) => { const b = e.target.closest('.pal-item'); if (b) run(Number(b.dataset.i)); });
    draw();
    input.focus();
  }

  /* ---------- shortcuts help ---------- */
  const helpSources = [];
  function openHelp() {
    if (layers) return;
    const rows = helpSources.flatMap((fn) => fn());
    const key = (k) => k.split('+').map((p) => `<kbd>${esc(p)}</kbd>`).join('<span>+</span>');
    modal('Keyboard shortcuts', `<table class="ux-keys"><tbody>${rows.map(([k, d]) => `<tr><td>${key(k)}</td><td>${esc(d)}</td></tr>`).join('')}</tbody></table>`);
  }

  /* ---------- an Undo bar ---------- */
  let undoBar = null, undoTimer = 0;
  function undo({ text, onUndo, ms = 8000 }) {
    if (undoBar) { undoBar.remove(); clearTimeout(undoTimer); }
    const bar = document.createElement('div');
    bar.className = 'ux-undo';
    bar.setAttribute('role', 'status');
    bar.innerHTML = `<span></span><button type="button">Undo</button><i style="animation-duration:${ms}ms"></i>`;
    bar.firstChild.textContent = text;
    document.body.appendChild(bar);
    undoBar = bar;
    void bar.offsetWidth; bar.classList.add('show');
    const hide = () => { bar.classList.remove('show'); setTimeout(() => bar.remove(), 260); if (undoBar === bar) undoBar = null; };
    bar.querySelector('button').onclick = async () => { clearTimeout(undoTimer); bar.querySelector('button').disabled = true; try { await onUndo(); } finally { hide(); } };
    undoTimer = setTimeout(hide, ms);
  }

  /* ---------- offline banner ---------- */
  let banner = null;
  function netState(online) {
    if (!banner) {
      banner = document.createElement('div');
      banner.className = 'ux-net'; banner.setAttribute('role', 'status');
      document.body.appendChild(banner);
    }
    banner.classList.toggle('ok', online);
    banner.textContent = online ? 'Back online' : 'You are offline. Changes will not save until the connection returns.';
    void banner.offsetWidth; banner.classList.add('show');
    clearTimeout(netState.t);
    if (online) netState.t = setTimeout(() => banner.classList.remove('show'), 2200);
  }
  window.addEventListener('offline', () => netState(false));
  window.addEventListener('online', () => netState(true));
  if (navigator.onLine === false) document.addEventListener('DOMContentLoaded', () => netState(false));

  /* ---------- keys ---------- */
  document.addEventListener('keydown', (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 'k') { e.preventDefault(); e.stopImmediatePropagation(); openPalette(); }
    else if (e.key === '?' && !mod && !typing(e.target) && !layers) { e.preventDefault(); openHelp(); }
  }, true);

  window.ux = {
    confirm: confirmBox, dialog, reveal, modal, undo,
    palette: { register(fn) { sources.push(fn); }, open: openPalette },
    help: { register(fn) { helpSources.push(fn); }, open: openHelp },
    get busy() { return layers > 0; },
  };
})();
