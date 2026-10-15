"""Verified lost/found signals, permanent QR retirement, tickets and student notices."""
import secrets
from datetime import timedelta
from urllib.parse import urlparse

from flask import abort, g, jsonify, request, url_for

from pickup import school_now

LOSS_STATES = ('Lost', 'Reported Lost')
READY_STATES = ('Ready for release', 'Unclaimed')


def new_code():
    return 'PGPC-' + secrets.token_hex(12).upper()


class IDLifecycle:
    def __init__(self, app, connect, record):
        self.connect, self.record = connect, record
        with connect() as db:
            if 'ready_since' not in {row['name'] for row in db.execute('PRAGMA table_info(inventory)')}:
                db.execute("ALTER TABLE inventory ADD COLUMN ready_since TEXT NOT NULL DEFAULT ''")
                # Existing stock has no verified age. Start its clock now, never invent an old date.
                db.execute("UPDATE inventory SET ready_since=? WHERE status='Ready for release'", (self.now(),))
            db.execute('''CREATE TABLE IF NOT EXISTS qr_codes (
                token TEXT PRIMARY KEY,record_id INTEGER NOT NULL REFERENCES inventory(id),
                state TEXT NOT NULL CHECK(state IN ('active','flagged','retired')),
                issued_at TEXT NOT NULL,retired_at TEXT NOT NULL DEFAULT '')''')
            db.execute('''CREATE TABLE IF NOT EXISTS qr_images (
                token TEXT NOT NULL REFERENCES qr_codes(token),side TEXT NOT NULL,image BLOB NOT NULL,
                PRIMARY KEY(token,side))''')
            db.execute('''CREATE TABLE IF NOT EXISTS id_tickets (
                id INTEGER PRIMARY KEY,record_id INTEGER NOT NULL REFERENCES inventory(id),
                token TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,
                reported_by INTEGER NOT NULL REFERENCES accounts(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY,record_id INTEGER NOT NULL REFERENCES inventory(id),
                message TEXT NOT NULL,created_at TEXT NOT NULL,is_read INTEGER NOT NULL DEFAULT 0)''')
            db.execute('''CREATE TABLE IF NOT EXISTS id_policy (
                id INTEGER PRIMARY KEY CHECK(id=1),requirements TEXT NOT NULL DEFAULT '',
                fee TEXT NOT NULL DEFAULT '',claim_location TEXT NOT NULL DEFAULT 'Security Office')''')
            db.execute('INSERT OR IGNORE INTO id_policy (id) VALUES (1)')
            for row in db.execute('SELECT * FROM inventory').fetchall():
                self.register_code(db,row)
        self.register_routes(app)

    @staticmethod
    def now():
        return school_now().strftime('%Y-%m-%d %H:%M:%S')

    def register_code(self,db,row):
        state='flagged' if row['id_status'] in (*LOSS_STATES,'Missing from Stock') else 'active'
        db.execute('INSERT OR IGNORE INTO qr_codes (token,record_id,state,issued_at) VALUES (?,?,?,?)',
                   (row['token'],row['id'],state,self.now()))

    def notify(self,db,rid,message):
        db.execute('INSERT INTO notifications (record_id,message,created_at) VALUES (?,?,?)',(rid,message,self.now()))

    def ticket(self,db,row,kind,state='Open',token=None):
        token=token or row['token']
        existing=db.execute("SELECT id FROM id_tickets WHERE record_id=? AND token=? AND kind=? AND state IN ('Open','Replacement Processing','For claiming') ORDER BY id DESC LIMIT 1",(row['id'],token,kind)).fetchone()
        if existing:
            return existing['id']
        return db.execute('INSERT INTO id_tickets (record_id,token,kind,state,reported_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                          (row['id'],token,kind,state,g.user['id'],self.now(),self.now())).lastrowid

    @staticmethod
    def reference(ticket_id):
        return f'ID-{ticket_id:06d}'

    def audit_unclaimed(self):
        cutoff=(school_now()-timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows=db.execute("SELECT id FROM inventory WHERE status='Ready for release' AND ready_since!='' AND ready_since<?",(cutoff,)).fetchall()
            for row in rows:
                db.execute("UPDATE inventory SET status='Unclaimed' WHERE id=?",(row['id'],))
                self.notify(db,row['id'],'Your ID has been available for more than 30 days and is marked Unclaimed. Contact the ID office or choose an available pickup appointment.')

    def policy(self):
        with self.connect() as db:
            return dict(db.execute('SELECT requirements,fee,claim_location FROM id_policy WHERE id=1').fetchone())

    def policy_reply(self):
        policy=self.policy()
        return 'Requirements: '+(policy['requirements'] or 'Hindi pa configured—confirm sa ID office.')+' Fee: '+(policy['fee'] or 'Hindi pa configured—confirm sa ID office.')

    def transition(self,db,current,previous):
        """Apply staff updates within the inventory transaction, including QR issuance."""
        changed_id=current['id_status']!=previous['id_status']
        if changed_id and current['id_status'] in ('Reported Lost','Found - For Claiming','Missing from Stock'):
            return 'Use the verified lost/found report or stock audit action for that status.'
        if previous['id_status']=='Found - For Claiming' and (changed_id or current['status']!='For claiming'):
            return 'Confirm claiming with another valid ID using the claim action.'
        if previous['id_status'] in LOSS_STATES and changed_id and not (current['id_status']=='Active' and current['status']=='Released'):
            return 'Use the found/claim flow or release the replacement; do not reactivate a reported ID manually.'
        if previous['id_status']=='Missing from Stock' and (changed_id or current['status']!=previous['status']):
            return 'Use the found report after the missing stock is physically recovered.'
        if current['status']=='Unclaimed' and previous['status']!='Unclaimed':
            return 'Unclaimed is assigned only after more than 30 days in ready stock.'
        open_lost=db.execute("SELECT id FROM id_tickets WHERE record_id=? AND kind='lost' AND state IN ('Open','Replacement Processing')",(current['id'],)).fetchall()
        if open_lost and current['status']=='Ready for release' and previous['status'] not in ('Replacement Processing',*READY_STATES):
            return 'Start Replacement Processing before marking the replacement Ready for release.'
        if open_lost and current['status'] not in ('Pending','Replacement Processing',*READY_STATES,'Released'):
            return 'Use Replacement Processing for a confirmed replacement request.'
        if current['status']=='Replacement Processing':
            if current['id_status'] not in LOSS_STATES:
                return 'Replacement Processing requires a confirmed lost ID.'
            if previous['status']!='Replacement Processing':
                current['schedule_type']=current['schedule_date']=current['schedule_time']=''
            db.execute("UPDATE id_tickets SET state='Replacement Processing',updated_at=? WHERE record_id=? AND kind='lost' AND state='Open'",(self.now(),current['id']))
        if previous['id_status'] in LOSS_STATES and current['status']=='Released':
            if open_lost and previous['status'] not in READY_STATES:
                return 'Mark the replacement Ready for release before issuing it.'
            old=previous['token']
            db.execute("UPDATE qr_codes SET state='retired',retired_at=? WHERE token=?",(self.now(),old))
            db.execute('INSERT OR REPLACE INTO qr_images (token,side,image) SELECT ?,side,image FROM id_images WHERE record_id=?',(old,current['id']))
            db.execute('DELETE FROM id_images WHERE record_id=?',(current['id'],))
            current['token']=new_code();current['id_status']='Active'
            db.execute('UPDATE inventory SET token=? WHERE id=?',(current['token'],current['id']))
            self.register_code(db,current)
            db.execute("UPDATE id_tickets SET state='Resolved - replacement issued',updated_at=? WHERE record_id=? AND kind='lost' AND state IN ('Open','Replacement Processing')",(self.now(),current['id']))
            self.notify(db,current['id'],'Your replacement ID has been released with a new QR code. The old QR is permanently deactivated.')
        elif current['id_status']=='Lost' and previous['id_status']!='Lost':
            db.execute("UPDATE qr_codes SET state='flagged' WHERE token=? AND state!='retired'",(previous['token'],))
        if current['status']=='Ready for release' and previous['status'] not in READY_STATES:
            current['ready_since']=self.now()
            if current['schedule_type']=='Lost ID application':
                current['schedule_type']=current['schedule_date']=current['schedule_time']=''
            self.notify(db,current['id'],'Your ID is Ready for release. Available pickup dates will appear in your chatbot.')
        elif current['status'] not in READY_STATES:
            current['ready_since']=''
        return None

    def scanned(self,token):
        self.audit_unclaimed()
        with self.connect() as db:
            qr=db.execute('SELECT * FROM qr_codes WHERE token=?',(token,)).fetchone()
            row=db.execute('SELECT * FROM inventory WHERE id=?',(qr['record_id'],)).fetchone() if qr else None
        if row is None:
            abort(404)
        return dict(row),dict(qr)

    def register_routes(self,app):
        @app.get('/api/id-policy')
        def id_policy():
            return jsonify(self.policy())

        @app.put('/api/id-policy')
        def save_id_policy():
            if g.user['role']!='coordinator': abort(403)
            payload=request.get_json(silent=True)
            if not isinstance(payload,dict) or set(payload)!={'requirements','fee','claim_location'} or any(not isinstance(v,str) or len(v)>2000 for v in payload.values()) or not payload['claim_location'].strip():
                return jsonify(error='Supply requirements, fee, and a claim location (text, up to 2000 characters each).'),400
            with self.connect() as db:
                db.execute('UPDATE id_policy SET requirements=?,fee=?,claim_location=? WHERE id=1',tuple(payload[k].strip() for k in ('requirements','fee','claim_location')))
            return jsonify(self.policy())

        @app.post('/api/id-reports/lost')
        def report_lost_id():
            if g.user['role']!='student': abort(403)
            row=self.record(g.user['record_id'])
            payload=request.get_json(silent=True)
            if not isinstance(payload,dict) or set(payload)!={'student_id','course','section','confirmed'} or payload['confirmed'] is not True:
                return jsonify(error='Confirm the lost ID and enter your student number, course, and section.'),400
            if any(not isinstance(payload[k],str) or payload[k].strip().casefold()!=row[k].strip().casefold() for k in ('student_id','course','section')):
                return jsonify(error='Student number, course, or section does not match your signed-in record.'),400
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row=dict(db.execute('SELECT * FROM inventory WHERE id=?',(row['id'],)).fetchone())
                if row['id_status'] not in ('Active',*LOSS_STATES):
                    return jsonify(error='The record is not an issued ID that can be reported lost. Contact the ID office.'),409
                ticket=self.ticket(db,row,'lost')
                # Repeated confirmation is idempotent and cannot reset an ongoing replacement.
                if row['id_status']!='Reported Lost':
                    db.execute("UPDATE inventory SET id_status='Reported Lost',status='Pending',request_type='Lost ID',schedule_type='',schedule_date='',schedule_time='',ready_since='' WHERE id=?",(row['id'],))
                db.execute("UPDATE qr_codes SET state='flagged' WHERE token=? AND state!='retired'",(row['token'],))
            return jsonify(reply=f'Ticket {self.reference(ticket)}: Reported Lost. Your QR is flagged. '+self.policy_reply(),ticket=self.reference(ticket),record=self.record(row['id']))

        @app.post('/api/id-reports/found')
        def report_found_id():
            if g.user['role'] not in ('guard','coordinator'): abort(403)
            payload=request.get_json(silent=True)
            if not isinstance(payload,dict) or set(payload)!={'code','confirmed'} or not isinstance(payload['code'],str) or payload['confirmed'] is not True:
                return jsonify(error='Scan the QR code and confirm the physical ID was received by Security.'),400
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                qr=db.execute('SELECT * FROM qr_codes WHERE token=?',(payload['code'],)).fetchone()
                if qr is None: abort(404)
                row=dict(db.execute('SELECT * FROM inventory WHERE id=?',(qr['record_id'],)).fetchone())
                prior_retired=db.execute("SELECT id FROM id_tickets WHERE token=? AND kind='found' AND state='Resolved - retired ID'",(qr['token'],)).fetchone()
                if qr['state']=='retired' and prior_retired:
                    return jsonify(reply=f"{self.reference(prior_retired['id'])}: Found old ID already logged; this QR remains permanently deactivated.")
                ticket=self.ticket(db,row,'found',state='For claiming' if qr['state']!='retired' else 'Resolved - retired ID',token=qr['token'])
                if qr['state']=='retired' or qr['token']!=row['token']:
                    reply='Found old ID logged. This QR is permanently deactivated; the current replacement and its appointments remain unchanged.'
                    self.notify(db,row['id'],reply+' Contact '+self.policy()['claim_location']+'.')
                elif row['id_status']!='Found - For Claiming':
                    db.execute("UPDATE id_tickets SET state='Cancelled - original found',updated_at=? WHERE record_id=? AND kind='lost' AND state IN ('Open','Replacement Processing')",(self.now(),row['id']))
                    db.execute("UPDATE id_tickets SET state='Resolved - stock recovered',updated_at=? WHERE record_id=? AND kind='stock missing' AND state='Open'",(self.now(),row['id']))
                    db.execute("UPDATE inventory SET id_status='Found - For Claiming',status='For claiming',schedule_type='',schedule_date='',schedule_time='',ready_since='' WHERE id=?",(row['id'],))
                    db.execute("UPDATE qr_codes SET state='active' WHERE token=? AND state!='retired'",(qr['token'],))
                    reply='Found - For Claiming. The replacement request is cancelled and the original QR is reactivated.'
                    self.notify(db,row['id'],reply+' Collect your ID at '+self.policy()['claim_location']+' and present another valid ID.')
                else:
                    reply='Found - For Claiming is already recorded. The student has been notified.'
            return jsonify(reply=f'{self.reference(ticket)}: {reply}',record=self.record(row['id']))

        @app.post('/api/inventory/<int:record_id>/claim')
        def claim_found_id(record_id):
            if g.user['role'] not in ('guard','coordinator'): abort(403)
            self.record(record_id)
            payload=request.get_json(silent=True)
            if not isinstance(payload,dict) or set(payload)!={'verified'} or payload['verified'] is not True:
                return jsonify(error='Verify the owner using another valid ID before confirming claiming.'),400
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT * FROM inventory WHERE id=?',(record_id,)).fetchone()
                if row['id_status']!='Found - For Claiming':
                    return jsonify(error='This ID is not waiting to be claimed.'),409
                db.execute("UPDATE inventory SET id_status='Active',status='Released' WHERE id=?",(record_id,))
                db.execute("UPDATE id_tickets SET state='Resolved - claimed',updated_at=? WHERE record_id=? AND kind='found' AND state='For claiming'",(self.now(),record_id))
                self.notify(db,record_id,'Your original ID has been claimed and is Active.')
            return jsonify(reply='Claiming confirmed. The original ID is Active.',record=self.record(record_id))

        @app.post('/api/inventory/<int:record_id>/audit')
        def stock_audit(record_id):
            row=self.record(record_id)
            payload=request.get_json(silent=True)
            if not isinstance(payload,dict) or set(payload)!={'confirmed'} or payload['confirmed'] is not True:
                return jsonify(error='Confirm that the ID expected in office stock was physically checked and is missing.'),400
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row=dict(db.execute('SELECT * FROM inventory WHERE id=?',(record_id,)).fetchone())
                if row['status'] not in READY_STATES:
                    return jsonify(error='Only an ID expected in ready stock can be marked Missing from Stock.'),409
                ticket=self.ticket(db,row,'stock missing')
                db.execute("UPDATE inventory SET id_status='Missing from Stock',status='Pending',schedule_type='',schedule_date='',schedule_time='',ready_since='' WHERE id=?",(record_id,))
                db.execute("UPDATE qr_codes SET state='flagged' WHERE token=? AND state!='retired'",(row['token'],))
                self.notify(db,record_id,'The ID office has recorded Missing from Stock after a stock audit. Contact the office for assistance.')
            return jsonify(reply=f'{self.reference(ticket)}: Missing from Stock recorded from a confirmed office audit.',record=self.record(record_id))

        @app.get('/api/id-tickets')
        def id_tickets():
            with self.connect() as db:
                rows=[dict(row) for row in db.execute('SELECT * FROM id_tickets WHERE (? IS NULL OR record_id=?) ORDER BY id DESC',(g.user['record_id'],g.user['record_id']))]
            for row in rows: row['reference']=self.reference(row['id'])
            return jsonify(tickets=rows)

        @app.get('/api/notifications')
        def student_notifications():
            if g.user['role']!='student': abort(403)
            with self.connect() as db:
                rows=[dict(row) for row in db.execute('SELECT id,message,created_at FROM notifications WHERE record_id=? AND is_read=0 ORDER BY id LIMIT 500',(g.user['record_id'],))]
            return jsonify(notifications=rows)

        @app.post('/api/notifications/read')
        def read_notifications():
            if g.user['role']!='student': abort(403)
            payload=request.get_json(silent=True)
            if not isinstance(payload,dict) or set(payload)!={'ids'} or not isinstance(payload['ids'],list) or len(payload['ids'])>500 or any(type(i) is not int for i in payload['ids']):
                return jsonify(error='Supply notification IDs.'),400
            with self.connect() as db:
                db.executemany('UPDATE notifications SET is_read=1 WHERE id=? AND record_id=?',[(i,g.user['record_id']) for i in payload['ids']])
            return jsonify(read=True)

        @app.get('/api/qr/lookup')
        def qr_lookup():
            if g.user['role'] not in ('guard','coordinator'): abort(403)
            code=request.args.get('code','').strip()
            # Existing stickers may encode a URL; resolve only its code, never fetch it.
            if '://' in code:
                path=urlparse(code).path
                if not path.startswith('/student/') or '/' in path[len('/student/'):]:
                    return jsonify(error='Enter a school ID QR code.'),400
                code=path[len('/student/'):]
            self.scanned(code)
            return jsonify(url=url_for('profile',token=code))
