"""The coordinator's dashboard: student fields, release hours, lost IDs, importing students, and the schema upgrade."""
import csv
import io
import unittest

from pg_support import NEW, AppTestCase, DatabaseTestCase, run_together
from student_data import IMPORT_COLUMNS, MAX_IMPORT_BYTES, MAX_IMPORT_ROWS, RELEASE_HOURS, STATUSES, read_student_csv

MONDAY = '2030-01-07'       # a Monday
SATURDAY = '2030-01-12'
HEADER = 'student_id,full_name,year_section,status,address,contact_number\n'


class DashboardFieldTests(AppTestCase):
    def test_every_record_has_the_six_dashboard_fields_and_the_server_lists_the_statuses(self):
        data = self.client.get('/api/inventory').json
        for record in data['records']:
            for field in ('student_id', 'name', 'year_section', 'status', 'address', 'contact_number'):
                self.assertIn(field, record)
        self.assertEqual(data['statuses'], list(STATUSES))
        self.assertEqual(data['not_yet_made'], ['Pending', 'Processing'])
        self.assertEqual(set(data['status_notes']), set(STATUSES))
        alex = self.student('P202400101')
        self.assertEqual((alex['name'], alex['year_section'], alex['status'], alex['contact_number']),
                         ('Alex M. Rivera', '1A', 'For release', '09000000101'))
        self.assertEqual([r['status'] for r in data['records']].count('Released'), 1)

    def test_adding_a_student_stores_all_six_fields_in_their_clean_form(self):
        created = self.client.post('/api/inventory', json={
            'student_id': ' p202400230 ', 'name': '  Reynald   Jake L.  Malabanan ', 'year_section': '3-e',
            'address': ' Purok 2,  Brgy. Poblacion ', 'contact_number': '+63 917 123 4567'})
        self.assertEqual(created.status_code, 201)
        record = created.json
        self.assertEqual((record['student_id'], record['name'], record['year_section'], record['status']),
                         ('P202400230', 'Reynald Jake L. Malabanan', '3E', 'Pending'))
        self.assertEqual((record['address'], record['contact_number'], record['request_type']),
                         ('Purok 2, Brgy. Poblacion', '09171234567', 'New ID'))
        self.assertEqual(len(record['token']), 24)
        bare = self.client.post('/api/inventory', json=NEW).json                # address and contact are optional
        self.assertEqual((bare['address'], bare['contact_number']), ('', ''))
        self.assertEqual(self.client.post('/api/inventory', json={**NEW, 'student_id': 'P202499002', 'request_type': 'Lost ID'}).json['request_type'], 'Lost ID')

    def test_a_student_with_a_bad_value_is_refused_and_the_message_says_what_to_fix(self):
        cases = [({'student_id': 'P2024'}, 'letter P and 9 digits'), ({'student_id': 'DEMO-9'}, 'letter P and 9 digits'),
                 ({'student_id': None}, 'letter P and 9 digits'), ({'name': ''}, 'Full name'), ({'name': ' '}, 'Full name'),
                 ({'name': 'x' * 101}, 'Full name'), ({'name': 7}, 'Full name'), ({'year_section': '9A'}, 'Year & Section'),
                 ({'address': 'x' * 201}, 'Address'), ({'address': 5}, 'Address'), ({'contact_number': 'call me'}, 'phone number'),
                 ({'contact_number': '0917123'}, 'phone number'), ({'request_type': 'Visitor'}, 'request type')]
        for change, words in cases:
            with self.subTest(change=str(change)[:40]):
                response = self.client.post('/api/inventory', json={**NEW, **change})
                self.assertEqual(response.status_code, 400)
                self.assertIn(words, response.json['error'])
        self.assertEqual(self.client.post('/api/inventory', json=[NEW]).status_code, 400)
        self.assertEqual(len(self.client.get('/api/inventory').json['records']), 6)           # nothing was added

    def test_the_fields_can_be_edited_but_not_the_student_id(self):
        record_id = self.student('P202400102')['id']
        response = self.client.patch(f'/api/inventory/{record_id}', json={
            'name': ' Jamie  L.  Santos-Cruz ', 'year_section': '4-b', 'address': 'New address', 'contact_number': '0917 111 2222'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json['name'], response.json['year_section'], response.json['address'], response.json['contact_number']),
                         ('Jamie L. Santos-Cruz', '4B', 'New address', '09171112222'))
        self.assertEqual((response.json['year_level'], response.json['section']), (4, 'B'))
        self.assertEqual(self.client.patch(f'/api/inventory/{record_id}', json={'address': '', 'contact_number': ''}).json['address'], '')
        for bad in ({'student_id': 'P202499999'}, {'token': 'x'}, {'name': ''}, {'year_section': '0A'}, {'contact_number': 'abc'},
                    {'address': 'x' * 201}, {'course': 'BSIT'}):
            self.assertEqual(self.client.patch(f'/api/inventory/{record_id}', json=bad).status_code, 400, bad)
        self.assertEqual(self.student('P202400102')['student_id'], 'P202400102')

    def test_every_spelling_of_a_status_is_accepted_and_stored_under_one_name(self):
        record_id = self.student('P202400102')['id']
        for written, stored in (('For released', 'For release'), ('ready for release', 'For release'), ('RELEASED', 'Released'),
                                ('processing', 'Processing'), ('Pending', 'Pending')):
            response = self.client.patch(f'/api/inventory/{record_id}', json={'status': written})
            self.assertEqual((response.status_code, response.json['status']), (200, stored), written)
        for bad in ('Done', '', 'Lost', 'x' * 40):
            self.assertEqual(self.client.patch(f'/api/inventory/{record_id}', json={'status': bad}).status_code, 400, bad)

    def test_select_all_then_change_the_status_of_everyone_at_once(self):
        ids = [r['id'] for r in self.records]
        done = self.client.patch('/api/inventory/bulk', json={'record_ids': ids, 'status': 'For released'})
        self.assertEqual((done.status_code, done.json['updated']), (200, 6))
        self.assertEqual({r['status'] for r in self.client.get('/api/inventory').json['records']}, {'For release'})
        some = self.client.patch('/api/inventory/bulk', json={'record_ids': ids[:2] + [987654], 'status': 'Pending'})
        self.assertEqual(some.json['updated'], 2)                                    # an id that no longer exists is skipped
        for bad in ({'record_ids': ids, 'status': 'Done'}, {'record_ids': ids}, {'record_ids': [], 'status': 'Pending'},
                    {'record_ids': 'all', 'status': 'Pending'}, {'record_ids': list(range(1, 502)), 'status': 'Pending'}):
            self.assertEqual(self.client.patch('/api/inventory/bulk', json=bad).status_code, 400, str(bad)[:50])
        self.assertIn('Bulk status update', [e['action'] for e in self.client.get('/api/activity').json['events']])

    def test_the_statistics_count_the_ids_that_are_not_made_yet_and_the_year_levels(self):
        stats = self.client.get('/api/stats').json
        self.assertEqual(stats['by_status'], {'For release': 1, 'Pending': 2, 'Processing': 2, 'Released': 1})
        self.assertEqual(stats['not_yet_made'], 4)
        self.assertEqual(stats['by_year'], {'Year 1': 2, 'Year 2': 2, 'Year 3': 1, 'Year 4': 1})
        self.assertNotIn('by_course', stats)

    def test_the_office_information_states_the_release_hours_with_the_lunch_break(self):
        office = self.client.get('/api/office').json
        self.assertEqual(office['release_hours'], RELEASE_HOURS)
        self.assertIn('closed 12:00 – 1:00 PM for lunch', office['hours'])
        self.assertIn('Cashier', office['requirements']['Lost ID'])


class ReleaseHoursApiTests(AppTestCase):
    def book(self, date, time, record_id=None):
        record_id = record_id or self.student('P202400102')['id']
        return self.client.patch(f'/api/inventory/{record_id}', json={
            'schedule_type': 'Release', 'schedule_date': date, 'schedule_time': time})

    def test_appointments_inside_the_hours_are_saved(self):
        for time in ('08:00', '09:30', '11:59', '13:00', '16:59'):
            self.assertEqual(self.book(MONDAY, time).status_code, 200, time)

    def test_lunch_before_opening_closing_time_and_weekends_are_refused_with_the_reason(self):
        for date, time, words in ((MONDAY, '12:00', 'lunch'), (MONDAY, '12:30', 'lunch'), (MONDAY, '07:59', '8:00 AM to 5:00 PM'),
                                  (MONDAY, '17:00', '8:00 AM to 5:00 PM'), (MONDAY, '23:30', '8:00 AM to 5:00 PM'),
                                  (SATURDAY, '09:00', 'Saturdays and Sundays'), ('2030-01-13', '10:00', 'Saturdays and Sundays')):
            with self.subTest(date=date, time=time):
                response = self.book(date, time)
                self.assertEqual(response.status_code, 400)
                self.assertIn(words, response.json['error'])
        self.assertEqual(self.student('P202400102')['schedule_date'], '')              # nothing was saved

    def test_an_appointment_that_was_there_before_is_not_checked_again_until_it_is_moved(self):
        record_id = self.student('P202400102')['id']
        with self.db() as db:                                   # an appointment on a Saturday, from before the hours existed
            db.execute("UPDATE inventory SET schedule_type='Release', schedule_date=%s, schedule_time='10:00' WHERE id=%s",
                       (SATURDAY, record_id))
        self.assertEqual(self.client.patch(f'/api/inventory/{record_id}', json={'notes': 'called'}).status_code, 200)
        self.assertEqual(self.client.patch(f'/api/inventory/{record_id}', json={'status': 'Processing'}).status_code, 200)
        self.assertEqual(self.book(SATURDAY, '10:30', record_id).status_code, 400)
        self.assertEqual(self.book(MONDAY, '10:30', record_id).status_code, 200)
        cleared = self.client.patch(f'/api/inventory/{record_id}', json={'schedule_type': '', 'schedule_date': '', 'schedule_time': ''})
        self.assertEqual(cleared.status_code, 200)

    def test_the_chat_gives_the_release_hours_with_the_release_date(self):
        record_id = self.student('P202400101')['id']
        self.assertEqual(self.book(MONDAY, '09:30', record_id).status_code, 200)
        reply = self.ask('When can I collect my school ID?', record_id=record_id)['reply']
        self.assertIn(f'{MONDAY} at 09:30', reply)
        self.assertIn(RELEASE_HOURS, reply)


class LostIdTests(AppTestCase):
    def give_release_appointment(self, record_id):
        with self.db() as db:
            db.execute("UPDATE inventory SET schedule_type='Release', schedule_date=%s, schedule_time='09:30' WHERE id=%s",
                       (MONDAY, record_id))

    def test_a_lost_id_sends_a_released_record_back_to_pending_and_ends_its_release_appointment(self):
        taylor = self.student('P202400104')
        self.assertEqual(taylor['status'], 'Released')
        self.give_release_appointment(taylor['id'])
        lost = self.client.post(f'/api/inventory/{taylor["id"]}/lost')
        self.assertEqual(lost.status_code, 200)
        self.assertEqual((lost.json['status'], lost.json['request_type'], lost.json['pending_reason']),
                         ('Pending', 'Lost ID', 'Lost ID reported to the office'))
        self.assertEqual((lost.json['schedule_type'], lost.json['schedule_date'], lost.json['schedule_time']), ('', '', ''))
        self.assertEqual(self.client.get('/api/stats').json['overdue_appointments'], 0)         # no stale appointment is left
        events = self.client.get('/api/activity').json['events']
        self.assertIn(('Lost ID', 'P202400104: Released → Pending'), [(e['action'], e['detail']) for e in events])
        again = self.client.post(f'/api/inventory/{taylor["id"]}/lost')                         # it is Pending now
        self.assertEqual(again.status_code, 409)
        self.assertIn('Pending', again.json['error'])

    def test_only_a_released_id_can_be_reported_lost(self):
        for student_id, status in (('P202400101', 'For release'), ('P202400102', 'Pending'), ('P202400103', 'Processing')):
            record = self.student(student_id)
            response = self.client.post(f'/api/inventory/{record["id"]}/lost')
            self.assertEqual(response.status_code, 409, student_id)
            self.assertIn(status, response.json['error'])
            self.assertEqual(self.student(student_id)['status'], status)
        self.assertEqual(self.client.post('/api/inventory/99999/lost').status_code, 404)

    def test_accepting_a_students_lost_id_request_does_the_same(self):
        taylor = self.student('P202400104')
        self.give_release_appointment(taylor['id'])
        ticket = self.client.post('/api/requests', json={
            'student_id': 'p202400104', 'request_type': 'Lost ID', 'detail': 'Wallet stolen on the jeepney'}).json
        self.assertEqual(self.student('P202400104')['status'], 'Released')                      # a report alone changes nothing
        self.assertEqual(self.client.patch(f'/api/requests/{ticket["id"]}', json={'status': 'Accepted'}).status_code, 200)
        after = self.student('P202400104')
        self.assertEqual((after['status'], after['request_type'], after['pending_reason']),
                         ('Pending', 'Lost ID', 'Wallet stolen on the jeepney'))
        self.assertEqual((after['schedule_type'], after['schedule_date']), ('', ''))
        self.assertIn('Lost ID', [e['action'] for e in self.client.get('/api/activity').json['events']])

    def test_a_card_that_was_never_released_keeps_its_release_appointment_when_a_request_is_accepted(self):
        alex = self.student('P202400101')
        self.give_release_appointment(alex['id'])
        ticket = self.client.post('/api/requests', json={'student_id': 'P202400101', 'request_type': 'Lost ID'}).json
        self.client.patch(f'/api/requests/{ticket["id"]}', json={'status': 'Accepted'})
        after = self.student('P202400101')
        self.assertEqual((after['status'], after['request_type'], after['schedule_date']), ('Pending', 'Lost ID', MONDAY))

    def test_a_lost_id_request_for_a_student_who_is_not_on_the_dashboard_adds_them_with_the_year_and_section_to_fill_in(self):
        ticket = self.client.post('/api/requests', json={
            'student_id': 'P202499050', 'name': 'Pat Lee', 'request_type': 'Lost ID', 'detail': 'Lost it'}).json
        self.client.patch(f'/api/requests/{ticket["id"]}', json={'status': 'Accepted'})
        pat = self.student('P202499050')
        self.assertEqual((pat['name'], pat['year_section'], pat['status'], pat['request_type'], pat['address']),
                         ('Pat Lee', 'TBD', 'Pending', 'Lost ID', ''))

    def test_a_student_request_needs_a_valid_id_and_a_lost_id_request_says_to_go_to_the_cashier(self):
        for bad in ('', 'P2024', 'DEMO-003', None, 12345, 'P2024\x0099999'):
            response = self.client.post('/api/requests', json={'student_id': bad, 'request_type': 'Other'})
            self.assertEqual(response.status_code, 400, repr(bad))
            self.assertIn('letter P and 9 digits', response.json['error'])
        lost = self.client.post('/api/requests', json={'student_id': 'P202400104', 'request_type': 'Lost ID'})
        self.assertEqual(lost.status_code, 201)
        self.assertIn('Cashier', lost.json['next_steps'][0])
        self.assertIn('Property Management Office', lost.json['next_steps'][1])
        other = self.client.post('/api/requests', json={'student_id': 'P202400104', 'request_type': 'Status inquiry'})
        self.assertEqual(other.json['next_steps'], [])
        self.assertEqual(lost.json['student_id'], 'P202400104')


class ImportApiTests(AppTestCase):
    def csv_file(self, *rows):
        return (HEADER + ''.join(row + '\n' for row in rows)).encode('utf-8')

    def upload(self, content, name='students.csv'):
        # The multipart body is written by hand: the test client would put a big file in a temporary file
        # it never closes, and the test run would end with "unclosed file" warnings.
        boundary = 'test-boundary-4f9c1e'
        if content is None:
            body = f'--{boundary}--\r\n'.encode()
        else:
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
                    'Content-Type: text/csv\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
        return self.client.post('/api/inventory/import', data=body, content_type=f'multipart/form-data; boundary={boundary}')

    ROWS = ('P202400301,Ana Lopez,1A,Pending,"12 Rizal St., Sample City",09171234567',
            'P202400302,Ben Uy,2B,For released,,',
            'P202400303,Cara Diaz,3E,,,',
            'P20240030,Bad Id,1A,,,',
            'p202400101,Alex Again,1A,,,')

    def test_students_are_added_the_bad_rows_are_reported_and_nobody_is_overwritten(self):
        response = self.upload(self.csv_file(*self.ROWS))
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json['added'], response.json['skipped'], response.json['total']), (3, 2, 5))
        bad, duplicate = response.json['problems']
        self.assertEqual((bad['line'], bad['student_id']), (5, 'P20240030'))
        self.assertIn('letter P and 9 digits', bad['error'])
        self.assertEqual((duplicate['line'], duplicate['student_id']), (6, 'P202400101'))   # reported in its cleaned form
        self.assertIn('Already on the dashboard', duplicate['error'])
        ana, ben, cara = (self.student(i) for i in ('P202400301', 'P202400302', 'P202400303'))
        self.assertEqual((ana['name'], ana['year_section'], ana['status'], ana['address'], ana['contact_number']),
                         ('Ana Lopez', '1A', 'Pending', '12 Rizal St., Sample City', '09171234567'))
        self.assertEqual((ben['status'], cara['status'], cara['year_section'], ana['request_type']), ('For release', 'Pending', '3E', 'New ID'))
        self.assertEqual(len({ana['token'], ben['token'], cara['token']}), 3)
        self.assertEqual(self.student('P202400101')['name'], 'Alex M. Rivera')                   # the duplicate did not overwrite
        self.assertIn(('Imported students', '3 added, 2 skipped of 5 rows'),
                      [(e['action'], e['detail']) for e in self.client.get('/api/activity').json['events']])

    def test_importing_the_same_file_again_adds_nobody(self):
        self.upload(self.csv_file(*self.ROWS))
        again = self.upload(self.csv_file(*self.ROWS))
        self.assertEqual((again.json['added'], again.json['skipped'], again.json['total']), (0, 5, 5))
        self.assertEqual(len(self.client.get('/api/inventory').json['records']), 9)

    def test_a_file_can_set_any_of_the_four_statuses(self):
        rows = [f'P2024004{n:02d},Student {n},1A,{status},,' for n, status in enumerate(STATUSES)]
        self.assertEqual(self.upload(self.csv_file(*rows)).json['added'], 4)
        self.assertEqual([self.student(f'P2024004{n:02d}')['status'] for n in range(4)], list(STATUSES))

    def test_the_same_file_sent_many_times_at_once_adds_each_student_once(self):
        content = self.csv_file(*self.ROWS[:3])
        results = run_together([
            lambda: self.app.test_client().post('/api/inventory/import', data={'file': (io.BytesIO(content), 's.csv')},
                                                content_type='multipart/form-data') for _ in range(6)])
        self.assertEqual([r for r in results if isinstance(r, BaseException)], [])
        self.assertEqual(sum(r.json['added'] for r in results), 3)
        self.assertEqual(len(self.client.get('/api/inventory').json['records']), 9)

    def test_a_file_that_cannot_be_used_is_refused_whole_with_a_message(self):
        png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + bytes(range(200))
        cases = [(None, 'Choose a CSV file'), (b'', 'empty'), (self.csv_file()[:0] + b'student_id,name\nP202400301,Ana\n', 'year_section'),
                 (png, 'CSV text file'),
                 (HEADER.encode() + ''.join(f'P{202400000 + n},Student {n},1A,,,\n' for n in range(MAX_IMPORT_ROWS + 1)).encode(),
                  str(MAX_IMPORT_ROWS))]
        for content, words in cases:
            with self.subTest(words=words):
                response = self.upload(content)
                self.assertEqual(response.status_code, 400)
                self.assertIn(words, response.json['error'])
        self.assertEqual(len(self.client.get('/api/inventory').json['records']), 6)

    def test_a_file_over_the_limit_is_a_413_with_a_message_in_megabytes(self):
        line = b'P202400301,Ana,1A,,,\n'
        just_over = HEADER.encode() + line * ((MAX_IMPORT_BYTES + 5000) // len(line) + 1)        # inside the allowance of the request
        way_over = b'x' * (2 * 1024 * 1024 + 10)                                                  # refused before it is read
        for label, content in (('just over', just_over), ('way over', way_over)):
            response = self.upload(content)
            self.assertEqual(response.status_code, 413, label)
            self.assertEqual(response.content_type, 'application/json', label)
            self.assertIn('1 MB', response.json['error'], label)
        # The bigger allowance belongs to the import alone: every other request keeps its 32 KB limit.
        chat = self.client.post('/api/chat', data='{"message": "' + 'x' * 40000 + '"}', content_type='application/json')
        self.assertEqual(chat.status_code, 413)
        self.assertIn('32 KB', chat.json['error'])
        self.assertEqual(len(self.client.get('/api/inventory').json['records']), 6)

    def test_the_template_is_a_csv_that_imports_cleanly(self):
        response = self.client.get('/api/inventory/template')
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.headers['Content-Disposition'])
        self.assertIn('student_import_template.csv', response.headers['Content-Disposition'])
        content = response.data
        self.assertTrue(content.startswith(b'\xef\xbb\xbf'))                                     # Excel opens it as UTF-8
        header = next(csv.reader(io.StringIO(content.decode('utf-8-sig'))))
        self.assertEqual(tuple(header), IMPORT_COLUMNS)
        students, problems, _ = read_student_csv(content)
        self.assertEqual((len(students), problems), (2, []))
        self.assertEqual(self.upload(content).json['added'], 2)

    def test_an_export_can_be_imported_again(self):
        export = self.client.get('/api/inventory/export')
        header = next(csv.reader(io.StringIO(export.data.decode('utf-8-sig'))))
        self.assertEqual(tuple(header[:6]), IMPORT_COLUMNS)
        taylor = self.student('P202400104')
        self.client.delete(f'/api/inventory/{taylor["id"]}')
        again = self.upload(export.data)
        self.assertEqual((again.json['added'], again.json['skipped'], again.json['total']), (1, 5, 6))
        back = self.student('P202400104')
        self.assertEqual((back['name'], back['year_section'], back['status'], back['address'], back['contact_number']),
                         (taylor['name'], taylor['year_section'], taylor['status'], taylor['address'], taylor['contact_number']))


    def test_values_the_export_protects_come_back_unchanged(self):
        """The export puts an apostrophe before text that starts like a formula; the import takes it off again."""
        created = self.client.post('/api/inventory', json={
            'student_id': 'P202400350', 'name': 'Dina Cruz', 'year_section': '2A',
            'address': '=Blk 4 Lot 2', 'contact_number': '+63 2 8888 1234'})
        self.assertLess(created.status_code, 300, created.json)
        export = self.client.get('/api/inventory/export').data
        self.assertIn(b"'+63288881234", export)
        self.assertIn(b"'=Blk 4 Lot 2", export)
        self.client.delete(f'/api/inventory/{created.json["id"]}')
        again = self.upload(export)
        self.assertEqual(again.json['added'], 1, again.json)                       # the others were already on the dashboard
        self.assertNotIn('P202400350', [problem['student_id'] for problem in again.json['problems']])
        back = self.student('P202400350')
        self.assertEqual((back['address'], back['contact_number']), ('=Blk 4 Lot 2', '+63288881234'))


class SchemaUpgradeTests(DatabaseTestCase):
    """A PostgreSQL database made by the previous version of the app: no address, no contact number, the old
    status name, a course that is required, and incidents without false statements."""

    def make_old_database(self):
        with self.db() as db:
            db.execute('''CREATE TABLE inventory (
                id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, student_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, course TEXT NOT NULL, section TEXT NOT NULL,
                year_level INTEGER NOT NULL CHECK (year_level BETWEEN 1 AND 6), status TEXT NOT NULL,
                request_type TEXT NOT NULL, pending_reason TEXT NOT NULL DEFAULT '', schedule_type TEXT NOT NULL DEFAULT '',
                schedule_date TEXT COLLATE "C" NOT NULL DEFAULT '', schedule_time TEXT COLLATE "C" NOT NULL DEFAULT '',
                token TEXT NOT NULL UNIQUE, notes TEXT NOT NULL DEFAULT '')''')
            db.execute('''CREATE TABLE incidents (
                id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, category TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL, student_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Open', timestamp TEXT COLLATE "C" NOT NULL)''')
            db.execute("INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,token) VALUES "
                       "('DEMO-001','Alex Rivera','BSIT','A',1,'Ready for release','New ID','tok-1'),"
                       "('LEGACY-9','Old Student','BSBA','B',2,'Pending','Lost ID','tok-2')")
            db.execute("INSERT INTO incidents (category,detail,student_id,timestamp) VALUES "
                       "('Visitor','Visitor without a card','','2026-10-01 08:00:00')")

    def test_the_upgrade_keeps_every_row_adds_the_new_columns_and_renames_the_status(self):
        self.make_old_database()
        client = self.make_app().test_client()
        records = client.get('/api/inventory').json['records']
        self.assertEqual(len(records), 2)                                      # nothing was added or removed
        alex = next(r for r in records if r['student_id'] == 'DEMO-001')
        self.assertEqual((alex['status'], alex['address'], alex['contact_number'], alex['year_section']), ('For release', '', '', '1A'))
        self.assertEqual(next(r for r in records if r['student_id'] == 'LEGACY-9')['year_section'], '2B')
        incident = client.get('/api/incidents').json['incidents'][0]
        self.assertEqual((incident['category'], incident['false_statement'], incident['penalty']), ('Visitor', False, 0))
        self.make_app()                                                        # starting again changes nothing more
        self.assertEqual(len(client.get('/api/inventory').json['records']), 2)

    def test_a_student_with_an_id_from_the_old_format_still_works_everywhere(self):
        self.make_old_database()
        app = self.make_app()
        client = app.test_client()
        reply = client.post('/api/chat', json={'message': 'LEGACY-9', 'workspace_role': 'coordinator'}).json
        self.assertEqual((reply['intent'], reply['student']['name']), ('student_lookup', 'Old Student'))
        self.assertEqual(client.post('/api/requests', json={'student_id': 'legacy-9', 'request_type': 'Other'}).status_code, 201)
        incident = client.post('/api/incidents', json={'category': 'No ID', 'detail': 'x', 'student_id': 'LEGACY-9'})
        self.assertEqual((incident.status_code, incident.json['student_name']), (201, 'Old Student'))
        # a new student is added with the course left empty
        created = client.post('/api/inventory', json=NEW)
        self.assertEqual(created.status_code, 201)
        with self.db() as db:
            self.assertEqual(db.execute("SELECT course FROM inventory WHERE student_id = %s", (NEW['student_id'],)).fetchone()[0], '')


if __name__ == '__main__':
    unittest.main()
