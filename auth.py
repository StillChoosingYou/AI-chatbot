"""Coordinator sign-in: one username and one password, kept in the environment and never in the repository.

COORDINATOR_USERNAME   default "Coordinator"
COORDINATOR_PASSWORD   the password (the admin can replace it from the panel with a PIN of their own, kept as a hash in the database). Sign-in is switched on when this is set, and always on Vercel
                       (there, with no password set, nobody can sign in: it fails closed).
SECRET_KEY             optional; signs the sign-in cookie. Without it one is derived from the password.

Locally, with COORDINATOR_PASSWORD unset, the app stays open as before so the tests and a quick demo still work.
"""
import hashlib
import hmac
import os
import time

from werkzeug.security import check_password_hash

FAILS_ALLOWED = 5          # wrong tries from one address...
LOCK_SECONDS = 60          # ...then it waits this long. Kept in memory: a best effort per server instance.


class CoordinatorLogin:
    PREFIX, DEFAULT_USER = 'COORDINATOR', 'Coordinator'

    def __init__(self, environ=None, database_url=''):
        environ = os.environ if environ is None else environ
        self.password = environ.get(f'{self.PREFIX}_PASSWORD', '')
        self.username = (environ.get(f'{self.PREFIX}_USERNAME', '').strip() or self.DEFAULT_USER)
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

    def check(self, username, password, address, now=None, stored_hash=''):
        """True for the right pair. Wrong tries are counted; too many lock the address for a minute.
        stored_hash is a PIN the admin set in the panel: while there is one, it replaces the environment password."""
        now = time.time() if now is None else now
        if not (self.configured or stored_hash) or self.locked(address, now):
            return False
        same_user = hmac.compare_digest(str(username).strip().lower().encode(), self.username.lower().encode())
        if stored_hash:
            same_password = check_password_hash(stored_hash, str(password))
        else:
            same_password = hmac.compare_digest(str(password).encode(), self.password.encode())
        if same_user and same_password:
            self.failures.pop(address, None)
            return True
        tries, _ = self.failures.get(address, (0, 0))
        self.failures[address] = (tries + 1, now)
        return False



class AdminLogin(CoordinatorLogin):
    """The admin panel's own sign-in: ADMIN_USERNAME (default "Admin") and ADMIN_PASSWORD, kept in the environment.
    Same rules as the coordinator's, and a separate password: knowing one opens nothing of the other."""
    PREFIX, DEFAULT_USER = 'ADMIN', 'Admin'
