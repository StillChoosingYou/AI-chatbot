/* School ID property management – motion.
   Every animation in the app is played from this file, so the rest of the code only says WHAT happened
   (a message arrived, a dialog opened, a row changed) and never HOW it moves.

   The engine is Motion (https://motion.dev), the animation library that Framer Motion is built on. Framer Motion
   itself needs React and this app is plain HTML and JavaScript, so it uses Motion's own plain-JavaScript build:
   static/vendor/motion.js (MIT). It is served by this app, so nothing is loaded from the internet and the chatbot
   still works offline.

   Nothing here is needed for the app to work. When Motion is missing, when the visitor has asked their system for
   reduced motion, or when `localStorage.motion` is 'off', every function returns at once and nothing moves.
   Animations change only opacity and transform (the browser draws those without touching the layout) and the other
   few properties named below. The inline styles an animation leaves behind are removed when it ends, so the
   stylesheet stays in charge of how things look. */
(function () {
  'use strict';

  const M = window.Motion;
  const root = document.documentElement;
  const reduced = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  const usable = !!(M && typeof M.animate === 'function');
  const EASE = [0.22, 1, 0.36, 1];                  // quick start, gentle landing
  const DONE = Promise.resolve();
  const ROW_LIMIT = 24;                             // rows that animate at once: the rest are below the fold

  let off = switchedOff();
  let busy = 0;                                     // animations playing right now (endless ones are not counted)
  let runs = 0;                                     // animations started since the page loaded
  let generation = 0;                               // reset() adds one: lists then forget what they had drawn
  const memory = new WeakMap();                     // what a list or a row of tiles showed the last time
  const touch = new WeakMap();                      // hover and press state of the buttons
  const loops = new WeakMap();                      // how to stop the endless animations of an element

  function switchedOff() {
    try { return localStorage.getItem('motion') === 'off'; } catch { return false; }
  }
  const active = () => usable && !off && !(reduced && reduced.matches);
  function mark() { root.classList.toggle('fx', active()); }     // the stylesheet turns its own fallbacks off with it
  mark();
  if (reduced) {
    if (reduced.addEventListener) reduced.addEventListener('change', mark);
    else if (reduced.addListener) reduced.addListener(mark);
  }
  window.addEventListener('storage', event => {
    if (event.key === 'motion' || event.key === null) { off = switchedOff(); mark(); }
  });

  const kebab = (key) => key.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`);
  const items = (x) => (!x ? [] : x.nodeType ? [x] : Array.from(x));

  function recall(el, slot) {
    const saved = memory.get(el);
    return saved && saved.generation === generation ? saved[slot] : undefined;
  }
  function remember(el, slot, value) {
    const saved = memory.get(el);
    if (saved && saved.generation === generation) saved[slot] = value;
    else memory.set(el, { generation, [slot]: value });
  }
  const forget = (el) => { if (el) memory.delete(el); };

  /* ── The two ways to play something ── */

  /* Motion writes an animation's final values into the element's style one frame AFTER it reports that the animation
     ended. Clearing them at once would be undone by that write, so the clean-up waits two frames (and a timer, for a
     tab that draws no frames). */
  function afterPaint(fn) {
    let called = false;
    const once = () => { if (!called) { called = true; fn(); } };
    requestAnimationFrame(() => requestAnimationFrame(once));
    setTimeout(once, 150);
  }

  /* Plays keyframes on one element. Returns a promise that settles when the animation is over and cleaned up (at once
     when nothing moves). The properties it animated are then cleared from the element's inline style, so the
     stylesheet is in charge again, unless the animation was `hold`ing a pose or a newer animation has taken the
     property over. A timer settles it too: an animation that another one replaces never reports that it finished. */
  function run(el, keyframes, options = {}) {
    if (!el || !el.isConnected || !active()) return DONE;
    const { hold = false, origin = '', ...timing } = options;
    const keys = Object.keys(keyframes);
    const owned = el.__fxOwn || (el.__fxOwn = {});
    const token = {};
    keys.forEach(key => { owned[key] = token; });
    if (origin) el.style.transformOrigin = origin;
    let controls = null;
    try { controls = M.animate(el, keyframes, timing); } catch { controls = null; }
    if (!controls) { keys.forEach(key => { if (owned[key] === token) delete owned[key]; }); return DONE; }
    runs += 1;
    busy += 1;
    return new Promise(resolve => {
      let over = false;
      const finish = () => {
        busy -= 1;
        keys.forEach(key => {
          if (owned[key] !== token) return;                                // a newer animation owns it now
          delete owned[key];
          if (!hold) el.style.removeProperty(kebab(key));
        });
        if (!hold && !owned.transform && el.style.transformOrigin) el.style.removeProperty('transform-origin');
        resolve();
      };
      const end = () => {
        if (over) return;
        over = true;
        clearTimeout(guard);
        if (hold) finish(); else afterPaint(finish);
      };
      const guard = setTimeout(end, ((timing.delay || 0) + (timing.duration || 0.4)) * 1000 + 250);
      Promise.resolve(controls.finished || controls).then(end, end);
    });
  }

  /* Plays keyframes for ever, such as the dots of the typing indicator. Returns the function that stops it. */
  function loop(el, keyframes, options) {
    if (!el || !el.isConnected || !active()) return () => {};
    const keys = Object.keys(keyframes);
    const owned = el.__fxOwn || (el.__fxOwn = {});
    const token = {};
    keys.forEach(key => { owned[key] = token; });
    let controls = null;
    try { controls = M.animate(el, keyframes, options); } catch { controls = null; }
    if (!controls) return () => {};
    runs += 1;
    let stopped = false;
    const clear = () => keys.forEach(key => {
      if (owned[key] !== token) return;
      el.style.removeProperty(kebab(key));
    });
    return () => {
      if (stopped) return;
      stopped = true;
      try { controls.stop(); } catch { /* it had already ended */ }
      clear();
      afterPaint(() => { clear(); keys.forEach(key => { if (owned[key] === token) delete owned[key]; }); });
    };
  }

  /* ── Small effects ── */

  /* Elements slide in from where they were told and fade in, one after the other. */
  function enter(list, o = {}) {
    const { x = 0, y = 12, scale = 1, duration = 0.34, delay = 0, step = 0.045, max = 14, origin = '' } = o;
    const from = `translate(${x}px, ${y}px) scale(${scale})`;
    return Promise.all(items(list).filter(el => !el.hidden).map((el, index) => run(el,
      { opacity: [0, 1], transform: [from, 'translate(0px, 0px) scale(1)'] },
      { duration, delay: delay + Math.min(index, max) * step, ease: EASE, origin })));
  }

  const fade = (el, { from = 0, duration = 0.25 } = {}) => run(el, { opacity: [from, 1] }, { duration, ease: 'easeOut' });
  const swap = (el) => run(el, { opacity: [0, 1], transform: ['translateY(-6px)', 'translateY(0px)'] }, { duration: 0.28, ease: EASE });
  const reveal = (el) => run(el,
    { opacity: [0, 1], transform: ['translateY(-8px) scale(0.98)', 'translateY(0px) scale(1)'] }, { duration: 0.28, ease: EASE });
  const pop = (el, { scale = 1.22 } = {}) => run(el, { transform: [`scale(${scale})`, 'scale(1)'] }, { type: 'spring', bounce: 0.45, duration: 0.45 });
  const shake = (el) => run(el,
    { transform: ['translateX(0px)', 'translateX(-9px)', 'translateX(8px)', 'translateX(-6px)', 'translateX(4px)', 'translateX(0px)'] },
    { duration: 0.42, ease: 'easeOut' });
  const nudge = (el) => run(el,
    { transform: ['translate(0px, 0px)', 'translate(5px, -5px)', 'translate(0px, 0px)'] }, { duration: 0.32, ease: 'easeInOut' });
  const turn = (el, degrees = 360) => run(el,
    { transform: ['rotate(0deg) scale(0.7)', `rotate(${degrees}deg) scale(1)`] }, { type: 'spring', bounce: 0.3, duration: 0.6 });

  /* A glow around an element (around the cells, for a table row) that fades out: something changed here.
     `tone` is the name of a colour in the stylesheet. */
  function accent(name) {
    const value = getComputedStyle(root).getPropertyValue(name).trim();
    const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value);
    if (hex) {
      const full = hex[1].length === 3 ? hex[1].replace(/./g, digit => digit + digit) : hex[1];
      return [0, 2, 4].map(at => parseInt(full.slice(at, at + 2), 16)).join(', ');
    }
    return '61, 186, 138';
  }
  function flash(el, tone = '--green-light') {
    if (!el || !active()) return DONE;
    const rgb = accent(tone);
    if (el.tagName === 'TR') {
      return Promise.all(Array.from(el.cells).map(cell => run(cell,
        { backgroundColor: [`rgba(${rgb}, 0.3)`, `rgba(${rgb}, 0)`] }, { duration: 1.2, ease: 'easeOut' })));
    }
    return run(el, { boxShadow: [
      `inset 0 0 0 2px rgba(${rgb}, 0.75), 0 0 0 5px rgba(${rgb}, 0.25)`,
      `inset 0 0 0 2px rgba(${rgb}, 0), 0 0 0 0px rgba(${rgb}, 0)`] }, { duration: 1.2, ease: 'easeOut' });
  }

  /* A number counts up (or down) to the figure it holds, then shows exactly that text again. */
  function count(el, { from = 0, duration = 0.8, delay = 0 } = {}) {
    if (!el || !active()) return DONE;
    const text = el.textContent;
    const match = /^(\D*)(\d+)(\D*)$/.exec(text);
    if (!match) return DONE;
    const [, head, digits, tail] = match;
    const to = Number(digits);
    if (from === to) return DONE;
    runs += 1;
    busy += 1;
    if (delay > 0) el.textContent = `${head}${from}${tail}`;
    return new Promise(resolve => {
      let over = false;
      const end = () => {
        if (over) return;
        over = true;
        clearTimeout(guard);
        busy -= 1;
        el.textContent = text;
        resolve();
      };
      const guard = setTimeout(end, (delay + duration) * 1000 + 250);
      let controls = null;
      try {
        controls = M.animate(from, to, { duration, delay, ease: EASE, onUpdate: value => { if (!over) el.textContent = `${head}${Math.round(value)}${tail}`; } });
      } catch { end(); return; }
      Promise.resolve(controls.finished || controls).then(end, end);
    });
  }

  /* Slides out to the side, then leaves the page. */
  function leave(el) {
    if (!el || !el.isConnected || !active()) return DONE;
    return run(el, { opacity: [1, 0], transform: ['translateX(0px)', 'translateX(32px)'] },
      { duration: 0.24, ease: 'easeIn', hold: true }).then(() => { el.remove(); });
  }

  /* ── Whole pages, dialogs, toasts ── */

  /* A workspace page opens: its heading and blocks rise in one after the other. The lists inside draw themselves
     when their data arrives, so the page forgets what they showed before. */
  function view(section) {
    if (!section) return DONE;
    section.querySelectorAll('.tool-list, .bars').forEach(forget);
    return enter(Array.from(section.children).filter(el => !el.classList.contains('tool-list')),
      { y: 16, duration: 0.4, step: 0.06, max: 6 });
  }

  /* A dialog has just been opened with showModal(): the box springs up and its fields follow. */
  function dialog(box) {
    if (!box || !active()) return DONE;
    const form = box.querySelector('form') || box;
    const parts = Array.from(form.children).filter(child => !child.hidden)
      .flatMap(child => (child.matches('.form-grid, .role-choices') ? Array.from(child.children) : [child]))
      .filter(el => !el.hidden);
    run(box, { opacity: [0, 1], transform: ['translateY(20px) scale(0.95)', 'translateY(0px) scale(1)'] },
      { type: 'spring', bounce: 0.18, duration: 0.5 });
    return enter(parts, { y: 10, delay: 0.1, step: 0.035, duration: 0.3, max: 9 });
  }

  const toast = (el) => run(el, { opacity: [0, 1], transform: ['translateY(18px) scale(0.95)', 'translateY(0px) scale(1)'] },
    { type: 'spring', bounce: 0.25, duration: 0.45, origin: 'right bottom' });
  const hide = (el) => run(el, { opacity: [1, 0], transform: ['translateY(0px)', 'translateY(8px)'] }, { duration: 0.2, ease: 'easeIn' });

  /* The ⋯ menu of a table row grows out of the button; it opens upwards when it had no room below. */
  function menu(box, up = false) {
    run(box, { opacity: [0, 1], transform: ['translateY(-4px) scale(0.94)', 'translateY(0px) scale(1)'] },
      { duration: 0.18, ease: EASE, origin: up ? 'bottom right' : 'top right' });
    return enter(Array.from(box.children), { x: -6, y: 0, delay: 0.04, step: 0.02, duration: 0.18, max: 8 });
  }

  /* ── The chat ── */

  /* A message arrives: yours from the right, the assistant's from the left, and the buttons under it a moment later.
     A confirmation (an incident filed, a request sent, a request sent) also glows once. */
  function message(node, { kind = 'bot', glow = false } = {}) {
    const from = kind === 'user' ? 28 : -28;
    const arrived = run(node,
      { opacity: [0, 1], transform: [`translate(${from}px, 12px) scale(0.94)`, 'translate(0px, 0px) scale(1)'] },
      { type: 'spring', bounce: 0.2, duration: 0.55, origin: kind === 'user' ? 'right bottom' : 'left bottom' });
    enter(node.querySelectorAll('.action-chip, .reply-chip'), { y: 8, delay: 0.2, step: 0.05, duration: 0.28, max: 8 });
    if (glow) arrived.then(() => flash(node));
    return arrived;
  }

  function typing(node) {
    if (!node || !active()) return;
    enter(node, { y: 8, duration: 0.22 });
    loops.set(node, Array.from(node.querySelectorAll('.typing-dot')).map((dot, index) => loop(dot,
      { transform: ['translateY(0px)', 'translateY(-6px)'], opacity: [0.45, 1] },
      { duration: 0.42, ease: 'easeInOut', repeat: Infinity, repeatType: 'reverse', delay: index * 0.14 })));
  }
  function untyping(node) {
    (loops.get(node) || []).forEach(stop => stop());
    loops.delete(node);
  }

  /* ── Lists, tables and tiles: only what is new or changed moves ── */

  /* A table body was drawn again: rows that were not there before slide in, rows whose data changed glow. */
  function rows(body) {
    if (!body) return;
    const trs = Array.from(body.querySelectorAll('tr[data-id]'));
    const before = recall(body, 'rows');                                   // undefined before the first draw
    remember(body, 'rows', new Map(trs.map(tr => [tr.dataset.id, tr.dataset.sig || ''])));
    if (!active()) return;
    const fresh = [], changed = [];
    trs.forEach(tr => {
      if (!before || !before.has(tr.dataset.id)) fresh.push(tr);
      else if (before.get(tr.dataset.id) !== (tr.dataset.sig || '')) changed.push(tr);
    });
    enter(fresh.slice(0, ROW_LIMIT), { y: 10, duration: 0.3, step: 0.03, max: 12 });
    changed.slice(0, ROW_LIMIT).forEach(tr => flash(tr));
    enter(body.querySelectorAll('tr.empty-row'), { y: 8 });
  }

  /* A list of cards was drawn again (`data-key` names each one): new cards slide in, cards that changed glow. */
  function sync(box, selector = '.tool-row, .pill-row, .list-title, .empty') {
    if (!box) return;
    const els = Array.from(box.querySelectorAll(selector));
    const keyOf = (el, index) => el.dataset.key || `${el.className}#${index}`;
    const before = recall(box, 'list');
    remember(box, 'list', new Map(els.map((el, index) => [keyOf(el, index), el.textContent])));
    if (!active()) return;
    const fresh = [], changed = [];
    els.forEach((el, index) => {
      const key = keyOf(el, index);
      if (!before || !before.has(key)) fresh.push(el);
      else if (before.get(key) !== el.textContent) changed.push(el);
    });
    enter(fresh.slice(0, 30), { y: 12, step: 0.05, max: 8 });
    changed.slice(0, 12).forEach(el => flash(el));
  }

  /* The answer to something just asked (a search, an import) is drawn: everything in it rises in, every time. */
  const fill = (box) => enter(box ? box.children : [], { y: 12, step: 0.05, max: 8 });

  /* Content that is redrawn often (a check card): it animates only when its content really changed, and the promise
     says whether it did. */
  function update(el) {
    if (!el || !active()) return Promise.resolve(false);
    const html = el.innerHTML;
    if (recall(el, 'html') === html) return Promise.resolve(false);
    remember(el, 'html', html);
    return reveal(el).then(() => true);                                    // true: it did move
  }

  /* A row of buttons that is redrawn after each click (the suggestions): it animates when the buttons changed. */
  function refill(box, selector = 'button') {
    if (!box) return;
    const els = Array.from(box.querySelectorAll(selector));
    const key = els.map(el => el.textContent).join('|');
    if (recall(box, 'refill') === key) return;
    remember(box, 'refill', key);
    enter(els, { y: 8, step: 0.04, duration: 0.28, max: 8 });
  }

  /* Filter chips: they rise in the first time, and the chip that became the chosen one pops. */
  function chips(box) {
    if (!box) return;
    const all = Array.from(box.children);
    const chosen = all.findIndex(chip => chip.getAttribute('aria-pressed') === 'true');
    const before = recall(box, 'chip');
    remember(box, 'chip', chosen);
    if (!active()) return;
    if (before === undefined) enter(all, { y: 8, step: 0.04, duration: 0.28, max: 8 });
    else if (before !== chosen && chosen >= 0) pop(all[chosen], { scale: 1.12 });
  }

  /* Dashboard numbers. The first time, each tile slides in and its number counts up from zero. After that, only a
     tile whose number changed moves: the number counts from the old figure and the tile glows. */
  function tiles(box) {
    if (!box) return;
    const before = recall(box, 'tiles');
    const now = new Map();
    Array.from(box.querySelectorAll('.tile')).forEach((tile, index) => {
      const label = (tile.querySelector('span') || {}).textContent || String(index);
      const figure = tile.querySelector('strong');
      const text = figure ? figure.textContent : '';
      now.set(label, text);
      if (!active() || !figure) return;
      const was = before ? before.get(label) : undefined;
      if (was === undefined) {
        enter(tile, { y: 14, scale: 0.97, delay: index * 0.06, duration: 0.4 });
        count(figure, { from: 0, delay: index * 0.06 });
      } else if (was !== text) {
        count(figure, { from: Number.parseInt(was, 10) || 0 });
        pop(figure, { scale: 1.2 });
        flash(tile, tile.classList.contains('alert') ? '--danger' : '--green-light');
      }
    });
    remember(box, 'tiles', now);
  }

  /* Bars grow from zero to their width (the width is already in the markup) whenever their numbers change. */
  function bars(box) {
    if (!box) return;
    const fills = Array.from(box.querySelectorAll('.bar-fill'));
    const key = fills.map(fill => fill.style.width).join('|');
    if (recall(box, 'bars') === key) return;
    remember(box, 'bars', key);
    if (!active()) return;
    fills.forEach((fill, index) => {
      const width = fill.style.width;
      fill.style.width = '0%';
      run(fill, { width: ['0%', width] }, { duration: 0.8, delay: index * 0.06, ease: EASE, hold: true })
        .then(() => { fill.style.width = width; });                       // whatever happened, the bar ends at its width
    });
  }

  /* ── The QR scanner ── */
  function scanner(line, frame) {
    if (!line || !frame) return () => {};
    return loop(line, { transform: ['translateY(0px)', `translateY(${Math.max(0, Math.round(frame.clientHeight * 0.84))}px)`] },
      { duration: 1.6, ease: 'easeInOut', repeat: Infinity, repeatType: 'reverse' });
  }

  /* ── Hover and press: every button and chip lifts when the pointer is on it and sinks while it is held ──
     Done here for every button at once, including the ones drawn later. The stylesheet's own hover transforms are
     switched off while Motion is running (html.fx), so the two never fight. */
  const TOUCHY = ['.primary', '.export-btn', '.text-button', '.action-chip', '.reply-chip', '.suggestions button',
    '.filter-chip', 'button.tile', '.role-card', '.nav', '.mobile-dock a', '.row-more', '.emoji-btn', '.theme-toggle',
    '.role-switch', '.ticker-toggle', '.menu-toggle', '.row-menu button'].join(', ');
  const LIFT = [['button.tile, .role-card', -3],
    ['.primary, .export-btn, .action-chip, .reply-chip, .suggestions button, .filter-chip', -1]];
  const GROW = [['.emoji-btn', 1.25], ['.theme-toggle', 1.1]];
  const SLIDE = [['.nav', 3]];
  const pick = (table, el, none) => { const row = table.find(([selector]) => el.matches(selector)); return row ? row[1] : none; };
  const REST = 'translate(0px, 0px) scale(1)';
  const pressed = new Set();
  const held = new Set();                           // buttons that keep a pose (lifted, sunk) while the pointer is on them

  /* A held pose stays in the button's inline style; going back to rest always plays an animation that clears it, even
     when the rest pose reads the same as the held one (a button that only sinks when it is pressed). */
  function aim(el, patch) {
    const state = Object.assign({ hover: false, press: false, pose: REST, held: false }, touch.get(el), patch);
    const idle = !state.hover && !state.press;
    const next = idle ? REST
      : `translate(${state.hover ? pick(SLIDE, el, 0) : 0}px, ${state.hover ? pick(LIFT, el, 0) : 0}px) scale(${state.press ? 0.94 : state.hover ? pick(GROW, el, 1) : 1})`;
    if (state.pose !== next || (idle && state.held)) {
      run(el, { transform: [state.pose, next] },
        { duration: state.pose === next ? 0.01 : state.press ? 0.1 : 0.22, ease: EASE, hold: !idle });
      state.held = !idle;
    }
    state.pose = next;
    touch.set(el, state);
    if (state.held) held.add(el); else held.delete(el);
  }
  const target = (event) => {
    const el = event.target && event.target.closest ? event.target.closest(TOUCHY) : null;
    return el && !el.disabled && el.getAttribute('aria-disabled') !== 'true' ? el : null;
  };
  const release = () => { pressed.forEach(el => aim(el, { press: false })); pressed.clear(); };

  document.addEventListener('pointerover', event => {
    const el = active() && event.pointerType === 'mouse' ? target(event) : null;
    if (el && !(event.relatedTarget && el.contains(event.relatedTarget))) aim(el, { hover: true });
  });
  document.addEventListener('pointerout', event => {
    const el = event.target && event.target.closest ? event.target.closest(TOUCHY) : null;
    if (el && touch.has(el) && !(event.relatedTarget && el.contains(event.relatedTarget))) aim(el, { hover: false });
  });
  document.addEventListener('pointerdown', event => {
    const el = active() && event.button === 0 ? target(event) : null;
    if (el) { pressed.add(el); aim(el, { press: true }); }
  });
  document.addEventListener('pointerup', release);
  document.addEventListener('pointercancel', release);
  window.addEventListener('blur', release);

  /* A button that was hidden, covered or removed while the pointer was on it never hears that the pointer left. The
     next time the pointer moves, a held pose whose button is no longer under it is let go. */
  let sweeping = false;
  document.addEventListener('pointermove', () => {
    if (!held.size || sweeping) return;
    sweeping = true;
    requestAnimationFrame(() => {
      sweeping = false;
      held.forEach(el => {
        const state = touch.get(el);
        if (!el.isConnected) { held.delete(el); touch.delete(el); }
        else if (state && !state.press && !el.matches(':hover')) aim(el, { hover: false });
      });
    });
  }, { passive: true });

  window.fx = {
    on: active,                                     // true while things move
    idle: () => busy === 0,                         // no animation is playing (tests wait for this)
    get runs() { return runs; },                    // how many animations have been started (tests read it)
    reset() { generation += 1; },                   // another workspace: lists forget what they had drawn
    forget,
    enter, fade, swap, reveal, pop, shake, nudge, turn, flash, count, leave,
    view, dialog, toast, hide, menu, message, typing, untyping,
    rows, sync, fill, update, refill, chips, tiles, bars, scanner,
  };
})();
