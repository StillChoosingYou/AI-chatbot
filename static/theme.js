/* Sets the saved colour theme before the page paints, so there is no white flash.
   Kept as a file (not inline) so the Content-Security-Policy can refuse every inline script.
   data-system="1" on the tag: with nothing saved, follow the device's light/dark setting. */
(function () {
  try {
    var follow = document.currentScript && document.currentScript.dataset.system === '1';
    var saved = localStorage.getItem('theme');
    var theme = saved || (follow ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : '');
    if (theme) document.documentElement.setAttribute('data-theme', theme);
  } catch (e) { /* storage blocked: the default theme stays */ }
})();
