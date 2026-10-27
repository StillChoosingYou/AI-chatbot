"""Coordinator sign-in: one username and one password, kept in the environment and never in the repository.

COORDINATOR_USERNAME   default "Coordinator"
COORDINATOR_PASSWORD   the password. Sign-in is switched on when this is set, and always on Vercel
                       (there, with no password set, nobody can sign in: it fails closed).
SECRET_KEY             optional; signs the sign-in cookie. Without it one is derived from the password.

Locally, with COORDINATOR_PASSWORD unset, the app stays open as before so the tests and a quick demo still work.
"""
import hashlib
import hmac
import os
import time

FAILS_ALLOWED = 5          # wrong tries from one address...
LOCK_SECONDS = 60          # ...then it waits this long. Kept in memory: a best effort per server instance.


class CoordinatorLogin:
    def __init__(self, environ=None, database_url=''):
        environ = os.environ if environ is None else environ
        self.password = environ.get('COORDINATOR_PASSWORD', '')
        self.username = (environ.get('COORDINATOR_USERNAME', '').strip() or 'Coordinator')
        self.on_vercel = bool(environ.get('VERCEL'))
        self.enabled = bool(self.password) or self.on_vercel
        self.secret = environ.get('SECRET_KEY', '') or hashlib.sha256(
            f'school-id|{database_url}|{self.password}'.encode()).hexdigest()
        self.failures = {}                       # address -> (wrong tries, time of the last one)

    @property
    def configured(self):
        return bool(self.password)

    def locked(self, address, now=None):
        tries, last = self.failures.get(address, (0, 0))
        now = time.time() if now is None else now
        if tries >= FAILS_ALLOWED and now - last < LOCK_SECONDS:
            return True
        if tries >= FAILS_ALLOWED:
            self.failures.pop(address, None)
        return False

    def check(self, username, password, address, now=None):
        """True for the right pair. Wrong tries are counted; too many lock the address for a minute."""
        now = time.time() if now is None else now
        if not self.configured or self.locked(address, now):
            return False
        same_user = hmac.compare_digest(str(username).strip().lower().encode(), self.username.lower().encode())
        same_password = hmac.compare_digest(str(password).encode(), self.password.encode())
        if same_user and same_password:
            self.failures.pop(address, None)
            return True
        tries, _ = self.failures.get(address, (0, 0))
        self.failures[address] = (tries + 1, now)
        return False
