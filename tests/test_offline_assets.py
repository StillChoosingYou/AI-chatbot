"""The page asks for nothing outside this app: no web fonts, no CDN scripts, no analytics.

The chatbot must run with no internet connection (a rule of the project), so every font, script and style the pages
use is a file that ships in static/ and is served by the app itself. These tests read the files and ask the app for
them; a browser would show the same thing, but these run anywhere, in a second.
"""
import hashlib
import re
import unittest
from pathlib import Path

from pg_support import AppTestCase

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'static'
FONTS = {
    'dm-sans-latin-wght-normal.woff2': '9fea608a947e67020c33cad9a6fe3d60c54119dfb8cff87768a8117a15ed7543',
    'manrope-latin-wght-normal.woff2': 'a30ddcd349703aff7464c34bef3fffdff405ee50c113440d7c8693c02d210972',
}


def without_comments(text):
    return re.sub(r'/\*.*?\*/', '', text, flags=re.S)


def stylesheet():
    return without_comments((STATIC / 'style.css').read_text(encoding='utf-8'))


class StylesheetTests(unittest.TestCase):
    def test_it_imports_nothing_and_names_no_other_host(self):
        css = stylesheet()
        self.assertNotIn('@import', css)
        self.assertNotRegex(css, r'url\(\s*[\'"]?(?:https?:)?//')
        self.assertNotIn('googleapis', css)

    def test_every_file_it_points_to_ships_with_the_app(self):
        urls = [u for u in re.findall(r'url\(\s*[\'"]?([^\'")\s]+)', stylesheet()) if not u.startswith(('data:', '#'))]
        self.assertGreaterEqual(len(urls), 2, urls)                      # the two fonts, at least
        for url in urls:
            path = (STATIC / url).resolve()
            self.assertTrue(path.is_file(), f'{url} is not a file in static/')
            self.assertTrue(STATIC.resolve() in path.parents, f'{url} leaves static/')

    def test_both_typefaces_are_declared_with_a_fallback_after_them(self):
        css = stylesheet()
        for family in ('DM Sans', 'Manrope'):
            self.assertRegex(css, r"@font-face\s*\{[^}]*font-family:\s*'%s'" % family)
        self.assertRegex(css, r"font:\s*14px\s*'DM Sans',\s*system-ui")       # a character the font lacks (₱) uses the next one
        self.assertRegex(css, r'font-family:\s*Manrope,\s*system-ui')


class FontFilesTests(unittest.TestCase):
    def test_the_files_are_the_ones_recorded_in_the_readme(self):
        readme = (STATIC / 'vendor' / 'README.md').read_text(encoding='utf-8')
        for name, sha256 in FONTS.items():
            data = (STATIC / 'vendor' / 'fonts' / name).read_bytes()
            self.assertEqual(data[:4], b'wOF2', name)                       # a real WOFF2 file, not an error page
            self.assertGreater(len(data), 10_000, name)
            self.assertEqual(hashlib.sha256(data).hexdigest(), sha256, name)
            self.assertIn(sha256, readme, name)
        self.assertIn('SIL Open Font License', readme)

    def test_the_licenses_ship_with_the_fonts(self):
        for name in ('DM-Sans.OFL.txt', 'Manrope.OFL.txt'):
            text = (STATIC / 'vendor' / 'fonts' / name).read_text(encoding='utf-8')
            self.assertIn('SIL OPEN FONT LICENSE Version 1.1', text, name)
            self.assertIn('Permission is hereby granted', text, name)


class PagesAskForNothingOutsideTests(AppTestCase):
    def test_the_page_names_no_other_host(self):
        page = self.client.get('/').get_data(as_text=True)
        self.assertNotRegex(page, r'(?:src|href|action)\s*=\s*["\'](?:https?:)?//')
        self.assertNotRegex(page, r'url\(\s*[\'"]?(?:https?:)?//')
        self.assertNotIn('googleapis', page)
        self.assertNotIn('gstatic', page)

    def test_the_scripts_do_not_load_anything_from_the_internet(self):
        for name in ('app.js', 'fx.js'):
            code = without_comments((STATIC / name).read_text(encoding='utf-8'))
            code = re.sub(r'//[^\n]*', '', code)
            self.assertNotIn('googleapis', code, name)
            self.assertNotRegex(code, r'''(?:fetch|import)\(\s*['"`]https?://''', name)
            self.assertNotRegex(code, r'''\.src\s*=\s*['"`]https?://''', name)

    def test_the_fonts_and_the_stylesheet_are_served(self):
        for name in FONTS:
            with self.client.get(f'/static/vendor/fonts/{name}') as response:
                self.assertEqual(response.status_code, 200, name)
                self.assertEqual(response.data[:4], b'wOF2', name)
        with self.client.get('/static/style.css') as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn('vendor/fonts/dm-sans-latin-wght-normal.woff2', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
