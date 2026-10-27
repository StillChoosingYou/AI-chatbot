import os
import unittest
from unittest.mock import patch

from auth import FAILS_ALLOWED, LOCK_SECONDS, CoordinatorLogin
from pg_support import DatabaseTestCase

PAIR = {'username': 'Coordinator', 'password': 'right-password'}
PROTECTED = ['/api/inventory', '/api/inventory/export', '/api/stats', '/api/queue', '/api/activity',
             '/api/requests', '/api/feedback', '/api/feedback/export', '/api/incidents', '/stickers?ids=1', '/sticker/1',
             '/card/1', '/qr/1.png']


class LoginRulesTests(unittest.TestCase):
    def test_off_without_a_password_and_on_with_one_or_on_vercel(self):
        self.assertFalse(CoordinatorLogin({}).enabled)
        self.assertTrue(CoordinatorLogin({'COORDINATOR_PASSWORD': 'x'}).enabled)
        on_vercel = CoordinatorLogin({'VERCEL': '1'})
        self.assertTrue(on_vercel.enabled)
        self.assertFalse(on_vercel.configured)                               # fails closed: nobody can sign in
        self.assertFalse(on_vercel.check('Coordinator', '', '1.1.1.1'))

    def test_username_ignores_case_and_spaces_but_the_password_is_exact(self):
        login = CoordinatorLogin({'COORDINATOR_PASSWORD': 'Secret1'})
        self.assertTrue(login.check(' coordinator ', 'Secret1', 'a'))
        self.assertFalse(login.check('Coordinator', 'secret1', 'a'))
        self.assertFalse(login.check('Other', 'Secret1', 'a'))
        self.assertEqual(CoordinatorLogin({'COORDINATOR_PASSWORD': 'x', 'COORDINATOR_USERNAME': 'Boss'}).username, 'Boss')

    def test_too_many_wrong_tries_lock_the_address_for_a_minute(self):
        login = CoordinatorLogin({'COORDINATOR_PASSWORD': 'Secret1'})
        for _ in range(FAILS_ALLOWED):
            self.assertFalse(login.check('Coordinator', 'wrong', 'a', now=100))
        self.assertTrue(login.locked('a', now=101))
        self.assertFalse(login.check('Coordinator', 'Secret1', 'a', now=101))    # even the right one waits
        self.assertTrue(login.check('Coordinator', 'Secret1', 'b', now=101))     # other addresses are not affected
        self.assertTrue(login.check('Coordinator', 'Secret1', 'a', now=100 + LOCK_SECONDS + 1))


class CoordinatorLoginRouteTests(DatabaseTestCase):
    def start(self, **environment):
        env = {'COORDINATOR_PASSWORD': 'right-password', **environment}
        with patch.dict(os.environ, env):
            app = self.make_app()
        return app.test_client()

    def test_nothing_of_the_coordinator_is_reachable_before_signing_in(self):
        client = self.start()
        for path in PROTECTED:
            with self.subTest(path=path):
                response = client.get(path, follow_redirects=False)
                self.assertIn(response.status_code, (401, 302))
                if response.status_code == 401:
                    self.assertEqual(response.get_json()['sign_in'], '/coordinator')
        for method, path in (('post', '/api/inventory'), ('patch', '/api/inventory/1'), ('delete', '/api/inventory/1'),
                             ('patch', '/api/requests/1'), ('patch', '/api/incidents/1')):
            with self.subTest(path=path, method=method):
                self.assertEqual(getattr(client, method)(path, json={}).status_code, 401)
        page = client.get('/coordinator').get_data(as_text=True)
        self.assertIn('Sign in', page)
        self.assertNotIn('data-page-role="coordinator"', page)

    def test_guard_student_and_the_chooser_stay_open(self):
        client = self.start()
        for path in ('/', '/guard', '/student', '/api/office', '/api/verify?q=P202400101'):
            with self.subTest(path=path):
                self.assertNotIn(client.get(path).status_code, (401, 302))

    def test_wrong_and_right_sign_in(self):
        client = self.start()
        wrong = client.post('/coordinator/login', data={**PAIR, 'password': 'nope'})
        self.assertEqual(wrong.status_code, 401)
        self.assertIn('Wrong username or password', wrong.get_data(as_text=True))
        self.assertEqual(client.get('/api/stats').status_code, 401)
        ok = client.post('/coordinator/login', data=PAIR)
        self.assertEqual((ok.status_code, ok.headers['Location']), (302, '/coordinator'))
        self.assertIn('data-page-role="coordinator"', client.get('/coordinator').get_data(as_text=True))
        self.assertEqual(client.get('/api/stats').status_code, 200)
        self.assertEqual(client.get('/api/inventory').status_code, 200)

    def test_sign_out_closes_it_again(self):
        client = self.start()
        client.post('/coordinator/login', data=PAIR)
        self.assertEqual(client.post('/coordinator/logout').status_code, 302)
        self.assertEqual(client.get('/api/inventory').status_code, 401)

    def test_naming_the_coordinator_role_in_the_chat_does_not_give_its_answers(self):
        client = self.start()
        reply = client.post('/api/chat', json={'message': 'Download the inventory as CSV', 'workspace_role': 'coordinator'})
        self.assertEqual(reply.status_code, 200)
        self.assertNotIn('export_csv', [a['type'] for a in reply.get_json().get('actions', [])])

    def test_a_server_with_no_password_set_on_vercel_lets_nobody_in(self):
        client = self.start(COORDINATOR_PASSWORD='', VERCEL='1')
        page = client.get('/coordinator').get_data(as_text=True)
        self.assertIn('not set up', page)
        self.assertEqual(client.post('/coordinator/login', data=PAIR).status_code, 401)
        self.assertEqual(client.get('/api/inventory').status_code, 401)


if __name__ == '__main__':
    unittest.main()
