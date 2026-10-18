"""PostgreSQL behaviour: start-up, row locks and races, text PostgreSQL cannot store, lost connections."""
import logging
import os
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import psycopg

from Chatbot import create_app
from database import lock_student
from pg_support import DatabaseTestCase, close_app

STUDENT = {'student_id': 'RACE-3', 'name': 'Pat Lee', 'course': 'BSIT', 'section': 'A', 'year_level': 1}


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


class StartupTests(DatabaseTestCase):
    def test_the_database_url_can_come_from_the_environment(self):
        with patch.dict(os.environ, {'DATABASE_URL': self.url}):
            app = create_app()
        self.addCleanup(close_app, app)
        self.assertEqual(app.test_client().get('/api/inventory').status_code, 200)

    def test_a_missing_database_url_says_what_to_do(self):
        with patch.dict(os.environ, {'DATABASE_URL': ''}):
            with self.assertRaises(RuntimeError) as caught:
                create_app()
        self.assertIn('DATABASE_URL', str(caught.exception))
        self.assertIn('docker-compose.yml', str(caught.exception))

    def test_an_unreachable_database_is_a_clear_error(self):
        with self.assertRaises(RuntimeError) as caught:
            create_app('postgresql://nobody:nothing@127.0.0.1:1/none')
        self.assertIn('Cannot connect to PostgreSQL', str(caught.exception))

    def test_demo_students_are_added_once_even_when_several_apps_start_together(self):
        results = run_together([self.make_app for _ in range(5)])
        self.assertEqual([r for r in results if isinstance(r, BaseException)], [])
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inventory').fetchone()[0], 6)

    def test_restarting_keeps_the_data_and_adds_no_demo_students(self):
        first = self.make_app().test_client()
        self.assertEqual(first.post('/api/inventory', json=STUDENT).status_code, 201)
        records = self.make_app().test_client().get('/api/inventory').json['records']
        self.assertEqual(len(records), 7)
        self.assertEqual(records[0]['student_id'], 'RACE-3')


class PostgresBehaviourTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.app = self.make_app()
        self.client = self.app.test_client()
        # A new pool opens connections one by one, which would line concurrent requests up behind
        # each other. Open eight first, so the "at once" tests below are really at once.
        pool = self.app.extensions['database'].pool
        held = [pool.getconn(timeout=10) for _ in range(8)]
        for connection in held:
            pool.putconn(connection)

    def new_client(self):
        return self.app.test_client()

    def record(self, record_id):
        return next(r for r in self.client.get('/api/inventory').json['records'] if r['id'] == record_id)

    # ── the database enforces what the app promises ──────────────
    def test_the_database_itself_rejects_ids_that_differ_only_in_letter_case(self):
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.db() as db:
                db.execute("INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,token) "
                           "VALUES ('demo-001','x','x','x',1,'Pending','New ID','token-ci')")

    def test_ids_are_never_reused_after_a_delete(self):
        first = self.client.post('/api/inventory', json=STUDENT).json['id']
        self.assertEqual(self.client.delete(f'/api/inventory/{first}').status_code, 200)
        second = self.client.post('/api/inventory', json=STUDENT).json['id']
        self.assertGreater(second, first)

    def test_appointment_dates_are_stored_in_one_fixed_shape_so_they_sort(self):
        first_of_next_month = (datetime.now().replace(day=1) + timedelta(days=32)).replace(day=1)
        ids = [r['id'] for r in self.client.get('/api/inventory').json['records']][:2]
        month = f'{first_of_next_month.year}-{first_of_next_month.month:02d}'
        saved = self.client.patch(f'/api/inventory/{ids[0]}', json={
            'schedule_type': 'Release', 'schedule_date': f'{month}-9', 'schedule_time': '9:05'})
        self.assertEqual((saved.json['schedule_date'], saved.json['schedule_time']), (f'{month}-09', '09:05'))
        self.client.patch(f'/api/inventory/{ids[1]}', json={
            'schedule_type': 'Release', 'schedule_date': f'{month}-10', 'schedule_time': '08:00'})
        upcoming = [row['schedule_date'] for row in self.client.get('/api/stats').json['upcoming_appointments']]
        self.assertEqual(upcoming, [f'{month}-09', f'{month}-10'])

    def test_verify_lists_exact_matches_before_name_matches(self):
        self.client.post('/api/inventory', json={**STUDENT, 'student_id': 'NAMEHIT-1', 'name': 'Has EXACT-9 in the name'})
        self.client.post('/api/inventory', json={**STUDENT, 'student_id': 'EXACT-9', 'name': 'Someone Else'})
        found = [m['student_id'] for m in self.client.get('/api/verify', query_string={'q': 'exact-9'}).json['matches']]
        self.assertEqual(found, ['EXACT-9', 'NAMEHIT-1'])

    # ── races ────────────────────────────────────────────────────
    def test_accepting_the_same_lost_id_request_many_times_at_once_changes_the_record_once(self):
        ticket = self.client.post('/api/requests', json={
            'student_id': 'RACE-1', 'name': 'Pat Lee', 'request_type': 'Lost ID', 'detail': 'Wallet'}).json['id']
        results = run_together([
            lambda: self.new_client().patch(f'/api/requests/{ticket}', json={'status': 'Accepted'}).status_code
            for _ in range(8)])
        self.assertEqual(results, [200] * 8)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inventory WHERE student_id = 'RACE-1'").fetchone()[0], 1)
        events = [e for e in self.client.get('/api/activity').json['events'] if e['action'] == 'Updated request']
        self.assertEqual(len(events), 1)

    def test_two_lost_id_requests_for_one_new_student_create_one_record(self):
        tickets = [self.client.post('/api/requests', json={
            'student_id': sid, 'name': 'Pat Lee', 'request_type': 'Lost ID'}).json['id'] for sid in ('RACE-2', 'race-2')]
        results = run_together([
            lambda ticket=ticket: self.new_client().patch(f'/api/requests/{ticket}', json={'status': 'Accepted'}).status_code
            for ticket in tickets])
        self.assertEqual(results, [200, 200])
        with self.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM inventory WHERE LOWER(student_id) = 'race-2'").fetchone()[0], 1)

    def test_adding_the_same_student_many_times_at_once_creates_one_record(self):
        results = run_together([
            lambda: self.new_client().post('/api/inventory', json=STUDENT).status_code for _ in range(8)])
        self.assertEqual(sorted(results), [201] + [409] * 7)

    def test_edits_to_different_fields_made_at_once_are_all_kept(self):
        record_id = self.client.get('/api/inventory').json['records'][0]['id']
        edits = [{'notes': 'called the guardian'}, {'pending_reason': 'photo is blurry'}, {'status': 'Processing'},
                 {'schedule_type': 'Release', 'schedule_date': '2030-01-02', 'schedule_time': '10:30'}]
        results = run_together([
            lambda edit=edit: self.new_client().patch(f'/api/inventory/{record_id}', json=edit).status_code
            for edit in edits])
        self.assertEqual(results, [200] * 4)
        row = self.record(record_id)
        self.assertEqual((row['notes'], row['pending_reason'], row['status']),
                         ('called the guardian', 'photo is blurry', 'Processing'))
        self.assertEqual((row['schedule_type'], row['schedule_date'], row['schedule_time']),
                         ('Release', '2030-01-02', '10:30'))

    # The tests above run real races, so they can pass by luck. These hold a lock on purpose and
    # prove that the action really waits for it.
    def waits_for_lock(self, take_lock, action):
        """take_lock(connection) locks something in a transaction that stays open while action() runs
        on another thread. Returns (was it still waiting after 0.7 s, its result once the lock is released)."""
        outcome = []
        with self.db() as holder:
            take_lock(holder)
            thread = threading.Thread(target=lambda: outcome.append(action()))
            thread.start()
            thread.join(0.7)
            still_waiting = thread.is_alive() and not outcome
        thread.join(15)
        return still_waiting, outcome

    def test_an_edit_is_applied_on_top_of_an_edit_that_has_not_finished_yet(self):
        record_id = self.client.get('/api/inventory').json['records'][0]['id']

        def hold(db):                                   # another coordinator is saving notes right now
            db.execute('SELECT 1 FROM inventory WHERE id = %s FOR UPDATE', (record_id,))
            db.execute("UPDATE inventory SET notes = 'from the other coordinator' WHERE id = %s", (record_id,))

        waiting, outcome = self.waits_for_lock(
            hold, lambda: self.new_client().patch(f'/api/inventory/{record_id}', json={'status': 'Processing'}).status_code)
        self.assertTrue(waiting)
        self.assertEqual(outcome, [200])
        row = self.record(record_id)
        self.assertEqual((row['status'], row['notes']), ('Processing', 'from the other coordinator'))

    def test_a_request_cannot_be_moved_on_from_a_state_that_someone_else_is_changing(self):
        ticket = self.client.post('/api/requests', json={'student_id': 'WAIT-1', 'request_type': 'Other'}).json['id']

        def hold(db):                                   # another coordinator is closing it right now
            db.execute('SELECT 1 FROM student_requests WHERE id = %s FOR UPDATE', (ticket,))
            db.execute("UPDATE student_requests SET status = 'Closed' WHERE id = %s", (ticket,))

        waiting, outcome = self.waits_for_lock(
            hold, lambda: self.new_client().patch(f'/api/requests/{ticket}', json={'status': 'Accepted'}).status_code)
        self.assertTrue(waiting)
        self.assertEqual(outcome, [409])                # a closed request stays closed

    def test_adding_a_student_waits_while_that_student_is_being_created_elsewhere(self):
        waiting, outcome = self.waits_for_lock(
            lambda db: lock_student(db, 'LOCK-1'),
            lambda: self.new_client().post('/api/inventory', json={**STUDENT, 'student_id': 'lock-1'}).status_code)
        self.assertTrue(waiting)
        self.assertEqual(outcome, [201])

    def test_accepting_a_lost_id_request_waits_while_that_student_is_being_created_elsewhere(self):
        ticket = self.client.post('/api/requests', json={
            'student_id': 'LOCK-2', 'name': 'Pat Lee', 'request_type': 'Lost ID'}).json['id']
        waiting, outcome = self.waits_for_lock(
            lambda db: lock_student(db, 'lock-2'),
            lambda: self.new_client().patch(f'/api/requests/{ticket}', json={'status': 'Accepted'}).status_code)
        self.assertTrue(waiting)
        self.assertEqual(outcome, [200])

    def test_more_requests_at_once_than_the_pool_has_connections_all_succeed(self):
        results = run_together([lambda: self.new_client().get('/api/stats').status_code for _ in range(40)])
        self.assertEqual(set(results), {200})

    # ── text PostgreSQL cannot store ─────────────────────────────
    def test_nul_characters_are_a_bad_request_or_not_found_never_a_server_error(self):
        for method, url, expected in [
                ('get', '/student/a%00b', 404),
                ('get', '/api/chat/history?session_id=a%00b', 200),
                ('delete', '/api/chat/history?session_id=a%00b', 200),
                ('get', '/api/verify?q=%00', 400),
                ('delete', '/api/chat/feedback?message_id=1&session_id=a%00b', 400)]:
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, expected)
        for url, payload in [('/api/inventory', {**STUDENT, 'name': 'Al\x00ex'}),
                             ('/api/chat', {'message': 'hi\x00there', 'session_id': 's'}),
                             ('/api/requests', {'student_id': 'A\x00B', 'request_type': 'Other'})]:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, json=payload).status_code, 400)

    def test_text_that_slips_past_the_checks_is_still_a_400_and_the_app_carries_on(self):
        with patch('Chatbot.clean_text', return_value=True):
            for text in ('hi\x00there', 'lone \ud800 surrogate'):
                with self.subTest(text=text):
                    response = self.client.post('/api/chat', json={'message': text, 'session_id': 'net'})
                    self.assertEqual(response.status_code, 400)
                    self.assertIn('error', response.json)
        self.assertEqual(self.client.get('/api/inventory').status_code, 200)

    # ── the database goes away ───────────────────────────────────
    def test_a_database_that_is_gone_is_a_503_not_a_crash(self):
        self.app.extensions['database'].close()
        api = self.client.get('/api/inventory')
        self.assertEqual(api.status_code, 503)
        self.assertIn('not available', api.json['error'])
        self.assertEqual(self.client.get('/student/anything').status_code, 503)

    def test_the_app_recovers_when_the_server_drops_its_connections(self):
        pool_log = logging.getLogger('psycopg.pool')    # it warns about every dead connection it discards; expected here
        self.addCleanup(pool_log.setLevel, pool_log.level)
        pool_log.setLevel(logging.ERROR)
        self.assertEqual(self.client.get('/api/inventory').status_code, 200)
        with psycopg.connect(self.schema.base_url, autocommit=True) as admin:
            dropped = admin.execute('SELECT COUNT(pg_terminate_backend(pid)) FROM pg_stat_activity '
                                    'WHERE application_name = %s', (self.schema.name,)).fetchone()[0]
        self.assertGreaterEqual(dropped, 1)
        self.assertEqual(self.client.get('/api/inventory').status_code, 200)
        self.assertEqual(self.client.post('/api/inventory', json=STUDENT).status_code, 201)


if __name__ == '__main__':
    unittest.main()
