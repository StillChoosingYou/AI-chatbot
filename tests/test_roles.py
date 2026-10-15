import sqlite3
import tempfile
import unittest
from pathlib import Path

from account_helpers import signed_client
from Chatbot import create_app


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'inventory.sqlite3'
        self.app = create_app(self.db)
        self.coordinator = signed_client(self.app)
        self.guard = signed_client(self.app, 'guard')
        self.rows = self.coordinator.get('/api/inventory').json['records']
        self.sam = next(r for r in self.rows if r['name'] == 'Sam Reyes')
        self.student = signed_client(self.app, 'student', self.sam['id'])

    def tearDown(self):
        self.temp.cleanup()

    def ask(self, client, message, **fields):
        return client.post('/api/chat', json={'message':message, **fields})

    def test_sign_in_csrf_and_permissions(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get('/api/inventory').status_code, 401)
        self.assertEqual(anonymous.post('/api/chat',json={'message':'hello'}).status_code, 401)
        self.assertEqual(anonymous.get('/').status_code, 302)
        anonymous.get('/login')
        self.assertEqual(anonymous.post('/login',data={'username':'coordinator','password':'test-password-123'}).status_code, 400)
        with anonymous.session_transaction() as session:
            token = session['csrf']
        self.assertEqual(anonymous.post('/login',data={'username':'coordinator','password':'wrong','csrf_token':token}).status_code, 401)
        for client in (self.guard, self.student):
            for path, method in [('/api/inventory','post'), (f'/api/inventory/{self.sam["id"]}','patch'),
                                 (f'/api/inventory/{self.sam["id"]}/images','post'),
                                 (f'/api/inventory/{self.sam["id"]}/images/front','delete')]:
                self.assertEqual(getattr(client, method)(path,json={'status':'Released'}).status_code, 403)
            page=client.get('/').get_data(as_text=True)
            self.assertIn('hidden class="primary" id="new-record"',page)
        self.assertEqual(self.coordinator.patch(f'/api/inventory/{self.sam["id"]}',json={'id_status':'Active'}, headers={'X-CSRF-Token':'bad'}).status_code,400)
        self.assertEqual(self.guard.post('/logout').status_code,302)
        self.assertEqual(self.guard.get('/api/inventory').status_code,401)

    def test_guard_name_lookup_and_current_scan(self):
        for message in ("What's the processing status of Sam Reyes’s ID?", "What is the status of sam reyes ID?", "Is Sam Reyes's ID active?", "What is the status of DEMO-003 ID?", "What is the ID status of Sam Reyes?"):
            response = self.ask(self.guard,message,record_id=self.rows[0]['id'])
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json['id_status'],'Lost')
            self.assertEqual(response.json['processing_status'],'Processing')
            self.assertIn('No ID, No Entry',response.json['reply'])
            self.assertEqual(response.json['qr_url'],f'/qr/{self.sam["id"]}.png')
            self.assertEqual(self.guard.get(response.json['qr_url']).status_code,200)
        url = '/student/' + self.sam['token']
        public = self.app.test_client()
        self.assertIn('Lost',public.get(url).get_data(as_text=True))
        result=self.coordinator.patch(f'/api/inventory/{self.sam["id"]}',json={'id_status':'Active','status':'Released'})
        self.assertEqual(result.status_code,200)
        page=public.get(url).get_data(as_text=True)
        for text in ('Active','Released','Front of ID','Back of ID','does not authorize entry'):
            self.assertIn(text,page)
        self.assertEqual(result.json['token'],self.sam['token'])
        self.assertEqual(self.coordinator.patch(f'/api/inventory/{self.sam["id"]}',json={'id_status':'Invalid'}).status_code,400)

    def test_ambiguous_missing_and_partial_names(self):
        new={'student_id':'DEMO-007','name':'Sam Reyes','course':'BSIT','section':'C','year_level':2}
        self.assertEqual(self.coordinator.post('/api/inventory',json=new).status_code,201)
        result=self.ask(self.guard,"What's the status of Sam Reyes's ID?").json
        self.assertEqual(len(result['candidates']),2)
        self.assertNotIn('qr_url',result)
        result=self.ask(self.guard,"What is the status of Sam's ID?").json
        self.assertEqual(len(result['candidates']),2)
        result=self.ask(self.guard,"What is the status of Missing Person's ID?",record_id=self.sam['id']).json
        self.assertIn('No matching student',result['reply'])
        self.assertNotIn('qr_url',result)
        result=self.ask(self.guard,'What is the ID processing status?',record_id=self.sam['id']).json
        self.assertEqual(result['id_status'],'Lost')

    def test_student_sees_only_own_record(self):
        rows=self.student.get('/api/inventory').json['records']
        self.assertEqual([row['id'] for row in rows],[self.sam['id']])
        result=self.ask(self.student,'What is my ID processing status?').json
        self.assertEqual(result['student_name'],'Sam Reyes')
        other=next(r for r in self.rows if r['id']!=self.sam['id'])
        self.assertEqual(self.ask(self.student,'What is the ID status?',record_id=other['id']).status_code,403)
        for path in (f'/qr/{other["id"]}.png',f'/sticker/{other["id"]}'):
            self.assertEqual(self.student.get(path).status_code,403)
        result=self.ask(self.student,f'What is the status of {other["name"]} ID?').json
        self.assertIn('No matching student',result['reply'])
        self.assertNotIn('qr_url',result)
        result=self.ask(self.student,f"Is {other['name']}'s ID active?").json
        self.assertIn('No matching student',result['reply'])
        self.assertNotIn('qr_url',result)

    def test_existing_database_migration_preserves_records_and_images(self):
        # Recreate the old schema exactly by dropping just the new column.
        with sqlite3.connect(self.db) as db:
            db.execute('ALTER TABLE inventory DROP COLUMN id_status')
            db.execute("INSERT INTO id_images VALUES (?,'front',?)",(self.sam['id'],b'test-image'))
        migrated=create_app(self.db)
        rows=signed_client(migrated).get('/api/inventory').json['records']
        self.assertEqual([(r['id'],r['token']) for r in rows],[(r['id'],r['token']) for r in self.rows])
        self.assertEqual(next(r for r in rows if r['name']=='Sam Reyes')['id_status'],'Lost')
        self.assertEqual(next(r for r in rows if r['name']=='Taylor Cruz')['id_status'],'Active')
        self.assertEqual(next(r for r in rows if r['name']=='Alex Rivera')['id_status'],'Not issued')
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT image FROM id_images').fetchone()[0],b'test-image')
        again=signed_client(create_app(self.db)).get('/api/inventory').json['records']
        self.assertEqual(rows,again)

    def test_account_provisioning(self):
        runner=self.app.test_cli_runner()
        args=['create-account','--username','another-student','--role','student','--student-id','DEMO-001']
        result=runner.invoke(args=args,input='long-password-123\nlong-password-123\n')
        self.assertEqual(result.exit_code,0,result.output)
        result=runner.invoke(args=['create-account','--username','wrong','--role','student','--student-id','MISSING'],input='long-password-123\nlong-password-123\n')
        self.assertNotEqual(result.exit_code,0)
        result=runner.invoke(args=['create-account','--username','short','--role','guard'],input='short\nshort\n')
        self.assertNotEqual(result.exit_code,0)
