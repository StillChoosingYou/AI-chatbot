"""Sample ID photos: a simple illustrated portrait that is the same every time for the same student.

These are drawings, not photographs of real people. The records have no photo column, so the scan page uses this until
real photos are added.
"""
import hashlib

BACKGROUNDS = ('#dbe6fb', '#fdf0c8', '#e3f2e9', '#f6e0e6', '#e6e3f6', '#dff1f3')
SKINS = ('#f2c9a5', '#e0a97d', '#c68863', '#a86b49', '#8a5538')
HAIRS = ('#1b1b1f', '#3a2616', '#5b3a21', '#8b5a2b', '#222a44')
SHIRT = '#cfe0f5'                                   # the light blue school shirt
TIES = ('#14367f', '#14367f', '#1f4aa8')


def _pick(digest, index, options):
    return options[digest[index] % len(options)]


def avatar_svg(seed):
    """A 100x100 SVG portrait chosen by hashing `seed` (the student's token)."""
    digest = hashlib.sha256(str(seed).encode('utf-8')).digest()
    background, skin = _pick(digest, 0, BACKGROUNDS), _pick(digest, 1, SKINS)
    hair, tie = _pick(digest, 2, HAIRS), _pick(digest, 3, TIES)
    style = digest[4] % 4
    hair_shapes = (
        f'<path d="M30 42c0-18 9-28 20-28s20 10 20 28c-4-9-10-13-20-13s-16 4-20 13z" fill="{hair}"/>',                 # short
        f'<path d="M28 48c-3-22 8-34 22-34s25 12 22 34c-2-12-8-20-22-20s-20 8-22 20z" fill="{hair}"/>'
        f'<path d="M26 48c0 14 3 22 6 26-1-8 0-16 2-24z M74 48c0 14-3 22-6 26 1-8 0-16-2-24z" fill="{hair}"/>',        # long
        f'<path d="M29 40c1-16 10-26 21-26 13 0 21 10 21 26-6-6-12-8-21-6-7-4-15-2-21 6z" fill="{hair}"/>',             # side part
        f'<circle cx="50" cy="26" r="23" fill="{hair}"/><circle cx="50" cy="46" r="19" fill="{skin}"/>',                # curly / rounded
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" role="img" aria-label="Sample student photo">'
        f'<rect width="100" height="100" fill="{background}"/>'
        f'<path d="M10 100c0-22 17-32 40-32s40 10 40 32z" fill="{SHIRT}"/>'
        f'<path d="M43 68l7 9 7-9z" fill="#fff"/><path d="M47 77l3-6 3 6-3 14z" fill="{tie}"/>'
        f'<rect x="43" y="56" width="14" height="14" rx="5" fill="{skin}"/>'
        f'<ellipse cx="50" cy="46" rx="19" ry="22" fill="{skin}"/>'
        f'{hair_shapes[style]}'
        f'<circle cx="43" cy="47" r="2" fill="#2b2b2b"/><circle cx="57" cy="47" r="2" fill="#2b2b2b"/>'
        f'<path d="M44 56q6 5 12 0" stroke="#7a3b2e" stroke-width="2" fill="none" stroke-linecap="round"/>'
        '</svg>')
