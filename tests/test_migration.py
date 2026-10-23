"""The one-time SQLite -> PostgreSQL copy: what it copies, what it refuses, and that it never loses a row."""
import contextlib
import hashlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import DEMO_COLUMNS, LEGACY_DEMO_RECORDS
from migrate_sqlite_to_postgres import MigrationError, main, migrate
from pg_support import DatabaseTestCase

# The tables exactly as the SQLite version of the app created them.
INVENTORY = '''CREATE TABLE inventory (
    id INTEGER PRIMARY KEY, student_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL, course TEXT NOT NULL, section TEXT NOT NULL,
    year_level INTEGER NOT NULL CHECK(year_level BETWEEN 1 AND 6),
    status TEXT NOT NULL, request_type TEXT NOT NULL, pending_reason TEXT NOT NULL DEFAULT '',
    schedule_type TEXT NOT NULL DEFAULT '', schedule_date TEXT NOT NULL DEFAULT '',
    schedule_time TEXT NOT NULL DEFAULT '', token TEXT NOT NULL UNIQUE,
    notes TEXT NOT NULL DEFAULT '')'''
CHAT_HISTORY = '''CREATE TABLE chat_history (
    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'bot')),
    message TEXT NOT NULL, intent TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    record_id INTEGER, timestamp TEXT NOT NULL, workspace_role TEXT NOT NULL DEFAULT '')'''
OTHER_TABLES = ['''CREATE TABLE chat_feedback (
    id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL UNIQUE,
    rating TEXT NOT NULL CHECK(rating IN ('up', 'down')),
    reason TEXT NOT NULL DEFAULT '', comment TEXT NOT NULL DEFAULT '',
    workspace_role TEXT NOT NULL DEFAULT '', intent TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0, shared_question TEXT NOT NULL DEFAULT '',
    correct_intent TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'New' CHECK(status IN ('New', 'Reviewed')),
    timestamp TEXT NOT NULL)''',
                '''CREATE TABLE gate_logs (
    id INTEGER PRIMARY KEY, student_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '', direction TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '', timestamp TEXT NOT NULL)''',
                '''CREATE TABLE incidents (
    id INTEGER PRIMARY KEY, category TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL, student_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Open', timestamp TEXT NOT NULL)''',
                '''CREATE TABLE student_requests (
    id INTEGER PRIMARY KEY, student_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
    request_type TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Submitted', timestamp TEXT NOT NULL)''',
                '''CREATE TABLE activity_log (
    id INTEGER PRIMARY KEY, actor_role TEXT NOT NULL, action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '', timestamp TEXT NOT NULL)''']
# What the very first versions created: no notes, no workspace_role (and, earliest of all, no token).
OLD_INVENTORY = INVENTORY.replace(",\n    notes TEXT NOT NULL DEFAULT ''", '')
OLD_CHAT_HISTORY = CHAT_HISTORY.replace(', workspace_role TEXT NOT NULL DEFAULT \'\'', '')
OLDEST_INVENTORY = OLD_INVENTORY.replace(', token TEXT NOT NULL UNIQUE', '')
assert 'notes' not in OLD_INVENTORY and 'workspace_role' not in OLD_CHAT_HISTORY and 'token' not in OLDEST_INVENTORY

ADD_STUDENT = ('INSERT INTO inventory (id,student_id,name,course,section,year_level,status,request_type,'
               'pending_reason,schedule_type,schedule_date,schedule_time,token,notes) '
               'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
FULL_DATA = [
    (ADD_STUDENT, (1, 'DEMO-001', 'Alex Rivera', 'BSIT', 'A', 1, 'Ready for release', 'New ID', '',
                   'Release', '2026-11-04', '09:30', 'tok_one', 'Called guardian')),
    (ADD_STUDENT, (2, 'DEMO-002', 'Jamie Santos', 'BSCS', 'B', 2, 'Pending', 'Pending ID',
                   'Photo verification needed', '', '', '', 'tok-two', '')),
    (ADD_STUDENT, (7, 'LOST-7', 'Pat Lee', 'TBD', 'TBD', 1, 'Pending', 'Lost ID', 'Wallet stolen',
                   '', '', '', 'tok_seven', '')),
] + [('INSERT INTO chat_history VALUES (?,?,?,?,?,?,?,?,?)', row) for row in [
    (4, 's1', 'user', 'hello', '', 0, None, '2026-10-01 08:00:00', 'student'),
    (5, 's1', 'bot', 'Hi!', 'greeting', 0.8731, None, '2026-10-01 08:00:01', 'student'),
    (9, 's2', 'bot', 'Look up your ID', 'bot_help', 0.5, 2, '2026-10-02 09:00:00', 'guard')]] + [
    ('INSERT INTO chat_feedback VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
     (3, 5, 'down', 'Wrong answer', 'meh', 'student', 'greeting', 0.8731, 'hello', 'bot_help', 'Reviewed',
      '2026-10-01 08:05:00')),
    ('INSERT INTO gate_logs VALUES (?,?,?,?,?,?)', (1, 'DEMO-001', 'Alex Rivera', 'in', '', '2026-10-01 07:30:00')),
    ('INSERT INTO gate_logs VALUES (?,?,?,?,?,?)', (2, '', 'Visitor', 'out', 'Left early', '2026-10-01 17:00:00')),
    ('INSERT INTO incidents VALUES (?,?,?,?,?,?,?)',
     (1, 'No ID', 'Main gate', 'Visitor without a card', '', 'Open', '2026-10-01 07:45:00')),
    ('INSERT INTO student_requests VALUES (?,?,?,?,?,?,?)',
     (1, 'LOST-7', 'Pat Lee', 'Lost ID', 'Wallet stolen', 'Accepted', '2026-10-01 10:00:00')),
    ('INSERT INTO student_requests VALUES (?,?,?,?,?,?,?)',
     (2, 'DEMO-002', 'Jamie Santos', 'Status inquiry', '', 'Submitted', '2026-10-02 10:00:00')),
] + [('INSERT INTO activity_log VALUES (?,?,?,?,?)', (n, 'coordinator', 'Added record', f'ID-{n}', '2026-10-01 09:00:00'))
     for n in (1, 2, 3)]
FULL_COUNTS = {'inventory': 3, 'chat_history': 3, 'chat_feedback': 1, 'gate_logs': 2, 'incidents': 1,
               'student_requests': 2, 'activity_log': 3}


# The six demo students exactly as the SQLite version of the app stored them (note the old status name).
ORIGINAL_DEMO = (
    ('DEMO-001', 'Alex Rivera', 'BSIT', 'A', 1, 'Ready for release', 'New ID', ''),
    ('DEMO-002', 'Jamie Santos', 'BSCS', 'B', 2, 'Pending', 'Pending ID', 'Photo verification needed'),
    ('DEMO-003', 'Sam Reyes', 'BSBA', 'A', 3, 'Processing', 'Lost ID', ''),
    ('DEMO-004', 'Taylor Cruz', 'BSIT', 'B', 4, 'Released', 'New ID', ''),
    ('DEMO-005', 'Casey Garcia', 'BSED', 'A', 1, 'Processing', 'New ID', ''),
    ('DEMO-006', 'Jordan Flores', 'BSCS', 'A', 2, 'Pending', 'Pending ID', 'Enrollment verification needed'),
)


def demo_rows(with_notes=True, with_token=True):
    """The six demo students the way the first version of the app stored them."""
    rows = []
    for number, demo in enumerate(ORIGINAL_DEMO, start=1):
        columns = ['id', 'student_id', 'name', 'course', 'section', 'year_level', 'status', 'request_type',
                   'pending_reason', 'schedule_type', 'schedule_date', 'schedule_time']
        values = [number, *demo, '', '', '']
        if with_token:
            columns.append('token')
            values.append(f'demo_token_{number}')
        if with_notes:
            columns.append('notes')
            values.append('')
        rows.append((f'INSERT INTO inventory ({",".join(columns)}) VALUES ({",".join("?" * len(values))})', tuple(values)))
    return rows


class MigrationTestCase(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.messages = []

    def sqlite_file(self, ddl, rows, name='old.sqlite3'):
        path = self.folder / name
        connection = sqlite3.connect(path)
        try:
            with connection:
                for statement in ddl:
                    connection.execute(statement)
                for statement, params in rows:
                    connection.execute(statement, params)
        finally:
            connection.close()                # an open file cannot be deleted on Windows
        return path

    def full_file(self):
        return self.sqlite_file([INVENTORY, CHAT_HISTORY, *OTHER_TABLES], FULL_DATA)

    def run_migration(self, path, **options):
        return migrate(path, self.url, say=self.messages.append, **options)

    def table_exists(self, table):
        with self.db() as db:
            return db.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] is not None


class CopyTests(MigrationTestCase):
    def test_copies_every_table_with_the_original_ids_and_the_app_carries_on(self):
        self.assertEqual(self.run_migration(self.full_file()), FULL_COUNTS)
        client = self.make_app().test_client()
        records = client.get('/api/inventory').json['records']
        self.assertEqual([r['id'] for r in records], [7, 2, 1])             # nothing re-seeded, ids kept
        first = records[-1]
        self.assertEqual((first['token'], first['notes'], first['schedule_time']), ('tok_one', 'Called guardian', '09:30'))
        self.assertEqual((first['status'], first['address'], first['contact_number']), ('For release', '', ''))
        self.assertTrue(any('"Ready for release" is now called "For release" (1 rows changed)' in m for m in self.messages))
        incident = client.get('/api/incidents').json['incidents'][0]
        self.assertEqual((incident['false_statement'], incident['penalty'], incident['student_name']), (False, 0, None))
        self.assertEqual(len(client.get('/api/gate').json['logs']), 2)
        self.assertEqual(len(client.get('/api/activity').json['events']), 3)
        self.assertEqual(client.get('/api/requests').json['counts']['Accepted'], 1)
        feedback = client.get('/api/feedback').json
        self.assertEqual((feedback['summary']['total'], feedback['items'][0]['correct_intent']), (1, 'bot_help'))
        history = client.get('/api/chat/history', query_string={'session_id': 's1'}).json['messages']
        self.assertEqual([(m['id'], m['rating']) for m in history], [(4, None), (5, 'down')])

        # New rows continue after the highest copied id instead of colliding with it.
        student = {'student_id': 'P202499041', 'name': 'Newcomer', 'year_section': '1A'}
        self.assertEqual(client.post('/api/inventory', json=student).json['id'], 8)
        reply = client.post('/api/chat', json={'message': 'hello', 'session_id': 's1', 'workspace_role': 'student'}).json
        self.assertEqual(reply['message_id'], 11)                          # user row 10, bot row 11
        rated = client.post('/api/chat/feedback', json={
            'message_id': reply['message_id'], 'session_id': 's1', 'rating': 'down', 'reason': 'Wrong answer'})
        self.assertEqual(rated.json['id'], 4)
        self.assertEqual(client.post('/api/gate', json={'name': 'Visitor', 'direction': 'in'}).json['id'], 3)
        self.assertEqual(client.post('/api/incidents', json={'category': 'No ID', 'detail': 'x'}).json['id'], 2)
        self.assertEqual(client.post('/api/requests', json={'student_id': 'P202499042', 'request_type': 'Other'}).json['id'], 3)

    def test_the_first_versions_of_the_database_get_defaults_for_the_newer_columns(self):
        path = self.sqlite_file([OLD_INVENTORY, OLD_CHAT_HISTORY], [
            *demo_rows(with_notes=False),
            ('INSERT INTO chat_history (session_id,role,message,timestamp) VALUES (?,?,?,?)',
             ('s', 'bot', 'old', '2026-01-01 00:00:00'))])
        self.assertEqual(self.run_migration(path), {'inventory': 6, 'chat_history': 1})
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inventory WHERE notes = ''").fetchone()[0], 6)
            self.assertEqual(db.execute('SELECT workspace_role FROM chat_history').fetchone()[0], '')
        self.assertEqual(len(self.make_app().test_client().get('/api/inventory').json['records']), 6)

    def test_the_old_status_name_becomes_for_release_and_nothing_else_changes(self):
        path = self.sqlite_file([INVENTORY], demo_rows())
        self.run_migration(path)
        with self.db() as db:
            statuses = [row[0] for row in db.execute('SELECT status FROM inventory ORDER BY id')]
        self.assertEqual(statuses, ['For release', 'Pending', 'Processing', 'Released', 'Processing', 'Pending'])
        with self.db() as db:                          # the SQLite file itself keeps the name it had
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inventory WHERE status = 'Ready for release'").fetchone()[0], 0)
        self.assertEqual(sqlite3.connect(path).execute("SELECT COUNT(*) FROM inventory WHERE status = 'Ready for release'").fetchone()[0], 1)

    def test_a_file_without_qr_tokens_gets_new_ones(self):
        path = self.sqlite_file([OLDEST_INVENTORY], demo_rows(with_notes=False, with_token=False))
        self.assertEqual(self.run_migration(path), {'inventory': 6})
        with self.db() as db:
            tokens = [row[0] for row in db.execute('SELECT token FROM inventory')]
        self.assertEqual(len(set(tokens)), 6)
        self.assertTrue(all(len(token) >= 20 for token in tokens))
        self.assertTrue(any('new ones were made' in message for message in self.messages))

    def test_columns_that_postgresql_does_not_have_are_ignored_with_a_note(self):
        path = self.sqlite_file([INVENTORY.replace("notes TEXT NOT NULL DEFAULT '')", "notes TEXT NOT NULL DEFAULT '', photo TEXT)")],
                                demo_rows())
        self.assertEqual(self.run_migration(path), {'inventory': 6})
        self.assertTrue(any('ignored columns' in message and 'photo' in message for message in self.messages))

    def test_nul_characters_are_removed_and_reported(self):
        rows = demo_rows()
        statement, values = rows[0]
        rows[0] = (statement, tuple('Al\x00ex Rivera' if value == 'Alex Rivera' else value for value in values))
        self.assertEqual(self.run_migration(self.sqlite_file([INVENTORY], rows)), {'inventory': 6})
        with self.db() as db:
            self.assertEqual(db.execute("SELECT name FROM inventory WHERE student_id = 'DEMO-001'").fetchone()[0], 'Alex Rivera')
        self.assertTrue(any('1 values with NUL characters removed' in message for message in self.messages))

    def test_the_sqlite_file_is_left_exactly_as_it_was(self):
        path = self.full_file()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        self.run_migration(path, dry_run=True)
        self.run_migration(path)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ['old.sqlite3'])      # no journal left behind


class SafetyTests(MigrationTestCase):
    def test_the_demo_students_of_a_first_start_are_replaced_by_the_real_ones(self):
        client = self.make_app().test_client()                               # a first start seeds six demo students
        self.assertEqual(len(client.get('/api/inventory').json['records']), 6)
        path = self.sqlite_file([OLD_INVENTORY], demo_rows(with_notes=False))
        self.assertEqual(self.run_migration(path), {'inventory': 6})
        self.assertTrue(any('replacing them' in message for message in self.messages))
        tokens = {r['token'] for r in client.get('/api/inventory').json['records']}
        self.assertEqual(tokens, {f'demo_token_{n}' for n in range(1, 7)})

    def test_a_database_that_has_your_own_data_is_never_overwritten(self):
        client = self.make_app().test_client()
        student = {'student_id': 'P202499043', 'name': 'Mine', 'year_section': '1A'}
        self.assertEqual(client.post('/api/inventory', json=student).status_code, 201)
        with self.assertRaises(MigrationError) as caught:
            self.run_migration(self.full_file())
        self.assertIn('already has data', str(caught.exception))
        self.assertEqual(len(client.get('/api/inventory').json['records']), 7)

    def test_demo_students_that_were_edited_count_as_your_data(self):
        client = self.make_app().test_client()
        demo = client.get('/api/inventory').json['records'][0]
        self.assertEqual(client.patch(f'/api/inventory/{demo["id"]}', json={'notes': 'edited'}).status_code, 200)
        with self.assertRaises(MigrationError):
            self.run_migration(self.full_file())

    def start_with_the_demo_students_of_the_earlier_versions(self):
        """The six DEMO-00n students that earlier versions seeded, as the upgrade leaves them."""
        client = self.make_app().test_client()
        with self.db() as db:
            db.execute('DELETE FROM inventory')
            for n, row in enumerate(LEGACY_DEMO_RECORDS):
                db.execute(f'INSERT INTO inventory ({DEMO_COLUMNS},token) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                           (*row, f'earlier_{n}'))
        return client

    def test_the_demo_students_of_the_earlier_versions_are_replaced_too(self):
        client = self.start_with_the_demo_students_of_the_earlier_versions()
        self.assertEqual({r['student_id'] for r in client.get('/api/inventory').json['records']},
                         {f'DEMO-00{n}' for n in range(1, 7)})
        path = self.sqlite_file([OLD_INVENTORY], demo_rows(with_notes=False))
        self.assertEqual(self.run_migration(path), {'inventory': 6})
        self.assertTrue(any('replacing them' in message for message in self.messages))
        tokens = {r['token'] for r in client.get('/api/inventory').json['records']}
        self.assertEqual(tokens, {f'demo_token_{n}' for n in range(1, 7)})

    def test_earlier_demo_students_that_were_edited_count_as_your_data_as_well(self):
        client = self.start_with_the_demo_students_of_the_earlier_versions()
        with self.db() as db:
            db.execute("UPDATE inventory SET name = 'Alex Riviera' WHERE student_id = 'DEMO-001'")
        with self.assertRaises(MigrationError):
            self.run_migration(self.full_file())
        self.assertEqual(len(client.get('/api/inventory').json['records']), 6)

    def test_a_dry_run_checks_everything_and_writes_nothing(self):
        self.assertEqual(self.run_migration(self.full_file(), dry_run=True), FULL_COUNTS)
        self.assertFalse(self.table_exists('inventory'))

    def test_ids_that_differ_only_in_letter_case_stop_the_copy_before_anything_is_written(self):
        rows = [(ADD_STUDENT, (1, 'abc-1', 'A', 'BSIT', 'A', 1, 'Pending', 'New ID', '', '', '', '', 't1', '')),
                (ADD_STUDENT, (2, 'ABC-1', 'B', 'BSIT', 'A', 1, 'Pending', 'New ID', '', '', '', '', 't2', ''))]
        with self.assertRaises(MigrationError) as caught:
            self.run_migration(self.sqlite_file([INVENTORY], rows))
        self.assertIn('abc-1 = ABC-1', str(caught.exception))
        self.assertFalse(self.table_exists('inventory'))

    def test_it_is_all_or_nothing_when_postgresql_rejects_a_row_late_in_the_copy(self):
        loose_chat = ('CREATE TABLE chat_history (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, '
                      "message TEXT NOT NULL, intent TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0, "
                      'record_id INTEGER, timestamp TEXT NOT NULL)')
        path = self.sqlite_file([INVENTORY, loose_chat], [
            *demo_rows(),
            ('INSERT INTO chat_history (session_id,role,message,timestamp) VALUES (?,?,?,?)',
             ('s', 'robot', 'not a valid role', '2026-01-01 00:00:00'))])
        with self.assertRaises(MigrationError) as caught:
            self.run_migration(path)
        self.assertIn('rejected', str(caught.exception))
        self.assertFalse(self.table_exists('inventory'))                     # the six inventory rows went with it

    def test_files_that_are_not_school_id_databases_get_clear_errors(self):
        with self.assertRaises(MigrationError) as missing:
            self.run_migration(self.folder / 'nope.sqlite3')
        self.assertIn('does not exist', str(missing.exception))
        text = self.folder / 'text.sqlite3'
        text.write_text('this is not a database at all' * 20, encoding='utf-8')
        with self.assertRaises(MigrationError) as foreign:
            self.run_migration(text)
        self.assertIn('not a readable', str(foreign.exception))
        with self.assertRaises(MigrationError) as empty:
            self.run_migration(self.sqlite_file(['CREATE TABLE other (x TEXT)'], [], name='other.sqlite3'))
        self.assertIn('none of the school ID tables', str(empty.exception))


class CommandLineTests(MigrationTestCase):
    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([str(arg) for arg in args])
        return code, out.getvalue(), err.getvalue()

    def test_it_asks_for_a_database_url_when_there_is_none(self):
        with patch.dict(os.environ, {'DATABASE_URL': ''}):
            code, _, err = self.run_main(self.full_file())
        self.assertEqual(code, 2)
        self.assertIn('DATABASE_URL', err)

    def test_it_copies_and_reports_the_rows_per_table(self):
        code, out, err = self.run_main(self.full_file(), '--database-url', self.url)
        self.assertEqual((code, err), (0, ''))
        for table, count in FULL_COUNTS.items():
            self.assertRegex(out, rf'{table}\s+{count} rows')
        self.assertIn('Every row was read back and matched', out)

    def test_it_reads_the_url_from_the_environment_and_supports_dry_run(self):
        with patch.dict(os.environ, {'DATABASE_URL': self.url}):
            code, out, _ = self.run_main(self.full_file(), '--dry-run')
        self.assertEqual(code, 0)
        self.assertIn('dry run, nothing written', out)
        self.assertFalse(self.table_exists('inventory'))

    def test_a_problem_exits_with_1_and_says_nothing_was_copied(self):
        code, _, err = self.run_main(self.folder / 'missing.sqlite3', '--database-url', self.url)
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith('Nothing was copied.'))


if __name__ == '__main__':
    unittest.main()
