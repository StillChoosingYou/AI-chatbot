import json
import os
import time
from unittest.mock import patch

from pg_support import DatabaseTestCase

ENV = {'ADMIN_PASSWORD': 'admin-pass', 'COORDINATOR_PASSWORD': 'coord-pass'}


class AdminTests(DatabaseTestCase):
    def start(self, **extra):
        patcher = patch.dict(os.environ, {**ENV, **extra})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()
        client = self.app.test_client()
        return client

    def signed_in(self, **extra):
        client = self.start(**extra)
        self.assertEqual(client.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'}).status_code, 302)
        return client

    def test_the_panel_needs_the_admin_password_and_the_coordinator_password_does_not_open_it(self):
        client = self.start()
        self.assertIn(b'Sign in', client.get('/admin').data)
        self.assertNotIn(b'Admin panel', client.get('/admin').data)
        self.assertEqual(client.post('/admin/login', data={'username': 'Admin', 'password': 'coord-pass'}).status_code, 401)
        self.assertEqual(client.post('/admin/login', data={'username': 'Coordinator', 'password': 'admin-pass'}).status_code, 401)
        for path in ('/api/admin/overview', '/api/admin/links', '/api/admin/pins', '/api/admin/insights', '/api/admin/news',
                     '/api/admin/security', '/api/admin/system'):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 401)
        for path in ('/api/admin/backup', '/api/admin/missed.csv', '/api/admin/qr.png'):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 401)
        self.assertEqual(client.post('/api/admin/pins/bulk', json={'scope': 'all'}).status_code, 401)

    def test_a_coordinator_session_cannot_reach_the_admin_panel(self):
        client = self.start()
        client.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'coord-pass'})
        self.assertEqual(client.get('/api/inventory').status_code, 200)
        self.assertEqual(client.get('/api/admin/overview').status_code, 401)

    def test_fails_closed_on_vercel_without_an_admin_password(self):
        with patch.dict(os.environ, {'VERCEL': '1', 'COORDINATOR_PASSWORD': 'x'}):
            os.environ.pop('ADMIN_PASSWORD', None)
            client = self.make_app().test_client()
            self.assertEqual(client.post('/admin/login', data={'username': 'Admin', 'password': ''}).status_code, 401)
            self.assertEqual(client.get('/api/admin/overview').status_code, 401)

    def test_five_wrong_tries_lock_the_admin_address(self):
        client = self.start()
        for _ in range(5):
            client.post('/admin/login', data={'username': 'Admin', 'password': 'nope'})
        self.assertEqual(client.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'}).status_code, 401)

    def test_overview_links_and_qr(self):
        client = self.signed_in(STUDENT_HOST='pgpc-student.example.com', COORDINATOR_HOST='pgpc-coord.example.com')
        links = client.get('/api/admin/links').json
        self.assertEqual(links['student'], 'https://pgpc-student.example.com/student')
        self.assertEqual(links['coordinator'], 'https://pgpc-coord.example.com/coordinator')
        self.assertTrue(links['separate_hosts'])
        self.assertTrue(client.get('/api/admin/qr.png?which=student').data.startswith(b'\x89PNG'))
        overview = client.get('/api/admin/overview').json
        self.assertEqual(overview['students'], 6)
        self.assertTrue(overview['portal_open'])

    def test_the_admin_switches_into_the_coordinator_workspace_and_back(self):
        client = self.signed_in()
        self.assertEqual(client.get('/api/inventory').status_code, 401)          # admin is not automatically the coordinator
        switched = client.post('/admin/switch', data={'to': 'coordinator'})
        self.assertEqual((switched.status_code, switched.headers['Location']), (302, '/coordinator'))
        self.assertEqual(client.get('/api/inventory').status_code, 200)
        page = client.get('/coordinator').get_data(as_text=True)
        self.assertIn('data-admin="1"', page)
        self.assertIn('Back to Admin panel', page)
        self.assertEqual(client.get('/api/admin/overview').status_code, 200)       # still signed in as admin

    def test_closing_the_portal_stops_the_student_side_but_not_the_scan_page(self):
        client = self.signed_in()
        token = self.coordinator_token(client)
        self.assertEqual(client.post('/api/admin/portal', json={'open': False}).json, {'open': False})
        visitor = self.app.test_client()
        self.assertEqual(visitor.get('/student').status_code, 503)
        self.assertEqual(visitor.post('/api/chat', json={'message': 'hi', 'workspace_role': 'student'}).status_code, 503)
        self.assertEqual(visitor.get(f'/student/{token}').status_code, 200)
        self.assertEqual(client.post('/api/admin/portal', json={'open': True}).status_code, 200)
        self.assertEqual(visitor.get('/student').status_code, 200)
        self.assertEqual(client.post('/api/admin/portal', json={'open': 'yes'}).status_code, 400)

    def coordinator_token(self, client):
        client.post('/admin/switch', data={'to': 'coordinator'})
        return client.get('/api/inventory').json['records'][0]['token']

    def test_ticker_news_can_be_edited_and_reset(self):
        client = self.signed_in()
        visitor = self.app.test_client()
        before = visitor.get('/api/news').json['news']
        saved = client.put('/api/admin/news', json={'news': [{'icon': '🎉', 'text': 'Enrollment is open'}, {'text': '  '}, 5]}).json
        self.assertEqual(saved['news'], [{'icon': '🎉', 'text': 'Enrollment is open'}])
        self.assertEqual(visitor.get('/api/news').json['news'], saved['news'])
        self.assertEqual(client.put('/api/admin/news', json={'news': 'x'}).status_code, 400)
        self.assertEqual(client.delete('/api/admin/news').json['news'], before)

    def test_bulk_pins_unlock_and_student_sign_in(self):
        client = self.signed_in(STUDENT_LOGIN='1')
        issued = client.post('/api/admin/pins/bulk', json={'scope': 'missing'}).json['issued']
        self.assertEqual(len(issued), 6)
        self.assertEqual(client.post('/api/admin/pins/bulk', json={'scope': 'missing'}).json['issued'], [])
        body = client.get('/api/admin/pins').get_data(as_text=True)
        self.assertNotIn('pin_hash', body)
        first = issued[0]
        student = self.app.test_client()
        self.assertEqual(student.post('/student/login', data={'student_id': first['student_id'], 'pin': first['pin']}).status_code, 302)
        for _ in range(5):
            self.app.test_client().post('/student/login', data={'student_id': issued[1]['student_id'], 'pin': 'wrong'})
        locked = [s for s in client.get('/api/admin/pins').json['students'] if s['locked']]
        self.assertEqual([s['student_id'] for s in locked], [issued[1]['student_id']])
        self.assertEqual(client.post('/api/admin/pins/unlock', json={'student_id': issued[1]['student_id']}).status_code, 200)
        self.assertEqual(client.post('/api/admin/pins/unlock', json={'student_id': 'NOPE'}).status_code, 404)
        self.assertEqual(client.post('/api/admin/pins/bulk', json={'scope': 'bad'}).status_code, 400)

    def test_insights_missed_questions_and_csv(self):
        client = self.signed_in()
        chat = self.app.test_client()
        chat.post('/api/chat', json={'message': 'what is the meaning of life', 'session_id': 's-x', 'workspace_role': 'student'})
        chat.post('/api/chat', json={'message': 'office hours', 'session_id': 's-x', 'workspace_role': 'student'})
        data = client.get('/api/admin/insights').json
        self.assertTrue(any('meaning of life' in m['question'] for m in data['missed']))
        self.assertTrue(data['intents'])
        csv_text = client.get('/api/admin/missed.csv').get_data(as_text=True)
        self.assertTrue(csv_text.startswith('question,times_asked,bot_intent'))
        self.assertIn('meaning of life', csv_text)

    def test_backup_has_no_pin_hashes_and_purge_counts_before_deleting(self):
        client = self.signed_in()
        client.post('/api/admin/pins/bulk', json={'scope': 'all'})
        chat = self.app.test_client()
        chat.post('/api/chat', json={'message': 'hello', 'session_id': 's-old', 'workspace_role': 'student'})
        with self.db() as db:
            db.execute("UPDATE chat_history SET timestamp = '2020-01-01 00:00:00'")
            db.commit()
        backup = client.get('/api/admin/backup?chat=1')
        self.assertIn('attachment', backup.headers['Content-Disposition'])
        text = backup.get_data(as_text=True)
        self.assertNotIn('pin_hash', text)
        tables = json.loads(text)['tables']
        self.assertEqual(len(tables['inventory']), 6)
        self.assertTrue(tables['chat_history'])
        self.assertEqual(client.post('/api/admin/purge', json={'days': 30}).json['dry_run'], True)
        still = client.post('/api/admin/purge', json={'days': 30}).json['count']
        self.assertGreater(still, 0)
        self.assertEqual(client.post('/api/admin/purge', json={'days': 3}).status_code, 400)
        gone = client.post('/api/admin/purge', json={'days': 30, 'dry_run': False}).json
        self.assertEqual(gone['count'], still)
        self.assertEqual(client.post('/api/admin/purge', json={'days': 30}).json['count'], 0)

    def test_security_log_and_system_page(self):
        client = self.signed_in()
        self.app.test_client().post('/admin/login', data={'username': 'Admin', 'password': 'wrong'})
        events = client.get('/api/admin/security').json
        actions = {e['action'] for e in events['events']}
        self.assertTrue({'admin sign in', 'admin failed sign in'} <= actions)
        self.assertGreaterEqual(events['failed_today'], 1)
        system = client.get('/api/admin/system').json
        self.assertTrue(system['database']['ok'])
        self.assertIn('id_process', system['model']['intents'])
        self.assertNotIn('admin-pass', json.dumps(system))

    def test_admin_is_hidden_on_the_student_and_coordinator_addresses_and_times_out(self):
        client = self.start(STUDENT_HOST='stu.example.com', COORDINATOR_HOST='coord.example.com')
        for host in ('stu.example.com', 'coord.example.com'):
            self.assertEqual(client.get('/admin', base_url=f'https://{host}').status_code, 404)
            self.assertEqual(client.get('/api/admin/overview', base_url=f'https://{host}').status_code, 404)
        self.assertEqual(client.get('/admin', base_url='https://admin.example.com').status_code, 200)
        client.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'})
        self.assertEqual(client.get('/api/admin/overview').status_code, 200)
        with client.session_transaction() as s:
            s['seen'] = time.time() - 16 * 60
        self.assertEqual(client.get('/api/admin/overview').status_code, 401)

    def test_links_use_the_live_site_on_localhost_and_the_admin_can_change_the_address(self):
        client = self.signed_in()
        links = client.get('/api/admin/links').json
        self.assertEqual(links['student'], 'https://pgpc-ai-chatbot.vercel.app/student')
        self.assertEqual(links['coordinator'], 'https://pgpc-ai-chatbot.vercel.app/coordinator')
        saved = client.put('/api/admin/public-url', json={'url': 'https://school.example.org/'}).json
        self.assertEqual(saved['student'], 'https://school.example.org/student')
        self.assertEqual(client.get('/api/admin/links').json['coordinator'], 'https://school.example.org/coordinator')
        for bad in ('ftp://x.org', 'https://x.org/path', 'not a url'):
            self.assertEqual(client.put('/api/admin/public-url', json={'url': bad}).status_code, 400)
        self.assertEqual(client.put('/api/admin/public-url', json={'url': ''}).json['student'], 'https://pgpc-ai-chatbot.vercel.app/student')
        self.assertEqual(self.app.test_client().put('/api/admin/public-url', json={'url': 'https://evil.example'}).status_code, 401)

    def test_a_real_host_is_used_as_is(self):
        client = self.start()
        client.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'}, base_url='https://my-site.vercel.app')
        links = client.get('/api/admin/links', base_url='https://my-site.vercel.app').json
        self.assertEqual(links['student'], 'https://my-site.vercel.app/student')

    def test_the_home_page_is_the_admin_sign_in_and_signing_in_leads_back_to_it(self):
        client = self.start()
        home = client.get('/')
        self.assertEqual(home.status_code, 200)
        self.assertIn('Admin sign in', home.get_data(as_text=True))
        self.assertNotIn('Property coordinator', home.get_data(as_text=True))       # no public role chooser
        login = client.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'})
        self.assertEqual(login.headers['Location'], '/')
        self.assertIn('Admin panel', client.get('/').get_data(as_text=True))
        out = client.post('/admin/logout')
        self.assertEqual(out.headers['Location'], '/')
        self.assertIn('Admin sign in', client.get('/').get_data(as_text=True))
