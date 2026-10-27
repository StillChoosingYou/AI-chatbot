"""Animations: Motion (the engine behind Framer Motion) is vendored, loaded from this app, and optional.

The browser behaviour is checked by hand and with the browser suites; these tests guard what a browser cannot show:
that the files exist and are the ones recorded in the vendor README, that nothing is loaded from the internet (the
chatbot must run offline), that the page loads the scripts in the right order, and that every `fx.something()` the
page calls is something `fx.js` really defines (a typo there would stop the page).
"""
import hashlib
import re
import unittest
from pathlib import Path

from Chatbot import BUILD
from pg_support import AppTestCase

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / 'static'
MOTION_SHA256 = '5cd165acfdf8e14ca5358c3d876142e5465419ee0d100bcf195da474cf8db2bf'      # also in static/vendor/README.md


def source(name):
    return (STATIC / name).read_text(encoding='utf-8')


def exported_names():
    """The names fx.js puts on window.fx, read from its source (no JavaScript engine needed)."""
    text = source('fx.js')
    block = re.search(r'window\.fx = \{(.*?)\n  \};', text, re.S).group(1)
    block = re.sub(r'//[^\n]*', '', block)
    return set(re.findall(r'(?:get\s+)?\b(\w+)\s*(?::|\(|,|$)', block, re.M))


class VendoredMotionTests(unittest.TestCase):
    def test_the_vendored_file_is_the_one_in_the_readme(self):
        data = (STATIC / 'vendor' / 'motion.js').read_bytes()
        self.assertGreater(len(data), 100_000)
        self.assertEqual(hashlib.sha256(data).hexdigest(), MOTION_SHA256)
        readme = source('vendor/README.md')
        self.assertIn(MOTION_SHA256, readme)
        self.assertIn('motion-14.6.0', readme)
        self.assertIn('MIT', readme)

    def test_it_defines_the_global_the_page_uses(self):
        text = source('vendor/motion.js')
        self.assertIn('Motion={}', text)                      # the UMD wrapper assigns window.Motion
        for name in ('animate', 'stagger', 'spring'):
            self.assertIn(f't.{name}=', text)

    def test_it_never_reaches_the_network_or_storage(self):
        text = source('vendor/motion.js')
        for word in ('fetch(', 'XMLHttpRequest', 'sendBeacon', 'WebSocket', 'importScripts', 'eval(', 'new Function',
                     'localStorage', 'sessionStorage', 'indexedDB', 'document.cookie'):
            self.assertNotIn(word, text, word)

    def test_the_license_ships_with_it(self):
        license_text = source('vendor/motion.LICENSE.txt')
        self.assertIn('MIT License', license_text)
        self.assertIn('Permission is hereby granted', license_text)


class MotionLayerTests(unittest.TestCase):
    def test_fx_makes_no_requests_and_loads_nothing(self):
        text = re.sub(r'/\*.*?\*/', '', source('fx.js'), flags=re.S)         # comments may name the website
        text = re.sub(r'//[^\n]*', '', text)
        for word in ('fetch(', 'XMLHttpRequest', 'sendBeacon', 'WebSocket', 'importScripts', 'import(', 'http://', 'https://',
                     'createElement(\'script\')', 'eval('):
            self.assertNotIn(word, text, word)

    def test_it_is_switched_off_by_reduced_motion_and_by_a_stored_switch(self):
        text = source('fx.js')
        self.assertIn('(prefers-reduced-motion: reduce)', text)
        self.assertIn("localStorage.getItem('motion') === 'off'", text)
        self.assertIn("classList.toggle('fx', active())", text)                # the stylesheet's fallbacks key off this

    def test_every_call_in_the_page_exists_in_fx(self):
        exported = exported_names()
        self.assertTrue({'enter', 'dialog', 'message', 'toast', 'view', 'rows', 'tiles'} <= exported, exported)
        used = set(re.findall(r'\bfx\.(\w+)\(', source('app.js')))
        self.assertTrue(used, 'app.js should use fx')
        self.assertEqual(sorted(used - exported), [], 'app.js calls fx functions that fx.js does not define')

    def test_app_js_still_works_when_fx_is_missing(self):
        text = source('app.js')
        self.assertIn('const fx = window.fx || new Proxy({}, { get: () => () => Promise.resolve() });', text)

    def test_the_stylesheet_gives_way_to_motion_only_under_the_fx_class(self):
        css = source('style.css')
        for selector in ('html.fx .message', 'html.fx #toast', 'html.fx .bulk-toolbar', 'html.fx .row-menu',
                         'html.fx .typing-indicator', 'html.fx .typing-dot', 'html.fx button.tile:hover'):
            self.assertIn(selector, css, selector)
        # the plain rules are still there, for a page without Motion
        for rule in ('animation: fadeIn 0.3s ease-out', 'animation: slideUp 0.3s ease-out', 'animation: slideDown 0.2s ease-out'):
            self.assertIn(rule, css, rule)


class PageLoadsMotionTests(AppTestCase):
    def test_scripts_load_in_order_and_only_from_this_app(self):
        page = self.client.get('/').get_data(as_text=True)
        scripts = re.findall(r'<script\b[^>]*\bsrc="([^"]+)"[^>]*>', page)
        self.assertEqual(scripts, ['/static/vendor/motion.js', '/static/fx.js', '/static/app.js'])
        for tag in re.findall(r'<script\b[^>]*\bsrc="[^"]+"[^>]*>', page):
            self.assertIn(' defer', tag, tag)                  # deferred scripts run in this order, after the page is read
        self.assertNotRegex(page, r'(?:src|href)="https?://')

    def test_every_script_is_served(self):
        for path in ('/static/vendor/motion.js', '/static/vendor/motion.LICENSE.txt', '/static/fx.js', '/static/app.js'):
            with self.client.get(path) as response:
                self.assertEqual(response.status_code, 200, path)
                self.assertGreater(len(response.data), 500, path)

    def test_the_scanner_has_the_frame_the_scan_line_moves_in(self):
        page = self.client.get('/').get_data(as_text=True)
        for ident in ('scan-frame', 'scan-line', 'scan-video'):
            self.assertIn(f'id="{ident}"', page)
        self.assertLess(page.index('id="scan-frame"'), page.index('id="scan-video"'))

    def test_the_build_label_is_still_shown(self):
        self.assertIn(f'build {BUILD}', self.client.get('/').get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
