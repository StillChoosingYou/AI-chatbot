/* Sign-in pages: theme button, show/hide password, Caps Lock hint, a busy button and digits-only PIN. */
(function () {
  'use strict';
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* theme: the same saved choice as the workspace */
  const html = document.documentElement;
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'auth-theme'; button.setAttribute('aria-label', 'Toggle dark mode');
  const paint = () => { button.textContent = html.getAttribute('data-theme') === 'dark' ? '☀️' : '🌙'; };
  button.onclick = () => {
    const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch { /* not kept */ }
    paint();
    if (!reduced) { button.classList.remove('turn'); void button.offsetWidth; button.classList.add('turn'); }
  };
  paint();
  document.body.appendChild(button);

  document.querySelectorAll('input[type="password"]').forEach((input) => {
    const wrap = document.createElement('span');
    wrap.className = 'pw-wrap';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const toggle = document.createElement('button');
    toggle.type = 'button'; toggle.className = 'pw-toggle'; toggle.textContent = '👁'; toggle.setAttribute('aria-label', 'Show');
    toggle.onclick = () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      toggle.textContent = show ? '🙈' : '👁';
      toggle.setAttribute('aria-label', show ? 'Hide' : 'Show');
      input.focus();
    };
    wrap.appendChild(toggle);
    const note = document.createElement('p');
    note.className = 'caps-note'; note.hidden = true; note.textContent = 'Caps Lock is on.';
    wrap.parentNode.insertBefore(note, wrap.nextSibling);
    const caps = (e) => { note.hidden = !(e.getModifierState && e.getModifierState('CapsLock')); };
    input.addEventListener('keydown', caps); input.addEventListener('keyup', caps);
    input.addEventListener('blur', () => { note.hidden = true; });
    if (input.name === 'pin') {                                                  // the PIN is digits only
      input.addEventListener('input', () => { input.value = input.value.replace(/\D/g, '').slice(0, 6); });
    }
  });

  const picture = document.getElementById('cap-img'), again = document.getElementById('cap-refresh');
  if (picture && again) {                                                          // admin captcha: another picture
    again.onclick = () => {
      picture.src = `/admin/captcha.png?${Date.now()}`;
      const input = document.getElementById('cap-input'); input.value = ''; input.focus();
      again.classList.remove('spin'); void again.offsetWidth; again.classList.add('spin');
    };
  }

  document.querySelectorAll('form').forEach((form) => {
    form.addEventListener('submit', () => {
      const submit = form.querySelector('button[type="submit"]');
      if (!submit || submit.disabled) return;
      setTimeout(() => { submit.classList.add('busy'); submit.disabled = true; }, 0);   // after the browser has read the form
    });
  });
  window.addEventListener('pageshow', (e) => {                                    // the back button must not leave a spinning button
    if (e.persisted) document.querySelectorAll('button.busy').forEach((b) => { b.classList.remove('busy'); b.disabled = false; });
  });
})();
