import csv
from pathlib import Path
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from Chatbot import ROOT
from intent_model import ANNIntentModel
from pg_support import DatabaseTestCase


class DatasetTests(unittest.TestCase):
    """These need no database."""

    def test_dataset_integrity(self):
        with (ROOT/'property_management_dataset.csv').open() as f:
            rows = list(csv.DictReader(f))
        with (ROOT/'property_management_intent.csv').open() as f:
            intents = {r['intent'] for r in csv.DictReader(f)}
        self.assertEqual(len(rows),180)
        self.assertEqual(len({r['id'] for r in rows}),len(rows))
        self.assertEqual(len({r['text'].lower() for r in rows}),len(rows))
        self.assertEqual({r['intent'] for r in rows},intents-{'out_of_scope'})   # the fallback has no examples
        self.assertEqual({sum(r['intent']==i for r in rows) for i in intents-{'out_of_scope'}},{15})
        ElementTree.parse(ROOT/'chatbot.ows')

    def test_dataset_is_about_school_ids_only(self):
        not_ids={'greeting','thanks_goodbye','bot_help','talk_to_human','out_of_scope'}
        with (ROOT/'property_management_dataset.csv').open() as f:
            rows=list(csv.DictReader(f))
        with (ROOT/'property_management_intent.csv').open() as f:
            intents={r['intent'] for r in csv.DictReader(f)}
        self.assertFalse({r['intent'] for r in rows} & not_ids)
        self.assertEqual(intents & not_ids,{'out_of_scope'})        # the catalog keeps only the fallback label
        with (ROOT/'property_management_dataset.tab').open() as f:   # the Orange copy holds the same rows
            tab=[line.rstrip('\n').split('\t') for line in f][3:]
        self.assertEqual(tab,[[r[k] for k in ('id','text','intent','language','author')] for r in rows])

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
            ('purple asteroid quantum banana', 'out_of_scope'),
            ('Where is the canteen?', 'out_of_scope'),
            ('Tell me a joke', 'out_of_scope')]:
            with self.subTest(text=text):
                self.assertEqual(matcher.match(text),intent)


class SchoolIDTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.app = self.make_app()
        self.client = self.app.test_client()
        self.row = self.client.get('/api/inventory').json['records'][0]

    def test_appointments_status_and_live_chat(self):
        rid=self.row['id']
        self.assertIn('Type a student ID',self.client.post('/api/chat',json={'message':'What is the status of my student ID?'}).json['reply'])
        saved=self.client.patch(f'/api/inventory/{rid}',json={'status':'For release','schedule_type':'Release','schedule_date':'2026-11-04','schedule_time':'09:30'})
        self.assertEqual(saved.status_code,200)
        answer=self.client.post('/api/chat',json={'message':'When can I collect my school ID?','record_id':rid}).json['reply']
        self.assertIn('2026-11-04 at 09:30',answer)
        rows=self.make_app().test_client().get('/api/inventory').json['records']
        self.assertEqual(next(r for r in rows if r['id']==rid)['schedule_time'],'09:30')
        self.assertEqual(self.client.patch(f'/api/inventory/{rid}',json={'schedule_time':''}).status_code,400)
        self.assertEqual(self.client.patch(f'/api/inventory/{rid}',json={'schedule_type':'','schedule_time':'','schedule_date':''}).status_code,200)
        self.assertIn('No release appointment',self.client.post('/api/chat',json={'message':'When can I collect my school ID?','record_id':rid}).json['reply'])
        for kind,message in [('Pending follow-up','When is my pending ID follow-up appointment?'),('Lost ID replacement','When is my lost ID replacement schedule?')]:
            self.client.patch(f'/api/inventory/{rid}',json={'schedule_type':kind,'schedule_date':'2026-11-05','schedule_time':'14:00'})
            self.assertIn('2026-11-05 at 14:00',self.client.post('/api/chat',json={'message':message,'record_id':rid}).json['reply'])

    def test_invalid_and_duplicate_records(self):
        row={'student_id':'P202400230','name':'<script>student</script>','year_section':'2B','request_type':'Lost ID'}
        added=self.client.post('/api/inventory',json=row)
        self.assertEqual(added.status_code,201)
        self.assertEqual(self.client.post('/api/inventory',json=row).status_code,409)
        self.assertEqual(self.client.post('/api/inventory',json={**row,'student_id':'P202400231','year_section':'8A'}).status_code,400)
        rid=added.json['id']
        for payload in ({'status':'invalid'},{'schedule_type':'Release','schedule_date':'2026-02-30','schedule_time':'10:00'},
                        {'name':''},{'year_section':'9Z'},{'contact_number':'call me'},{'status':3},{'student_id':'P202400232'}):
            self.assertEqual(self.client.patch(f'/api/inventory/{rid}',json=payload).status_code,400,payload)
        self.assertEqual(self.client.post('/api/chat',json={'message':'hello','record_id':True}).status_code,400)
        self.assertEqual(self.client.get('/qr/9999.png').status_code,404)
        page=self.client.get('/student/'+added.json['token']).get_data(as_text=True)
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>student</script>',page)

    def test_scan_page_is_the_id_front_and_back_and_keeps_private_details_off_a_public_scan(self):
        url = '/student/' + self.row['token']
        page = self.client.get(url).get_data(as_text=True)
        for text in ('PADRE GARCIA', 'POLYTECHNIC COLLEGE', 'Front of the school ID', 'Back of the school ID', 'Important reminder',
                     'CONTACT US', 'A.Y. 2026 - 2027', self.row['name'], self.row['year_section'], url + '/qr.png'):
            self.assertIn(text, page)
        self.assertIn(self.row['student_id'], page)             # printed on the card, so the scan shows it
        for private in (self.row['address'], self.row['contact_number']):
            self.assertNotIn(private, page)
        self.assertIn('shown to school staff', page)
        with patch.dict('os.environ', {'PUBLIC_CARD_DETAILS': '1'}):
            shown = self.client.get(url).get_data(as_text=True)
        for private in (self.row['student_id'], self.row['address'], self.row['contact_number']):
            self.assertIn(private, shown)
        preview = self.client.get(f'/card/{self.row["id"]}').get_data(as_text=True)      # the preview is the same card
        for text in ('PADRE GARCIA', 'Important reminder', f'STUDENT NO. {self.row["student_id"]}', self.row['name'],
                     f'/student/{self.row["token"]}/photo.svg'):
            self.assertIn(text, preview)
        self.assertIn(f'STUDENT NO. {self.row["student_id"]}', page)
        qr = self.client.get(url + '/qr.png')
        self.assertEqual((qr.status_code, qr.data[:4]), (200, b'\x89PNG'))
        self.assertEqual(self.client.get('/student/unknown/qr.png').status_code, 404)

    def test_sample_photos_are_drawings_that_stay_the_same_for_a_student(self):
        from avatars import avatar_svg
        with self.db() as db:
            tokens = [token for (token,) in db.execute('SELECT token FROM inventory ORDER BY id').fetchall()]
        svgs = [avatar_svg(t) for t in tokens]
        self.assertEqual(svgs, [avatar_svg(t) for t in tokens])
        self.assertGreater(len(set(svgs)), 1)                               # not the same face for everyone
        for svg in svgs:
            ElementTree.fromstring(svg)
        photo = self.client.get(f'/student/{tokens[0]}/photo.svg')
        self.assertEqual((photo.status_code, photo.mimetype), (200, 'image/svg+xml'))
        self.assertEqual(photo.get_data(as_text=True), svgs[0])
        self.assertIn(f'/student/{tokens[0]}/photo.svg', self.client.get(f'/student/{tokens[0]}').get_data(as_text=True))
        self.assertEqual(self.client.get('/student/unknown/photo.svg').status_code, 404)

    def test_qr_url_and_public_fields(self):
        with patch.dict('os.environ',{'PUBLIC_BASE_URL':'https://school.example'}):
            app=self.make_app()
        with patch('Chatbot.qrcode.QRCode.add_data') as add:
            image=app.test_client().get(f'/qr/{self.row["id"]}.png')
            self.assertEqual(image.status_code,200)
            self.assertTrue(image.data.startswith(b'\x89PNG'))
            add.assert_called_once_with('https://school.example/student/'+self.row['token'])
        profile=self.client.get('/student/'+self.row['token'])
        page=profile.get_data(as_text=True)
        for field in ('name','year_section'):
            self.assertIn(str(self.row[field]),page)
        self.assertNotIn(self.row['address'],page)
        self.assertNotIn(self.row['contact_number'],page)
        if self.row['pending_reason']:
            self.assertNotIn(self.row['pending_reason'],page)
        self.assertEqual(profile.headers['Cache-Control'],'no-store')
        self.assertEqual(self.client.get('/student/unknown').status_code,404)
        self.assertIn('30 × 30 mm',self.client.get(f'/sticker/{self.row["id"]}').get_data(as_text=True))

    def test_each_role_has_its_own_address_and_the_home_page_is_the_chooser(self):
        self.assertIn('data-page-role=""', self.client.get('/').get_data(as_text=True))
        for role in ('coordinator', 'guard', 'student'):
            for path in (f'/{role}', f'/{role}/'):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(f'data-page-role="{role}"', response.get_data(as_text=True))
        self.assertEqual(self.client.get('/admin').status_code, 404)
        self.assertEqual(self.client.get('/student/unknown').status_code, 404)    # a sticker token still works as before

    def test_role_chooser_is_opaque_white_and_goes_to_the_role_address(self):
        css = Path(ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertRegex(css, r'#role-dialog::backdrop[^{]*\{ background: #fff; opacity: 1;')
        script = Path(ROOT / 'static' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("location.assign(`/${role}`)", script)

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
            'student_id': 'P202400231', 'name': 'Pat Lee', 'request_type': 'Lost ID', 'detail': 'Wallet stolen'})
        self.assertEqual(ticket.status_code, 201)
        accepted = self.client.patch(f'/api/requests/{ticket.json["id"]}', json={'status': 'Accepted'})
        self.assertEqual(accepted.status_code, 200)
        inventory = self.client.get('/api/inventory').json['records']
        self.assertTrue(any(r['student_id'] == 'P202400231' and r['request_type'] == 'Lost ID' for r in inventory))
        student_chat = self.client.post('/api/chat', json={
            'message': 'What can you do?', 'workspace_role': 'student'}).json
        self.assertIn('Type a student ID', student_chat['reply'])
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
