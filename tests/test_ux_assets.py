import os
import re
from unittest.mock import patch

from pg_support import AppTestCase


class UxAssetTests(AppTestCase):
    def test_shared_scripts_are_served(self):
        for path in ('/static/ux.js', '/static/auth.js'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                response.close()

    def test_every_sign_in_page_loads_the_sign_in_script_and_the_saved_theme(self):
        with patch.dict(os.environ, {'STUDENT_LOGIN': '1', 'COORDINATOR_PASSWORD': 'x', 'ADMIN_PASSWORD': 'y'}):
            client = self.make_app().test_client()
            for path in ('/student', '/coordinator', '/'):
                with self.subTest(path=path):
                    page = client.get(path).get_data(as_text=True)
                    self.assertIn('/static/auth.js', page)
                    self.assertIn('/static/theme.js', page)

    def test_the_workspace_and_the_admin_panel_load_the_shared_tools_before_their_own_script(self):
        page = self.client.get('/coordinator').get_data(as_text=True)
        scripts = re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', page)
        self.assertLess(scripts.index('/static/ux.js'), scripts.index('/static/app.js'))
        with patch.dict(os.environ, {'ADMIN_PASSWORD': 'y'}):
            client = self.make_app().test_client()
            client.post('/admin/login', data={'username': 'Admin', 'password': 'y'})
            scripts = re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', client.get('/').get_data(as_text=True))
            self.assertLess(scripts.index('/static/ux.js'), scripts.index('/static/admin.js'))

    def test_the_tutorial_is_loaded_everywhere_with_a_replay_button(self):
        page = self.client.get('/coordinator').get_data(as_text=True)
        scripts = re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', page)
        self.assertLess(scripts.index('/static/tour.js'), scripts.index('/static/app.js'))
        self.assertIn('id="tour-btn"', page)
        with patch.dict(os.environ, {'ADMIN_PASSWORD': 'y'}):
            client = self.make_app().test_client()
            client.post('/admin/login', data={'username': 'Admin', 'password': 'y'})
            admin = client.get('/').get_data(as_text=True)
            self.assertIn('id="tour-btn"', admin)
            scripts = re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', admin)
            self.assertLess(scripts.index('/static/tour.js'), scripts.index('/static/admin.js'))
        for path, needles in (('/static/tour.js', ('tour-hand', "mode === 'click'", 'Escape', 'prefers-reduced-motion')),
                              ('/static/app.js', ('tourSteps', 'tour.auto')), ('/static/admin.js', ('tourSteps', "tour.auto('admin'")),
                              ('/static/style.css', ('.tour-hand', '.tour-hole', '.tour-card'))):
            body = self.client.get(path).get_data(as_text=True)
            for needle in needles:
                with self.subTest(path=path, needle=needle):
                    self.assertIn(needle, body)

    def test_one_shared_toast_serves_the_workspace_and_the_admin_panel(self):
        script = self.client.get('/static/ux.js').get_data(as_text=True)
        self.assertIn('toast,', script)
        self.assertIn("setAttribute('aria-live', 'polite')", script)
        self.assertIn('ux.toast', self.client.get('/static/admin.js').get_data(as_text=True))
        self.assertIn('ux.toast', self.client.get('/static/app.js').get_data(as_text=True))
        self.assertIn('.ux-toast', self.client.get('/static/style.css').get_data(as_text=True))

    def test_the_student_pin_box_takes_six_digits(self):
        with patch.dict(os.environ, {'STUDENT_LOGIN': '1'}):
            page = self.make_app().test_client().get('/student').get_data(as_text=True)
            self.assertIn('maxlength="6"', page)

    def test_the_overview_can_open_and_close_the_student_portal_after_asking(self):
        script = self.client.get('/static/admin.js').get_data(as_text=True)
        self.assertIn('ov-portal-switch', script)
        self.assertIn('/api/admin/portal', script)
        self.assertIn('Close the student portal?', script)
