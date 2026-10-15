import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from account_helpers import public_client, signed_client
from access import PUBLIC_INTENTS, STUDENT_INTENTS, STAFF_INTENTS, GUARD_INTENTS
from Chatbot import ROOT, create_app


class IntentAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'test.sqlite3'
        self.app=create_app(self.db)
        self.staff=signed_client(self.app)
        self.guard=signed_client(self.app,'guard')
        rows=self.staff.get('/api/inventory').json['records']
        self.owner=next(row for row in rows if row['name']=='Taylor Cruz')
        self.other=next(row for row in rows if row['id']!=self.owner['id'])
        self.student=signed_client(self.app,'student',self.owner['id'])
        self.public=public_client(self.app)

    def test_access_role_catalog_matches_enforced_rules(self):
        with (ROOT/'property_management_intent.csv').open() as f:
            catalog={row['intent']:row['access_role'] for row in csv.DictReader(f)}
        self.assertEqual(set(catalog),PUBLIC_INTENTS | STUDENT_INTENTS | STAFF_INTENTS | {'found_id_report'})
        for intent,role in catalog.items():
            self.assertEqual(role,'student_or_guard' if intent in GUARD_INTENTS else 'student' if intent in STUDENT_INTENTS else 'coordinator' if intent in STAFF_INTENTS else 'public')

    def test_authorization_is_independent_of_neural_prediction(self):
        with patch('Chatbot.ANNIntentModel') as model:
            app=create_app(self.db)
            clients={'public':public_client(app),'student':signed_client(app,'student',self.owner['id']),
                     'coordinator':signed_client(app),'guard':signed_client(app,'guard')}
            for intent in PUBLIC_INTENTS | STUDENT_INTENTS | STAFF_INTENTS | {'found_id_report'}:
                model.return_value.match.return_value=intent
                for role,client in clients.items():
                    with self.subTest(intent=intent,role=role):
                        response=client.post('/api/chat',json={'message':'neutral request'})
                        allowed=(role!='coordinator' and (intent in PUBLIC_INTENTS or intent=='found_id_report' or
                                 intent in STUDENT_INTENTS and role=='student' or
                                 intent in GUARD_INTENTS and role=='guard'))
                        self.assertEqual(response.status_code,200 if allowed else 401 if role=='public' else 403)
                        if role=='public':
                            self.assertNotIn(self.owner['name'],response.get_data(as_text=True))
                            self.assertNotIn('qr_url',response.json)
                            self.assertNotIn('record',response.json)
                        if not allowed:
                            self.assertNotIn('student_name',response.json)

    def test_login_requires_assigned_account_and_matching_student_number(self):
        client=self.app.test_client();client.get('/login')
        with client.session_transaction() as session:csrf=session['csrf']
        body={'username':'student','password':'test-password-123','csrf_token':csrf}
        for number in ('',self.other['student_id']):
            self.assertEqual(client.post('/login',data={**body,'student_id':number}).status_code,401)
        self.assertEqual(client.post('/login',data={**body,'student_id':self.owner['student_id']}).status_code,302)
        with client.session_transaction() as session:self.assertTrue(session['student_verified'])
        with self.student.session_transaction() as session:session.pop('student_verified')
        self.assertEqual(self.student.get('/api/inventory').status_code,403)
        self.assertEqual(self.student.post('/api/chat',json={'message':'What is my ID status?'}).status_code,403)
        self.assertEqual(self.student.post('/api/id-reports/lost',json={'confirmed':True}).status_code,403)

    def test_public_guidance_and_found_intent_never_change_inventory(self):
        before=self.staff.get('/api/inventory').json['records']
        for text,intent in [('What can this chatbot do?','bot_help'),('What are the ID requirements and fees?','id_requirements'),
                            ('Where is the ID office?','office_hours_location'),('What information shows after scanning the QR code?','qr_student_details'),
                            ('May nakita akong ID','found_id_report')]:
            response=self.public.post('/api/chat',json={'message':text,'record_id':self.other['id']})
            self.assertEqual(response.status_code,200,response.json)
            self.assertEqual(response.json['intent'],intent)
            self.assertNotIn('record',response.json)
            self.assertNotIn('action',response.json)
        self.assertEqual(self.staff.get('/api/inventory').json['records'],before)
        self.assertEqual(self.public.post('/api/chat',json={'message':'hello'},headers={'X-CSRF-Token':'bad'}).status_code,400)

    def test_public_raw_code_checker_and_legacy_url_are_local_only(self):
        for code in (self.owner['token'],'https://school.example/student/'+self.owner['token']):
            response=self.public.get('/scan',query_string={'code':code})
            self.assertEqual(response.status_code,302)
            self.assertEqual(response.location,'/student/'+self.owner['token'])
            page=self.public.get(response.location).get_data(as_text=True)
            self.assertNotIn(self.owner['name'],page)
        for code in ('https://evil.example/path','//evil.example','invalid<script>'):
            response=self.public.get('/scan',query_string={'code':code})
            self.assertEqual(response.status_code,200)
            self.assertNotIn('<script>',response.get_data(as_text=True))

    def test_printing_and_replacing_stickers_are_staff_only(self):
        for prefix in ('/qr/','/sticker/'):
            path=prefix+str(self.owner['id'])+('.png' if prefix=='/qr/' else '')
            self.assertEqual(self.public.get(path).status_code,302)
            self.assertEqual(self.student.get(path).status_code,403)
            self.assertEqual(self.staff.get(path).status_code,200)
            self.assertEqual(self.guard.get(path).status_code,200 if prefix=='/qr/' else 403)
        self.assertEqual(self.student.patch(f'/api/inventory/{self.owner["id"]}',json={'status':'Released'}).status_code,403)

    def test_public_scan_never_shows_owner_images_or_processing(self):
        path='/student/'+self.owner['token']
        for client in (self.public,self.student):
            page=client.get(path).get_data(as_text=True)
            for value in (self.owner['name'],self.owner['student_id'],self.owner['course'],'Front of ID','Processing status'):
                self.assertNotIn(value,page)
            self.assertNotIn('<img',page)
        self.assertEqual(self.public.get('/student/unknown').status_code,200)
        page=self.guard.get(path).get_data(as_text=True)
        self.assertIn(self.owner['name'],page)
        self.assertIn('Front of ID',page)

    def test_representative_question_requires_login_and_never_approves_pickup(self):
        text='Pwede bang kunin ng kaibigan ko ang ID ko?'
        self.assertEqual(self.public.post('/api/chat',json={'message':text}).status_code,401)
        response=self.student.post('/api/chat',json={'message':text})
        self.assertEqual(response.json['intent'],'authorized_pickup')
        self.assertIn('cannot approve',response.json['reply'])
        self.assertEqual(self.student.get('/api/my-id').json['record']['status'],'Released')
