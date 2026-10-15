import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from account_helpers import signed_client
from Chatbot import create_app


class LostIDRescheduleTests(unittest.TestCase):
    def setUp(self):
        self.clock=patch('pickup.school_now',return_value=datetime(2026,10,15,8))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        app_clock=patch('Chatbot.school_now',return_value=datetime(2026,10,15,8))
        app_clock.start()
        self.addCleanup(app_clock.stop)
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'inventory.sqlite3'
        self.app=create_app(self.db)
        self.coordinator=signed_client(self.app)
        self.rows=self.coordinator.get('/api/inventory').json['records']
        self.sam=next(row for row in self.rows if row['name']=='Sam Reyes')
        self.alex=next(row for row in self.rows if row['name']=='Alex Rivera')
        self.student=signed_client(self.app,'student',self.sam['id'])
        self.other_student=signed_client(self.app,'student',self.alex['id'],username='alex')
        self.guard=signed_client(self.app,'guard')
        self.url=f'/api/inventory/{self.sam["id"]}/reschedule'

    def publish(self,date='2026-12-10',time='10:30',capacity=1):
        response=self.coordinator.post('/api/pickup-slots',json={'schedule_date':date,'schedule_time':time,'capacity':capacity})
        self.assertEqual(response.status_code,201,response.json)
        return response.json['id']

    def ready(self,row=None):
        row=row or self.sam
        self.assertEqual(self.coordinator.patch(f'/api/inventory/{row["id"]}',json={'id_status':'Lost','status':'Ready for release'}).status_code,200)

    def current(self,rid=None):
        rid=rid or self.sam['id']
        return next(row for row in self.coordinator.get('/api/inventory').json['records'] if row['id']==rid)

    def test_student_booking_readiness_ownership_and_csrf(self):
        slot=self.publish()
        self.assertEqual(self.student.post(self.url,json={'slot_id':slot}).status_code,409)
        self.ready()
        for client,code in ((self.guard,403),(self.app.test_client(),401)):
            self.assertEqual(client.post(self.url,json={'slot_id':slot}).status_code,code)
        self.assertEqual(self.student.post(self.url,json={'slot_id':slot},headers={'X-CSRF-Token':'bad'}).status_code,400)
        self.assertEqual(self.other_student.post(self.url,json={'slot_id':slot}).status_code,403)
        for client in (self.student,self.guard):
            self.assertEqual(client.post('/api/pickup-slots',json={}).status_code,403)
            self.assertEqual(client.patch(f'/api/pickup-slots/{slot}',json={'enabled':False}).status_code,403)
            self.assertEqual(client.get('/api/pickup-slots').status_code,403)
        self.assertEqual(self.student.get(f'/api/inventory/{self.alex["id"]}/pickup-slots').status_code,403)
        self.assertEqual(self.coordinator.get('/api/pickup-offer').status_code,403)
        self.assertEqual(self.student.post(self.url,json={'slot_id':slot}).status_code,200)
        # Student still cannot change statuses or images through the general inventory APIs.
        self.assertEqual(self.student.patch(f'/api/inventory/{self.sam["id"]}',json={'id_status':'Active'}).status_code,403)
        self.assertEqual(self.student.post(f'/api/inventory/{self.sam["id"]}/images',data={}).status_code,403)

    def test_available_offer_booking_reschedule_persistence_and_qr(self):
        first=self.publish()
        second=self.publish('2026-12-11','14:15')
        waiting=self.student.get('/api/pickup-offer').json['offer']
        self.assertFalse(waiting['ready'])
        self.assertEqual(waiting['slots'],[])
        self.ready()
        offered=self.student.get('/api/pickup-offer').json['offer']
        self.assertEqual([slot['id'] for slot in offered['slots']],[first,second])
        self.assertIn('2026-12-10 at 10:30',offered['reply'])
        self.assertEqual(self.current()['schedule_date'],'')
        booked=self.student.post(self.url,json={'slot_id':first})
        self.assertEqual(booked.status_code,200)
        self.assertIn('confirmed',booked.json['reply'])
        self.assertTrue(self.student.get('/api/pickup-offer').json['offer']['slots'][0]['current'])
        # Booking the current slot again is idempotent, even at capacity one.
        self.assertEqual(self.student.post(self.url,json={'slot_id':first}).status_code,200)
        changed=self.student.post(self.url,json={'slot_id':second})
        self.assertEqual(changed.status_code,200)
        row=changed.json['record']
        self.assertEqual((row['id_status'],row['status'],row['token']),('Lost','Ready for release',self.sam['token']))
        restored=signed_client(create_app(self.db)).get('/api/inventory').json['records']
        self.assertEqual(next(r for r in restored if r['id']==row['id'])['schedule_date'],'2026-12-11')
        page=self.guard.get('/student/'+row['token']).get_data(as_text=True)
        self.assertIn('2026-12-11 at 14:15',page)
        self.assertNotIn('2026-12-10',page)
        self.assertNotIn('qr_url',changed.json)
        self.assertEqual(changed.json['scan_url'],f'/api/inventory/{row["id"]}/view')
        slots=self.coordinator.get('/api/pickup-slots').json['slots']
        self.assertEqual([slot['booked'] for slot in slots],[0,1])

    def test_full_closed_expired_and_failed_reschedule_keep_appointment(self):
        first=self.publish()
        second=self.publish('2026-12-11','14:15')
        self.ready(); self.ready(self.alex)
        self.student.post(self.url,json={'slot_id':first})
        other_url=f'/api/inventory/{self.alex["id"]}/reschedule'
        self.other_student.post(other_url,json={'slot_id':second})
        before=self.current()
        self.assertEqual(self.student.post(self.url,json={'slot_id':second}).status_code,409)
        self.assertEqual(self.current(),before)
        self.assertIn('No other pickup dates',self.student.get('/api/pickup-offer').json['offer']['reply'])
        offered=self.student.get('/api/pickup-offer').json['offer']['slots']
        self.assertNotIn(second,[slot['id'] for slot in offered])
        self.coordinator.patch(f'/api/pickup-slots/{first}',json={'enabled':False})
        self.assertEqual(self.student.post(self.url,json={'slot_id':first}).status_code,409)
        self.assertEqual(self.current(),before)
        self.assertEqual(self.student.get('/api/pickup-offer').json['offer']['slots'],[])
        # Past availability disappears automatically, even if the slot is still enabled.
        with patch('pickup.school_now',return_value=datetime(2027,1,1)):
            self.assertEqual(self.student.post(self.url,json={'slot_id':second}).status_code,409)
            self.assertEqual(self.coordinator.patch(f'/api/pickup-slots/{first}',json={'enabled':True}).status_code,409)
        self.assertEqual(self.current(),before)

    def test_slot_validation_duplicate_and_existing_appointment_capacity(self):
        for payload in ({}, [], {'schedule_date':'2026-02-30','schedule_time':'10:30','capacity':1},
                        {'schedule_date':'2026-10-14','schedule_time':'10:30','capacity':1},
                        {'schedule_date':'2026-12-10','schedule_time':'10:30','capacity':True},
                        {'schedule_date':'2026-12-10','schedule_time':'10:30','capacity':501},
                        {'schedule_date':'2026-12-10','schedule_time':'25:30','capacity':1}):
            with self.subTest(payload=payload):
                self.assertEqual(self.coordinator.post('/api/pickup-slots',json=payload).status_code,400)
        slot=self.publish()
        self.assertEqual(self.coordinator.post('/api/pickup-slots',json={'schedule_date':'2026-12-10','schedule_time':'10:30','capacity':1}).status_code,409)
        self.ready()
        before=self.current()
        for payload in ({}, [], None, {'slot_id':True}, {'slot_id':'1'}, {'slot_id':slot,'record_id':self.alex['id']}, {'schedule_date':'2026-12-10','schedule_time':'10:30'}):
            self.assertEqual(self.student.post(self.url,json=payload).status_code,400)
            self.assertEqual(self.current(),before)
        self.assertEqual(self.student.post(self.url,json={'slot_id':9999}).status_code,409)
        self.assertEqual(self.coordinator.patch('/api/pickup-slots/9999',json={'enabled':False}).status_code,404)
        self.assertEqual(self.coordinator.patch(f'/api/pickup-slots/{slot}',json={'enabled':1}).status_code,400)
        # Existing coordinator appointments count toward available capacity.
        self.coordinator.patch(f'/api/inventory/{self.sam["id"]}',json={'schedule_type':'Lost ID replacement','schedule_date':'2026-12-10','schedule_time':'10:30'})
        self.ready(self.alex)
        self.assertNotIn(slot,[s['id'] for s in self.other_student.get('/api/pickup-offer').json['offer']['slots']])
        blocked=self.coordinator.patch(f'/api/inventory/{self.alex["id"]}',json={'schedule_type':'Lost ID replacement','schedule_date':'2026-12-10','schedule_time':'10:30'})
        self.assertEqual(blocked.status_code,409)
        noncanonical=self.coordinator.patch(f'/api/inventory/{self.alex["id"]}',json={'schedule_type':'Lost ID replacement','schedule_date':'2026-12-10','schedule_time':'010:30'})
        self.assertEqual(noncanonical.status_code,400)
        self.coordinator.patch(f'/api/pickup-slots/{slot}',json={'enabled':False})
        closed=self.coordinator.patch(f'/api/inventory/{self.alex["id"]}',json={'schedule_type':'Lost ID replacement','schedule_date':'2026-12-10','schedule_time':'10:30'})
        self.assertEqual(closed.status_code,409)
        self.coordinator.patch(f'/api/pickup-slots/{slot}',json={'enabled':True})
        # Release frees capacity; closing publication preserves the old appointment.
        self.coordinator.patch(f'/api/inventory/{self.sam["id"]}',json={'status':'Released','id_status':'Active'})
        self.assertIn(slot,[s['id'] for s in self.other_student.get('/api/pickup-offer').json['offer']['slots']])
        self.assertIsNone(self.student.get('/api/pickup-offer').json['offer'])

    def test_competing_bookings_do_not_overbook(self):
        slot=self.publish(); self.ready(); self.ready(self.alex)
        barrier=threading.Barrier(2)
        def book(client,rid):
            barrier.wait(timeout=5)
            return client.post(f'/api/inventory/{rid}/reschedule',json={'slot_id':slot}).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            one=executor.submit(book,self.student,self.sam['id'])
            two=executor.submit(book,self.other_student,self.alex['id'])
            self.assertEqual(sorted([one.result(timeout=10),two.result(timeout=10)]),[200,409])
        self.assertEqual(self.coordinator.get('/api/pickup-slots').json['slots'][0]['booked'],1)

    def test_chat_automatically_includes_choices_for_ready_lost_id(self):
        slot=self.publish()
        waiting=self.student.post('/api/chat',json={'message':'Can I reschedule my lost ID pickup?'}).json
        self.assertIn('not available for pickup yet',waiting['reply'])
        self.assertEqual(waiting['pickup_offer']['slots'],[])
        self.ready()
        for text in ('Can I reschedule my lost ID pickup?','What is my ID status?'):
            reply=self.student.post('/api/chat',json={'message':text}).json
            self.assertEqual(reply['pickup_offer']['slots'][0]['id'],slot)
            self.assertTrue(reply['pickup_offer']['can_book'])
        self.assertEqual(self.guard.post('/api/chat',json={'message':"What is Sam Reyes's ID status?"}).status_code,403)
        self.assertFalse(self.guard.get(f'/api/inventory/{self.sam["id"]}/pickup-slots').json['offer']['can_book'])
        self.assertEqual(self.current()['schedule_date'],'')
        self.coordinator.patch(f'/api/pickup-slots/{slot}',json={'enabled':False})
        self.assertIn('no available pickup dates',self.student.get('/api/pickup-offer').json['offer']['reply'])
