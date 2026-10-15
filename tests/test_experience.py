"""Calendar exports must use the authenticated owner and the school's timezone."""
import tempfile
import unittest
from pathlib import Path

from account_helpers import signed_client
from Chatbot import create_app


class AppointmentCalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Path(self.temp.name) / 'test.sqlite3')
        self.coordinator = signed_client(self.app)
        self.rows = self.coordinator.get('/api/inventory').json['records']
        self.owner = next(row for row in self.rows if row['name'] == 'Taylor Cruz')
        self.student = signed_client(self.app, 'student', self.owner['id'])
        self.path = '/api/my-appointment.ics'

    def schedule(self, row, date, time):
        response = self.coordinator.patch(f'/api/inventory/{row["id"]}', json={
            'schedule_type': 'Release', 'schedule_date': date, 'schedule_time': time})
        self.assertEqual(response.status_code, 200, response.json)

    def test_requires_verified_student_and_existing_appointment(self):
        self.assertEqual(self.app.test_client().get(self.path).status_code, 401)
        for client in (self.coordinator, signed_client(self.app, 'guard')):
            self.assertEqual(client.get(self.path).status_code, 403)
        self.assertEqual(self.student.get(self.path).status_code, 404)
        with self.student.session_transaction() as session:
            session.pop('student_verified')
        self.assertEqual(self.student.get(self.path).status_code, 403)

    def test_calendar_converts_school_time_and_omits_personal_details(self):
        self.schedule(self.owner, '2027-01-01', '00:15')
        response = self.student.get(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'text/calendar')
        self.assertIn('attachment', response.headers['Content-Disposition'])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        data = response.get_data(as_text=True)
        self.assertIn('DTSTART:20261231T161500Z\r\n', data)
        self.assertIn('TRIGGER:-PT1H\r\n', data)
        self.assertTrue(data.startswith('BEGIN:VCALENDAR\r\nVERSION:2.0\r\n'))
        self.assertTrue(data.endswith('END:VEVENT\r\nEND:VCALENDAR\r\n'))
        for value in (self.owner['name'], self.owner['student_id'], self.owner['token']):
            self.assertNotIn(value, data)

    def test_export_always_uses_owner_and_tracks_rescheduling(self):
        other = next(row for row in self.rows if row['id'] != self.owner['id'])
        self.schedule(self.owner, '2026-12-10', '10:30')
        self.schedule(other, '2026-12-11', '14:15')
        before = self.student.get(self.path, query_string={'record_id': other['id']}).get_data(as_text=True)
        self.assertIn('DTSTART:20261210T023000Z', before)
        self.assertNotIn('DTSTART:20261211T061500Z', before)
        self.schedule(self.owner, '2026-12-12', '11:00')
        after = self.student.get(self.path).get_data(as_text=True)
        self.assertIn('DTSTART:20261212T030000Z', after)
        self.assertNotIn('DTSTART:20261210T023000Z', after)
        self.coordinator.patch(f'/api/inventory/{self.owner["id"]}', json={
            'schedule_type': '', 'schedule_date': '', 'schedule_time': ''})
        self.assertEqual(self.student.get(self.path).status_code, 404)
