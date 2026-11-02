"""Time-based one-time passwords (RFC 6238) for the admin's second sign-in step. Standard library only.

The admin scans a QR code once with any authenticator app (Google Authenticator, Microsoft Authenticator,
Authy, 1Password ...). From then on the app shows a 6-digit code that changes every 30 seconds.

Replay protection: `verify` returns the time step that matched, and the caller stores it; a code from that
step or an earlier one is refused afterwards, so a code that was watched over a shoulder cannot be reused.
Recovery codes are random, shown once, kept only as keyed hashes and each works a single time.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP = 30            # seconds one code lives
DIGITS = 6
WINDOW = 1           # one step either side: tolerates a phone clock that is up to 30 seconds off
RECOVERY_COUNT = 8


def new_secret():
    """20 random bytes as base32 (160 bits, the size RFC 4226 recommends)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def _key(secret):
    text = ''.join(str(secret).split()).upper()
    return base64.b32decode(text + '=' * (-len(text) % 8))


def code_at(secret, counter, digits=DIGITS):
    mac = hmac.new(_key(secret), struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = (struct.unpack('>I', mac[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(value).zfill(digits)


def counter_for(moment=None):
    return int((time.time() if moment is None else moment) // STEP)


def clean(code):
    return ''.join(ch for ch in str(code or '') if ch.isdigit())


def verify(secret, code, last_counter=0, moment=None):
    """The time step the code belongs to, or None. A step at or before `last_counter` is a replay."""
    code = clean(code)
    if len(code) != DIGITS:
        return None
    now = counter_for(moment)
    found = None
    for counter in range(now - WINDOW, now + WINDOW + 1):         # compare every step: no early exit to time
        if hmac.compare_digest(code_at(secret, counter), code) and counter > int(last_counter or 0):
            found = counter
    return found


def uri(secret, account, issuer):
    label = quote(f'{issuer}:{account}', safe='')
    return f'otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe="")}&algorithm=SHA1&digits={DIGITS}&period={STEP}'


def new_recovery_codes():
    return [f'{secrets.token_hex(2)}-{secrets.token_hex(2)}' for _ in range(RECOVERY_COUNT)]


def _norm(code):
    return ''.join(ch for ch in str(code or '').lower() if ch.isalnum())


def recovery_hash(code, key):
    return hmac.new(str(key).encode(), _norm(code).encode(), hashlib.sha256).hexdigest()


def use_recovery(code, hashes, key):
    """(matched, remaining hashes). A matching code is removed so it cannot be used twice."""
    wanted = recovery_hash(code, key)
    if len(_norm(code)) != 8:
        return False, list(hashes)
    hit = None
    for item in hashes:
        if hmac.compare_digest(item, wanted):
            hit = item
    if hit is None:
        return False, list(hashes)
    rest = list(hashes)
    rest.remove(hit)
    return True, rest
