"""Test helpers: every database test gets its own empty schema inside TEST_DATABASE_URL.

    export TEST_DATABASE_URL=postgresql://school_id:school_id_dev@localhost:5432/school_id

The schema is named test_<random> and dropped afterwards. The app under test only sees that schema,
so your own tables (in "public") are never read, changed or dropped. Without TEST_DATABASE_URL the
database tests are skipped, and unittest's summary says how many.
"""
import os
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
