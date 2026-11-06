// On-campus check: ask the browser for its position, let the server compare it with the campus, then reload.
(function () {
  const button = document.getElementById('campus-check');
  const message = document.getElementById('campus-msg');
  if (!button) return;
  const say = text => { message.textContent = text; };
  button.addEventListener('click', () => {
    if (!navigator.geolocation) { say('This browser cannot share its location. Please use the school Wi-Fi or another browser.'); return; }
    button.disabled = true;
    say('Checking your location…');
    navigator.geolocation.getCurrentPosition(async position => {
      try {
        const response = await fetch('/api/campus/check', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ lat: position.coords.latitude, lng: position.coords.longitude, accuracy: position.coords.accuracy })
        });
        const data = await response.json().catch(() => ({}));
        if (response.ok) { location.reload(); return; }
        say(data.error || 'The check did not pass. Please try again.');
      } catch (error) {
        say('Could not reach the server. Check your connection and try again.');
      }
      button.disabled = false;
    }, error => {
      say(error.code === 1 ? 'Location is blocked. Allow location for this site in your browser settings, then press the button again.'
                           : 'Your location could not be read. Turn on GPS and try again.');
      button.disabled = false;
    }, { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
  });
})();
