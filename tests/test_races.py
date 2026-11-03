"""Races on security counters: many requests at the same moment must not get more than their fair share.

Each test fires the requests together (pg_support.run_together) and also has a deterministic twin that holds
the row lock in another connection and checks that the request waits for it.
"""
import os
import threading
import time
import unittest
from unittest.mock import patch

import totp
from pg_support import DatabaseTestCase, run_together
from student_auth import FAILS_ALLOWED

ALEX = 'P202400101'
ENV = {'STUDENT_LOGIN': '1', 'ADMIN_PASSWORD': 'admin-pass', 'COORDINATOR_PASSWORD': 'coord-pass', 'ADMIN_CAPTCHA': '1'}
CREDS = {'username': 'Admin', 'password': 'admin-pass'}
GUESSES = 12


class EnvTestCase(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()

    def picture(self, client):
        with patch('captcha.new_code', return_value='AB7KM'):
            client.get('/admin/captcha.png')

    def waits_for_lock(self, take_lock, action):
        """(was `action` still waiting after 0.7 s, its result): the lock is held on another connection meanwhile."""
        outcome = []
        with self.db() as holder:
            take_lock(holder)
            thread = threading.Thread(target=lambda: outcome.append(action()))
            thread.start()
            thread.join(0.7)
            still_waiting = thread.is_alive() and not outcome
        thread.join(15)
        return still_waiting, outcome


class StudentPinCounterTests(EnvTestCase):
    def setUp(self):
        super().setUp()
        admin = self.app.test_client()
        admin.post('/admin/login', data=CREDS)
        self.picture(admin)
        admin.post('/admin/verify', data={'captcha': 'AB7KM'})
        self.pin = admin.post('/api/admin/pins/issue', json={'student_id': ALEX}).json['pin']

    def guess(self, pin='wrong'):
        return lambda: self.app.test_client().post('/student/login', data={'student_id': ALEX, 'pin': pin}).status_code

    def test_guesses_made_at_the_same_moment_are_all_counted_so_the_lockout_still_comes(self):
        results = run_together([self.guess() for _ in range(GUESSES)])
        self.assertEqual([r for r in results if isinstance(r, BaseException)], [])
        # Counted one by one: the first FAILS_ALLOWED are plain wrong guesses, every later one meets the lock.
        self.assertEqual(results.count(401), FAILS_ALLOWED)
        self.assertEqual(results.count(429), GUESSES - FAILS_ALLOWED)
        with self.db() as db:
            locked_until = db.execute('SELECT pin_locked_until FROM inventory WHERE student_id = %s', (ALEX,)).fetchone()[0]
        self.assertGreater(locked_until, 0)
        self.assertEqual(self.guess(self.pin)(), 429)           # even the right PIN waits while it is locked

    def test_a_wrong_guess_waits_for_whoever_is_already_changing_that_student(self):
        waiting, outcome = self.waits_for_lock(
            lambda db: db.execute('SELECT 1 FROM inventory WHERE student_id = %s FOR UPDATE', (ALEX,)),
            self.guess())
        self.assertTrue(waiting)
        self.assertEqual(outcome, [401])


class TwoFactorRaceTests(EnvTestCase):
    def signed_in_admins(self, count):
        clients = []
        for _ in range(count):
            client = self.app.test_client()
            client.post('/admin/login', data=CREDS)
            self.picture(client)
            client.post('/admin/verify', data={'captcha': 'AB7KM'})
            clients.append(client)
        return clients

    def enable(self, client):
        secret = client.post('/api/admin/totp/begin', json={}).json['secret'].replace(' ', '')
        done = client.post('/api/admin/totp/enable', json={'code': totp.code_at(secret, totp.counter_for())})
        self.assertEqual(done.status_code, 200, done.json)
        return secret, done.json['recovery']

    def test_one_code_cannot_be_spent_twice_by_requests_made_together(self):
        clients = self.signed_in_admins(6)
        secret, _ = self.enable(clients[0])
        code = totp.code_at(secret, totp.counter_for() + 1)         # a step the setup code did not use
        results = run_together([
            (lambda c=client: c.post('/api/admin/totp/recovery', json={'code': code}).status_code) for client in clients])
        self.assertEqual([r for r in results if isinstance(r, BaseException)], [])
        self.assertEqual(results.count(200), 1)

    def test_one_recovery_code_signs_in_only_one_person(self):
        first = self.signed_in_admins(1)[0]
        _, recovery = self.enable(first)
        waiting = []
        for _ in range(6):
            client = self.app.test_client()
            client.post('/admin/login', data=CREDS)
            self.picture(client)
            waiting.append(client)
        results = run_together([
            (lambda c=client: c.post('/admin/verify', data={'captcha': 'AB7KM', 'code': recovery[0]}).status_code)
            for client in waiting])
        self.assertEqual([r for r in results if isinstance(r, BaseException)], [])
        in_now = [c for c in waiting if c.get('/api/admin/totp').status_code == 200]
        self.assertEqual(len(in_now), 1)
        self.assertEqual(first.get('/api/admin/totp').json['recovery_left'], len(recovery) - 1)

    def test_a_code_check_waits_for_whoever_is_already_changing_the_two_factor_record(self):
        client = self.signed_in_admins(1)[0]
        secret, _ = self.enable(client)
        code = totp.code_at(secret, totp.counter_for() + 1)
        waiting, outcome = self.waits_for_lock(
            lambda db: db.execute("SELECT 1 FROM app_settings WHERE key = 'admin_totp' FOR UPDATE"),
            lambda: client.post('/api/admin/totp/recovery', json={'code': code}).status_code)
        self.assertTrue(waiting)
        self.assertEqual(outcome, [200])


class SharedCookieTests(DatabaseTestCase):
    """Admin, coordinator and student share one cookie. A page that only asks "anything new?" must not rewrite it."""

    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, {**ENV, 'ADMIN_CAPTCHA': '0'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()

    def sign_in_admin_and_coordinator(self):
        client = self.app.test_client()
        client.post('/admin/login', data=CREDS)
        client.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'coord-pass'})
        return client

    def test_a_poll_does_not_send_the_cookie_again(self):
        client = self.sign_in_admin_and_coordinator()
        for role in ('admin', 'coordinator'):
            reply = client.get(f'/api/live?role={role}')
            self.assertEqual(reply.status_code, 200, role)
            self.assertEqual(reply.headers.getlist('Set-Cookie'), [], role)

    def test_a_late_poll_reply_cannot_sign_out_a_role_that_signed_in_meanwhile(self):
        admin_only = self.app.test_client()
        admin_only.post('/admin/login', data=CREDS)
        old_cookie = admin_only.get_cookie('session').value
        late = self.app.test_client()                        # the poll was sent with the cookie as it was then
        late.set_cookie('session', old_cookie)
        reply = late.get('/api/live?role=admin')
        # ...and while it was on its way the coordinator signed in from another tab:
        admin_only.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'coord-pass'})
        for header in reply.headers.getlist('Set-Cookie'):   # the browser would store whatever the late reply says
            self.fail(f'the poll sent a cookie: {header[:60]}')
        self.assertEqual(admin_only.get('/api/live?role=coordinator').status_code, 200)
        self.assertEqual(admin_only.get('/api/live?role=admin').status_code, 200)

    def test_the_idle_clock_still_moves_but_only_now_and_then(self):
        client = self.sign_in_admin_and_coordinator()
        with client.session_transaction() as s:
            s['seen_admin'] = time.time() - 120
        reply = client.get('/api/live?role=admin')
        self.assertEqual(len(reply.headers.getlist('Set-Cookie')), 1)
        with client.session_transaction() as s:
            self.assertLess(time.time() - s['seen_admin'], 5)
        self.assertEqual(client.get('/api/live?role=admin').headers.getlist('Set-Cookie'), [])


if __name__ == '__main__':
    unittest.main()
