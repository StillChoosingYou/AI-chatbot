"""A self-made captcha for the admin sign-in: distorted letters in a PNG, no third-party service.

The answer never reaches the browser. The session cookie (signed, readable) holds only a keyed hash of the answer
and a nonce, so the letters cannot be read from it or brute-forced without the server's secret key.
"""
import hashlib
import hmac
import io
import random
import secrets

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'        # no O/0, I/1/L: letters people mix up
LENGTH = 5
WIDTH, HEIGHT = 230, 84
_RAND = random.SystemRandom()


def new_code():
    return ''.join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def seal(code, nonce, secret):
    return hmac.new(str(secret).encode(), f'captcha|{nonce}|{code}'.encode(), hashlib.sha256).hexdigest()


def matches(answer, sealed, nonce, secret):
    """True when the typed letters are the pictured ones (case and spaces do not matter)."""
    typed = ''.join(str(answer).split()).upper()[:12]
    return bool(sealed) and hmac.compare_digest(seal(typed, nonce, secret), str(sealed))


def render(code):
    """The picture, as PNG bytes: rotated letters of mixed size and colour, crossing lines, speckle, a little blur."""
    image = Image.new('RGB', (WIDTH, HEIGHT), (244, 246, 252))
    draw = ImageDraw.Draw(image)
    for _ in range(5):                                                      # soft background bands
        x = _RAND.randint(-40, WIDTH)
        draw.ellipse((x, _RAND.randint(-30, 20), x + _RAND.randint(60, 140), _RAND.randint(50, 110)),
                     fill=(_RAND.randint(215, 238), _RAND.randint(220, 240), _RAND.randint(232, 250)))
    slot = WIDTH / (len(code) + 0.6)
    for i, char in enumerate(code):
        size = _RAND.randint(38, 50)
        font = ImageFont.load_default(size=size)
        tile = Image.new('RGBA', (size + 24, size + 24), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((10, 4), char, font=font,
                                  fill=(_RAND.randint(10, 80), _RAND.randint(20, 90), _RAND.randint(80, 170), 255))
        tile = tile.rotate(_RAND.randint(-28, 28), resample=Image.BICUBIC, expand=True)
        image.paste(tile, (int(10 + i * slot + _RAND.randint(-3, 5)), _RAND.randint(2, 18)), tile)
    for _ in range(6):                                                      # lines through the letters
        draw.line([(_RAND.randint(0, 40), _RAND.randint(8, HEIGHT - 8)), (_RAND.randint(WIDTH - 40, WIDTH), _RAND.randint(8, HEIGHT - 8))],
                  fill=(_RAND.randint(60, 150), _RAND.randint(60, 150), _RAND.randint(110, 200)), width=_RAND.randint(1, 2))
    for _ in range(220):
        draw.point((_RAND.randint(0, WIDTH - 1), _RAND.randint(0, HEIGHT - 1)), fill=(_RAND.randint(60, 190),) * 3)
    image = image.filter(ImageFilter.GaussianBlur(0.55))
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()
