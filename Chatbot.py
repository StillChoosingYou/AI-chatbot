"""School ID management prototype. Needs PostgreSQL: set DATABASE_URL, then run .venv/bin/python Chatbot.py."""
import csv
import io
import os
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import qrcode
from flask import Flask, abort, jsonify, render_template, request, send_file, url_for
from psycopg import errors as pg_errors
from werkzeug.exceptions import BadRequest, HTTPException, MethodNotAllowed
from werkzeug.routing import IntegerConverter

from chat_support import FEEDBACK_REASONS, clean_text, csv_safe, intent_label, redact_text, reply_chips
from database import Database, init_schema, lock_student, seed_demo_records
from id_cards import sample_card
from intent_model import ANNIntentModel, classify, rank_intents

ROOT = Path(__file__).resolve().parent
MAX_ROW_ID = 2 ** 63 - 1   # the largest BIGINT; anything larger would raise instead of "not found"
MAX_STICKERS = 60          # one printed sheet


class RowIdConverter(IntegerConverter):
    """URL number that always fits a BIGINT; longer numbers are a plain 404."""
    regex = r'\d{1,18}'


def valid_row_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_ROW_ID


def parse_year(value):
    """Year level 1 to 6 from a JSON number or a short string of ASCII digits, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = int(value) if value.isascii() and value.isdigit() and len(value) <= 3 else None
    if not isinstance(value, int):
        return None
    return value if 1 <= value <= 6 else None
STATUSES = ('Pending', 'Processing', 'Ready for release', 'Released')
KINDS = ('New ID', 'Pending ID', 'Lost ID')
SCHEDULES = ('Release', 'Pending follow-up', 'Lost ID replacement')
ROLES = ('coordinator', 'guard', 'student')
GATE_DIRECTIONS = ('in', 'out')
INCIDENT_CATEGORIES = ('No ID', 'Lost ID found', 'Visitor', 'Suspicious activity', 'Other')
INCIDENT_STATUSES = ('Open', 'Resolved')
REQUEST_TYPES = ('Lost ID', 'Status inquiry', 'Appointment help', 'Other')
REQUEST_STATUSES = ('Submitted', 'In review', 'Accepted', 'Closed')
# Where a request may go next. Accepting a lost-ID request changes the inventory record,
# so it can only happen once and a closed request stays closed.
REQUEST_FLOW = {
    'Submitted': ('In review', 'Accepted', 'Closed'),
    'In review': ('Accepted', 'Closed'),
    'Accepted': ('Closed',),
    'Closed': (),
}
FEEDBACK_RATINGS = ('up', 'down')
FEEDBACK_STATUSES = ('New', 'Reviewed')
UNSURE_REPLY = 'I’m an automated assistant and I’m not sure what you meant. Did you mean one of these?'
API_ERRORS = {
    400: 'The request could not be understood.',
    404: 'That record or page was not found.',
    405: 'That method is not allowed here.',
    413: 'The request is too large (the limit is 32 KB).',
}
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
    'id_inventory': [
        {'label': '🔄 Reset filters', 'type': 'reset_filters'},
        {'label': '📥 Export CSV', 'type': 'export_csv'},
    ],
    'export_data': [{'label': '📥 Export CSV', 'type': 'export_csv'}],
}


def create_app(database_url=None):
    """Build the app. The database is the PostgreSQL URL given here, or else the DATABASE_URL variable."""
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 32 * 1024
    database_url = database_url or os.environ.get('DATABASE_URL', '').strip()
    if not database_url:
        raise RuntimeError(
            'DATABASE_URL is not set. Point it at a PostgreSQL database, for example the one from '
            'docker-compose.yml:\n  postgresql://school_id:school_id_dev@localhost:5432/school_id\n'
            'See the "Database" section of README.md.')
    public_base = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
    if public_base and (urlparse(public_base).scheme not in ('http', 'https') or not urlparse(public_base).netloc):
        raise ValueError('PUBLIC_BASE_URL must be an absolute HTTP(S) URL')
    matcher = ANNIntentModel()
    app.url_map.converters['rowid'] = RowIdConverter

    @app.errorhandler(HTTPException)
    def http_error(error):
        """JSON for the API, the normal page for everything else."""
        if not request.path.startswith('/api/'):
            return error
        response = jsonify(error=API_ERRORS.get(error.code, error.name))
        response.status_code = error.code
        if isinstance(error, MethodNotAllowed) and error.valid_methods:
            response.headers['Allow'] = ', '.join(error.valid_methods)
        return response

    @app.errorhandler(Exception)
    def unexpected_error(error):
        if isinstance(error, HTTPException):
            return http_error(error)
        app.logger.exception('Unhandled error on %s %s', request.method, request.path)
        if request.path.startswith('/api/'):
            return jsonify(error='Something went wrong on the server. Please try again.'), 500
        return 'Something went wrong on the server.', 500

    @app.errorhandler(psycopg.DataError)
    @app.errorhandler(UnicodeEncodeError)
    def unusable_text(error):
        """Text PostgreSQL cannot store (a NUL character, a lone surrogate) is a bad request, not a crash."""
        app.logger.warning('Rejected unusable text on %s %s (%s)', request.method, request.path, type(error).__name__)
        return http_error(BadRequest())

    @app.errorhandler(psycopg.OperationalError)
    def database_unavailable(error):
        """The database is down, restarting, out of connections or deadlocked: ask the caller to retry."""
        app.logger.error('Database unavailable on %s %s: %s', request.method, request.path, str(error).strip())
        message = 'The database is not available right now. Please try again in a moment.'
        if request.path.startswith('/api/'):
            return jsonify(error=message), 503
        return message, 503

    database = Database(database_url)
    try:
        with database.connect() as db:         # creates missing tables; an empty database gets the demo students
            init_schema(db)
            seed_demo_records(db)
    except BaseException:
        database.close()
        raise
    app.extensions['database'] = database
    connect = database.connect                 # `with connect() as db:` commits on success, rolls back on error

    def now_stamp():
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def log_activity(actor_role, action, detail=''):
        with connect() as db:
            db.execute(
                'INSERT INTO activity_log (actor_role, action, detail, timestamp) VALUES (%s,%s,%s,%s)',
                (actor_role, action, detail[:500], now_stamp()))

    def record(record_id):
        if not valid_row_id(record_id):
            abort(404)
        with connect() as db:
            row = db.execute('SELECT * FROM inventory WHERE id=%s', (record_id,)).fetchone()
        if row is None:
            abort(404)
        return dict(row)

    def scan_url(token):
        path = url_for('profile', token=token)
        return public_base + path if public_base else url_for('profile', token=token, _external=True)

    def text_field(value, max_len=100, required=True):
        if not isinstance(value, str) or not clean_text(value):
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
                'SELECT * FROM inventory WHERE LOWER(student_id) = LOWER(%s)',
                (student_id,)).fetchone()

    # ─── Page routes ───────────────────────────────────────────────

    @app.get('/')
    def home():
        return render_template(
            'index.html', statuses=STATUSES, kinds=KINDS, schedules=SCHEDULES,
            office=OFFICE, incident_categories=INCIDENT_CATEGORIES,
            request_types=REQUEST_TYPES, feedback_reasons=FEEDBACK_REASONS,
            max_stickers=MAX_STICKERS)

    @app.get('/student/<token>')
    def profile(token):
        if not clean_text(token):                # a NUL or a lone surrogate cannot be part of a real token
            abort(404)
        with connect() as db:
            row = db.execute(
                'SELECT name,course,section,year_level FROM inventory WHERE token=%s',
                (token,)).fetchone()
        if row is None:
            abort(404)
        return render_template('profile.html', student=dict(row))

    @app.get('/sticker/<rowid:record_id>')
    def sticker(record_id):
        row = record(record_id)
        return render_template('sticker.html', student=row, scan_url=scan_url(row['token']))

    @app.get('/card/<rowid:record_id>')
    def card_preview(record_id):
        row = record(record_id)
        return render_template('card.html', student=row)

    @app.get('/qr/<rowid:record_id>.png')
    def qr_image(record_id):
        row = record(record_id)
        stream = io.BytesIO()
        code = qrcode.QRCode(box_size=8, border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
        code.add_data(scan_url(row['token']))
        code.make(fit=True)
        code.make_image(fill_color='black', back_color='white').save(stream, format='PNG')
        stream.seek(0)
        return send_file(stream, mimetype='image/png')

    @app.get('/card/<rowid:record_id>/<side>.png')
    def card_image(record_id, side):
        if side not in ('front', 'back'):
            abort(404)
        row = record(record_id)
        stream = io.BytesIO()
        sample_card(row, side).save(stream, format='PNG')
        stream.seek(0)
        return send_file(stream, mimetype='image/png')

    @app.get('/stickers')
    def sticker_sheet():
        """Print many QR stickers at once: /stickers?ids=1,2,3 (up to MAX_STICKERS)."""
        try:
            ids = list(dict.fromkeys(int(part) for part in request.args.get('ids', '').split(',') if part.strip()))
        except ValueError:
            abort(400)
        if not ids or len(ids) > MAX_STICKERS or not all(valid_row_id(i) for i in ids):
            abort(400)
        with connect() as db:
            rows = {row['id']: dict(row) for row in db.execute(
                'SELECT id, student_id FROM inventory WHERE id = ANY(%s::bigint[])', (ids,))}
        students = [rows[i] for i in ids if i in rows]
        if not students:
            abort(404)
        return render_template('stickers.html', students=students, skipped=len(ids) - len(students))

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
            value = text_field(payload.get(key))
            if not value:
                return jsonify(error=f'{key.replace("_", " ")} is required (maximum 100 characters).'), 400
            fields[key] = value
        year = parse_year(payload.get('year_level'))
        if year is None:
            return jsonify(error='Year level must be between 1 and 6.'), 400
        kind = payload.get('request_type', 'New ID')
        if kind not in KINDS:
            return jsonify(error='Choose a valid request type.'), 400
        # A quick, friendly answer. The unique index on LOWER(student_id) is what guarantees it.
        if find_by_student_id(fields['student_id']):
            return jsonify(error='That student ID already exists.'), 409
        try:
            with connect() as db:
                lock_student(db, fields['student_id'])        # one writer per student ID at a time
                created = db.execute(
                    'INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,token) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                    (*fields.values(), year, 'Pending', kind, secrets.token_urlsafe(18))).fetchone()
        except pg_errors.UniqueViolation:
            return jsonify(error='That student ID already exists.'), 409
        log_activity('coordinator', 'Added record', fields['student_id'])
        return jsonify(dict(created)), 201

    @app.patch('/api/inventory/<rowid:record_id>')
    def update_record(record_id):
        payload = request.get_json(silent=True)
        allowed = ('status', 'pending_reason', 'schedule_type', 'schedule_date', 'schedule_time', 'notes')
        with connect() as db:
            # FOR UPDATE: two coordinators editing different fields must not overwrite each other.
            row = db.execute('SELECT * FROM inventory WHERE id=%s FOR UPDATE', (record_id,)).fetchone()
            if row is None:
                abort(404)
            current = dict(row)
            if not isinstance(payload, dict) or not payload or set(payload) - set(allowed):
                return jsonify(error='Supply status, pending reason, notes, or schedule fields.'), 400
            if any(not isinstance(v, str) or len(v) > 500 or not clean_text(v) for v in payload.values()):
                return jsonify(error='Fields must be text of up to 500 characters.'), 400
            current.update(payload)
            if current['status'] not in STATUSES or current['schedule_type'] not in ('', *SCHEDULES):
                return jsonify(error='Choose a valid status and appointment type.'), 400
            schedule = [current[k] for k in ('schedule_type', 'schedule_date', 'schedule_time')]
            if any(schedule):
                if not all(schedule):
                    return jsonify(error='Appointment type, date, and time must be filled together.'), 400
                try:
                    when = datetime.strptime(current['schedule_date'] + ' ' + current['schedule_time'], '%Y-%m-%d %H:%M')
                except ValueError:
                    return jsonify(error='Use a valid date and time.'), 400
                # Always stored as 2026-01-05 and 09:30, so dates sort and compare correctly.
                current['schedule_date'], current['schedule_time'] = when.isoformat(sep=' ', timespec='minutes').split(' ')
            db.execute(
                'UPDATE inventory SET status=%s,pending_reason=%s,schedule_type=%s,schedule_date=%s,schedule_time=%s,notes=%s WHERE id=%s',
                (*[current[k] for k in allowed], record_id))
        log_activity('coordinator', 'Updated record', current['student_id'])
        return jsonify(record(record_id))

    @app.delete('/api/inventory/<rowid:record_id>')
    def delete_record(record_id):
        row = record(record_id)
        with connect() as db:
            db.execute('DELETE FROM inventory WHERE id=%s', (record_id,))
        log_activity('coordinator', 'Deleted record', row['student_id'])
        return jsonify(success=True)

    @app.patch('/api/inventory/bulk')
    def bulk_update():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        ids = payload.get('record_ids')
        status = payload.get('status')
        if not isinstance(ids, list) or not ids or not all(valid_row_id(i) for i in ids):
            return jsonify(error='Provide a list of record IDs.'), 400
        if len(ids) > 500:
            return jsonify(error='Bulk update is limited to 500 records.'), 400
        if not isinstance(status, str) or status not in STATUSES:
            return jsonify(error='Choose a valid processing status.'), 400
        with connect() as db:
            cursor = db.execute('UPDATE inventory SET status=%s WHERE id = ANY(%s::bigint[])', (status, ids))
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
        writer.writerows([{key: csv_safe(value) for key, value in row.items()} for row in rows])
        mem = io.BytesIO(output.getvalue().encode('utf-8-sig'))
        return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='id_inventory.csv')

    # ─── Dashboard, queue, activity ────────────────────────────────

    @app.get('/api/stats')
    def stats():
        today = datetime.now().strftime('%Y-%m-%d')
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
                "FROM inventory WHERE schedule_date >= %s ORDER BY schedule_date, schedule_time, id LIMIT 8", (today,))]
            today_appointments = db.execute(
                'SELECT COUNT(*) FROM inventory WHERE schedule_date = %s', (today,)).fetchone()[0]
            overdue_appointments = db.execute(
                "SELECT COUNT(*) FROM inventory WHERE schedule_date != '' AND schedule_date < %s AND status != 'Released'",
                (today,)).fetchone()[0]
            open_incidents = db.execute("SELECT COUNT(*) FROM incidents WHERE status='Open'").fetchone()[0]
            open_requests = db.execute("SELECT COUNT(*) FROM student_requests WHERE status IN ('Submitted','In review')").fetchone()[0]
            new_feedback = db.execute(
                "SELECT COUNT(*) FROM chat_feedback WHERE rating='down' AND status='New'").fetchone()[0]
        return jsonify(total=total, by_status=by_status, by_course=by_course,
                       by_request_type=by_request, upcoming_appointments=upcoming,
                       today=today, today_appointments=today_appointments,
                       overdue_appointments=overdue_appointments,
                       open_incidents=open_incidents, open_requests=open_requests,
                       new_feedback=new_feedback)

    @app.get('/api/queue')
    def appointment_queue():
        today = datetime.now().strftime('%Y-%m-%d')
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT id, student_id, name, course, status, schedule_type, schedule_date, schedule_time "
                "FROM inventory WHERE schedule_date != '' ORDER BY schedule_date, schedule_time, id")]
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
        query = request.args.get('q', '').strip()
        if not query or len(query) > 100 or not clean_text(query):
            return jsonify(error='Enter a student ID, name, or QR token.'), 400
        # Exact matches use the text as typed: QR tokens contain "_" and "-" (about one token in
        # three has an underscore), so stripping them made those students impossible to find.
        # Only the name search treats % and _ as wildcards, so they are escaped there.
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                '''SELECT id, student_id, name, course, section, year_level, status, request_type, token
                   FROM inventory
                   WHERE LOWER(student_id) = LOWER(%s) OR token = %s OR name ILIKE %s ESCAPE '\\'
                   ORDER BY (LOWER(student_id) = LOWER(%s) OR token = %s) DESC, id
                   LIMIT 8''', (query, query, f'%{escaped}%', query, query))]
        return jsonify(matches=rows)

    @app.get('/api/gate')
    def list_gate():
        today = datetime.now().strftime('%Y-%m-%d')
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT * FROM gate_logs ORDER BY id DESC LIMIT 50')]
            counts = {row[0]: row[1] for row in db.execute(
                'SELECT direction, COUNT(*) FROM gate_logs WHERE timestamp >= %s GROUP BY direction', (today,))}
        return jsonify(logs=rows, today={'in': counts.get('in', 0), 'out': counts.get('out', 0)})

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
            row = dict(db.execute(
                'INSERT INTO gate_logs (student_id,name,direction,note,timestamp) VALUES (%s,%s,%s,%s,%s) RETURNING *',
                (student_id, name or 'Visitor', direction, note, now_stamp())).fetchone())
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
            row = dict(db.execute(
                'INSERT INTO incidents (category,location,detail,student_id,status,timestamp) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                (category, location, detail, student_id, 'Open', now_stamp())).fetchone())
        log_activity('guard', 'Logged incident', category)
        return jsonify(row), 201

    @app.patch('/api/incidents/<rowid:incident_id>')
    def update_incident(incident_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get('status') not in INCIDENT_STATUSES:
            return jsonify(error='Choose Open or Resolved.'), 400
        with connect() as db:
            row = db.execute('UPDATE incidents SET status=%s WHERE id=%s RETURNING *',
                             (payload['status'], incident_id)).fetchone()
            if row is None:
                abort(404)
            updated = dict(row)
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
        # Staff notes and the QR token are not for students.
        return jsonify(record={k: v for k, v in dict(found).items() if k not in ('notes', 'token')})

    @app.post('/api/student/requests')
    def student_requests():
        """A student's own requests, newest first, without the free-text details."""
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        student_id = text_field(payload.get('student_id'))
        if not student_id:
            return jsonify(error='Enter your student ID.'), 400
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT id, request_type, status, timestamp FROM student_requests '
                'WHERE LOWER(student_id) = LOWER(%s) ORDER BY id DESC LIMIT 10', (student_id,))]
        return jsonify(requests=rows)

    @app.get('/api/requests')
    def list_requests():
        status = request.args.get('status', '')
        if status and status not in REQUEST_STATUSES:
            return jsonify(error='Choose a valid request status.'), 400
        where, params = ('WHERE status=%s ', (status,)) if status else ('', ())
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                f'SELECT * FROM student_requests {where}ORDER BY id DESC LIMIT 50', params)]
            counts = {row[0]: row[1] for row in db.execute(
                'SELECT status, COUNT(*) FROM student_requests GROUP BY status')}
        # JSON objects come back with sorted keys, so the workflow order travels as a list.
        return jsonify(requests=rows, statuses=list(REQUEST_STATUSES),
                       counts={name: counts.get(name, 0) for name in REQUEST_STATUSES},
                       flow={name: list(options) for name, options in REQUEST_FLOW.items()})

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
            row = dict(db.execute(
                'INSERT INTO student_requests (student_id,name,request_type,detail,status,timestamp) '
                'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                (student_id, name, request_type, detail, 'Submitted', now_stamp())).fetchone())
        log_activity('student', 'Submitted request', f'{request_type} · {student_id}')
        return jsonify(row), 201

    @app.patch('/api/requests/<rowid:request_id>')
    def update_request(request_id):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get('status') not in REQUEST_STATUSES:
            return jsonify(error='Choose a valid request status.'), 400
        with connect() as db:
            # FOR UPDATE: a double click, or two coordinators, must not accept the same request twice.
            row = db.execute('SELECT * FROM student_requests WHERE id=%s FOR UPDATE', (request_id,)).fetchone()
            if row is None:
                abort(404)
            current = dict(row)
            if payload['status'] == current['status']:
                return jsonify(current)
            if payload['status'] not in REQUEST_FLOW[current['status']]:
                return jsonify(error=f"A request cannot move from {current['status']} to {payload['status']}."), 409
            updated = dict(db.execute('UPDATE student_requests SET status=%s WHERE id=%s RETURNING *',
                                      (payload['status'], request_id)).fetchone())
            if payload['status'] == 'Accepted' and current['request_type'] == 'Lost ID':
                lock_student(db, current['student_id'])       # same lock as "add a record": no duplicate record
                existing = db.execute(
                    'SELECT id FROM inventory WHERE LOWER(student_id) = LOWER(%s)',
                    (current['student_id'],)).fetchone()
                if existing:
                    db.execute(
                        "UPDATE inventory SET status='Pending', request_type='Lost ID', "
                        "pending_reason=%s WHERE id=%s",
                        (current['detail'] or 'Lost ID request accepted', existing['id']))
                else:
                    db.execute(
                        'INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,pending_reason,token) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (current['student_id'], current['name'] or current['student_id'], 'TBD', 'TBD', 1,
                         'Pending', 'Lost ID', current['detail'] or 'Lost ID request accepted', secrets.token_urlsafe(18)))
        log_activity('coordinator', 'Updated request', f"{current['student_id']} → {payload['status']}")
        return jsonify(updated)

    # ─── Chat ─────────────────────────────────────────────────────

    @app.get('/api/chat/history')
    def chat_history():
        session_id = request.args.get('session_id', '')
        if not session_id or len(session_id) > 120 or not clean_text(session_id):
            return jsonify(messages=[])
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT h.id, h.role, h.message, h.intent, h.confidence, h.record_id, h.timestamp, f.rating '
                'FROM chat_history h LEFT JOIN chat_feedback f ON f.message_id = h.id AND h.role = %s '
                'WHERE h.session_id=%s ORDER BY h.id DESC LIMIT 50',
                ('bot', session_id))]
        rows.reverse()
        return jsonify(messages=rows)

    @app.delete('/api/chat/history')
    def clear_chat_history():
        session_id = request.args.get('session_id', '')
        if session_id and len(session_id) <= 120 and clean_text(session_id):
            with connect() as db:
                db.execute('DELETE FROM chat_history WHERE session_id=%s', (session_id,))
        return jsonify(success=True)

    @app.post('/api/chat')
    def chat():
        payload = request.get_json(silent=True)
        if (not isinstance(payload, dict) or not isinstance(payload.get('message'), str)
                or not payload['message'].strip() or len(payload['message']) > 1000
                or not clean_text(payload['message'])):
            return jsonify(error='Enter a message of 1–1000 characters.'), 400

        user_text = payload['message']
        session_id = payload.get('session_id', '')
        if not isinstance(session_id, str) or len(session_id) > 120 or not clean_text(session_id):
            session_id = ''
        workspace = payload.get('workspace_role', 'coordinator')
        if workspace not in ROLES:
            workspace = 'coordinator'

        labels, confidences = classify(matcher.pipeline, [user_text], matcher.threshold)
        intent = str(labels[0])
        confidence = round(float(confidences[0]), 4)
        ranked = rank_intents(matcher.pipeline, user_text)

        selected = None
        if payload.get('record_id') is not None:
            if not valid_row_id(payload['record_id']):
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
                'coordinator': 'Coordinator tools:\n• Inventory search, “Needs attention” filters, bulk status updates, CSV export\n• Appointment queue, staff notes, and QR sticker sheets for selected IDs\n• Student request inbox (accept lost ID reports)\n• Review which answers people rated 👎 on the Feedback page\n\nSelect a record with “Ask about ID”, then ask about status or schedules.',
                'guard': 'Guard tools:\n• Verify ID by student number, name, QR token, or camera scan\n• One-tap gate check-in / check-out from the Verify result\n• Incident reports (no ID, visitor, lost ID found)\n• Confirm whether an ID is released and valid at the gate\n\nUse Verify ID, then ask me about that student.',
                'student': 'Student tools:\n• Look up your ID with your student number\n• Check processing status and appointments\n• Submit a lost ID or appointment request, then track it\n• Learn requirements, office hours, and QR scanning\n\nLook up your ID first so I can answer about your record. Tap 👍 or 👎 under my answers to tell the office how I did.',
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
                'coordinator': 'Use search and filters to find records by name, student ID, course, section, year, request type, or status. The “Needs attention” chips show overdue and unscheduled IDs. Open Queue for today’s appointments, or Export CSV to download everything.',
                'guard': 'Guards do not edit inventory. Use Verify ID to look up a student at the gate.',
                'student': 'You can look up only your own record with your student ID. Staff manage the full inventory.',
            },
            'out_of_scope': 'I’m an automated assistant for school IDs, so I can’t help with that. Try “When is my ID release?”, “Why is my ID pending?”, or “How do I print a QR sticker?” For anything else, please contact the office.',
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
                    if selected['status'] == 'Released':
                        gate_hint = ' Gate: **Released** — physical ID should be with the student.'
                    else:
                        gate_hint = ' Gate: **ID not yet released** — send the student to Property Management if they need the physical card.'
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

        # Second guesses when the match is weak, otherwise likely next questions. A lookup that
        # only asked the user to pick a record gets neither: the next step is picking the record.
        suggestions, suggestions_title, follow_ups = reply_chips(
            ranked, intent, confidence, workspace,
            with_follow_ups=not (intent in lookups and selected is None))
        if intent == 'out_of_scope' and suggestions:
            answer = UNSURE_REPLY

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
                    {'label': '👍 Chatbot feedback', 'type': 'open_feedback'},
                ])
            if intent == 'lost_id_report':
                actions.append({'label': '📥 Open requests', 'type': 'open_requests'})
            if selected and intent in lookups:
                existing_types = {a['type'] for a in actions}
                if 'print_sticker' not in existing_types:
                    actions.append({'label': '🖨️ Print sticker', 'type': 'print_sticker'})
                actions.append({'label': '🪪 Preview ID card', 'type': 'preview_card'})

        if intent == 'out_of_scope':
            actions.append({'label': '☎ Talk to the office', 'type': 'ask_human'})

        seen = set()
        unique_actions = []
        for action in actions:
            if action['type'] not in seen:
                seen.add(action['type'])
                unique_actions.append(action)
        actions = unique_actions

        now = now_stamp()
        message_id = None
        if session_id:
            with connect() as db:
                db.execute(
                    'INSERT INTO chat_history (session_id,role,message,intent,confidence,record_id,timestamp,workspace_role) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                    (session_id, 'user', user_text, '', 0, payload.get('record_id'), now, workspace))
                message_id = db.execute(
                    'INSERT INTO chat_history (session_id,role,message,intent,confidence,record_id,timestamp,workspace_role) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                    (session_id, 'bot', answer, intent, confidence, payload.get('record_id'), now, workspace)).fetchone()[0]
                db.execute(
                    'DELETE FROM chat_history WHERE session_id=%s AND id NOT IN '
                    '(SELECT id FROM chat_history WHERE session_id=%s ORDER BY id DESC LIMIT 100)',
                    (session_id, session_id))

        return jsonify(intent=intent, reply=answer, confidence=confidence, actions=actions, timestamp=now,
                       message_id=message_id, suggestions=suggestions, suggestions_title=suggestions_title,
                       follow_ups=follow_ups)

    # ─── Chat feedback ────────────────────────────────────────────
    # A rating belongs to the chat session that received the reply: the caller must send that
    # session's id, so nobody can rate (or "share") another person's messages by counting ids.

    def known_people():
        with connect() as db:
            people = db.execute('SELECT student_id, name FROM inventory').fetchall()
        return [p['student_id'] for p in people], [p['name'] for p in people]

    @app.post('/api/chat/feedback')
    def save_feedback():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        message_id, session_id, rating = payload.get('message_id'), payload.get('session_id'), payload.get('rating')
        if (not valid_row_id(message_id) or not isinstance(session_id, str) or not session_id
                or len(session_id) > 120 or not clean_text(session_id)):
            return jsonify(error='Choose a reply to rate.'), 400
        if rating not in FEEDBACK_RATINGS:
            return jsonify(error='Choose thumbs up or thumbs down.'), 400
        reason = payload.get('reason', '')
        if rating == 'down' and reason not in ('', *FEEDBACK_REASONS):
            return jsonify(error='Choose one of the listed reasons.'), 400
        comment = text_field(payload.get('comment', ''), max_len=200, required=False)
        if comment is None:
            return jsonify(error='Comments must be text of up to 200 characters.'), 400
        share = payload.get('share_text', False)
        if not isinstance(share, bool):
            return jsonify(error='share_text must be true or false.'), 400
        if rating == 'up':
            reason, comment, share = '', '', False
        ids, names = known_people() if (share or comment) else ([], [])
        if comment:
            comment = redact_text(comment, ids, names)
        with connect() as db:
            bot = db.execute(
                "SELECT id, intent, confidence, workspace_role FROM chat_history "
                "WHERE id=%s AND role='bot' AND session_id=%s", (message_id, session_id)).fetchone()
            if bot is None:
                abort(404)
            question = ''
            if share:
                previous = db.execute(
                    "SELECT message FROM chat_history WHERE session_id=%s AND role='user' AND id<%s "
                    "ORDER BY id DESC LIMIT 1", (session_id, message_id)).fetchone()
                if previous:
                    question = redact_text(previous['message'], ids, names)
            saved = db.execute(
                '''INSERT INTO chat_feedback
                   (message_id,rating,reason,comment,workspace_role,intent,confidence,shared_question,status,timestamp)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'New',%s)
                   ON CONFLICT (message_id) DO UPDATE SET rating=EXCLUDED.rating, reason=EXCLUDED.reason,
                       comment=EXCLUDED.comment, shared_question=EXCLUDED.shared_question,
                       correct_intent='', status='New', timestamp=EXCLUDED.timestamp
                   RETURNING id''',
                (message_id, rating, reason, comment, bot['workspace_role'], bot['intent'],
                 bot['confidence'], question, now_stamp())).fetchone()
        return jsonify(id=saved['id'], rating=rating, reason=reason, shared=bool(question))

    @app.delete('/api/chat/feedback')
    def remove_feedback():
        message_id = request.args.get('message_id', type=int)
        session_id = request.args.get('session_id', '')
        if not valid_row_id(message_id) or not session_id or len(session_id) > 120 or not clean_text(session_id):
            return jsonify(error='Choose a reply to un-rate.'), 400
        removed = 0
        with connect() as db:
            owned = db.execute(
                "SELECT 1 FROM chat_history WHERE id=%s AND role='bot' AND session_id=%s",
                (message_id, session_id)).fetchone()
            if owned:
                removed = db.execute('DELETE FROM chat_feedback WHERE message_id=%s', (message_id,)).rowcount
        return jsonify(removed=bool(removed))

    FEEDBACK_COLUMNS = ('id, rating, reason, comment, workspace_role, intent, confidence, '
                        'shared_question, correct_intent, status, timestamp')

    @app.get('/api/feedback')
    def list_feedback():
        rating = request.args.get('rating', '')
        status = request.args.get('status', '')
        if rating and rating not in FEEDBACK_RATINGS:
            return jsonify(error='Choose up or down.'), 400
        if status and status not in FEEDBACK_STATUSES:
            return jsonify(error='Choose New or Reviewed.'), 400
        clauses = [f'{column}=%s' for column, value in (('rating', rating), ('status', status)) if value]
        params = [value for value in (rating, status) if value]
        where = f'WHERE {" AND ".join(clauses)} ' if clauses else ''
        with connect() as db:
            items = [dict(row) for row in db.execute(
                f'SELECT {FEEDBACK_COLUMNS} FROM chat_feedback {where}ORDER BY id DESC LIMIT 100', params)]
            totals = {row[0]: row[1] for row in db.execute(
                'SELECT rating, COUNT(*) FROM chat_feedback GROUP BY rating')}
            shared = db.execute("SELECT COUNT(*) FROM chat_feedback WHERE shared_question != ''").fetchone()[0]
            new_down = db.execute(
                "SELECT COUNT(*) FROM chat_feedback WHERE rating='down' AND status='New'").fetchone()[0]
            by_intent = [dict(row) for row in db.execute(
                "SELECT intent, COUNT(*) FILTER (WHERE rating='up') AS up, "
                "COUNT(*) FILTER (WHERE rating='down') AS down FROM chat_feedback "
                'GROUP BY intent ORDER BY down DESC, up DESC, intent COLLATE "C" LIMIT 8')]
            by_reason = {row[0]: row[1] for row in db.execute(
                "SELECT reason, COUNT(*) FROM chat_feedback WHERE rating='down' AND reason != '' "
                'GROUP BY reason ORDER BY COUNT(*) DESC, reason COLLATE "C"')}
        up, down = totals.get('up', 0), totals.get('down', 0)
        summary = {'total': up + down, 'up': up, 'down': down, 'new_down': new_down, 'shared': shared,
                   'helpful_percent': round(100 * up / (up + down)) if up + down else None,
                   'by_intent': by_intent, 'by_reason': by_reason}
        return jsonify(summary=summary, items=items, intents=matcher.intents,
                       intent_labels={name: intent_label(name) for name in matcher.intents})

    @app.patch('/api/feedback/<rowid:feedback_id>')
    def review_feedback(feedback_id):
        payload = request.get_json(silent=True)
        allowed = ('status', 'correct_intent')
        if not isinstance(payload, dict) or not payload or set(payload) - set(allowed):
            return jsonify(error='Supply status or correct_intent.'), 400
        if 'status' in payload and payload['status'] not in FEEDBACK_STATUSES:
            return jsonify(error='Choose New or Reviewed.'), 400
        if 'correct_intent' in payload:
            correct = payload['correct_intent']
            if not isinstance(correct, str) or (correct and correct not in matcher.intents):
                return jsonify(error='Choose one of the assistant’s topics.'), 400
        changes = ', '.join(f'{column}=%s' for column in payload)       # the column names come from `allowed`
        with connect() as db:
            row = db.execute(f'UPDATE chat_feedback SET {changes} WHERE id=%s RETURNING {FEEDBACK_COLUMNS}',
                             (*payload.values(), feedback_id)).fetchone()
            if row is None:
                abort(404)
            item = dict(row)
        log_activity('coordinator', 'Reviewed chat feedback', f'#{feedback_id} → {item["status"]}')
        return jsonify(item)

    @app.get('/api/feedback/export')
    def export_feedback():
        with connect() as db:
            rows = db.execute(
                'SELECT id, timestamp, rating, reason, comment, intent, confidence, workspace_role, '
                'shared_question, correct_intent, status FROM chat_feedback ORDER BY id DESC').fetchall()
        output = io.StringIO()
        writer = csv.writer(output, lineterminator='\n')
        writer.writerow(['id', 'timestamp', 'rating', 'reason', 'comment', 'predicted_intent', 'match_score',
                         'role', 'shared_question', 'correct_intent', 'status'])
        writer.writerows([[csv_safe(value) for value in row] for row in rows])
        mem = io.BytesIO(output.getvalue().encode('utf-8-sig'))
        return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='chat_feedback.csv')

    @app.after_request
    def private_pages(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    return app


if __name__ == '__main__':
    try:
        application = create_app()
    except RuntimeError as error:              # DATABASE_URL missing, or the database cannot be reached
        raise SystemExit(f'Cannot start: {error}')
    application.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
