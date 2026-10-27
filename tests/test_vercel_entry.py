"""The hosted entry point (app.py) and what Vercel needs around it.

Vercel starts the app by importing app.py and taking its top-level `app`. It cannot train the model, and it
installs exactly the scikit-learn version in requirements.txt, so the model committed to Git has to match that pin.
These tests load app.py the way a host does, with a made-up environment, and put the process back afterwards.
"""
import ast
import importlib.util
import os
import re
import time
import unittest
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import joblib
from flask import Flask

import Chatbot
from pg_support import DatabaseTestCase, close_app

ROOT = Path(__file__).resolve().parent.parent
LOG = 'school-id.start'


@contextmanager
def hosted(env=None, create_app=None):
    """Import app.py afresh, as a host does, and hand over the module. The environment and the clock go back after."""
    spec = importlib.util.spec_from_file_location('hosted_entry', ROOT / 'app.py')
    module = importlib.util.module_from_spec(spec)
    with ExitStack() as stack:
        if hasattr(time, 'tzset'):
            stack.callback(time.tzset)             # runs last: re-reads the TZ that patch.dict has put back
        stack.enter_context(patch.dict(os.environ, env or {}))
        if create_app is not None:
            stack.enter_context(patch.object(Chatbot, 'create_app', create_app))
        spec.loader.exec_module(module)
        yield module


def stub_app():
    real = Flask('stub')
    real.add_url_rule('/', 'home', lambda: 'hello from the real app')
    return real


class EntryFileTests(unittest.TestCase):
    def test_app_is_assigned_at_the_top_level(self):
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
        targets = [t.id for node in tree.body if isinstance(node, ast.Assign) for t in node.targets
                   if isinstance(t, ast.Name)]
        self.assertIn('app', targets, 'Vercel only sees `app = ...` written at the top level of app.py, '
                                      'not inside try, if or def')

    def test_python_version_is_one_vercel_offers(self):
        self.assertRegex((ROOT / '.python-version').read_text(encoding='utf-8'), r'^3\.(12|13)\s*$')

    def test_the_committed_model_was_trained_with_the_pinned_scikit_learn(self):
        pinned = re.search(r'^scikit-learn==([0-9.]+)\s*$', (ROOT / 'requirements.txt').read_text(encoding='utf-8'),
                           re.M).group(1)
        trained_with = joblib.load(ROOT / 'data' / 'intent_model.joblib')['sklearn_version']
        self.assertEqual(trained_with, pinned, 'Vercel installs the pinned version and cannot train. '
                                               'Run: python train_model.py, then commit data/intent_model.joblib')


class StandInTests(unittest.TestCase):
    """What a host gets when the app cannot start."""

    def test_without_a_database_every_request_gets_a_503_that_says_what_to_set(self):
        with self.assertLogs(LOG, 'ERROR'), hosted({'DATABASE_URL': ''}) as entry:
            self.assertIsInstance(entry.app, Flask)
            client = entry.app.test_client()
            page = client.get('/')
            self.assertEqual(page.status_code, 503)
            self.assertEqual(page.mimetype, 'text/html')
            self.assertIn('DATABASE_URL', page.get_data(as_text=True))
            self.assertEqual(page.headers['Cache-Control'], 'no-store')
            self.assertEqual(page.headers['Retry-After'], str(entry.RETRY_SECONDS))
            for method, path in (('get', '/api/inventory'), ('post', '/api/chat'), ('delete', '/api/inventory')):
                reply = getattr(client, method)(path)
                self.assertEqual(reply.status_code, 503, (method, path))
                self.assertIn('DATABASE_URL', reply.json['error'])
            head = client.head('/')
            self.assertEqual((head.status_code, head.data), (503, b''))
            self.assertEqual(client.get('/static/style.css').status_code, 503)      # a stand-in serves no files

    def test_the_503_never_shows_where_the_database_is(self):
        url = 'postgresql://someone:s3cr3t-pass@127.0.0.1:9/nodb'      # port 9: nothing listens, refused at once
        with self.assertLogs(LOG, 'ERROR') as logged, hosted({'DATABASE_URL': url}) as entry:
            reply = entry.app.test_client().get('/')
            shown = reply.get_data(as_text=True) + str(reply.headers)
            self.assertEqual(reply.status_code, 503)
            for secret in ('s3cr3t-pass', 'someone', '127.0.0.1', 'nodb'):
                self.assertNotIn(secret, shown)
        self.assertIn('Cannot connect to PostgreSQL', '\n'.join(logged.output))   # the owner still gets the reason

    def test_it_starts_the_real_app_by_itself_when_the_database_comes_back(self):
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError('the database is asleep')
            return stub_app()

        with self.assertLogs(LOG, 'WARNING'), hosted(create_app=flaky) as entry:
            entry.RETRY_SECONDS = 0
            client = entry.app.test_client()
            self.assertEqual(attempts, [1])                                  # the start at import failed
            self.assertEqual(client.get('/').status_code, 503)               # a second attempt, also failed
            self.assertEqual(len(attempts), 2)
            home = client.get('/')                                           # the third works and answers this request
            self.assertEqual((home.status_code, home.get_data(as_text=True)), (200, 'hello from the real app'))
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(len(attempts), 3)                               # started once, never again

    def test_it_does_not_hammer_a_database_that_is_down(self):
        attempts = []

        def down():
            attempts.append(1)
            raise RuntimeError('the database is down')

        with self.assertLogs(LOG, 'ERROR'), hosted(create_app=down) as entry:
            entry.RETRY_SECONDS = 1000
            client = entry.app.test_client()
            for _ in range(5):
                self.assertEqual(client.get('/').status_code, 503)
            self.assertEqual(len(attempts), 1)                               # only the start at import

    def test_only_one_request_tries_at_a_time(self):
        attempts = []

        def down():
            attempts.append(1)
            raise RuntimeError('the database is down')

        with self.assertLogs(LOG, 'ERROR'), hosted(create_app=down) as entry:
            entry.RETRY_SECONDS = 0
            with entry.app.wsgi_app.lock:                                    # another request is mid-attempt
                self.assertEqual(entry.app.test_client().get('/').status_code, 503)
            self.assertEqual(len(attempts), 1)


@unittest.skipUnless(hasattr(time, 'tzset'), "Windows keeps the computer's own clock zone")
class ClockTests(unittest.TestCase):
    @staticmethod
    def hours_ahead_of_utc():
        return round((datetime.now() - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 3600, 2)

    def clock_of_a_hosted_copy(self, school_tz):
        with self.assertLogs(LOG, 'ERROR'), hosted({'DATABASE_URL': '', 'SCHOOL_TZ': school_tz}):
            return self.hours_ahead_of_utc()

    def test_a_hosted_copy_runs_on_philippine_time(self):
        self.assertEqual(self.clock_of_a_hosted_copy(''), 8.0)

    def test_school_tz_changes_it(self):
        self.assertEqual(self.clock_of_a_hosted_copy('UTC'), 0.0)

    def test_the_clock_of_the_other_tests_is_put_back(self):
        before = self.hours_ahead_of_utc()
        self.clock_of_a_hosted_copy('')
        self.assertEqual(self.hours_ahead_of_utc(), before)


class RealAppTests(DatabaseTestCase):
    def test_with_a_database_app_is_the_real_chatbot(self):
        with hosted({'DATABASE_URL': self.url}) as entry:
            self.addCleanup(close_app, entry.app)
            self.assertIsInstance(entry.app, Flask)
            client = entry.app.test_client()
            self.assertEqual(client.get('/').status_code, 200)
            with client.get('/static/fx.js') as script:                      # Flask serves static/ itself, also on Vercel
                self.assertEqual(script.status_code, 200)
            self.assertGreaterEqual(len(client.get('/api/inventory').json['records']), 1)


if __name__ == '__main__':
    unittest.main()
