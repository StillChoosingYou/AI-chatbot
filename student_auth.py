"""Student sign-in: Student ID + a PIN the office issues.

Sign-in is always required. STUDENT_LOGIN=0 switches it off (local demos only).

The PIN is 6 digits, made by the coordinator's dashboard ("Issue PIN" in a student's row menu), shown once,
and stored only as a salted hash. A wrong PIN is counted on the account itself in the database, so the lock
holds across server instances: 5 wrong tries lock that account for 15 minutes. Whoever is signed in can see
only their own record in the chat.
"""
import base64
import hashlib
import os
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken

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


def pin_problem(pin):
    """Why a PIN a student chose is not allowed, or '' when it is fine: 6 digits, not one repeated digit, not a run."""
    pin = str(pin)
    if len(pin) != PIN_LENGTH or not pin.isascii() or not pin.isdigit():
        return f'The PIN must be exactly {PIN_LENGTH} digits.'
    if len(set(pin)) == 1:
        return 'Choose a PIN that is not one digit repeated.'
    steps = {int(b) - int(a) for a, b in zip(pin, pin[1:])}
    if steps in ({1}, {-1}):
        return 'Choose a PIN that is not a run like 123456.'
    return ''


# ─── the PIN vault ───
# The office can see the PIN it issued (admin Accounts table). It is kept encrypted, separate from the sign-in hash,
# and cleared the moment the student chooses their own PIN: a PIN a student picked is never readable by anyone.

def _vault(secret):
    key = os.environ.get('PIN_VAULT_KEY', '') or str(secret)
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(('pin-vault|' + key).encode()).digest()))


def seal_pin(pin, secret):
    return _vault(secret).encrypt(str(pin).encode()).decode()


def open_pin(token, secret):
    """The issued PIN, or '' when there is none (empty, cleared by the student, or the key changed)."""
    if not token:
        return ''
    try:
        return _vault(secret).decrypt(str(token).encode()).decode()
    except (InvalidToken, ValueError):
        return ''
