"""The chatbot as a WSGI object called `app`, for hosts that look for one: Vercel, gunicorn, `flask run`.

The app itself is built by create_app() in Chatbot.py. On your own computer keep starting it with
`python Chatbot.py`; nothing here changes that.

What this file adds for a hosted copy:

* The top-level `app = ...` line at the bottom is what Vercel looks for. It has to stay a plain top-level
  assignment: Vercel does not see an `app` that is assigned inside try/except, if or def.
* The server's clock decides "today", "overdue" and every time the app writes. Hosts run on UTC, eight hours
  behind the school, so the clock is set to Philippine time here. SCHOOL_TZ changes it (a TZ string such as
  Asia/Manila, or UTC). Windows keeps its own clock zone.
* If the app cannot start (DATABASE_URL is not set yet, the database is unreachable or still waking up),
  `app` still answers. Every request gets a 503 page that says what to check, and the real app is started
  again on a later request (at most one attempt every RETRY_SECONDS), so a short database outage does not
  leave the site broken until the host recycles the process. The exact reason goes to the server log, never
  to the page, because it can contain the database's address and user name.
"""
import json
import logging
import os
import threading
import time

from flask import Flask

from Chatbot import create_app

RETRY_SECONDS = 5            # the stand-in tries to start the real app again at most this often
log = logging.getLogger('school-id.start')

NOT_RUNNING = ('The chatbot is not running yet: the app could not start. '
               'Check that DATABASE_URL is set and the database is up; the deployment logs say which.')
PAGE = '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chatbot not running yet</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; font: 16px/1.55 system-ui, sans-serif; }
  main { max-width: 34rem; margin: 1.5rem; padding: 1.5rem 1.75rem; border: 1px solid #8884; border-radius: 12px; }
  h1 { margin: 0 0 .5rem; font-size: 1.35rem; }
  code { padding: .1em .35em; border-radius: 4px; background: #8882; }
  li { margin: .35rem 0; }
  .note { margin-bottom: 0; opacity: .75; font-size: .92rem; }
</style>
</head>
<body>
<main>
  <h1>The chatbot is not running yet</h1>
  <p>This site is deployed, but the app could not start. The usual reason is that it has no database.</p>
  <ol>
    <li>Create a PostgreSQL database and copy its connection string.</li>
    <li>Add it to this deployment's environment variables as <code>DATABASE_URL</code>.</li>
    <li>Redeploy. Environment variable changes only reach new deployments.</li>
  </ol>
  <p class="note">Done that already? The database may still be waking up: reload in a few seconds.
  The exact reason is in the deployment's runtime logs.</p>
</main>
</body>
</html>
'''


def set_school_clock():
    """Make datetime.now() read school time: Philippine time unless SCHOOL_TZ names another zone.

    The default is a plain offset ("PHT-8" means UTC+8), so it works on a host with no time zone database.
    Windows has no tzset(); there the computer's own clock zone stays in charge.
    """
    if hasattr(time, 'tzset'):
        os.environ['TZ'] = os.environ.get('SCHOOL_TZ', '').strip() or 'PHT-8'
        time.tzset()


def unavailable(environ, start_response):
    """The answer to every request while the real app is not running: 503, JSON under /api/, else a page."""
    if environ.get('PATH_INFO', '').startswith('/api/'):
        payload, kind = json.dumps({'error': NOT_RUNNING}).encode('utf-8'), 'application/json'
    else:
        payload, kind = PAGE.encode('utf-8'), 'text/html; charset=utf-8'
    start_response('503 Service Unavailable', [
        ('Content-Type', kind), ('Content-Length', str(len(payload))), ('Retry-After', str(RETRY_SECONDS)),
        ('Cache-Control', 'no-store'), ('Referrer-Policy', 'no-referrer'), ('X-Content-Type-Options', 'nosniff')])
    return [] if environ.get('REQUEST_METHOD') == 'HEAD' else [payload]


class Waiting:
    """The stand-in's WSGI entry: 503 until create_app() works, then the real app answers everything."""

    def __init__(self, stand_in):
        self.stand_in = stand_in
        self.lock = threading.Lock()
        self.tried = time.monotonic()

    def __call__(self, environ, start_response):
        real = self.try_again()
        if real is None:
            return unavailable(environ, start_response)
        return real.wsgi_app(environ, start_response)

    def try_again(self):
        """Start the real app if the last attempt was long enough ago and no other request is trying right now."""
        if time.monotonic() - self.tried < RETRY_SECONDS or not self.lock.acquire(blocking=False):
            return None
        try:
            real = create_app()
        except Exception as error:
            log.warning('The chatbot still cannot start: %s', str(error).strip())
            self.tried = time.monotonic()
            return None
        else:
            self.stand_in.wsgi_app = real.wsgi_app           # from now on this object is the app
            return real
        finally:
            self.lock.release()


def start():
    """The real app, or a Flask stand-in that answers 503 and keeps trying to start the real one."""
    try:
        return create_app()
    except Exception:
        log.exception('The chatbot could not start; answering 503 and trying again on later requests.')
    stand_in = Flask(__name__, static_folder=None)
    stand_in.wsgi_app = Waiting(stand_in)
    return stand_in


set_school_clock()
app = start()
