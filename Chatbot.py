"""Local school ID management prototype. Run with .venv/bin/python Chatbot.py."""
import csv
import io
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import qrcode
from flask import Flask, abort, jsonify, render_template, request, send_file, url_for

from intent_model import ANNIntentModel, classify

ROOT = Path(__file__).resolve().parent
STATUSES = ('Pending', 'Processing', 'Ready for release', 'Released')
KINDS = ('New ID', 'Pending ID', 'Lost ID')
SCHEDULES = ('Release', 'Pending follow-up', 'Lost ID replacement')

# Quick actions mapped to intents for actionable bot responses.
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
}


def create_app(database=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 32 * 1024
    app.config['DATABASE'] = str(database or ROOT / 'data' / 'inventory.sqlite3')
    public_base = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
    if public_base and (urlparse(public_base).scheme not in ('http', 'https') or not urlparse(public_base).netloc):
        raise ValueError('PUBLIC_BASE_URL must be an absolute HTTP(S) URL')
    matcher = ANNIntentModel()

    def connect():
        connection = sqlite3.connect(app.config['DATABASE'])
        connection.row_factory = sqlite3.Row
        return connection

    Path(app.config['DATABASE']).parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY, student_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, course TEXT NOT NULL, section TEXT NOT NULL,
            year_level INTEGER NOT NULL CHECK(year_level BETWEEN 1 AND 6),
            status TEXT NOT NULL, request_type TEXT NOT NULL, pending_reason TEXT NOT NULL DEFAULT '',
            schedule_type TEXT NOT NULL DEFAULT '', schedule_date TEXT NOT NULL DEFAULT '',
            schedule_time TEXT NOT NULL DEFAULT '', token TEXT NOT NULL UNIQUE)''')
        db.execute('''CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user', 'bot')),
            message TEXT NOT NULL, intent TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            record_id INTEGER, timestamp TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_history(session_id)')
        # Seed once; these fictional records are never training examples.
        if not db.execute('SELECT 1 FROM inventory LIMIT 1').fetchone():
            demo = [
                ('DEMO-001','Alex Rivera','BSIT','A',1,'Ready for release','New ID',''),
                ('DEMO-002','Jamie Santos','BSCS','B',2,'Pending','Pending ID','Photo verification needed'),
                ('DEMO-003','Sam Reyes','BSBA','A',3,'Processing','Lost ID',''),
                ('DEMO-004','Taylor Cruz','BSIT','B',4,'Released','New ID',''),
                ('DEMO-005','Casey Garcia','BSED','A',1,'Processing','New ID',''),
                ('DEMO-006','Jordan Flores','BSCS','A',2,'Pending','Pending ID','Enrollment verification needed'),
            ]
            db.executemany('INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,pending_reason,token) VALUES (?,?,?,?,?,?,?,?,?)', [(*row,secrets.token_urlsafe(18)) for row in demo])

    def record(record_id):
        with connect() as db:
            row = db.execute('SELECT * FROM inventory WHERE id=?', (record_id,)).fetchone()
        if row is None:
            abort(404)
        return dict(row)

    def scan_url(token):
        path = url_for('profile', token=token)
        return public_base + path if public_base else url_for('profile', token=token, _external=True)

    # ─── Page routes ───────────────────────────────────────────────

    @app.get('/')
    def home():
        return render_template('index.html', statuses=STATUSES, kinds=KINDS, schedules=SCHEDULES)

    @app.get('/student/<token>')
    def profile(token):
        with connect() as db:
            row = db.execute('SELECT name,course,section,year_level FROM inventory WHERE token=?', (token,)).fetchone()
        if row is None:
            abort(404)
        return render_template('profile.html', student=row)

    @app.get('/sticker/<int:record_id>')
    def sticker(record_id):
        row = record(record_id)
        return render_template('sticker.html', student=row, scan_url=scan_url(row['token']))

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

    # ─── Inventory CRUD ───────────────────────────────────────────

    @app.get('/api/inventory')
    def inventory():
        with connect() as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM inventory ORDER BY id DESC')]
        # Only expose tokens needed to render QR links; raw student data stays out of training.
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
                cursor = db.execute('INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,token) VALUES (?,?,?,?,?,?,?,?)', (*fields.values(),int(year),'Pending',kind,secrets.token_urlsafe(18)))
                new_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            return jsonify(error='That student ID already exists.'), 409
        return jsonify(record(new_id)), 201

    @app.patch('/api/inventory/<int:record_id>')
    def update_record(record_id):
        current = record(record_id)
        payload = request.get_json(silent=True)
        allowed = ('status', 'pending_reason', 'schedule_type', 'schedule_date', 'schedule_time')
        if not isinstance(payload, dict) or not payload or set(payload)-set(allowed):
            return jsonify(error='Supply status, pending reason, or schedule fields.'), 400
        if any(not isinstance(v, str) or len(v) > 500 for v in payload.values()):
            return jsonify(error='Fields must be text of up to 500 characters.'), 400
        current.update(payload)
        if current['status'] not in STATUSES or current['schedule_type'] not in ('', *SCHEDULES):
            return jsonify(error='Choose a valid status and appointment type.'), 400
        schedule = [current[k] for k in ('schedule_type','schedule_date','schedule_time')]
        if any(schedule):
            if not all(schedule):
                return jsonify(error='Appointment type, date, and time must be filled together.'), 400
            try:
                datetime.strptime(current['schedule_date'] + ' ' + current['schedule_time'], '%Y-%m-%d %H:%M')
            except ValueError:
                return jsonify(error='Use a valid date and time.'), 400
        with connect() as db:
            db.execute('UPDATE inventory SET status=?,pending_reason=?,schedule_type=?,schedule_date=?,schedule_time=? WHERE id=?', (*[current[k] for k in allowed],record_id))
        return jsonify(record(record_id))

    @app.delete('/api/inventory/<int:record_id>')
    def delete_record(record_id):
        """Remove an inventory record permanently."""
        record(record_id)  # raises 404 if missing
        with connect() as db:
            db.execute('DELETE FROM inventory WHERE id=?', (record_id,))
        return jsonify(success=True)

    @app.patch('/api/inventory/bulk')
    def bulk_update():
        """Update the processing status of multiple records at once."""
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
        return jsonify(updated=updated)

    @app.get('/api/inventory/export')
    def export_inventory():
        """Download the full inventory as a CSV file."""
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT student_id, name, course, section, year_level, status, request_type, '
                'pending_reason, schedule_type, schedule_date, schedule_time '
                'FROM inventory ORDER BY id DESC')]
        output = io.StringIO()
        columns = ['student_id','name','course','section','year_level','status',
                    'request_type','pending_reason','schedule_type','schedule_date','schedule_time']
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        if rows:
            writer.writerows(rows)
        mem = io.BytesIO(output.getvalue().encode('utf-8-sig'))
        return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='id_inventory.csv')

    # ─── Dashboard stats ──────────────────────────────────────────

    @app.get('/api/stats')
    def stats():
        """Return richer analytics for the dashboard."""
        with connect() as db:
            total = db.execute('SELECT COUNT(*) FROM inventory').fetchone()[0]
            by_status = {row[0]: row[1] for row in db.execute(
                'SELECT status, COUNT(*) FROM inventory GROUP BY status')}
            by_course = {row[0]: row[1] for row in db.execute(
                'SELECT course, COUNT(*) FROM inventory GROUP BY course ORDER BY COUNT(*) DESC')}
            by_request = {row[0]: row[1] for row in db.execute(
                'SELECT request_type, COUNT(*) FROM inventory GROUP BY request_type')}
            upcoming = [dict(row) for row in db.execute(
                "SELECT name, schedule_type, schedule_date, schedule_time "
                "FROM inventory WHERE schedule_date != '' ORDER BY schedule_date, schedule_time LIMIT 5")]
        return jsonify(total=total, by_status=by_status, by_course=by_course,
                       by_request_type=by_request, upcoming_appointments=upcoming)

    # ─── Chat ─────────────────────────────────────────────────────

    @app.get('/api/chat/history')
    def chat_history():
        """Return the last 50 messages for a session."""
        session_id = request.args.get('session_id', '')
        if not session_id or len(session_id) > 100:
            return jsonify(messages=[])
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT role, message, intent, confidence, record_id, timestamp '
                'FROM chat_history WHERE session_id=? ORDER BY id DESC LIMIT 50',
                (session_id,))]
        rows.reverse()
        return jsonify(messages=rows)

    @app.post('/api/chat')
    def chat():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('message'), str) or not payload['message'].strip() or len(payload['message']) > 1000:
            return jsonify(error='Enter a message of 1–1000 characters.'), 400

        user_text = payload['message']
        session_id = payload.get('session_id', '')
        if not isinstance(session_id, str) or len(session_id) > 100:
            session_id = ''

        # Classify intent with confidence score.
        labels, confidences = classify(matcher.pipeline, [user_text], matcher.threshold)
        intent = str(labels[0])
        confidence = round(float(confidences[0]), 4)

        selected = None
        if payload.get('record_id') is not None:
            if not isinstance(payload['record_id'], int) or isinstance(payload['record_id'], bool):
                return jsonify(error='Choose a valid inventory record.'), 400
            selected = record(payload['record_id'])

        answers = {
            'greeting': 'Hello! 👋 I can help with school ID status, release schedules, pending or lost IDs, inventory, and QR stickers. What do you need?',
            'thanks_goodbye': 'You\u2019re welcome! 😊 Come back whenever you need help with your school ID. Have a great day!',
            'bot_help': 'Here\u2019s what I can do:\n\u2022 Check ID processing status\n\u2022 Look up release schedules\n\u2022 Explain pending requirements\n\u2022 Guide lost ID replacement\n\u2022 Help with QR stickers\n\u2022 Search the inventory\n\nSelect a student record, then ask me about its ID.',
            'talk_to_human': '\U0001f4de Please contact the Property Management Office staff for assistance. Office contact details have not been configured yet.',
            'lost_id_report': '\u26a0\ufe0f Please report the missing ID to the Property Management Office. Staff can record a Lost ID request and arrange a replacement appointment. This chat has not submitted a report.',
            'id_requirements': '\U0001f4cb Ask the Property Management Office to confirm the required documents, photo specifications, and any fees. School requirements have not been configured yet.',
            'office_hours_location': '\U0001f3e2 Please confirm the office hours and pickup location with the Property Management Office. These details have not been configured yet.',
            'qr_student_details': '\U0001f4f1 Scan the QR sticker with your phone camera. It opens the student\u2019s name, course, section, and year level. Your phone must be able to reach this app\u2019s URL.',
            'qr_sticker_request': '\U0001f5a8\ufe0f Use \u201cPrint sticker\u201d on the student\u2019s inventory card. The label has a 30\u2009mm QR code; print at actual size and test a scan before attaching it.',
            'id_inventory': '\U0001f50d Use the search box and filters above to find records by name or student ID, course, section, year, request type, or processing status.',
            'out_of_scope': '\U0001f914 I can help with school IDs. Try \u201cWhen is my ID release?\u201d, \u201cWhy is my ID pending?\u201d, or \u201cHow do I print a QR sticker?\u201d',
            'export_data': '\U0001f4e5 Click the \u201cExport CSV\u201d button in the inventory panel to download all records as a spreadsheet file.',
        }

        lookups = ('id_processing_status', 'pending_id', 'id_release_schedule', 'pending_id_schedule', 'lost_id_schedule')
        if intent in lookups:
            if selected is None:
                answer = '\u26a1 Select a student record using \u201cAsk about ID\u201d so I can check its status or appointment.'
            elif intent == 'id_processing_status':
                answer = f'\U0001f4cb {selected["name"]}: ID status is **{selected["status"]}** ({selected["request_type"]}).'
            elif intent == 'pending_id':
                answer = f'\U0001f4cb {selected["name"]}: status is **{selected["status"]}**. ' + (selected['pending_reason'] or 'No pending reason has been recorded. Ask staff to confirm any missing requirements.')
            else:
                expected = {'id_release_schedule':'Release','pending_id_schedule':'Pending follow-up','lost_id_schedule':'Lost ID replacement'}[intent]
                if selected['schedule_type'] == expected:
                    answer = f'\U0001f4c5 {selected["name"]}: {expected} is scheduled for **{selected["schedule_date"]}** at **{selected["schedule_time"]}** (school local time).'
                else:
                    answer = f'\U0001f4c5 No {expected.lower()} appointment is recorded for {selected["name"]}. Staff can set the date and time using \u201cUpdate / schedule\u201d. This chat has not booked an appointment.'
        else:
            answer = answers.get(intent, answers['out_of_scope'])

        # Build quick-action buttons for this intent.
        actions = list(INTENT_ACTIONS.get(intent, []))
        if selected and intent in lookups:
            existing_types = {a['type'] for a in actions}
            if 'print_sticker' not in existing_types:
                actions.append({'label': '\U0001f5a8\ufe0f Print sticker', 'type': 'print_sticker'})

        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Persist chat messages in the session history.
        if session_id:
            with connect() as db:
                db.execute(
                    'INSERT INTO chat_history (session_id,role,message,intent,confidence,record_id,timestamp) VALUES (?,?,?,?,?,?,?)',
                    (session_id, 'user', user_text, '', 0, payload.get('record_id'), now))
                db.execute(
                    'INSERT INTO chat_history (session_id,role,message,intent,confidence,record_id,timestamp) VALUES (?,?,?,?,?,?,?)',
                    (session_id, 'bot', answer, intent, confidence, payload.get('record_id'), now))
                # Keep only the last 100 messages per session to limit storage.
                db.execute(
                    'DELETE FROM chat_history WHERE session_id=? AND id NOT IN '
                    '(SELECT id FROM chat_history WHERE session_id=? ORDER BY id DESC LIMIT 100)',
                    (session_id, session_id))

        return jsonify(intent=intent, reply=answer, confidence=confidence, actions=actions, timestamp=now)

    # ─── Security headers ─────────────────────────────────────────

    @app.after_request
    def private_pages(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
