import os
import time
from unittest.mock import patch

import captcha
import student_auth
from pg_support import DatabaseTestCase

ALEX, CASEY = 'P202400101', 'P202400105'
ENV = {'STUDENT_LOGIN': '1', 'ADMIN_PASSWORD': 'admin-pass', 'COORDINATOR_PASSWORD': 'coord-pass', 'ADMIN_CAPTCHA': '1'}
CREDS = {'username': 'Admin', 'password': 'admin-pass'}


class CaptchaUnitTests(DatabaseTestCase):
    def test_code_alphabet_and_picture(self):
        for _ in range(20):
            code = captcha.new_code()
            self.assertEqual(len(code), captcha.LENGTH)
            self.assertTrue(set(code) <= set(captcha.ALPHABET))
        self.assertTrue(captcha.render('AB7KM').startswith(b'\x89PNG'))

    def test_seal_matches_case_and_spaces_but_not_other_text_or_nonce(self):
        sealed = captcha.seal('AB7KM', 'n1', 'key')
        self.assertTrue(captcha.matches('ab7 km', sealed, 'n1', 'key'))
        self.assertFalse(captcha.matches('AB7KN', sealed, 'n1', 'key'))
        self.assertFalse(captcha.matches('AB7KM', sealed, 'n2', 'key'))
        self.assertFalse(captcha.matches('AB7KM', sealed, 'n1', 'other'))
        self.assertFalse(captcha.matches('AB7KM', '', 'n1', 'key'))

    def test_vault_round_trip_and_wrong_key(self):
        token = student_auth.seal_pin('482913', 'secret-a')
        self.assertNotIn('482913', token)
        self.assertEqual(student_auth.open_pin(token, 'secret-a'), '482913')
        self.assertEqual(student_auth.open_pin(token, 'secret-b'), '')
        self.assertEqual(student_auth.open_pin('', 'secret-a'), '')


class AdminCaptchaAndVaultTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()

    def sign_in_password_only(self):
        client = self.app.test_client()
        response = client.post('/admin/login', data=CREDS)
        return client, response

    def fetch_picture(self, client):
        with patch('captcha.new_code', return_value='AB7KM'):
            return client.get('/admin/captcha.png')

    def full_admin(self):
        client, _ = self.sign_in_password_only()
        self.fetch_picture(client)
        client.post('/admin/verify', data={'captcha': 'ab7km'})
        return client

    def test_password_alone_does_not_open_the_panel(self):
        client, response = self.sign_in_password_only()
        self.assertEqual((response.status_code, response.headers['Location']), (302, '/admin/verify'))
        self.assertEqual(client.get('/api/admin/overview').status_code, 401)
        self.assertIn(b'name="username"', client.get('/').data)
        self.assertEqual(client.get('/admin/verify').status_code, 200)

    def test_picture_and_form_need_a_password_step_first(self):
        client = self.app.test_client()
        self.assertEqual(client.get('/admin/captcha.png').status_code, 404)
        self.assertEqual(client.get('/admin/verify').status_code, 302)
        self.assertEqual(client.post('/admin/verify', data={'captcha': 'AB7KM'}).status_code, 302)
        self.assertEqual(client.get('/api/admin/overview').status_code, 401)

    def test_wrong_password_never_reaches_the_picture(self):
        client = self.app.test_client()
        self.assertEqual(client.post('/admin/login', data={'username': 'Admin', 'password': 'nope'}).status_code, 401)
        self.assertEqual(client.get('/admin/captcha.png').status_code, 404)

    def test_right_letters_open_the_panel_and_are_logged(self):
        client = self.full_admin()
        self.assertEqual(client.get('/api/admin/overview').status_code, 200)
        self.assertEqual(client.get('/admin/captcha.png').status_code, 404)           # the step is over
        details = [e['detail'] for e in client.get('/api/admin/security').json['events'] if e['action'] == 'admin sign in']
        self.assertTrue(any('captcha passed' in d for d in details))

    def test_picture_is_a_png_and_not_cached(self):
        client, _ = self.sign_in_password_only()
        response = self.fetch_picture(client)
        self.assertEqual(response.mimetype, 'image/png')
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertTrue(response.data.startswith(b'\x89PNG'))

    def test_wrong_letters_burn_the_picture_and_are_logged(self):
        client, _ = self.sign_in_password_only()
        self.fetch_picture(client)
        response = client.post('/admin/verify', data={'captcha': 'ZZZZZ'})
        self.assertEqual(response.status_code, 401)
        self.assertIn(b'did not match', response.data)
        client.post('/admin/verify', data={'captcha': 'AB7KM'})                     # the old picture's answer no longer works
        self.assertEqual(client.get('/api/admin/overview').status_code, 401)
        full = self.full_admin()
        actions = [e['action'] for e in full.get('/api/admin/security').json['events']]
        self.assertIn('admin failed captcha', actions)

    def test_five_wrong_pictures_send_back_to_the_password(self):
        client, _ = self.sign_in_password_only()
        for _ in range(5):
            self.fetch_picture(client)
            response = client.post('/admin/verify', data={'captcha': 'ZZZZZ'})
        self.assertIn(b'name="username"', response.data)
        self.assertEqual(client.get('/admin/verify').status_code, 302)

    def test_the_step_expires(self):
        client, _ = self.sign_in_password_only()
        self.fetch_picture(client)
        with patch('time.time', return_value=time.time() + 6 * 60):
            self.assertEqual(client.post('/admin/verify', data={'captcha': 'AB7KM'}).status_code, 302)
        self.assertEqual(client.get('/api/admin/overview').status_code, 401)

    def test_cancel_returns_to_sign_in(self):
        client, _ = self.sign_in_password_only()
        self.assertEqual(client.get('/admin/cancel').status_code, 302)
        self.assertEqual(client.get('/admin/verify').status_code, 302)

    def test_answer_is_not_in_the_cookie(self):
        client, _ = self.sign_in_password_only()
        self.fetch_picture(client)
        cookie = client.get_cookie('session')
        self.assertNotIn('AB7KM', cookie.value)
        self.assertNotIn('AB7KM', str(client.get('/admin/verify').data))

    # ─── PIN vault ───
    def coordinator(self):
        client = self.app.test_client()
        client.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'coord-pass'})
        return client

    def record_id(self, coordinator, student_id):
        return next(r['id'] for r in coordinator.get('/api/inventory').json['records'] if r['student_id'] == student_id)

    def pins(self, admin):
        return {s['student_id']: s for s in admin.get('/api/admin/pins').json['students']}

    def test_admin_sees_the_pin_the_coordinator_issued(self):
        admin, coordinator = self.full_admin(), self.coordinator()
        pin = coordinator.post(f'/api/inventory/{self.record_id(coordinator, ALEX)}/pin').json['pin']
        self.assertEqual(self.pins(admin)[ALEX]['pin'], pin)
        self.assertEqual(self.pins(admin)[CASEY]['pin'], '')

    def test_admin_issue_and_bulk_are_visible(self):
        admin = self.full_admin()
        one = admin.post('/api/admin/pins/issue', json={'student_id': ALEX}).json['pin']
        bulk = {i['student_id']: i['pin'] for i in admin.post('/api/admin/pins/bulk', json={'scope': 'all'}).json['issued']}
        self.assertNotEqual(one, '')
        for student_id, pin in bulk.items():
            self.assertEqual(self.pins(admin)[student_id]['pin'], pin)

    def test_a_pin_the_student_chose_is_private_and_the_issued_one_is_gone(self):
        admin = self.full_admin()
        pin = admin.post('/api/admin/pins/issue', json={'student_id': ALEX}).json['pin']
        student = self.app.test_client()
        student.post('/student/login', data={'student_id': ALEX, 'pin': pin})
        self.assertEqual(student.post('/api/student/pin', json={'current': pin, 'new': '482913', 'confirm': '482913'}).status_code, 200)
        row = self.pins(admin)[ALEX]
        self.assertEqual(row['pin'], '')
        self.assertTrue(row['pin_changed'])
        self.assertNotIn('482913', admin.get('/api/admin/pins').get_data(as_text=True))

    def test_the_vault_never_leaks_through_other_routes(self):
        admin, coordinator = self.full_admin(), self.coordinator()
        pin = coordinator.post(f'/api/inventory/{self.record_id(coordinator, ALEX)}/pin').json['pin']
        student = self.app.test_client()
        student.post('/student/login', data={'student_id': ALEX, 'pin': pin})
        quoted = f'"{pin}"'
        for client, path in ((coordinator, '/api/inventory'), (coordinator, '/api/inventory/export'),
                             (admin, '/api/admin/backup'), (student, '/api/student/export'),
                             (student, '/api/student/me'), (student, '/me/data')):
            body = client.get(path).get_data(as_text=True)
            self.assertNotIn(quoted, body, path)
            self.assertNotIn('pin_vault', body, path)
            self.assertNotIn('gAAAA', body, path)

    def test_the_pin_list_still_needs_the_admin(self):
        self.assertEqual(self.app.test_client().get('/api/admin/pins').status_code, 401)
        self.assertEqual(self.coordinator().get('/api/admin/pins').status_code, 401)

    # ─── printable data report ───
    def test_data_report_is_the_students_own_and_has_no_pin(self):
        admin = self.full_admin()
        pin = admin.post('/api/admin/pins/issue', json={'student_id': ALEX}).json['pin']
        student = self.app.test_client()
        student.post('/student/login', data={'student_id': ALEX, 'pin': pin})
        page = student.get('/me/data')
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        self.assertIn(ALEX, text)
        self.assertNotIn(CASEY, text)
        self.assertIn('Print / Save as PDF', text)
        self.assertEqual(page.headers['Cache-Control'], 'no-store')
        self.assertEqual(self.app.test_client().get('/me/data').status_code, 302)
