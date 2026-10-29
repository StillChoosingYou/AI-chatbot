"""Student sign-in: Student ID + a PIN the office issues.

STUDENT_LOGIN=1    switches it on (off by default, so nothing locks out until the office has issued PINs).

The PIN is 6 digits, made by the coordinator's dashboard ("Issue PIN" in a student's row menu), shown once,
and stored only as a salted hash. A wrong PIN is counted on the account itself in the database, so the lock
holds across server instances: 5 wrong tries lock that account for 15 minutes. Whoever is signed in can see
only their own record in the chat.
"""
import secrets
import time

from werkzeug.security import check_password_hash, generate_password_hash

PIN_LENGTH = 6
FAILS_ALLOWED = 5
LOCK_SECONDS = 15 * 60
HASH_METHOD = 'pbkdf2:sha256:260000'
_DUMMY_HASH = generate_password_hash('000000', method=HASH_METHOD)      # compared when the ID is unknown, so timing does not tell


def new_pin():
    return ''.join(secrets.choice('0123456789') for _ in range(PIN_LENGTH))


def hash_pin(pin):
    return generate_password_hash(pin, method=HASH_METHOD)


def pin_matches(pin, stored_hash):
    check_password_hash(stored_hash or _DUMMY_HASH, str(pin))
    return bool(stored_hash) and check_password_hash(stored_hash, str(pin))


def seconds_locked(locked_until, now=None):
    now = time.time() if now is None else now
    return max(0, int(locked_until - now) + (1 if locked_until > now else 0))
