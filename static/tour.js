/* A guided tour for new users, shared by the admin panel, the coordinator workspace and the student page.

   tour.start(steps, { key })   run a tour now (the "Tutorial" button)
   tour.auto(key, steps)        run it once for a new user: it remembers on this device that it was seen
   tour.seen(key) / tour.forget(key)

   A step: { target, title, text, mode, wait, before }
     target  a selector, a list of selectors (the first one that is on screen) or a function. No target = a centred card.
     mode    'point' (default): an animated hand points at it, the user presses Next.
             'click': Guided Click. The page is dimmed except the target; the hand taps it and the step moves on
             when the user clicks it. "Show me" lets the hand do the click.
     wait    a selector to wait for before the step starts (a page that is still loading)
     before  an async function that runs first (open a page, for example)
   A step whose target never appears is skipped, so a tour never gets stuck. Escape ends it.  */
(function () {
  'use strict';

  const SEEN = (key) => `tour-v1:${key}`;
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches || document.documentElement.classList.contains('reduce-motion');
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const HAND = '<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M11 3.2c1.2 0 2.2 1 2.2 2.2v8.9l1.3-.4c1.1-.3 2.2.3 2.5 1.3.9-.4 2 .1 2.5 1 1-.3 2.1.3 2.4 1.3l.9 4.4c.3 1.7-.1 3.4-1.2 4.8l-1.3 1.6H12.4L7 21.1c-.8-1-.6-2.4.4-3.2.8-.6 1.7-.6 2.5-.1l.9.7V5.4c0-1.2 1-2.2 2.2-2.2z" fill="#fff" stroke="#16213d" stroke-width="1.6" stroke-linejoin="round"/><path d="M17 15.5v4M20.2 16.6v3.6" stroke="#16213d" stroke-width="1.3" stroke-linecap="round"/></svg>';
  const TIP = { x: 11, y: 3.5 };                                   // the fingertip inside the 32 x 32 drawing

  const visible = (el) => {
    if (!el || !el.getClientRects().length) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
  };
  function find(target) {
    if (!target) return null;
    const list = typeof target === 'function' ? [target()] : [].concat(target);
    for (const item of list) {
      const el = typeof item === 'string' ? document.querySelector(item) : item;
      if (visible(el)) return el;
    }
    return null;
  }
  async function until(target, ms) {
    const end = Date.now() + ms;
    for (;;) {
      const el = find(target);
      if (el || Date.now() > end) return el;
      await wait(80);
    }
  }

  let run = null;                                                  // the tour that is on screen

  function start(steps, { key = '', onEnd } = {}) {
    if (run) run.end(false);
    const total = steps.length;
    const root = document.createElement('div');
    root.className = 'tour' + (reduced() ? ' still' : '');
    root.innerHTML = '<div class="tour-block"></div><div class="tour-hole"></div>'
      + '<div class="tour-card" role="dialog" aria-modal="true" aria-live="polite" tabindex="-1"></div>'
      + `<div class="tour-hand" aria-hidden="true">${HAND}<i class="tour-ripple"></i></div>`;
    document.body.appendChild(root);
    document.body.classList.add('touring');
    const block = root.querySelector('.tour-block'), hole = root.querySelector('.tour-hole');
    const card = root.querySelector('.tour-card'), hand = root.querySelector('.tour-hand');

    let index = -1, spec = null, el = null, mode = 'point', token = 0, raf = 0, handAt = null, handTimer = 0, last = '';
    const pad = 7;

    const rectOf = (node) => {
      const r = node.getBoundingClientRect();
      return { x: r.left - pad, y: r.top - pad, w: r.width + pad * 2, h: r.height + pad * 2 };
    };

    /* the dim layer, the hole in it and the clickable gap follow the target every frame */
    function track() {
      raf = requestAnimationFrame(track);
      if (el && !el.isConnected && spec) el = find(spec) || el;           // the page redrew itself: find the target again
      let r = null;
      if (el && !visible(el)) { /* the target went away: stay where we were */ } else if (el) r = rectOf(el);
      const view = r || { x: innerWidth / 2, y: innerHeight / 2, w: 0, h: 0 };
      const sig = `${view.x | 0},${view.y | 0},${view.w | 0},${view.h | 0},${mode}`;
      if (sig === last) return;
      last = sig;
      hole.style.cssText = `transform:translate(${view.x}px,${view.y}px);width:${view.w}px;height:${view.h}px;`
        + `opacity:${r ? 1 : 0}`;
      if (r && mode === 'click') {
        const x2 = view.x + view.w, y2 = view.y + view.h;
        block.style.clipPath = `polygon(evenodd,0 0,100% 0,100% 100%,0 100%,0 0,${view.x}px ${view.y}px,${view.x}px ${y2}px,${x2}px ${y2}px,${x2}px ${view.y}px,${view.x}px ${view.y}px)`;
      } else block.style.clipPath = 'none';
      if (r) { placeCard(view); aim(view); }
    }

    function placeCard(view) {
      const w = Math.min(340, innerWidth - 24), h = card.offsetHeight || 190;
      card.style.width = w + 'px';
      let x, y, side = 'center';
      if (!el) { x = (innerWidth - w) / 2; y = (innerHeight - h) / 2; }
      else if (innerWidth < 640) {                                   // phones: a sheet at the edge away from the target
        x = 12; side = 'sheet';
        y = view.y + view.h / 2 > innerHeight * 0.5 ? 12 : innerHeight - h - 12;
      } else {
        const gap = 18, roomBelow = innerHeight - (view.y + view.h), roomAbove = view.y;
        const roomRight = innerWidth - (view.x + view.w), roomLeft = view.x;
        if (roomBelow >= h + gap) { side = 'below'; y = view.y + view.h + gap; x = view.x + view.w / 2 - w / 2; }
        else if (roomAbove >= h + gap) { side = 'above'; y = view.y - h - gap; x = view.x + view.w / 2 - w / 2; }
        else if (roomRight >= w + gap) { side = 'right'; x = view.x + view.w + gap; y = view.y + view.h / 2 - h / 2; }
        else if (roomLeft >= w + gap) { side = 'left'; x = view.x - w - gap; y = view.y + view.h / 2 - h / 2; }
        else { side = 'center'; x = (innerWidth - w) / 2; y = Math.max(12, innerHeight - h - 16); }
      }
      x = Math.max(12, Math.min(innerWidth - w - 12, x));
      y = Math.max(12, Math.min(innerHeight - h - 12, y));
      card.dataset.side = side;
      card.style.transform = `translate(${x}px,${y}px)`;
      const arrow = el ? Math.max(18, Math.min(w - 18, view.x + view.w / 2 - x)) : 0;
      card.style.setProperty('--arrow', arrow + 'px');
    }

    /* the hand glides to the middle of the target; once there it taps (and keeps tapping) */
    function aim(view) {
      const tx = view.x + view.w / 2 - TIP.x * 1.4 + (view.w > 220 ? 0 : 6), ty = view.y + view.h / 2 - TIP.y * 1.4 + Math.min(view.h / 4, 10);
      const key2 = `${tx | 0},${ty | 0}`;
      if (key2 === handAt) return;
      const first = handAt === null;
      handAt = key2;
      hand.classList.remove('tap', 'show');
      if (first) hand.style.transform = `translate(${innerWidth - 90}px,${innerHeight - 60}px) scale(.8)`;
      void hand.offsetWidth;
      hand.classList.add('show');
      hand.style.transform = `translate(${tx}px,${ty}px)`;
      clearTimeout(handTimer);
      handTimer = setTimeout(() => hand.classList.add('tap'), root.classList.contains('still') ? 0 : 900);
    }

    const dots = (n) => Array.from({ length: total }, (_, i) => `<i class="${i < n ? 'done' : i === n ? 'now' : ''}"></i>`).join('');
    function paint(step, n) {
      const last_ = n === total - 1, first = n === 0;
      const click = mode === 'click';
      card.innerHTML = `<div class="tour-top"><span class="tour-count">Step ${n + 1} of ${total}</span>`
        + `<button type="button" class="tour-x" data-act="end" aria-label="Skip the tutorial">Skip</button></div>`
        + `<div class="tour-dots" aria-hidden="true">${dots(n)}</div>`
        + `<h3></h3><p></p>`
        + (click ? '<p class="tour-do"><span aria-hidden="true">👆</span> Click the highlighted spot to continue.</p>' : '')
        + `<div class="tour-btns">${first ? '' : '<button type="button" data-act="back">Back</button>'}`
        + (click ? '<button type="button" class="primary" data-act="show">Show me</button>'
          : `<button type="button" class="primary" data-act="next">${last_ ? 'Finish' : first && !step.target ? 'Start the tour' : 'Next'}</button>`)
        + '</div><i class="tour-arrow"></i>';
      card.querySelector('h3').textContent = step.title || '';
      card.querySelector('p').textContent = step.text || '';
      card.classList.remove('swap'); void card.offsetWidth; card.classList.add('swap');
    }

    async function go(n, dir = 1) {
      const mine = ++token;
      if (n < 0) n = 0;
      if (n >= total) return finish(true);
      const step = steps[n];
      el = null; last = '';
      hand.classList.remove('tap');
      try { if (step.before) await step.before(); } catch { /* the step still tries to show */ }
      if (mine !== token) return;
      if (step.wait) await until(step.wait, 3000);
      if (mine !== token) return;
      let found = null;
      if (step.target) {
        found = await until(step.target, step.wait || step.before || step.mode === 'click' ? 2500 : 500);
        if (mine !== token) return;
        if (!found) return go(n + dir, dir);                                    // not on this screen: skip the step
      }
      index = n; spec = step.target; el = found; mode = found && step.mode === 'click' ? 'click' : 'point';
      if (found) {
        const r = found.getBoundingClientRect();
        if (r.top < 0 || r.bottom > innerHeight - 70) { found.scrollIntoView({ block: 'center', behavior: root.classList.contains('still') ? 'auto' : 'smooth' }); await wait(root.classList.contains('still') ? 0 : 420); }
      }
      if (mine !== token) return;
      paint(step, n);
      last = '';
      if (!found) { handAt = null; hand.classList.remove('show', 'tap'); }
      card.focus({ preventScroll: true });
    }

    /* Guided Click: the user's own click on the target moves the tour on (after the page reacted to it) */
    function onClick(e) {
      if (mode !== 'click' || !el || !el.contains(e.target)) return;
      const mine = token;
      hand.classList.add('press');
      setTimeout(() => { hand.classList.remove('press'); if (mine === token) go(index + 1); }, 420);
    }
    function showMe() {
      if (!el) return;
      const target = el;
      hand.classList.remove('tap'); void hand.offsetWidth; hand.classList.add('press');
      setTimeout(() => { hand.classList.remove('press'); target.click(); }, 450);
    }
    document.addEventListener('click', onClick, true);

    card.addEventListener('click', (e) => {
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (act === 'next') go(index + 1);
      else if (act === 'back') go(index - 1, -1);
      else if (act === 'show') showMe();
      else if (act === 'end') finish(false);
    });

    function onKey(e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); finish(false); }
      else if ((e.key === 'ArrowRight' || e.key === 'Enter') && mode === 'point' && !e.target.closest?.('[data-act="end"], [data-act="back"]')) { e.preventDefault(); e.stopImmediatePropagation(); go(index + 1); }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); e.stopImmediatePropagation(); go(index - 1, -1); }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); e.stopImmediatePropagation(); }
    }
    document.addEventListener('keydown', onKey, true);

    function finish(completed) {
      token++;
      cancelAnimationFrame(raf); clearTimeout(handTimer);
      document.removeEventListener('click', onClick, true);
      document.removeEventListener('keydown', onKey, true);
      if (key) { try { localStorage.setItem(SEEN(key), '1'); } catch { /* the tour may show again */ } }
      root.classList.add('leaving');
      document.body.classList.remove('touring');
      setTimeout(() => root.remove(), 260);
      if (run && run.root === root) run = null;
      if (window.ux && ux.toast) ux.toast(completed ? 'Tutorial done. You can replay it any time from the ? Tutorial button.' : 'Tutorial closed. Replay it any time from the ? Tutorial button.', { kind: 'info', ms: 4500 });
      if (onEnd) onEnd(completed);
    }

    run = { root, end: finish };
    requestAnimationFrame(() => root.classList.add('on'));
    raf = requestAnimationFrame(track);
    go(0);
    return run;
  }

  /* once per user and device: starts a moment after the page has settled, never over a dialog */
  function auto(key, steps, delay = 1400) {
    try { if (localStorage.getItem(SEEN(key))) return; } catch { return; }          // cannot remember it: do not nag
    setTimeout(function again(tries = 0) {
      if (run) return;
      if (window.ux && ux.busy && tries < 20) return setTimeout(() => again(tries + 1), 600);
      start(steps, { key });
    }, delay);
  }

  window.tour = {
    start, auto,
    seen: (key) => { try { return !!localStorage.getItem(SEEN(key)); } catch { return false; } },
    forget: (key) => { try { localStorage.removeItem(SEEN(key)); } catch { /* nothing to forget */ } },
    get active() { return !!run; },
  };
})();
