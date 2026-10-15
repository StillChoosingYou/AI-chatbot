import tempfile
import unittest
from pathlib import Path

from account_helpers import signed_client
from Chatbot import create_app


class LostIDRescheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'inventory.sqlite3'
        self.app = create_app(self.db)
        self.coordinator = signed_client(self.app)
        self.rows = self.coordinator.get('/api/inventory').json['records']
        self.sam = next(r for r in self.rows if r['name'] == 'Sam Reyes')
        self.student = signed_client(self.app, 'student', self.sam['id'])
        self.guard = signed_client(self.app, 'guard')
        self.url = f'/api/inventory/{self.sam["id"]}/reschedule'
        self.appointment = {'schedule_date':'2026-12-10','schedule_time':'10:30'}

    def tearDown(self):
        self.temp.cleanup()

    def current(self):
        return next(r for r in self.coordinator.get('/api/inventory').json['records'] if r['id'] == self.sam['id'])

    def ready(self):
        self.assertEqual(self.coordinator.patch(f'/api/inventory/{self.sam["id"]}',json={'status':'Ready for release'}).status_code,200)

    def test_readiness_and_role_gates(self):
        before = self.current()
        self.assertEqual(self.coordinator.post(self.url,json=self.appointment).status_code,409)
        self.assertEqual(self.current(),before)
        self.ready()
        for client in (self.student,self.guard):
            self.assertEqual(client.post(self.url,json=self.appointment).status_code,403)
        self.assertEqual(self.app.test_client().post(self.url,json=self.appointment).status_code,401)
        self.assertEqual(self.coordinator.post(self.url,json=self.appointment,headers={'X-CSRF-Token':'bad'}).status_code,400)
        other=next(r for r in self.rows if r['name']=='Alex Rivera')
        self.assertEqual(self.coordinator.post(f'/api/inventory/{other["id"]}/reschedule',json=self.appointment).status_code,409)
        self.coordinator.patch(f'/api/inventory/{self.sam["id"]}',json={'status':'Released','id_status':'Active'})
        self.assertEqual(self.coordinator.post(self.url,json=self.appointment).status_code,409)

    def test_reschedule_persists_and_updates_existing_qr(self):
        self.ready()
        first=self.coordinator.post(self.url,json=self.appointment)
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json['record']['schedule_type'],'Lost ID replacement')
        second={'schedule_date':'2026-12-11','schedule_time':'14:15'}
        response=self.coordinator.post(self.url,json=second)
        self.assertEqual(response.status_code,200)
        row=response.json['record']
        self.assertEqual(row['token'],self.sam['token'])
        self.assertEqual(row['id_status'],'Lost')
        self.assertEqual(row['status'],'Ready for release')
        restored=signed_client(create_app(self.db)).get('/api/inventory').json['records']
        self.assertEqual(next(r for r in restored if r['id']==row['id']),row | {'has_front_image':0,'has_back_image':0})
        page=self.app.test_client().get('/student/'+self.sam['token']).get_data(as_text=True)
        self.assertIn('2026-12-11 at 14:15',page)
        self.assertNotIn('2026-12-10',page)
        self.assertIn('Available for pickup',page)
        reply=self.student.post('/api/chat',json={'message':'Can I reschedule my lost ID pickup?'}).json
        for phrase in ('available for pickup','2026-12-11 at 14:15','has not changed'):
            self.assertIn(phrase,reply['reply'])
        self.assertEqual(reply['qr_url'],f'/qr/{row["id"]}.png')
        self.assertEqual(self.current()['schedule_date'],'2026-12-11')

    def test_invalid_requests_leave_previous_appointment(self):
        self.ready()
        self.coordinator.post(self.url,json=self.appointment)
        before=self.current()
        for payload in ({}, {'schedule_date':'2026-12-11'},
                        {'schedule_date':'2026-02-30','schedule_time':'10:30'},
                        {'schedule_date':'2026-12-11','schedule_time':'25:00'},
                        {'schedule_date':'2026-1-1','schedule_time':'9:00'},
                        {'schedule_date':False,'schedule_time':'10:00'},
                        {**self.appointment,'id_status':'Active'}, [], None):
            with self.subTest(payload=payload):
                self.assertEqual(self.coordinator.post(self.url,json=payload).status_code,400)
                self.assertEqual(self.current(),before)
        self.assertEqual(self.coordinator.post('/api/inventory/9999/reschedule',json=self.appointment).status_code,404)

    def test_chat_waiting_available_and_not_lost(self):
        messages=('Can I reschedule my lost ID pickup?', 'I need to reschedule my ID', 'Rescheduling my lost ID appointment')
        for text in messages:
            response=self.student.post('/api/chat',json={'message':text})
            self.assertEqual(response.json['intent'],'lost_id_schedule')
            self.assertIn('not available for pickup yet',response.json['reply'])
        page=self.student.get('/student/'+self.sam['token']).get_data(as_text=True)
        self.assertIn('Not yet available for pickup',page)
        self.ready()
        response=self.student.post('/api/chat',json={'message':messages[0]}).json
        self.assertIn('No replacement pickup appointment',response['reply'])
        self.assertEqual(self.current()['schedule_date'],'')
        self.coordinator.patch(f'/api/inventory/{self.sam["id"]}',json={'id_status':'Active','status':'Released'})
        response=self.student.post('/api/chat',json={'message':messages[0]}).json
        self.assertIn('not currently marked Lost',response['reply'])
