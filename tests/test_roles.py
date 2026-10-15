import sqlite3
import tempfile
import unittest
from pathlib import Path

from account_helpers import signed_client, public_client
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
        self.assertEqual(anonymous.post('/api/chat',json={'message':'hello'}).status_code, 400)
        self.assertEqual(public_client(self.app).post('/api/chat',json={'message':'hello'}).status_code,200)
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
            self.assertNotIn('id="new-record"',page)
            self.assertNotIn('id="records"',page)
            self.assertIn('id="chat-form"',page)
            self.assertEqual(client.get('/api/inventory').status_code,403)
        self.assertEqual(self.coordinator.patch(f'/api/inventory/{self.sam["id"]}',json={'id_status':'Active'}, headers={'X-CSRF-Token':'bad'}).status_code,400)
        self.assertEqual(self.guard.post('/logout').status_code,302)
        self.assertEqual(self.guard.get('/api/inventory').status_code,401)

    def test_staff_inventory_and_scan_use_explicit_role_rules(self):
        result=self.ask(self.guard,"What's the processing status of Sam Reyes's ID?")
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['student_name'],'Sam Reyes')
        self.assertIn('qr_url',result.json)
        self.assertNotIn('pickup_offer',result.json)
        self.assertEqual(self.ask(self.guard,'Show ID inventory').status_code,403)
        for text in ('hello','Show ID inventory',"What's the status of Sam Reyes's ID?"):
            self.assertEqual(self.ask(self.coordinator,text).status_code,403)
        page=self.coordinator.get('/').get_data(as_text=True)
        self.assertIn('id="records"',page)
        self.assertNotIn('id="chat-form"',page)
        self.assertEqual(self.guard.get('/api/id-tickets').status_code,403)
        url='/student/'+self.sam['token']
        public=self.app.test_client().get(url)
        self.assertEqual(public.status_code,200)
        self.assertIn('Reported lost, contact PMO',public.get_data(as_text=True))
        self.assertNotIn(self.sam['name'],public.get_data(as_text=True))
        self.assertIn(self.sam['name'],self.guard.get(url).get_data(as_text=True))
        result=self.coordinator.patch(f'/api/inventory/{self.sam["id"]}',json={'status':'Released','id_status':'Active'})
        self.assertEqual(result.status_code,200)
        self.assertNotEqual(result.json['token'],self.sam['token'])
        self.assertIn('PERMANENTLY DEACTIVATED',self.guard.get(url).get_data(as_text=True))

    def test_names_never_expand_student_visibility(self):
        new={'student_id':'DEMO-007','name':'Sam Reyes','course':'BSIT','section':'C','year_level':2}
        self.coordinator.post('/api/inventory',json=new)
        result=self.ask(self.student,"What is Sam Reyes's ID status?")
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json['student_name'],'Sam Reyes')
        self.assertNotIn('candidates',result.json)
        self.assertEqual(self.student.get('/api/my-id').json['record']['id'],self.sam['id'])

    def test_student_sees_only_own_record(self):
        rows=[self.student.get('/api/my-id').json['record']]
        self.assertEqual([row['id'] for row in rows],[self.sam['id']])
        self.assertNotIn('token',rows[0])
        self.assertEqual(self.guard.get('/api/my-id').status_code,403)
        self.assertEqual(self.coordinator.get('/api/my-id').status_code,403)
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
