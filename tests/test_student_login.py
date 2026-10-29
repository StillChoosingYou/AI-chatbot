import os
import time
from unittest.mock import patch

from pg_support import DatabaseTestCase
from student_auth import FAILS_ALLOWED

ALEX, CASEY = 'P202400101', 'P202400105'


class StudentLoginTests(DatabaseTestCase):
    def start(self, **environment):
        env = {'STUDENT_LOGIN': '1', 'COORDINATOR_PASSWORD': 'right-password', **environment}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()
        coordinator = self.app.test_client()
        coordinator.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'right-password'})
        self.coordinator = coordinator
        return self.app.test_client()

    def pin_for(self, student_id):
        rows = self.coordinator.get('/api/inventory').json['records']
        row = next(r for r in rows if r['student_id'] == student_id)
        return self.coordinator.post(f'/api/inventory/{row["id"]}/pin').json['pin']

    def login(self, client, student_id, pin):
        return client.post('/student/login', data={'student_id': student_id, 'pin': pin})

    def ask(self, client, message, **extra):
        return client.post('/api/chat', json={'message': message, 'session_id': 's1', 'workspace_role': 'student', **extra})

    def row_id(self, student_id):
        rows = self.coordinator.get('/api/inventory').json['records']
        return next(r['id'] for r in rows if r['student_id'] == student_id)

    def test_student_previews_own_card_and_sticker_without_coordinator_sign_in(self):
        client = self.start()
        pin = self.pin_for(ALEX)
        self.login(client, ALEX, pin)
        rid = self.row_id(ALEX)
        card = client.get(f'/me/card/{rid}')
        self.assertEqual(card.status_code, 200)
        self.assertIn(b'shown to school staff only', card.data)
        self.assertEqual(client.get(f'/me/sticker/{rid}').status_code, 200)
        self.assertEqual(client.get(f'/card/{rid}').status_code, 302)
        self.assertEqual(client.get(f'/sticker/{rid}').status_code, 302)
        self.assertEqual(client.get(f'/me/card/{self.row_id(CASEY)}').status_code, 302)

    def test_preview_pages_ask_for_sign_in_when_not_signed_in(self):
        client = self.start()
        response = client.get(f'/me/card/{self.row_id(ALEX)}')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].endswith('/student'))

    def test_open_student_page_previews_without_any_sign_in(self):
        client = self.start(STUDENT_LOGIN='0')
        rid = self.row_id(ALEX)
        self.assertEqual(client.get(f'/me/card/{rid}').status_code, 200)
        self.assertEqual(client.get(f'/card/{rid}').status_code, 302)

    def test_the_student_page_asks_for_sign_in_first(self):
        client = self.start()
        page = client.get('/student')
        self.assertIn(b'name="pin"', page.data)
        self.assertEqual(self.ask(client, 'hello').status_code, 401)

    def test_login_off_by_default_keeps_the_open_student_page(self):
        with patch.dict(os.environ, {'COORDINATOR_PASSWORD': 'x'}):
            os.environ.pop('STUDENT_LOGIN', None)
            client = self.make_app().test_client()
            self.assertNotIn(b'name="pin"', client.get('/student').data)

    def test_issue_pin_is_coordinator_only_and_the_hash_never_leaks(self):
        client = self.start()
        rows = self.coordinator.get('/api/inventory').json['records']
        row = next(r for r in rows if r['student_id'] == ALEX)
        self.assertEqual(client.post(f'/api/inventory/{row["id"]}/pin').status_code, 401)
        pin = self.pin_for(ALEX)
        self.assertRegex(pin, r'^\d{6}$')
        body = self.coordinator.get('/api/inventory').get_data(as_text=True)
        self.assertNotIn('pin_hash', body)
        self.assertNotIn(pin, body)

    def test_right_pin_signs_in_and_the_chat_is_about_that_student_only(self):
        client = self.start()
        pin = self.pin_for(ALEX)
        self.assertEqual(self.login(client, ALEX.lower(), pin).status_code, 302)
        own = self.ask(client, 'my status').json
        self.assertIn('Alex M. Rivera', own['reply'])
        other = self.ask(client, CASEY).json
        self.assertEqual(other['intent'], 'own_record_only')
        self.assertNotIn('Casey', other['reply'])
        byname = self.ask(client, 'Casey Garcia').json
        self.assertNotIn('Casey', byname['reply'])
        spoof = self.ask(client, 'status', record_id=self.coordinator.get('/api/inventory').json['records'][0]['id']).json
        self.assertIn('Alex M. Rivera', spoof['reply'])
        mine = self.ask(client, f'my requests {CASEY}').json
        self.assertNotIn('Casey', mine['reply'])

    def test_wrong_pin_and_unknown_id_look_the_same_and_five_misses_lock_the_account(self):
        client = self.start()
        pin = self.pin_for(ALEX)
        unknown = self.login(client, 'P209999999', '123456')
        wrong = self.login(client, ALEX, '000000' if pin != '000000' else '111111')
        self.assertEqual((unknown.status_code, wrong.status_code), (401, 401))
        self.assertEqual(unknown.get_data(), wrong.get_data())
        for _ in range(FAILS_ALLOWED):
            self.login(client, ALEX, '999999' if pin != '999999' else '888888')
        locked = self.login(client, ALEX, pin)                      # even the right PIN waits
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(self.ask(client, 'hello').status_code, 401)
        self.pin_for(ALEX)                                           # the office issuing a new PIN lifts the lock
        self.assertEqual(self.login(client, ALEX, self.pin_for(ALEX)).status_code, 302)

    def test_a_request_is_always_filed_for_the_signed_in_student(self):
        client = self.start()
        self.login(client, ALEX, self.pin_for(ALEX))
        reply = client.post('/api/chat/request', json={'student_id': CASEY, 'request_type': 'Lost ID',
                                                       'session_id': 's1'})
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(reply.json['student']['student_id'], ALEX)
        self.assertEqual(client.post('/api/chat/request', json={'student_id': ALEX, 'request_type': 'Lost ID'}).status_code, 201)
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.post('/api/chat/request', json={'student_id': ALEX, 'request_type': 'Lost ID'}).status_code, 401)

    def test_logout_and_idle_timeout_end_the_session(self):
        client = self.start()
        self.login(client, ALEX, self.pin_for(ALEX))
        self.assertEqual(self.ask(client, 'hello').status_code, 200)
        with client.session_transaction() as s:
            s['seen'] = time.time() - 21 * 60
        self.assertEqual(self.ask(client, 'hello').status_code, 401)
        self.login(client, ALEX, self.pin_for(ALEX))
        client.post('/student/logout')
        self.assertEqual(self.ask(client, 'hello').status_code, 401)

    def test_the_coordinator_is_not_reachable_from_a_student_session(self):
        client = self.start()
        self.login(client, ALEX, self.pin_for(ALEX))
        self.assertEqual(client.get('/api/inventory').status_code, 401)


class HostAndHeaderTests(DatabaseTestCase):
    def app_with(self, **env):
        patcher = patch.dict(os.environ, {'COORDINATOR_PASSWORD': 'x', **env})
        patcher.start()
        self.addCleanup(patcher.stop)
        return self.make_app().test_client()

    def test_each_host_serves_only_its_own_role(self):
        client = self.app_with(STUDENT_HOST='stu.example.com', COORDINATOR_HOST='coord.example.com')
        self.assertEqual(client.get('/', base_url='https://stu.example.com').headers['Location'], '/student')
        self.assertEqual(client.get('/coordinator', base_url='https://stu.example.com').status_code, 404)
        self.assertEqual(client.get('/api/inventory', base_url='https://stu.example.com').status_code, 404)
        self.assertEqual(client.get('/', base_url='https://coord.example.com').headers['Location'], '/coordinator')
        self.assertEqual(client.get('/student', base_url='https://coord.example.com').status_code, 404)
        self.assertEqual(client.get('/student', base_url='https://other.example.com').status_code, 200)   # a preview is unrestricted

    def test_a_write_from_another_site_is_refused(self):
        client = self.app_with()
        self.assertEqual(client.post('/api/chat', json={'message': 'hi'}, headers={'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(client.post('/api/chat', json={'message': 'hi', 'workspace_role': 'student'},
                                     headers={'Origin': 'http://localhost'}).status_code, 200)

    def test_security_headers_and_no_store(self):
        client = self.app_with()
        reply = client.get('/api/stats')
        self.assertEqual(reply.headers['X-Frame-Options'], 'DENY')
        self.assertEqual(reply.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(reply.headers['Cache-Control'], 'no-store')

    def test_coordinator_session_expires_after_30_idle_minutes(self):
        client = self.app_with()
        client.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'x'})
        self.assertEqual(client.get('/api/stats').status_code, 200)
        with client.session_transaction() as s:
            s['seen'] = time.time() - 31 * 60
        self.assertEqual(client.get('/api/stats').status_code, 401)
