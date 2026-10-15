import io
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from account_helpers import signed_client
from Chatbot import create_app


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'inventory.sqlite3'
        self.app=create_app(self.db)
        self.staff=signed_client(self.app)
        self.guard=signed_client(self.app,'guard')
        rows=self.staff.get('/api/inventory').json['records']
        self.owner=next(r for r in rows if r['name']=='Taylor Cruz')
        self.other=next(r for r in rows if r['name']=='Alex Rivera')
        self.student=signed_client(self.app,'student',self.owner['id'])
        self.other_student=signed_client(self.app,'student',self.other['id'],username='other')
        self.url=f'/api/inventory/{self.owner["id"]}'
        self.code=self.owner['token']

    def current(self):
        return next(row for row in self.staff.get('/api/inventory').json['records'] if row['id']==self.owner['id'])

    def lost(self,client=None,**fields):
        payload={k:self.owner[k] for k in ('student_id','course','section')}
        payload.update(confirmed=True,**fields)
        return (client or self.student).post('/api/id-reports/lost',json=payload)

    def found(self,code=None):
        return self.guard.post('/api/id-reports/found',json={'code':code or self.code,'confirmed':True})

    def update(self,**fields):
        return self.staff.patch(self.url,json=fields)

    def ready(self):
        self.assertEqual(self.update(status='Replacement Processing').status_code,200)
        self.assertEqual(self.update(status='Ready for release').status_code,200)

    def publish(self,purpose,date='2027-12-10'):
        result=self.staff.post('/api/pickup-slots',json={'schedule_date':date,'schedule_time':'10:00','capacity':1,'purpose':purpose})
        self.assertEqual(result.status_code,201,result.json)
        return result.json['id']

    def test_explicit_student_verification_and_idempotent_ticket(self):
        before=self.current()
        chat=self.student.post('/api/chat',json={'message':'nawala ang ID ko'}).json
        self.assertEqual(chat['action'],'report_lost')
        self.assertEqual(self.current(),before)
        self.assertEqual(self.lost(student_id='WRONG').status_code,400)
        self.assertEqual(self.student.post('/api/id-reports/lost',json={'confirmed':False}).status_code,400)
        self.assertEqual(self.lost(self.guard).status_code,403)
        self.assertEqual(self.lost(self.other_student).status_code,400)
        result=self.lost();self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(self.current()['id_status'],'Reported Lost')
        self.assertEqual(self.update(status='Replacement Processing').status_code,200)
        again=self.lost()
        self.assertEqual(result.json['ticket'],again.json['ticket'])
        self.assertEqual(self.current()['status'],'Replacement Processing')
        self.assertEqual(len(self.student.get('/api/id-tickets').json['tickets']),1)
        self.assertIn('Hindi pa configured',result.json['reply'])

    def test_policy_and_notification_privacy(self):
        policy={'requirements':'Approved test requirement','fee':'Approved test fee','claim_location':'Security Office'}
        self.assertEqual(self.guard.put('/api/id-policy',json=policy).status_code,403)
        self.assertEqual(self.staff.put('/api/id-policy',json=policy).status_code,200)
        self.assertEqual(self.staff.put('/api/id-policy',json={**policy,'claim_location':''}).status_code,400)
        self.assertIn('Approved test fee',self.student.post('/api/chat',json={'message':'What are the ID requirements?'}).json['reply'])
        self.found()
        self.assertEqual(self.student.get('/api/notifications').json['notifications'],[])
        ticket=self.staff.get('/api/id-tickets').json['tickets'][0]
        endpoint=f'/api/id-tickets/{ticket["id"]}/notify-owner'
        self.assertEqual(self.guard.post(endpoint,json={'confirmed':True}).status_code,403)
        self.assertEqual(self.staff.post(endpoint,json={'confirmed':False}).status_code,400)
        self.assertEqual(self.staff.post(endpoint,json={'confirmed':True}).status_code,200)
        self.assertEqual(self.staff.post(endpoint,json={'confirmed':True}).status_code,200)
        notices=self.student.get('/api/notifications').json['notifications']
        self.assertEqual(len(notices),1)
        self.assertIn('another valid ID',notices[0]['message'])
        self.assertEqual(self.other_student.get('/api/notifications').json['notifications'],[])
        self.assertEqual(self.other_student.get('/api/id-tickets').json['tickets'],[])
        self.other_student.post('/api/notifications/read',json={'ids':[notices[0]['id']]})
        self.assertEqual(len(self.student.get('/api/notifications').json['notifications']),1)
        self.student.post('/api/notifications/read',json={'ids':[notices[0]['id']]})
        self.assertEqual(self.student.get('/api/notifications').json['notifications'],[])

    def test_scan_privacy_images_and_guard_warning(self):
        self.lost()
        path='/student/'+self.code
        for suffix in ('','/id/front.png','/id/back.png'):
            result=self.app.test_client().get(path+suffix)
            self.assertEqual(result.status_code,200 if not suffix else 302)
            self.assertNotIn(self.owner['name'],result.get_data(as_text=True))
            self.assertEqual(self.student.get(path+suffix).status_code,200 if not suffix else 403)
        for suffix in ('/view','/id/front.png','/id/back.png'):
            self.assertEqual(self.student.get(self.url+suffix).status_code,200)
            self.assertEqual(self.other_student.get(self.url+suffix).status_code,403)
        page=self.guard.get(path).get_data(as_text=True)
        self.assertIn('REVOKED: Reported lost, contact PMO.',page)
        reply=self.student.post('/api/chat',json={'message':"What is my ID status?"}).json['reply']
        self.assertIn('REVOKED',reply)
        for code in (self.code,'https://school.example/student/'+self.code):
            result=self.guard.get('/api/qr/lookup',query_string={'code':code})
            self.assertEqual(result.json['url'],path)
        self.assertEqual(self.student.get('/api/qr/lookup',query_string={'code':self.code}).status_code,403)
        self.assertEqual(self.guard.get('/api/qr/lookup',query_string={'code':'https://example.org/nope'}).status_code,400)
        self.assertIn('/api/inventory/',self.student.get(self.url+'/view').get_data(as_text=True))

    def test_found_active_id_requires_custody_and_claim_verification(self):
        before=self.current()
        self.assertEqual(self.student.post('/api/id-reports/found',json={'code':self.code,'confirmed':True}).status_code,403)
        self.assertEqual(self.guard.post('/api/id-reports/found',json={'code':self.code,'confirmed':False}).status_code,400)
        chat=self.guard.post('/api/chat',json={'message':'Nakapulot ako ng ID'}).json
        self.assertEqual(chat['intent'],'found_id_report')
        self.assertEqual(self.current(),before)
        self.assertEqual(self.found().status_code,200)
        self.assertEqual(self.current()['id_status'],'Found - For Claiming')
        self.found()
        self.assertEqual(self.student.get('/api/notifications').json['notifications'],[])
        self.assertEqual(len(self.student.get('/api/id-tickets').json['tickets']),1)
        self.assertEqual(self.update(id_status='Active',status='Released').status_code,409)
        for value in (False,1):
            self.assertEqual(self.guard.post(self.url+'/claim',json={'verified':value}).status_code,400)
        self.assertEqual(self.guard.post(self.url+'/claim',json={'verified':True}).status_code,200)
        self.assertEqual((self.current()['id_status'],self.current()['token']),('Active',self.code))

    def test_found_revoked_id_never_reactivates_or_cancels_replacement(self):
        self.lost();slot=self.publish('Application')
        self.student.post(self.url+'/reschedule',json={'slot_id':slot})
        before=self.current()
        self.assertEqual(self.found().status_code,200)
        self.assertEqual(self.current(),before)
        self.assertEqual(self.staff.get('/api/pickup-slots').json['slots'][0]['booked'],1)
        self.assertIn('REVOKED',self.app.test_client().get('/student/'+self.code).get_data(as_text=True))
        self.assertEqual(self.student.get('/api/notifications').json['notifications'],[])
        ticket=self.staff.get('/api/id-tickets').json['tickets'][0]
        self.assertEqual(ticket['state'],'Revoked ID held')
        self.staff.post(f'/api/id-tickets/{ticket["id"]}/notify-owner',json={'confirmed':True})
        self.assertIn('REVOKED',self.student.get('/api/notifications').json['notifications'][0]['message'])
        self.assertEqual(self.guard.post(self.url+'/claim',json={'verified':True}).status_code,409)

    def test_replacement_retires_original_and_preserves_archive(self):
        image=io.BytesIO();Image.new('RGB',(20,30),'red').save(image,format='PNG');image.seek(0)
        self.assertEqual(self.staff.post(self.url+'/images',data={'front':(image,'front.png')}).status_code,200)
        self.lost()
        self.assertEqual(self.staff.delete(self.url+'/images/front').status_code,409)
        self.assertEqual(self.update(status='Released',id_status='Active').status_code,409)
        self.ready()
        self.assertEqual(self.update(status='Released',id_status='Active').status_code,200)
        row=self.current();new_code=row['token']
        self.assertNotEqual(new_code,self.code)
        self.assertTrue(new_code.startswith('PGPC-'))
        self.assertFalse(row['has_front_image'])
        with Image.open(io.BytesIO(self.guard.get('/student/'+self.code+'/id/front.png').data)) as old:
            self.assertEqual(old.getpixel((0,0)),(255,0,0))
        self.assertIn('PERMANENTLY DEACTIVATED',self.guard.get('/student/'+self.code).get_data(as_text=True))
        self.assertEqual(self.found().status_code,200)
        self.assertEqual(self.current(),row)
        self.found()
        # Repeated custody reports for the retired ID do not create duplicate tickets.
        tickets=self.student.get('/api/id-tickets').json['tickets']
        self.assertEqual(len([t for t in tickets if t['kind']=='found']),1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT state FROM qr_codes WHERE token=?',(self.code,)).fetchone()[0],'retired')
        again=signed_client(create_app(self.db),'guard')
        self.assertIn('PERMANENTLY DEACTIVATED',again.get('/student/'+self.code).get_data(as_text=True))

    def test_manual_status_updates_cannot_bypass_report_workflow(self):
        self.lost();before=self.current()
        for fields in ({'id_status':'Not issued'},{'id_status':'Lost'},{'id_status':'Active'},
                       {'status':'Ready for release'},{'status':'Processing'},{'status':'Unclaimed'}):
            self.assertEqual(self.update(**fields).status_code,409,fields)
            self.assertEqual(self.current(),before)
        self.ready();self.assertEqual(self.found().status_code,200)
        self.assertEqual(self.update(id_status='Active').status_code,409)
        self.assertEqual(self.update(status='Released').status_code,200)

    def test_application_and_pickup_stage_capacity(self):
        self.lost();application=self.publish('Application');pickup=self.publish('Pickup','2027-12-11')
        offer=self.student.get('/api/pickup-offer').json['offer']
        self.assertEqual(offer['purpose'],'Application')
        self.assertEqual([s['id'] for s in offer['slots']],[application])
        self.assertEqual(self.student.post(self.url+'/reschedule',json={'slot_id':pickup}).status_code,409)
        self.assertEqual(self.update(schedule_type='Lost ID application',schedule_date='2027-12-11',schedule_time='10:00').status_code,409)
        self.assertEqual(self.student.post(self.url+'/reschedule',json={'slot_id':application}).status_code,200)
        self.ready()
        self.assertEqual(self.current()['schedule_date'],'')
        offer=self.student.get('/api/pickup-offer').json['offer']
        self.assertEqual(offer['purpose'],'Pickup')
        self.assertEqual([s['id'] for s in offer['slots']],[pickup])
        self.assertEqual(self.student.post(self.url+'/reschedule',json={'slot_id':application}).status_code,409)
        self.assertEqual(self.student.post(self.url+'/reschedule',json={'slot_id':pickup}).status_code,200)

    def test_unclaimed_clock_and_confirmed_stock_audit(self):
        clock=datetime(2026,10,15,8)
        with patch('lifecycle.school_now',return_value=clock):
            self.assertEqual(self.update(status='Ready for release').status_code,200)
        with patch('lifecycle.school_now',return_value=clock+timedelta(days=30)):
            self.assertEqual(self.current()['status'],'Ready for release')
        with patch('lifecycle.school_now',return_value=clock+timedelta(days=30,seconds=1)):
            self.assertEqual(self.current()['status'],'Unclaimed')
            count=len(self.student.get('/api/notifications').json['notifications'])
            self.current()
            self.assertEqual(len(self.student.get('/api/notifications').json['notifications']),count)
        self.assertEqual(self.guard.post(self.url+'/audit',json={'confirmed':True}).status_code,403)
        self.assertEqual(self.staff.post(self.url+'/audit',json={'confirmed':False}).status_code,400)
        self.assertEqual(self.staff.post(self.url+'/audit',json={'confirmed':1}).status_code,400)
        self.assertEqual(self.staff.post(self.url+'/audit',json={'confirmed':True}).status_code,200)
        self.assertEqual(self.current()['id_status'],'Missing from Stock')
        self.assertEqual(self.update(id_status='Active',status='Released').status_code,409)
        self.assertEqual(self.found().status_code,200)
        self.assertEqual(self.current()['id_status'],'Found - For Claiming')

    def test_time_without_scanning_never_marks_loss(self):
        before=self.current()
        with patch('lifecycle.school_now',return_value=datetime(2036,1,1)):
            self.assertEqual(self.current(),before)
            self.guard.get('/student/'+self.code)
            self.assertEqual(self.current()['id_status'],'Active')
        self.assertEqual(self.student.get('/api/id-tickets').json['tickets'],[])

    def test_found_and_issuance_race_keeps_retired_qr_terminal(self):
        self.lost();self.ready()
        with ThreadPoolExecutor(max_workers=2) as executor:
            issue=executor.submit(self.update,status='Released',id_status='Active')
            found=executor.submit(self.found)
            statuses=[issue.result(timeout=10).status_code,found.result(timeout=10).status_code]
        self.assertEqual(statuses,[200,200])
        row=self.current()
        with sqlite3.connect(self.db) as db:
            state=db.execute('SELECT state FROM qr_codes WHERE token=?',(self.code,)).fetchone()[0]
        if row['token']!=self.code:
            self.assertEqual(state,'retired')
            self.assertEqual(row['id_status'],'Active')
        else:
            self.fail('Issuance must succeed even when the revoked original is found.')


if __name__=='__main__':
    unittest.main()
