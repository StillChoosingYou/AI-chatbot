import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from Chatbot import ROOT, create_app
from intent_model import ANNIntentModel


class SchoolIDTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'test.sqlite3'
        self.app = create_app(self.db)
        self.client = self.app.test_client()
        self.row = self.client.get('/api/inventory').json['records'][0]

    def tearDown(self):
        self.temp.cleanup()

    def test_dataset_integrity(self):
        with (ROOT/'property_management_dataset.csv').open() as f:
            rows = list(csv.DictReader(f))
        with (ROOT/'property_management_intent.csv').open() as f:
            intents = {r['intent'] for r in csv.DictReader(f)}
        self.assertEqual(len(rows),240)
        self.assertEqual(len({r['id'] for r in rows}),len(rows))
        self.assertEqual(len({r['text'].lower() for r in rows}),len(rows))
        self.assertEqual({r['intent'] for r in rows},intents)
        ElementTree.parse(ROOT/'chatbot.ows')

    def test_intent_boundaries(self):
        matcher=ANNIntentModel()
        for text,intent in [
            ('When is my ID release date?', 'id_release_schedule'),
            ('Why is my school ID pending?', 'pending_id'),
            ('Book a pending ID appointment', 'pending_id_schedule'),
            ('I lost my school ID', 'lost_id_report'),
            ('Schedule my lost ID replacement appointment', 'lost_id_schedule'),
            ('How can I print a QR sticker?', 'qr_sticker_request'),
            ('What information shows after scanning the QR code?', 'qr_student_details'),
            ('What is my ID processing status?', 'id_processing_status'),
            ('Ipakita ang inventory ng IDs', 'id_inventory'),
            ('purple asteroid quantum banana', 'out_of_scope')]:
            with self.subTest(text=text):
                self.assertEqual(matcher.match(text),intent)

    def test_appointments_status_and_live_chat(self):
        rid=self.row['id']
        self.assertIn('Select a student',self.client.post('/api/chat',json={'message':'What is the status of my student ID?'}).json['reply'])
        saved=self.client.patch(f'/api/inventory/{rid}',json={'status':'Ready for release','schedule_type':'Release','schedule_date':'2026-11-04','schedule_time':'09:30'})
        self.assertEqual(saved.status_code,200)
        answer=self.client.post('/api/chat',json={'message':'When can I collect my school ID?','record_id':rid}).json['reply']
        self.assertIn('2026-11-04 at 09:30',answer)
        rows=create_app(self.db).test_client().get('/api/inventory').json['records']
        self.assertEqual(next(r for r in rows if r['id']==rid)['schedule_time'],'09:30')
        self.assertEqual(self.client.patch(f'/api/inventory/{rid}',json={'schedule_time':''}).status_code,400)
        self.assertEqual(self.client.patch(f'/api/inventory/{rid}',json={'schedule_type':'','schedule_time':'','schedule_date':''}).status_code,200)
        self.assertIn('No release appointment',self.client.post('/api/chat',json={'message':'When can I collect my school ID?','record_id':rid}).json['reply'])
        for kind,message in [('Pending follow-up','When is my pending ID follow-up appointment?'),('Lost ID replacement','When is my lost ID replacement schedule?')]:
            self.client.patch(f'/api/inventory/{rid}',json={'schedule_type':kind,'schedule_date':'2026-11-05','schedule_time':'14:00'})
            self.assertIn('2026-11-05 at 14:00',self.client.post('/api/chat',json={'message':message,'record_id':rid}).json['reply'])

    def test_invalid_and_duplicate_records(self):
        row={'student_id':'TEST-01','name':'<script>student</script>','course':'BSIT','section':'A','year_level':2,'request_type':'Lost ID'}
        added=self.client.post('/api/inventory',json=row)
        self.assertEqual(added.status_code,201)
        self.assertEqual(self.client.post('/api/inventory',json=row).status_code,409)
        self.assertEqual(self.client.post('/api/inventory',json={**row,'year_level':8}).status_code,400)
        rid=added.json['id']
        for payload in ({'status':'invalid'},{'schedule_type':'Release','schedule_date':'2026-02-30','schedule_time':'10:00'},{'name':'tamper'},{'status':3}):
            self.assertEqual(self.client.patch(f'/api/inventory/{rid}',json=payload).status_code,400)
        self.assertEqual(self.client.post('/api/chat',json={'message':'hello','record_id':True}).status_code,400)
        self.assertEqual(self.client.get('/qr/9999.png').status_code,404)
        page=self.client.get('/student/'+added.json['token']).get_data(as_text=True)
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>student</script>',page)

    def test_qr_url_and_public_fields(self):
        with patch.dict('os.environ',{'PUBLIC_BASE_URL':'https://school.example'}):
            app=create_app(self.db)
        with patch('Chatbot.qrcode.QRCode.add_data') as add:
            image=app.test_client().get(f'/qr/{self.row["id"]}.png')
            self.assertEqual(image.status_code,200)
            self.assertTrue(image.data.startswith(b'\x89PNG'))
            add.assert_called_once_with('https://school.example/student/'+self.row['token'])
        profile=self.client.get('/student/'+self.row['token'])
        page=profile.get_data(as_text=True)
        for field in ('name','course','section','year_level'):
            self.assertIn(str(self.row[field]),page)
        self.assertNotIn(self.row['student_id'],page)
        if self.row['pending_reason']:
            self.assertNotIn(self.row['pending_reason'],page)
        self.assertEqual(profile.headers['Cache-Control'],'no-store')
        self.assertEqual(self.client.get('/student/unknown').status_code,404)
        self.assertIn('30 × 30 mm',self.client.get(f'/sticker/{self.row["id"]}').get_data(as_text=True))

    def test_role_tools_and_student_request(self):
        home = self.client.get('/').get_data(as_text=True)
        self.assertIn('role-dialog', home)
        self.assertIn('Verify ID', home)
        found = self.client.get('/api/verify', query_string={'q': self.row['student_id']})
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json['matches'][0]['student_id'], self.row['student_id'])
        lookup = self.client.post('/api/student/lookup', json={'student_id': self.row['student_id']})
        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(self.client.post('/api/student/lookup', json={'student_id': 'NOPE'}).status_code, 404)
        gate = self.client.post('/api/gate', json={'student_id': self.row['student_id'], 'direction': 'in'})
        self.assertEqual(gate.status_code, 201)
        incident = self.client.post('/api/incidents', json={
            'category': 'No ID', 'location': 'Main gate', 'detail': 'Visitor without a card'})
        self.assertEqual(incident.status_code, 201)
        ticket = self.client.post('/api/requests', json={
            'student_id': 'DEMO-LOST', 'name': 'Pat Lee', 'request_type': 'Lost ID', 'detail': 'Wallet stolen'})
        self.assertEqual(ticket.status_code, 201)
        accepted = self.client.patch(f'/api/requests/{ticket.json["id"]}', json={'status': 'Accepted'})
        self.assertEqual(accepted.status_code, 200)
        inventory = self.client.get('/api/inventory').json['records']
        self.assertTrue(any(r['student_id'] == 'DEMO-LOST' and r['request_type'] == 'Lost ID' for r in inventory))
        student_chat = self.client.post('/api/chat', json={
            'message': 'What can you do?', 'workspace_role': 'student'}).json
        self.assertIn('Look up', student_chat['reply'])
        guard_chat = self.client.post('/api/chat', json={
            'message': 'hello', 'workspace_role': 'guard'}).json
        self.assertTrue(any(a['type'] == 'open_verify' for a in guard_chat['actions']))
        card = self.client.get(f'/card/{self.row["id"]}/front.png')
        self.assertEqual(card.status_code, 200)
        self.assertTrue(card.data.startswith(b'\x89PNG'))
        notes = self.client.patch(f'/api/inventory/{self.row["id"]}', json={'notes': 'Called guardian'})
        self.assertEqual(notes.status_code, 200)
        self.assertEqual(notes.json['notes'], 'Called guardian')


if __name__=='__main__':
    unittest.main()
