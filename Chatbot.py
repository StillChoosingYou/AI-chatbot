"""Local school ID management prototype. Run with .venv/bin/python Chatbot.py."""
import csv
import io
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import qrcode
from flask import Flask, abort, jsonify, render_template, request, send_file, url_for

from id_cards import sample_card
from intent_model import ANNIntentModel, classify

ROOT = Path(__file__).resolve().parent
STATUSES = ('Pending', 'Processing', 'Ready for release', 'Released')
KINDS = ('New ID', 'Pending ID', 'Lost ID')
SCHEDULES = ('Release', 'Pending follow-up', 'Lost ID replacement')
ROLES = ('coordinator', 'guard', 'student')
GATE_DIRECTIONS = ('in', 'out')
INCIDENT_CATEGORIES = ('No ID', 'Lost ID found', 'Visitor', 'Suspicious activity', 'Other')
INCIDENT_STATUSES = ('Open', 'Resolved')
REQUEST_TYPES = ('Lost ID', 'Status inquiry', 'Appointment help', 'Other')
REQUEST_STATUSES = ('Submitted', 'In review', 'Accepted', 'Closed')
OFFICE = {
    'hours': 'Monday–Friday, 8:00 AM – 5:00 PM (school local time)',
    'location': 'Property Management Office, Ground Floor, Admin Building',
    'contact': 'pmo@school.example · local 2100',
    'requirements': {
        'New ID': 'Filled application form, recent 1×1 photo, and proof of enrollment.',
        'Lost ID': 'Affidavit of loss, recent 1×1 photo, and replacement fee receipt.',
        'Pending ID': 'Submit the missing documents listed on your record, then wait for processing.',
    },
}

INTENT_ACTIONS = {
    'id_processing_status': [{'label': '📋 View record', 'type': 'view_record'}],
    'pending_id': [
        {'label': '📋 View record', 'type': 'view_record'},
        {'label': '✏️ Update status', 'type': 'update_record'},
    ],
    'id_release_schedule': [
        {'label': '📋 View record', 'type': 'view_record'},
        {'label': '🖨️ Print sticker', 'type': 'print_sticker'},
    ],
    'pending_id_schedule': [{'label': '✏️ Update schedule', 'type': 'update_record'}],
    'lost_id_schedule': [{'label': '✏️ Update schedule', 'type': 'update_record'}],
    'lost_id_report': [{'label': '➕ Add lost ID request', 'type': 'new_record'}],
    'qr_sticker_request': [{'label': '🖨️ Print sticker', 'type': 'print_sticker'}],
    'id_inventory': [{'label': '🔄 Reset filters', 'type': 'reset_filters'}],
    'export_data': [{'label': '📥 Export CSV', 'type': 'export_csv'}],
    'id_process_steps': [{'label': '📋 View requirements', 'type': 'ask_requirements'}],
    'id_validity_renewal': [{'label': '🕒 Office hours', 'type': 'ask_office'}],
}


def create_app(database=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 32 * 1024
    app.config['DATABASE'] = str(database or ROOT / 'data' / 'inventory.sqlite3')
    public_base = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
    if public_base and (urlparse(public_base).scheme not in ('http', 'https') or not urlparse(public_base).netloc):
        raise ValueError('PUBLIC_BASE_URL must be an absolute HTTP(S) URL')
    matcher = ANNIntentModel()

    @contextmanager
    def connect():
        connection = sqlite3.connect(app.config['DATABASE'])
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def now_stamp():
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def log_activity(actor_role, action, detail=''):
        with connect() as db:
            db.execute(
                'INSERT INTO activity_log (actor_role, action, detail, timestamp) VALUES (?,?,?,?)',
                (actor_role, action, detail[:500], now_stamp()))

    Path(app.config['DATABASE']).parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY, student_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, course TEXT NOT NULL, section TEXT NOT NULL,
            year_level INTEGER NOT NULL CHECK(year_level BETWEEN 1 AND 6),
            status TEXT NOT NULL, request_type TEXT NOT NULL, pending_reason TEXT NOT NULL DEFAULT '',
            schedule_type TEXT NOT NULL DEFAULT '', schedule_date TEXT NOT NULL DEFAULT '',
            schedule_time TEXT NOT NULL DEFAULT '', token TEXT NOT NULL UNIQUE,
            notes TEXT NOT NULL DEFAULT '')''')
        columns = {row[1] for row in db.execute('PRAGMA table_info(inventory)')}
        if 'notes' not in columns:
            db.execute("ALTER TABLE inventory ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        db.execute('''CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'bot')),
            message TEXT NOT NULL, intent TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            record_id INTEGER, timestamp TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history(session_id)')
        db.execute('''CREATE TABLE IF NOT EXISTS gate_logs (
            id INTEGER PRIMARY KEY, student_id TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '', direction TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '', timestamp TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS incidents (
            id INTEGER PRIMARY KEY, category TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL, student_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Open', timestamp TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS student_requests (
            id INTEGER PRIMARY KEY, student_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
            request_type TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Submitted', timestamp TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY, actor_role TEXT NOT NULL, action TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '', timestamp TEXT NOT NULL)''')
        if not db.execute('SELECT 1 FROM inventory LIMIT 1').fetchone():
            demo = [
                ('DEMO-001','Alex Rivera','BSIT','A',1,'Ready for release','New ID',''),
                ('DEMO-002','Jamie Santos','BSCS','B',2,'Pending','Pending ID','Photo verification needed'),
                ('DEMO-003','Sam Reyes','BSBA','A',3,'Processing','Lost ID',''),
                ('DEMO-004','Taylor Cruz','BSIT','B',4,'Released','New ID',''),
                ('DEMO-005','Casey Garcia','BSED','A',1,'Processing','New ID',''),
                ('DEMO-006','Jordan Flores','BSCS','A',2,'Pending','Pending ID','Enrollment verification needed'),
            ]
            db.executemany(
                'INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,pending_reason,token) VALUES (?,?,?,?,?,?,?,?,?)',
                [(*row, secrets.token_urlsafe(18)) for row in demo])

    def record(record_id):
        with connect() as db:
            row = db.execute('SELECT * FROM inventory WHERE id=?', (record_id,)).fetchone()
        if row is None:
            abort(404)
        return dict(row)

    def scan_url(token):
        path = url_for('profile', token=token)
        return public_base + path if public_base else url_for('profile', token=token, _external=True)

    def text_field(value, max_len=100, required=True):
        if not isinstance(value, str):
            return None
        value = value.strip()
        if required and not value:
            return None
        if len(value) > max_len:
            return None
        return value

    def find_by_student_id(student_id):
        with connect() as db:
            return db.execute(
                'SELECT * FROM inventory WHERE student_id=? COLLATE NOCASE',
                (student_id,)).fetchone()

    # ─── Page routes ───────────────────────────────────────────────

    @app.get('/')
    def home():
        return render_template(
            'index.html', statuses=STATUSES, kinds=KINDS, schedules=SCHEDULES,
            office=OFFICE, incident_categories=INCIDENT_CATEGORIES,
            request_types=REQUEST_TYPES)

    @app.get('/student/<token>')
    def profile(token):
        with connect() as db:
            row = db.execute(
                'SELECT name,course,section,year_level FROM inventory WHERE token=?',
                (token,)).fetchone()
        if row is None:
            abort(404)
        return render_template('profile.html', student=row)

    @app.get('/sticker/<int:record_id>')
    def sticker(record_id):
        row = record(record_id)
        return render_template('sticker.html', student=row, scan_url=scan_url(row['token']))

    @app.get('/card/<int:record_id>')
    def card_preview(record_id):
        row = record(record_id)
        return render_template('card.html', student=row)

    @app.get('/qr/<int:record_id>.png')
    def qr_image(record_id):
        row = record(record_id)
        stream = io.BytesIO()
        code = qrcode.QRCode(box_size=8, border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
        code.add_data(scan_url(row['token']))
        code.make(fit=True)
        code.make_image(fill_color='black', back_color='white').save(stream, format='PNG')
        stream.seek(0)
        return send_file(stream, mimetype='image/png')

    @app.get('/card/<int:record_id>/<side>.png')
    def card_image(record_id, side):
        if side not in ('front', 'back'):
            abort(404)
        row = record(record_id)
        stream = io.BytesIO()
        sample_card(row, side).save(stream, format='PNG')
        stream.seek(0)
        return send_file(stream, mimetype='image/png')

    @app.get('/api/office')
    def office_info():
        return jsonify(OFFICE)

    # ─── Inventory CRUD ───────────────────────────────────────────

    @app.get('/api/inventory')
    def inventory():
        with connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM inventory ORDER BY id DESC')]
        return jsonify(records=rows, statuses=STATUSES)

    @app.post('/api/inventory')
    def add_record():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        fields = {}
        for key in ('student_id', 'name', 'course', 'section'):
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > 100:
                return jsonify(error=f'{key.replace("_", " ")} is required (maximum 100 characters).'), 400
            fields[key] = value.strip()
        year = payload.get('year_level')
        if isinstance(year, bool) or not str(year).isdigit() or not 1 <= int(year) <= 6:
            return jsonify(error='Year level must be between 1 and 6.'), 400
        kind = payload.get('request_type', 'New ID')
        if kind not in KINDS:
            return jsonify(error='Choose a valid request type.'), 400
        try:
            with connect() as db:
                cursor = db.execute(
                    'INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,token) VALUES (?,?,?,?,?,?,?,?)',
                    (*fields.values(), int(year), 'Pending', kind, secrets.token_urlsafe(18)))
                new_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            return jsonify(error='That student ID already exists.'), 409
        log_activity('coordinator', 'Added record', fields['student_id'])
        return jsonify(record(new_id)), 201

    @app.patch('/api/inventory/<int:record_id>')
    def update_record(record_id):
        current = record(record_id)
        payload = request.get_json(silent=True)
        allowed = ('status', 'pending_reason', 'schedule_type', 'schedule_date', 'schedule_time', 'notes')
        if not isinstance(payload, dict) or not payload or set(payload) - set(allowed):
            return jsonify(error='Supply status, pending reason, notes, or schedule fields.'), 400
        if any(not isinstance(v, str) or len(v) > 500 for v in payload.values()):
            return jsonify(error='Fields must be text of up to 500 characters.'), 400
        current.update(payload)
        if current['status'] not in STATUSES or current['schedule_type'] not in ('', *SCHEDULES):
            return jsonify(error='Choose a valid status and appointment type.'), 400
        schedule = [current[k] for k in ('schedule_type', 'schedule_date', 'schedule_time')]
        if any(schedule):
            if not all(schedule):
                return jsonify(error='Appointment type, date, and time must be filled together.'), 400
            try:
                datetime.strptime(current['schedule_date'] + ' ' + current['schedule_time'], '%Y-%m-%d %H:%M')
            except ValueError:
                return jsonify(error='Use a valid date and time.'), 400
        with connect() as db:
            db.execute(
                'UPDATE inventory SET status=?,pending_reason=?,schedule_type=?,schedule_date=?,schedule_time=?,notes=? WHERE id=?',
                (*[current[k] for k in allowed], record_id))
        log_activity('coordinator', 'Updated record', current['student_id'])
        return jsonify(record(record_id))

    @app.delete('/api/inventory/<int:record_id>')
    def delete_record(record_id):
        row = record(record_id)
        with connect() as db:
            db.execute('DELETE FROM inventory WHERE id=?', (record_id,))
        log_activity('coordinator', 'Deleted record', row['student_id'])
        return jsonify(success=True)

    @app.patch('/api/inventory/bulk')
    def bulk_update():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        ids = payload.get('record_ids')
        status = payload.get('status')
        if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            return jsonify(error='Provide a list of record IDs.'), 400
        if len(ids) > 500:
            return jsonify(error='Bulk update is limited to 500 records.'), 400
        if not isinstance(status, str) or status not in STATUSES:
            return jsonify(error='Choose a valid processing status.'), 400
        placeholders = ','.join('?' * len(ids))
        with connect() as db:
            cursor = db.execute(f'UPDATE inventory SET status=? WHERE id IN ({placeholders})', (status, *ids))
            updated = cursor.rowcount
        log_activity('coordinator', 'Bulk status update', f'{updated} → {status}')
        return jsonify(updated=updated)

    @app.get('/api/inventory/export')
    def export_inventory():
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT student_id, name, course, section, year_level, status, request_type, '
                'pending_reason, schedule_type, schedule_date, schedule_time, notes '
                'FROM inventory ORDER BY id DESC')]
        output = io.StringIO()
        columns = ['student_id', 'name', 'course', 'section', 'year_level', 'status',
                   'request_type', 'pending_reason', 'schedule_type', 'schedule_date', 'schedule_time', 'notes']
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        if rows:
            writer.writerows(rows)
        mem = io.BytesIO(output.getvalue().encode('utf-8-sig'))
        return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='id_inventory.csv')

    # ─── Dashboard, queue, activity ────────────────────────────────

    @app.get('/api/stats')
    def stats():
        with connect() as db:
            total = db.execute('SELECT COUNT(*) FROM inventory').fetchone()[0]
            by_status = {row[0]: row[1] for row in db.execute(
                'SELECT status, COUNT(*) FROM inventory GROUP BY status')}
            by_course = {row[0]: row[1] for row in db.execute(
                'SELECT course, COUNT(*) FROM inventory GROUP BY course ORDER BY COUNT(*) DESC')}
            by_request = {row[0]: row[1] for row in db.execute(
                'SELECT request_type, COUNT(*) FROM inventory GROUP BY request_type')}
            upcoming = [dict(row) for row in db.execute(
                "SELECT id, name, student_id, schedule_type, schedule_date, schedule_time "
                "FROM inventory WHERE schedule_date != '' ORDER BY schedule_date, schedule_time LIMIT 8")]
            open_incidents = db.execute("SELECT COUNT(*) FROM incidents WHERE status='Open'").fetchone()[0]
            open_requests = db.execute("SELECT COUNT(*) FROM student_requests WHERE status IN ('Submitted','In review')").fetchone()[0]
        return jsonify(total=total, by_status=by_status, by_course=by_course,
                       by_request_type=by_request, upcoming_appointments=upcoming,
                       open_incidents=open_incidents, open_requests=open_requests)

    @app.get('/api/queue')
    def appointment_queue():
        today = datetime.now().strftime('%Y-%m-%d')
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT id, student_id, name, course, status, schedule_type, schedule_date, schedule_time "
                "FROM inventory WHERE schedule_date != '' ORDER BY schedule_date, schedule_time")]
        return jsonify(today=today, appointments=rows)

    @app.get('/api/activity')
    def activity():
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT actor_role, action, detail, timestamp FROM activity_log ORDER BY id DESC LIMIT 40')]
        return jsonify(events=rows)

    # ─── Guard tools ──────────────────────────────────────────────

    @app.get('/api/verify')
    def verify():
        raw = request.args.get('q', '')
        if not isinstance(raw, str):
            return jsonify(error='Enter a student ID, name, or QR token.'), 400
        query = raw.strip().replace('%', '').replace('_', '')
        if not query or len(query) > 100:
            return jsonify(error='Enter a student ID, name, or QR token.'), 400
        like = f'%{query}%'
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                '''SELECT id, student_id, name, course, section, year_level, status, request_type, token
                   FROM inventory WHERE student_id = ? COLLATE NOCASE OR token = ? OR name LIKE ? COLLATE NOCASE
                   LIMIT 8''', (query, query, like))]
        return jsonify(matches=rows)

    @app.get('/api/gate')
    def list_gate():
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT * FROM gate_logs ORDER BY id DESC LIMIT 50')]
        return jsonify(logs=rows)

    @app.post('/api/gate')
    def add_gate():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        direction = payload.get('direction')
        if direction not in GATE_DIRECTIONS:
            return jsonify(error='Choose in or out.'), 400
        student_id = text_field(payload.get('student_id', ''), required=False) or ''
        note = text_field(payload.get('note', ''), max_len=500, required=False) or ''
        name = text_field(payload.get('name', ''), required=False) or ''
        if student_id:
            found = find_by_student_id(student_id)
            if found:
                name = found['name']
                student_id = found['student_id']
        if not name and not student_id:
            return jsonify(error='Enter a student ID or visitor name.'), 400
        with connect() as db:
            cursor = db.execute(
                'INSERT INTO gate_logs (student_id,name,direction,note,timestamp) VALUES (?,?,?,?,?)',
                (student_id, name or 'Visitor', direction, note, now_stamp()))
            new_id = cursor.lastrowid
            row = dict(db.execute('SELECT * FROM gate_logs WHERE id=?', (new_id,)).fetchone())
        log_activity('guard', f'Gate {direction}', student_id or name)
        return jsonify(row), 201

    @app.get('/api/incidents')
    def list_incidents():
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT * FROM incidents ORDER BY id DESC LIMIT 50')]
        return jsonify(incidents=rows)

    @app.post('/api/incidents')
    def add_incident():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        category = payload.get('category')
        if category not in INCIDENT_CATEGORIES:
            return jsonify(error='Choose a valid incident category.'), 400
        detail = text_field(payload.get('detail'), max_len=500)
        if not detail:
            return jsonify(error='Describe the incident (maximum 500 characters).'), 400
        location = text_field(payload.get('location', ''), required=False) or ''
        student_id = text_field(payload.get('student_id', ''), required=False) or ''
        with connect() as db:
            cursor = db.execute(
                'INSERT INTO incidents (category,location,detail,student_id,status,timestamp) VALUES (?,?,?,?,?,?)',
                (category, location, detail, student_id, 'Open', now_stamp()))
            new_id = cursor.lastrowid
            row = dict(db.execute('SELECT * FROM incidents WHERE id=?', (new_id,)).fetchone())
        log_activity('guard', 'Logged incident', category)
        return jsonify(row), 201

    @app.patch('/api/incidents/<int:incident_id>')
    def update_incident(incident_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get('status') not in INCIDENT_STATUSES:
            return jsonify(error='Choose Open or Resolved.'), 400
        with connect() as db:
            row = db.execute('SELECT * FROM incidents WHERE id=?', (incident_id,)).fetchone()
            if row is None:
                abort(404)
            db.execute('UPDATE incidents SET status=? WHERE id=?', (payload['status'], incident_id))
            updated = dict(db.execute('SELECT * FROM incidents WHERE id=?', (incident_id,)).fetchone())
        log_activity('guard', 'Updated incident', payload['status'])
        return jsonify(updated)

    # ─── Student tools ────────────────────────────────────────────

    @app.post('/api/student/lookup')
    def student_lookup():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        student_id = text_field(payload.get('student_id'))
        if not student_id:
            return jsonify(error='Enter your student ID.'), 400
        found = find_by_student_id(student_id)
        if found is None:
            return jsonify(error='No ID record found for that student ID. Visit the Property Management Office if you need a new ID.'), 404
        return jsonify(record=dict(found))

    @app.get('/api/requests')
    def list_requests():
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT * FROM student_requests ORDER BY id DESC LIMIT 50')]
        return jsonify(requests=rows)

    @app.post('/api/requests')
    def add_request():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        student_id = text_field(payload.get('student_id'))
        request_type = payload.get('request_type')
        if not student_id:
            return jsonify(error='Enter a student ID.'), 400
        if request_type not in REQUEST_TYPES:
            return jsonify(error='Choose a valid request type.'), 400
        name = text_field(payload.get('name', ''), required=False) or ''
        detail = text_field(payload.get('detail', ''), max_len=500, required=False) or ''
        found = find_by_student_id(student_id)
        if found and not name:
            name = found['name']
        with connect() as db:
            cursor = db.execute(
                'INSERT INTO student_requests (student_id,name,request_type,detail,status,timestamp) VALUES (?,?,?,?,?,?)',
                (student_id, name, request_type, detail, 'Submitted', now_stamp()))
            new_id = cursor.lastrowid
            row = dict(db.execute('SELECT * FROM student_requests WHERE id=?', (new_id,)).fetchone())
        log_activity('student', 'Submitted request', f'{request_type} · {student_id}')
        return jsonify(row), 201

    @app.patch('/api/requests/<int:request_id>')
    def update_request(request_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get('status') not in REQUEST_STATUSES:
            return jsonify(error='Choose a valid request status.'), 400
        with connect() as db:
            row = db.execute('SELECT * FROM student_requests WHERE id=?', (request_id,)).fetchone()
            if row is None:
                abort(404)
            current = dict(row)
            db.execute('UPDATE student_requests SET status=? WHERE id=?', (payload['status'], request_id))
            if payload['status'] == 'Accepted' and current['request_type'] == 'Lost ID':
                existing = db.execute(
                    'SELECT id FROM inventory WHERE student_id=? COLLATE NOCASE',
                    (current['student_id'],)).fetchone()
                if existing:
                    db.execute(
                        "UPDATE inventory SET status='Pending', request_type='Lost ID', "
                        "pending_reason=? WHERE id=?",
                        (current['detail'] or 'Lost ID request accepted', existing['id']))
                else:
                    db.execute(
                        'INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,pending_reason,token) VALUES (?,?,?,?,?,?,?,?,?)',
                        (current['student_id'], current['name'] or current['student_id'], 'TBD', 'TBD', 1,
                         'Pending', 'Lost ID', current['detail'] or 'Lost ID request accepted', secrets.token_urlsafe(18)))
            updated = dict(db.execute('SELECT * FROM student_requests WHERE id=?', (request_id,)).fetchone())
        log_activity('coordinator', 'Updated request', f"{current['student_id']} → {payload['status']}")
        return jsonify(updated)

    # ─── Chat ─────────────────────────────────────────────────────

    @app.get('/api/chat/history')
    def chat_history():
        session_id = request.args.get('session_id', '')
        if not session_id or len(session_id) > 120:
            return jsonify(messages=[])
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT role, message, intent, confidence, record_id, timestamp '
                'FROM chat_history WHERE session_id=? ORDER BY id DESC LIMIT 50',
                (session_id,))]
        rows.reverse()
        return jsonify(messages=rows)

    @app.delete('/api/chat/history')
    def clear_chat_history():
        session_id = request.args.get('session_id', '')
        if session_id and len(session_id) <= 120:
            with connect() as db:
                db.execute('DELETE FROM chat_history WHERE session_id=?', (session_id,))
        return jsonify(success=True)

    @app.post('/api/chat')
    def chat():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('message'), str) or not payload['message'].strip() or len(payload['message']) > 1000:
            return jsonify(error='Enter a message of 1–1000 characters.'), 400

        user_text = payload['message']
        session_id = payload.get('session_id', '')
        if not isinstance(session_id, str) or len(session_id) > 120:
            session_id = ''
        workspace = payload.get('workspace_role', 'coordinator')
        if workspace not in ROLES:
            workspace = 'coordinator'

        labels, confidences = classify(matcher.pipeline, [user_text], matcher.threshold)
        intent = str(labels[0])
        confidence = round(float(confidences[0]), 4)

        selected = None
        if payload.get('record_id') is not None:
            if not isinstance(payload['record_id'], int) or isinstance(payload['record_id'], bool):
                return jsonify(error='Choose a valid inventory record.'), 400
            selected = record(payload['record_id'])

        answers = {
            'greeting': {
                'coordinator': 'Hello, coordinator. I can check ID status, queues, requests, inventory, and QR stickers. What should we handle first?',
                'guard': 'Hello. I can help you verify a student ID, log a gate entry or exit, or file an incident. Scan or type a student ID to start.',
                'student': 'Hi! I can check your ID status, explain requirements, and help you request a lost ID replacement. Look up your student ID, then ask me anything.',
            },
            'thanks_goodbye': 'You’re welcome! Come back whenever you need help with school IDs. Have a great day!',
            'bot_help': {
                'coordinator': 'Coordinator tools:\n• Inventory search, filters, bulk status updates, CSV export\n• Appointment queue and staff notes\n• Student request inbox (accept lost ID reports)\n• Print QR stickers and preview sample ID cards\n\nSelect a record with “Ask about ID”, then ask about status or schedules.',
                'guard': 'Guard tools:\n• Verify ID by student number, name, or QR token\n• Gate check-in / check-out log\n• Incident reports (no ID, visitor, lost ID found)\n• Confirm whether an ID is released and valid at the gate\n\nUse Verify ID, then ask me about that student.',
                'student': 'Student tools:\n• Look up your ID with your student number\n• Check processing status and appointments\n• Submit a lost ID or appointment request\n• Learn requirements, office hours, and QR scanning\n\nLook up your ID first so I can answer about your record.',
            },
            'talk_to_human': f'Please contact the Property Management Office.\n📍 {OFFICE["location"]}\n🕒 {OFFICE["hours"]}\n☎ {OFFICE["contact"]}',
            'lost_id_report': {
                'coordinator': 'Use “Add student ID” with request type Lost ID, or accept a student request from the Requests inbox. This chat has not created a record yet.',
                'guard': 'If a student reports a lost ID at the gate, log an incident and direct them to the Property Management Office. You can also file “Lost ID found” if you recovered a card.',
                'student': 'Use the “Request help” form to submit a Lost ID request. Bring an affidavit of loss, a 1×1 photo, and the replacement fee receipt to the office. This chat does not replace the official report until you submit the form.',
            },
            'id_requirements': (
                f'New ID: {OFFICE["requirements"]["New ID"]}\n'
                f'Lost ID: {OFFICE["requirements"]["Lost ID"]}\n'
                f'Pending ID: {OFFICE["requirements"]["Pending ID"]}'
            ),
            'office_hours_location': f'📍 {OFFICE["location"]}\n🕒 {OFFICE["hours"]}\n☎ {OFFICE["contact"]}',
            'qr_student_details': 'Scan the QR sticker with your phone camera. It opens the student’s name, course, section, and year level. Your phone must be able to reach this app’s URL.',
            'qr_sticker_request': {
                'coordinator': 'Use “Print sticker” on a student record. The label has a 30 mm QR code; print at actual size and test a scan before attaching it.',
                'guard': 'Ask the student to show the QR on their sticker or printed card. Use Verify ID with the student number if the scan fails.',
                'student': 'After you look up your ID, use “Print my QR sticker”. Print at actual size (30 mm QR) and keep it with your card.',
            },
            'id_inventory': {
                'coordinator': 'Use search and filters to find records by name, student ID, course, section, year, request type, or status. Open Queue for today’s appointments.',
                'guard': 'Guards do not edit inventory. Use Verify ID to look up a student at the gate.',
                'student': 'You can look up only your own record with your student ID. Staff manage the full inventory.',
            },
            'out_of_scope': 'I can help with school IDs. Try “When is my ID release?”, “Why is my ID pending?”, or “How do I print a QR sticker?”',
            'export_data': {
                'coordinator': 'Click “Export CSV” in the inventory panel to download all records as a spreadsheet.',
                'guard': 'CSV export is a coordinator tool. I can still help you verify a student or log a gate event.',
                'student': 'Inventory export is for staff. You can copy your own status after looking up your student ID.',
            },
        }

        def pick(value):
            if isinstance(value, dict):
                return value.get(workspace, value.get('coordinator', ''))
            return value

        lookups = ('id_processing_status', 'pending_id', 'id_release_schedule', 'pending_id_schedule', 'lost_id_schedule')
        if intent in lookups:
            if selected is None:
                prompt = {
                    'coordinator': 'Select a student record using “Ask about ID” so I can check its status or appointment.',
                    'guard': 'Verify a student ID first (search box in Verify), then ask about that person.',
                    'student': 'Look up your student ID first, then ask about status or your appointment.',
                }[workspace]
                answer = '⚡ ' + prompt
            elif intent == 'id_processing_status':
                gate_hint = ''
                if workspace == 'guard':
                    allowed = selected['status'] in ('Ready for release', 'Released')
                    gate_hint = ' Gate: **admit with extra check**.' if not allowed else ' Gate: **ID recorded as issued or ready**.'
                    if selected['status'] != 'Released':
                        gate_hint = ' Gate: **ID not yet released** — send the student to Property Management if they need the physical card.'
                    else:
                        gate_hint = ' Gate: **Released** — physical ID should be with the student.'
                answer = f'📋 {selected["name"]}: ID status is **{selected["status"]}** ({selected["request_type"]}).{gate_hint}'
            elif intent == 'pending_id':
                answer = f'📋 {selected["name"]}: status is **{selected["status"]}**. ' + (
                    selected['pending_reason'] or 'No pending reason has been recorded. Ask staff to confirm any missing requirements.')
            else:
                expected = {
                    'id_release_schedule': 'Release',
                    'pending_id_schedule': 'Pending follow-up',
                    'lost_id_schedule': 'Lost ID replacement',
                }[intent]
                if selected['schedule_type'] == expected:
                    answer = (f'📅 {selected["name"]}: {expected} is scheduled for '
                              f'**{selected["schedule_date"]} at {selected["schedule_time"]}** (school local time).')
                else:
                    answer = (f'📅 No {expected.lower()} appointment is recorded for {selected["name"]}. '
                              'Staff can set the date and time using “Update / schedule”. This chat has not booked an appointment.')
        else:
            answer = pick(answers.get(intent, answers['out_of_scope']))

        actions = list(INTENT_ACTIONS.get(intent, []))
        if workspace == 'guard':
            actions = [a for a in actions if a['type'] not in ('update_record', 'new_record', 'export_csv', 'reset_filters')]
            if intent in ('greeting', 'bot_help', 'id_inventory'):
                actions = [
                    {'label': '🔎 Verify ID', 'type': 'open_verify'},
                    {'label': '🚪 Log gate entry', 'type': 'open_gate'},
                    {'label': '⚠️ File incident', 'type': 'open_incident'},
                ]
            if intent == 'lost_id_report':
                actions = [{'label': '⚠️ File incident', 'type': 'open_incident'}]
            if selected and intent in lookups:
                actions.append({'label': '🚪 Check in', 'type': 'open_gate'})
        elif workspace == 'student':
            actions = [a for a in actions if a['type'] not in ('update_record', 'new_record', 'export_csv', 'reset_filters', 'view_record')]
            if intent in ('greeting', 'bot_help'):
                actions = [
                    {'label': '🔍 Look up my ID', 'type': 'open_lookup'},
                    {'label': '📝 Request help', 'type': 'open_student_request'},
                    {'label': '🕒 Office hours', 'type': 'ask_office'},
                ]
            if intent == 'lost_id_report':
                actions = [{'label': '📝 Submit lost ID request', 'type': 'open_student_request'}]
            if intent == 'office_hours_location':
                actions = [{'label': '📞 Contact office', 'type': 'ask_human'}]
            if selected:
                if intent in ('qr_sticker_request', 'qr_student_details', *lookups):
                    actions.append({'label': '🖨️ Print my sticker', 'type': 'print_sticker'})
                    actions.append({'label': '🪪 Preview ID card', 'type': 'preview_card'})
        else:
            if intent in ('greeting', 'bot_help'):
                actions.extend([
                    {'label': '📅 Appointment queue', 'type': 'open_queue'},
                    {'label': '📥 Student requests', 'type': 'open_requests'},
                ])
            if intent == 'lost_id_report':
                actions.append({'label': '📥 Open requests', 'type': 'open_requests'})
            if selected and intent in lookups:
                existing_types = {a['type'] for a in actions}
                if 'print_sticker' not in existing_types:
                    actions.append({'label': '🖨️ Print sticker', 'type': 'print_sticker'})
                actions.append({'label': '🪪 Preview ID card', 'type': 'preview_card'})

        seen = set()
        unique_actions = []
        for action in actions:
            if action['type'] not in seen:
                seen.add(action['type'])
                unique_actions.append(action)
        actions = unique_actions

        now = now_stamp()
        if session_id:
            with connect() as db:
                db.execute(
                    'INSERT INTO chat_history (session_id,role,message,intent,confidence,record_id,timestamp) VALUES (?,?,?,?,?,?,?)',
                    (session_id, 'user', user_text, '', 0, payload.get('record_id'), now))
                db.execute(
                    'INSERT INTO chat_history (session_id,role,message,intent,confidence,record_id,timestamp) VALUES (?,?,?,?,?,?,?)',
                    (session_id, 'bot', answer, intent, confidence, payload.get('record_id'), now))
                db.execute(
                    'DELETE FROM chat_history WHERE session_id=? AND id NOT IN '
                    '(SELECT id FROM chat_history WHERE session_id=? ORDER BY id DESC LIMIT 100)',
                    (session_id, session_id))

        return jsonify(intent=intent, reply=answer, confidence=confidence, actions=actions, timestamp=now)

    @app.after_request
    def private_pages(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    return app

if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)