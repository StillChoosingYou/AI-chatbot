import os
from unittest.mock import patch

from pg_support import DatabaseTestCase

ALEX, CASEY = 'P202400101', 'P202400105'
ENV = {'STUDENT_LOGIN': '1', 'ADMIN_PASSWORD': 'admin-pass', 'COORDINATOR_PASSWORD': 'coord-pass'}


class StudentSettingsTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()
        self.admin = self.app.test_client()
        self.admin.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'})
        self.coordinator = self.app.test_client()
        self.coordinator.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'coord-pass'})

    def row(self, student_id):
        return next(r for r in self.coordinator.get('/api/inventory').json['records'] if r['student_id'] == student_id)

    def pin_for(self, student_id):
        return self.coordinator.post(f'/api/inventory/{self.row(student_id)["id"]}/pin').json['pin']

    def signed_in(self, student_id=ALEX):
        client = self.app.test_client()
        pin = self.pin_for(student_id)
        self.assertEqual(client.post('/student/login', data={'student_id': student_id, 'pin': pin}).status_code, 302)
        return client, pin

    def test_sign_in_is_required_when_nothing_is_set(self):
        with patch.dict(os.environ):
            os.environ.pop('STUDENT_LOGIN')
            client = self.make_app().test_client()
            self.assertIn(b'name="pin"', client.get('/student').data)

    def test_the_admin_panel_records_who_signed_in_and_who_failed(self):
        client, pin = self.signed_in()
        self.app.test_client().post('/student/login', data={'student_id': ALEX, 'pin': '000000'})
        self.app.test_client().post('/student/login', data={'student_id': 'P999', 'pin': '111111'})
        data = self.admin.get('/api/admin/pins').json
        alex = next(s for s in data['students'] if s['student_id'] == ALEX)
        self.assertEqual(alex['signins'], 1)
        self.assertRegex(alex['last_signin'], r'^\d{4}-\d\d-\d\d')
        self.assertEqual(data['today']['signed_in'], 1)
        self.assertEqual(data['today']['failed'], 2)
        actions = [e['action'] for e in self.admin.get('/api/admin/security').json['events']]
        self.assertIn('student sign in', actions)
        self.assertIn('student failed sign in', actions)
        client.post('/student/logout')
        self.assertIn('student sign out', [e['action'] for e in self.admin.get('/api/admin/security').json['events']])
        self.assertEqual(self.admin.get('/api/admin/overview').json['signed_in_today'], 1)
        self.assertNotIn(pin, str(self.admin.get('/api/admin/security').json))        # a PIN is never written to the log

    def test_a_student_changes_their_own_pin(self):
        client, pin = self.signed_in()
        other = self.app.test_client()
        other.post('/student/login', data={'student_id': ALEX, 'pin': pin})
        change = lambda **k: client.post('/api/student/pin', json={'current': pin, 'new': '482915', 'confirm': '482915', **k})
        self.assertEqual(change(current='111222').status_code, 403)                     # wrong current PIN
        self.assertEqual(change(new='123456', confirm='123456').status_code, 400)       # a run
        self.assertEqual(change(new='777777', confirm='777777').status_code, 400)       # one digit
        self.assertEqual(change(confirm='482916').status_code, 400)                     # not the same twice
        self.assertEqual(change(new=pin, confirm=pin).status_code, 400)                 # same as before
        self.assertEqual(change().status_code, 200)
        self.assertEqual(client.get('/api/live?role=student').status_code, 200)         # this device stays in
        self.assertEqual(other.get('/api/live?role=student').status_code, 401)          # the other one is signed out
        fresh = self.app.test_client()
        self.assertEqual(fresh.post('/student/login', data={'student_id': ALEX, 'pin': pin}).status_code, 401)
        self.assertEqual(fresh.post('/student/login', data={'student_id': ALEX, 'pin': '482915'}).status_code, 302)
        alex = next(s for s in self.admin.get('/api/admin/pins').json['students'] if s['student_id'] == ALEX)
        self.assertTrue(alex['pin_changed'])
        self.assertIn('student pin changed', [e['action'] for e in self.admin.get('/api/admin/security').json['events']])

    def test_wrong_current_pin_counts_toward_the_lock(self):
        client, pin = self.signed_in()
        for _ in range(4):
            self.assertEqual(client.post('/api/student/pin', json={'current': '000000', 'new': '482915', 'confirm': '482915'}).status_code, 403)
        last = client.post('/api/student/pin', json={'current': '000000', 'new': '482915', 'confirm': '482915'})
        self.assertEqual(last.status_code, 429)
        self.assertEqual(client.get('/api/live?role=student').status_code, 401)

    def test_change_pin_needs_a_signed_in_student(self):
        self.assertEqual(self.app.test_client().post('/api/student/pin', json={'current': '1', 'new': '2', 'confirm': '2'}).status_code, 401)
        self.assertEqual(self.app.test_client().get('/api/student/export').status_code, 401)

    def test_live_counter_moves_with_every_change_and_the_student_sees_their_own_status(self):
        client, _ = self.signed_in()
        first = client.get('/api/live?role=student').json
        self.assertEqual(first['me']['student_id'], ALEX)
        self.assertNotIn('pin_hash', str(first))
        self.assertEqual(self.coordinator.patch(f'/api/inventory/{self.row(ALEX)["id"]}', json={'status': 'Released'}).status_code, 200)
        second = client.get('/api/live?role=student').json
        self.assertGreater(second['rev'], first['rev'])
        self.assertEqual(second['me']['status'], 'Released')
        self.assertEqual(self.coordinator.get('/api/live?role=coordinator').json['rev'], second['rev'])
        self.assertGreaterEqual(self.admin.get('/api/live?role=admin').json['rev'], second['rev'])

    def test_live_is_private_to_each_role(self):
        anonymous = self.app.test_client()
        for role in ('admin', 'coordinator', 'student'):
            self.assertEqual(anonymous.get(f'/api/live?role={role}').status_code, 401, role)
        student, _ = self.signed_in()
        self.assertEqual(student.get('/api/live?role=coordinator').status_code, 401)
        self.assertEqual(student.get('/api/live?role=admin').status_code, 401)
        self.assertEqual(student.get('/api/live?role=nobody').status_code, 400)

    def test_an_office_pin_reset_signs_the_student_out_at_once(self):
        client, _ = self.signed_in()
        self.assertEqual(client.get('/api/live?role=student').status_code, 200)
        self.admin.post('/api/admin/pins/bulk', json={'scope': 'all'})
        self.assertEqual(client.get('/api/live?role=student').status_code, 401)

    def test_a_student_can_download_only_their_own_data(self):
        client, _ = self.signed_in()
        body = client.get('/api/student/export')
        self.assertEqual(body.status_code, 200)
        self.assertIn('attachment', body.headers['Content-Disposition'])
        self.assertEqual(body.json['student']['student_id'], ALEX)
        text = body.get_data(as_text=True)
        self.assertNotIn('pin_hash', text)
        self.assertNotIn(CASEY, text)

    def test_the_student_page_has_the_settings_page(self):
        client, _ = self.signed_in()
        page = client.get('/student').get_data(as_text=True)
        for needle in ('id="view-settings"', 'id="pin-form"', 'id="seg-theme"', 'id="live-chip"'):
            self.assertIn(needle, page)

    def test_admin_issues_a_pin_for_one_student_and_it_is_shown_only_in_that_reply(self):
        client, old = self.signed_in()
        self.assertEqual(self.app.test_client().post('/api/admin/pins/issue', json={'student_id': ALEX}).status_code, 401)
        reply = self.admin.post('/api/admin/pins/issue', json={'student_id': ALEX.lower()})
        self.assertEqual(reply.status_code, 200)
        pin = reply.json['pin']
        self.assertRegex(pin, r'^\d{6}$')
        self.assertEqual(client.get('/api/live?role=student').status_code, 401)          # the old session ends
        fresh = self.app.test_client()
        self.assertEqual(fresh.post('/student/login', data={'student_id': ALEX, 'pin': old}).status_code, 401)
        self.assertEqual(fresh.post('/student/login', data={'student_id': ALEX, 'pin': pin}).status_code, 302)
        self.assertNotIn(pin, str(self.admin.get('/api/admin/pins').json))               # never listed
        self.assertNotIn(pin, str(self.admin.get('/api/admin/security').json))
        self.assertEqual(self.admin.post('/api/admin/pins/issue', json={'student_id': 'P0'}).status_code, 404)


    # The admin, coordinator and student pages are often open side by side in one browser: one cookie, three roles.

    def test_signing_in_as_one_role_keeps_the_others_signed_in(self):
        browser = self.app.test_client()
        browser.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'})
        browser.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'coord-pass'})
        pin = self.pin_for(ALEX)
        browser.post('/student/login', data={'student_id': ALEX, 'pin': pin})
        self.assertEqual(browser.get('/api/live?role=admin').status_code, 200)
        self.assertEqual(browser.get('/api/live?role=coordinator').status_code, 200)
        self.assertEqual(browser.get('/api/live?role=student').status_code, 200)
        browser.post('/student/logout')                                   # one role leaves, the others stay
        self.assertEqual(browser.get('/api/live?role=student').status_code, 401)
        self.assertEqual(browser.get('/api/live?role=admin').status_code, 200)
        self.assertEqual(browser.get('/api/live?role=coordinator').status_code, 200)
        browser.post('/coordinator/logout')
        self.assertEqual(browser.get('/api/live?role=admin').status_code, 200)

    def test_each_role_has_its_own_idle_clock(self):
        browser = self.app.test_client()
        browser.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'})
        browser.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'coord-pass'})
        with browser.session_transaction() as s:
            s['seen_admin'] = __import__('time').time() - 16 * 60          # the admin tab sat idle past 15 minutes
        self.assertEqual(browser.get('/api/live?role=coordinator').status_code, 200)   # the coordinator tab is still fine
        self.assertEqual(browser.get('/api/live?role=admin').status_code, 401)
        self.assertEqual(browser.get('/api/live?role=coordinator').status_code, 200)

    def test_working_in_another_tab_does_not_keep_an_idle_role_alive(self):
        browser = self.app.test_client()
        browser.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'})
        browser.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'coord-pass'})
        with browser.session_transaction() as s:
            s['seen_admin'] = __import__('time').time() - 14 * 60
        browser.get('/api/live?role=coordinator')                          # coordinator activity only
        with browser.session_transaction() as s:
            self.assertLess(__import__('time').time() - s['seen_admin'], 14 * 60 + 30)
            self.assertGreater(__import__('time').time() - s['seen_admin'], 13 * 60)

    def test_admin_sign_out_also_ends_the_coordinator_workspace_it_opened(self):
        browser = self.app.test_client()
        browser.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'})
        browser.post('/admin/switch', data={'to': 'coordinator'})
        self.assertEqual(browser.get('/api/live?role=coordinator').status_code, 200)
        browser.post('/admin/logout')
        self.assertEqual(browser.get('/api/live?role=coordinator').status_code, 401)
