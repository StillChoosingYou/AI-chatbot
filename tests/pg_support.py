"""Test helpers: every database test gets its own empty schema inside TEST_DATABASE_URL.

    export TEST_DATABASE_URL=postgresql://school_id:school_id_dev@localhost:5432/school_id

The schema is named test_<random> and dropped afterwards. The app under test only sees that schema,
so your own tables (in "public") are never read, changed or dropped. Without TEST_DATABASE_URL the
database tests are skipped, and unittest's summary says how many.
"""
import os
import threading
import unittest
import uuid
from contextlib import contextmanager

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from Chatbot import create_app

TEST_DATABASE_URL = os.environ.get('TEST_DATABASE_URL', '').strip()
SKIP_MESSAGE = 'TEST_DATABASE_URL is not set, so this database test was skipped (see "Verification" in README.md)'


class TemporarySchema:
    """A throwaway schema. `url` connects to the same database with only that schema on the search path."""

    def __init__(self, base_url):
        self.base_url = base_url
        self.name = 'test_' + uuid.uuid4().hex[:12]
        with psycopg.connect(base_url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.name)))
        self.url = make_conninfo(base_url, options=f'-c search_path={self.name}', application_name=self.name)

    def drop(self):
        with psycopg.connect(self.base_url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(self.name)))


def close_app(app):
    app.extensions['database'].close()


class DatabaseTestCase(unittest.TestCase):
    """Each test runs against its own empty schema. Use make_app() to start an app on it."""

    def setUp(self):
        super().setUp()
        if not TEST_DATABASE_URL:
            self.skipTest(SKIP_MESSAGE)
        self.schema = TemporarySchema(TEST_DATABASE_URL)
        self.addCleanup(self.schema.drop)          # cleanups run last-in first-out: apps close before this
        self.url = self.schema.url

    def make_app(self):
        app = create_app(self.url)
        self.addCleanup(close_app, app)
        return app

    @contextmanager
    def db(self):
        """A plain connection to this test's schema, for arranging or checking data directly."""
        with psycopg.connect(self.url) as connection:
            yield connection


# A valid student that is not one of the demo students (the demo students are P202400101 to P202400106).
NEW = {'student_id': 'P202499001', 'name': 'New Student', 'year_section': '2A'}


class AppTestCase(DatabaseTestCase):
    """A running app on its own schema, with the six demo students already in it."""

    def setUp(self):
        super().setUp()
        self.app = self.make_app()
        self.client = self.app.test_client()
        self.records = self.client.get('/api/inventory').json['records']
        self.row = self.records[-1]            # P202400101, Alex M. Rivera: the first demo student
        self.session = 'test-session:coordinator'

    def ask(self, message, role='coordinator', session=None, **extra):
        return self.client.post('/api/chat', json={
            'message': message, 'session_id': session or self.session, 'workspace_role': role, **extra}).json

    def rate(self, reply, rating='up', session=None, **extra):
        return self.client.post('/api/chat/feedback', json={
            'message_id': reply['message_id'], 'session_id': session or self.session, 'rating': rating, **extra})

    def raw_post(self, url, text):
        return self.client.post(url, data=text, content_type='application/json')

    def student(self, student_id):
        """The inventory record of one student, as the dashboard receives it."""
        return next(r for r in self.client.get('/api/inventory').json['records'] if r['student_id'] == student_id)


def run_together(jobs):
    """Run each callable on its own thread, all released at the same moment. Returns result or exception per job."""
    barrier, results = threading.Barrier(len(jobs)), [None] * len(jobs)

    def work(index, job):
        try:
            barrier.wait(timeout=20)
            results[index] = job()
        except BaseException as error:         # shown by the assertions, not lost inside a thread
            results[index] = error

    threads = [threading.Thread(target=work, args=(index, job)) for index, job in enumerate(jobs)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results
