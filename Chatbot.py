"""School ID management prototype. Needs PostgreSQL: set DATABASE_URL, then run .venv/bin/python Chatbot.py."""
import csv
import io
import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import qrcode
from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from psycopg import errors as pg_errors
from werkzeug.exceptions import BadRequest, HTTPException, MethodNotAllowed
from werkzeug.routing import IntegerConverter

from auth import CoordinatorLogin
from chat_support import (
    FEEDBACK_REASONS, asks_for_gate_log, asks_for_incidents, asks_for_my_requests, asks_for_records,
    asks_to_file_incident, asks_to_request_help, clean_text, csv_safe, gate_verb_is_a_preposition, id_candidates,
    incident_filed_reply, incident_help_reply, incident_status_command, incidents_reply, intent_label,
    is_bare_gate_verb, my_requests_reply, parse_gate_command, redact_text, reply_chips, request_sent_reply,
    request_subject, staff_lines, student_reply)
from database import Database, init_schema, lock_student, seed_demo_records
from id_cards import sample_card
from intent_model import ANNIntentModel, classify, rank_intents
from small_talk import SMALL_TALK_INTENTS, small_talk_intent
from student_data import (
    ADDRESS_MAX, CONTACT_HINT, IMPORT_COLUMNS, MAX_IMPORT_BYTES, MAX_IMPORT_ROWS, MAX_YEAR, NAME_MAX,
    NOT_YET_MADE, PENALTY_NOTE, PENALTY_TEXT, RELEASE_HOURS, STATUS_NOTES, STATUSES, STUDENT_ID_EXAMPLE,
    STUDENT_ID_HINT, STUDENT_ID_RE, STUDENT_LOST_STEPS, YEAR_SECTION_HINT, ImportFileError, claim_check, clean_contact,
    clean_student_id, clean_status, find_by_name, format_year_section, gate_note, incident_steps,
    looks_like_a_name, name_query, office_hours_error, parse_year_section, penalty_for, read_student_csv, tidy)

ROOT = Path(__file__).resolve().parent
MAX_ROW_ID = 2 ** 63 - 1   # the largest BIGINT; anything larger would raise instead of "not found"
MAX_STICKERS = 60          # one printed sheet


class RowIdConverter(IntegerConverter):
    """URL number that always fits a BIGINT; longer numbers are a plain 404."""
    regex = r'\d{1,18}'


def valid_row_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_ROW_ID


def parse_year(value):
    """Year level 1 to MAX_YEAR from a JSON number or a short string of ASCII digits, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = int(value) if value.isascii() and value.isdigit() and len(value) <= 3 else None
    if not isinstance(value, int):
        return None
    return value if 1 <= value <= MAX_YEAR else None
KINDS = ('New ID', 'Pending ID', 'Lost ID')
SCHEDULES = ('Release', 'Pending follow-up', 'Lost ID replacement')
ROLES = ('coordinator', 'guard', 'student')
GATE_DIRECTIONS = ('in', 'out')
INCIDENT_CATEGORIES = ('No ID', 'Lost ID')
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
}
MAX_NEWS = 20              # how many announcements the ticker carries
MAX_LOOKUP_LIST = 5        # a name that matches more students than this is too vague to list
RECENT_ACTIVITY = 6        # how many of the latest events the dashboard ticker carries
MAX_INCIDENTS_LIST = 8     # how many incidents "incidents" lists in the chat
MAX_RESOLVE_BUTTONS = 3    # ...and how many of the open ones get a Resolve button under the list
BUILD = '25 Oct 2026 · forms in the chat + motion'     # shown in the page footer, so a copy that was not updated is easy to see
STUDENT_HIDDEN = ('notes', 'token', 'address', 'contact_number')    # what the student lookup leaves out
OFFICE = {
    'hours': 'Monday–Friday, 8:00 AM – 5:00 PM, closed 12:00 – 1:00 PM for lunch (school local time)',
    'release_hours': RELEASE_HOURS,
    'location': 'Property Management Office, Ground Floor, Admin Building',
    'contact': 'pmo@school.example · local 2100',
    'requirements': {
        'New ID': 'Filled application form, recent 1×1 photo, and proof of enrollment.',
        'Lost ID': ('Pay the replacement fee at the Cashier first. Then bring the Cashier’s receipt, an '
                    'affidavit of loss and a recent 1×1 photo to the office.'),
        'Pending ID': 'Submit the missing documents listed on your record, then wait for processing.',
    },
}

def help_action(student=None, request_type=''):
    """The small "Request help" button under a student's answer. It opens the form in the chat, filled in with
    the student the answer is about and, when the answer already shows what is needed, the type of request."""
    action = {'label': '📝 Request help', 'type': 'open_student_request'}
    if student:
        action.update(student_id=student['student_id'], name=student['name'])
    if request_type:
        action['request_type'] = request_type
    return action


def incident_action(student=None, category=''):
    """The small "File incident" button (guard and coordinator). It opens the incident form over the chat, filled in
    with the student the answer is about and, when the answer already shows it, the category."""
    action = {'label': '⚠️ File incident', 'type': 'open_incident'}
    if student:
        action['student_id'] = student['student_id']
    if category:
        action['category'] = category
    return action


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
    'lost_id_report': [{'label': '📋 Open Dashboard', 'type': 'open_dashboard'}],
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
    login = CoordinatorLogin(database_url=database_url)
    app.secret_key = login.secret
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=login.on_vercel, PERMANENT_SESSION_LIFETIME=8 * 3600)

    def coordinator_signed_in():
        return not login.enabled or session.get('coordinator') is True

    def coordinator_only(path, method):
        """The pages and API calls only the coordinator's dashboard uses. The guard and student never call these."""
        if path.startswith(('/api/inventory', '/api/stats', '/api/queue', '/api/activity', '/api/requests',
                            '/api/feedback', '/stickers', '/sticker/', '/card/', '/qr/')):
            return True
        return path == '/api/incidents' or (path.startswith('/api/incidents/') and method == 'PATCH')

    @app.before_request
    def require_coordinator():
        if coordinator_only(request.path, request.method) and not coordinator_signed_in():
            if request.path.startswith('/api/'):
                response = jsonify(error='Sign in as the coordinator first.', sign_in='/coordinator')
                response.status_code = 401
                return response
            return redirect('/coordinator')

    @app.errorhandler(HTTPException)
    def http_error(error):
        """JSON for the API, the normal page for everything else."""
        if not request.path.startswith('/api/'):
            return error
        message = API_ERRORS.get(error.code, error.name)
        if error.code == 413:
            limit = request.max_content_length or app.config['MAX_CONTENT_LENGTH']
            message = (f'The file is too large (the limit is {limit // 1024 // 1024} MB).' if limit >= 1024 * 1024
                       else f'The request is too large (the limit is {limit // 1024} KB).')
        response = jsonify(error=message)
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

    def shape(row):
        """A record as the browser sees it: the stored columns plus year and section written as one ("3E")."""
        row = dict(row)
        row['year_section'] = format_year_section(row['year_level'], row['section'])
        return row

    def record(record_id):
        if not valid_row_id(record_id):
            abort(404)
        with connect() as db:
            row = db.execute('SELECT * FROM inventory WHERE id=%s', (record_id,)).fetchone()
        if row is None:
            abort(404)
        return shape(row)

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

    def like_pattern(word):
        """A word as an ILIKE pattern that finds it anywhere; a % or _ typed in the word means itself."""
        escaped = word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        return f'%{escaped}%'

    def find_by_student_id(student_id):
        with connect() as db:
            return db.execute(
                'SELECT * FROM inventory WHERE LOWER(student_id) = LOWER(%s)',
                (student_id,)).fetchone()

    def known_student_id(value):
        """A student ID as it is stored: the ID of a record that already exists (whatever its format, for
        records made by earlier versions), else a well-formed new one. None when it is neither."""
        if not isinstance(value, str) or not clean_text(value):
            return None
        value = tidy(value)
        if not value or len(value) > 40:
            return None
        found = find_by_student_id(value)
        return found['student_id'] if found else clean_student_id(value)

    def add_incident_facts(rows):
        """Adds how many false statements each student has made and the penalty still due, for staff."""
        ids = [row['student_id'].lower() for row in rows]
        if not ids:
            return rows
        with connect() as db:
            facts = {row[0]: (row[1], int(row[2])) for row in db.execute(
                "SELECT LOWER(student_id), COUNT(*) FILTER (WHERE false_statement), "
                "COALESCE(SUM(penalty) FILTER (WHERE false_statement AND status = 'Open'), 0) "
                "FROM incidents WHERE LOWER(student_id) = ANY(%s) GROUP BY 1", (ids,))}
        for row in rows:
            row['false_statements'], row['penalty_due'] = facts.get(row['student_id'].lower(), (0, 0))
        return rows

    # ─── Page routes ───────────────────────────────────────────────


    def workspace(page_role=''):
        """The one-page app. '/' shows only the role chooser; /coordinator, /guard and /student open that role."""
        return render_template(
            'index.html', page_role=page_role, statuses=STATUSES, kinds=KINDS, schedules=SCHEDULES,
            office=OFFICE, incident_categories=INCIDENT_CATEGORIES,
            request_types=REQUEST_TYPES, feedback_reasons=FEEDBACK_REASONS,
            max_stickers=MAX_STICKERS, max_import_rows=MAX_IMPORT_ROWS, max_import_mb=MAX_IMPORT_BYTES // 1024 // 1024,
            student_id_example=STUDENT_ID_EXAMPLE, id_hint=STUDENT_ID_HINT, release_hours=RELEASE_HOURS,
            penalty_text=PENALTY_TEXT, year_hint=YEAR_SECTION_HINT, build=BUILD)

    @app.get('/')
    def home():
        return workspace()

    for role_page in ('guard', 'student'):
        app.add_url_rule(f'/{role_page}', f'role_{role_page}', lambda role_page=role_page: workspace(role_page),
                         strict_slashes=False)

    @app.get('/coordinator', strict_slashes=False)
    def coordinator_page():
        if coordinator_signed_in():
            return workspace('coordinator')
        return render_template('login.html', configured=login.configured, error='')

    @app.post('/coordinator/login')
    def coordinator_login():
        address = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()
        if login.check(request.form.get('username', ''), request.form.get('password', ''), address):
            session.clear()
            session['coordinator'] = True
            session.permanent = True
            return redirect('/coordinator')
        error = ('Too many wrong tries. Wait a minute and try again.' if login.locked(address)
                 else 'Wrong username or password.')
        return render_template('login.html', configured=login.configured, error=error), 401

    @app.post('/coordinator/logout')
    def coordinator_logout():
        session.clear()
        return redirect('/')

    @app.get('/student/<token>')
    def profile(token):
        if not clean_text(token):                # a NUL or a lone surrogate cannot be part of a real token
            abort(404)
        with connect() as db:
            row = db.execute(
                'SELECT name,section,year_level FROM inventory WHERE token=%s',
                (token,)).fetchone()
        if row is None:
            abort(404)
        return render_template('profile.html', student=shape(row))

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

    @app.get('/api/news')
    def news():
        """The announcements on the dashboard ticker: data/pgpc_news.json, a list of {"icon", "text"}. Bad entries are skipped."""
        try:
            entries = json.loads((ROOT / 'data' / 'pgpc_news.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            entries = []
        items = []
        for entry in entries if isinstance(entries, list) else []:
            text = entry.get('text') if isinstance(entry, dict) else None
            if isinstance(text, str) and text.strip():
                icon = entry.get('icon') if isinstance(entry.get('icon'), str) else ''
                items.append({'icon': icon.strip()[:4] or '📣', 'text': text.strip()[:200]})
        return jsonify(news=items[:MAX_NEWS])

    @app.get('/api/office')
    def office_info():
        return jsonify(OFFICE)

    # ─── Inventory CRUD ───────────────────────────────────────────

    def reset_after_lost(db, row, reason):
        """The card is gone, so the record starts over: Pending again, as a Lost ID request.

        If the card had already been Released, its release appointment is over, so that appointment is
        cleared too (left in place it would show up as overdue).
        """
        fields = {'status': 'Pending', 'request_type': 'Lost ID', 'pending_reason': reason[:500]}
        if row['status'] == 'Released' and row['schedule_type'] == 'Release':
            fields.update(schedule_type='', schedule_date='', schedule_time='')
        columns = ', '.join(f'{column}=%s' for column in fields)          # the names are the ones above
        db.execute(f'UPDATE inventory SET {columns} WHERE id=%s', (*fields.values(), row['id']))

    @app.get('/api/inventory')
    def inventory():
        with connect() as db:
            rows = [shape(row) for row in db.execute('SELECT * FROM inventory ORDER BY id DESC')]
        return jsonify(records=rows, statuses=STATUSES, status_notes=STATUS_NOTES, not_yet_made=list(NOT_YET_MADE))

    @app.post('/api/inventory')
    def add_record():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        student_id = clean_student_id(payload.get('student_id'))
        if student_id is None:
            return jsonify(error=STUDENT_ID_HINT), 400
        name = text_field(payload.get('name'), NAME_MAX)
        if not name:
            return jsonify(error=f'Full name is required (maximum {NAME_MAX} characters).'), 400
        if 'section' in payload and 'year_section' not in payload:        # the older form: year level and section apart
            section, year = text_field(payload.get('section'), 10), parse_year(payload.get('year_level'))
            year_section = (year, section.upper()) if section and year else None
        else:
            year_section = parse_year_section(payload.get('year_section'))
        if year_section is None:
            return jsonify(error=YEAR_SECTION_HINT), 400
        address = text_field(payload.get('address', ''), ADDRESS_MAX, required=False)
        if address is None:
            return jsonify(error=f'Address must be text of up to {ADDRESS_MAX} characters.'), 400
        contact = clean_contact(payload.get('contact_number', ''))
        if contact is None:
            return jsonify(error=CONTACT_HINT), 400
        course = text_field(payload.get('course', ''), required=False)
        if course is None:
            return jsonify(error='Course must be text of up to 100 characters.'), 400
        kind = payload.get('request_type', 'New ID')
        if kind not in KINDS:
            return jsonify(error='Choose a valid request type.'), 400
        # A quick, friendly answer. The unique index on LOWER(student_id) is what guarantees it.
        if find_by_student_id(student_id):
            return jsonify(error='That student ID already exists.'), 409
        try:
            with connect() as db:
                lock_student(db, student_id)                  # one writer per student ID at a time
                created = db.execute(
                    'INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,'
                    'address,contact_number,token) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *',
                    (student_id, tidy(name), course, year_section[1], year_section[0], 'Pending', kind,
                     tidy(address), contact, secrets.token_urlsafe(18))).fetchone()
        except pg_errors.UniqueViolation:
            return jsonify(error='That student ID already exists.'), 409
        log_activity('coordinator', 'Added record', student_id)
        return jsonify(shape(created)), 201

    @app.patch('/api/inventory/<rowid:record_id>')
    def update_record(record_id):
        payload = request.get_json(silent=True)
        allowed = ('status', 'pending_reason', 'schedule_type', 'schedule_date', 'schedule_time', 'notes',
                   'name', 'year_section', 'address', 'contact_number')
        with connect() as db:
            # FOR UPDATE: two coordinators editing different fields must not overwrite each other.
            row = db.execute('SELECT * FROM inventory WHERE id=%s FOR UPDATE', (record_id,)).fetchone()
            if row is None:
                abort(404)
            current = dict(row)
            if not isinstance(payload, dict) or not payload or set(payload) - set(allowed):
                return jsonify(error='Supply status, pending reason, notes, appointment or student fields.'), 400
            if any(not isinstance(v, str) or len(v) > 500 or not clean_text(v) for v in payload.values()):
                return jsonify(error='Fields must be text of up to 500 characters.'), 400
            changes = dict(payload)
            if 'status' in changes:
                changes['status'] = clean_status(changes['status'], default=None)
                if changes['status'] is None:
                    return jsonify(error='Choose a valid status: ' + ', '.join(STATUSES) + '.'), 400
            if 'name' in changes:
                changes['name'] = tidy(changes['name'])
                if not changes['name'] or len(changes['name']) > NAME_MAX:
                    return jsonify(error=f'Full name is required (maximum {NAME_MAX} characters).'), 400
            if 'year_section' in changes:
                year_section = parse_year_section(changes.pop('year_section'))
                if year_section is None:
                    return jsonify(error=YEAR_SECTION_HINT), 400
                changes['year_level'], changes['section'] = year_section
            if 'address' in changes:
                changes['address'] = tidy(changes['address'])
                if len(changes['address']) > ADDRESS_MAX:
                    return jsonify(error=f'Address must be text of up to {ADDRESS_MAX} characters.'), 400
            if 'contact_number' in changes:
                changes['contact_number'] = clean_contact(changes['contact_number'])
                if changes['contact_number'] is None:
                    return jsonify(error=CONTACT_HINT), 400
            current.update(changes)
            if current['status'] not in STATUSES or current['schedule_type'] not in ('', *SCHEDULES):
                return jsonify(error='Choose a valid status and appointment type.'), 400
            keys = ('schedule_type', 'schedule_date', 'schedule_time')
            schedule = [current[k] for k in keys]
            if any(schedule):
                if not all(schedule):
                    return jsonify(error='Appointment type, date, and time must be filled together.'), 400
                try:
                    when = datetime.strptime(current['schedule_date'] + ' ' + current['schedule_time'], '%Y-%m-%d %H:%M')
                except ValueError:
                    return jsonify(error='Use a valid date and time.'), 400
                # Always stored as 2026-01-05 and 09:30, so dates sort and compare correctly.
                current['schedule_date'], current['schedule_time'] = when.isoformat(sep=' ', timespec='minutes').split(' ')
                # The release hours are checked when the appointment is set or moved, not every time
                # someone edits another field of a record that already has an older appointment.
                if [current[k] for k in keys] != [row[k] for k in keys]:
                    problem = office_hours_error(when)
                    if problem:
                        return jsonify(error=problem), 400
            columns = ('status', 'pending_reason', 'schedule_type', 'schedule_date', 'schedule_time', 'notes',
                       'name', 'year_level', 'section', 'address', 'contact_number')
            db.execute(
                f'UPDATE inventory SET {", ".join(f"{c}=%s" for c in columns)} WHERE id=%s',
                (*[current[c] for c in columns], record_id))
        log_activity('coordinator', 'Updated record', current['student_id'])
        return jsonify(record(record_id))

    @app.post('/api/inventory/<rowid:record_id>/lost')
    def report_lost(record_id):
        """The student's ID card was lost. A Released record goes back to Pending."""
        with connect() as db:
            row = db.execute('SELECT * FROM inventory WHERE id=%s FOR UPDATE', (record_id,)).fetchone()
            if row is None:
                abort(404)
            row = dict(row)
            if row['status'] != 'Released':
                return jsonify(error=f'Only a released ID can be reported lost. This one is {row["status"]}.'), 409
            reset_after_lost(db, row, 'Lost ID reported to the office')
        log_activity('coordinator', 'Lost ID', f"{row['student_id']}: {row['status']} → Pending")
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
        status = clean_status(payload.get('status'), default=None)
        if not isinstance(ids, list) or not ids or not all(valid_row_id(i) for i in ids):
            return jsonify(error='Provide a list of record IDs.'), 400
        if len(ids) > 500:
            return jsonify(error='Bulk update is limited to 500 records.'), 400
        if status is None:
            return jsonify(error='Choose a valid status: ' + ', '.join(STATUSES) + '.'), 400
        with connect() as db:
            cursor = db.execute('UPDATE inventory SET status=%s WHERE id = ANY(%s::bigint[])', (status, ids))
            updated = cursor.rowcount
        log_activity('coordinator', 'Bulk status update', f'{updated} → {status}')
        return jsonify(updated=updated)

    @app.post('/api/inventory/import')
    def import_students():
        """Add students from a CSV file. Students already on the dashboard are skipped, never overwritten."""
        request.max_content_length = MAX_IMPORT_BYTES + 64 * 1024      # before the body is read: room for the file
        upload = request.files.get('file')
        if upload is None or not upload.filename:
            return jsonify(error='Choose a CSV file to import.'), 400
        data = upload.read(MAX_IMPORT_BYTES + 1)
        if len(data) > MAX_IMPORT_BYTES:
            return jsonify(error=f'The file is too large (the limit is {MAX_IMPORT_BYTES // 1024 // 1024} MB).'), 413
        try:
            students, problems, total = read_student_csv(data)
        except ImportFileError as error:
            return jsonify(error=str(error)), 400
        added = 0
        with connect() as db:                     # one transaction: a database error adds nobody
            for student in students:
                created = db.execute(
                    'INSERT INTO inventory (student_id,name,section,year_level,status,request_type,address,'
                    'contact_number,token) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                    (student['student_id'], student['name'], student['section'], student['year_level'],
                     student['status'], 'New ID', student['address'], student['contact_number'],
                     secrets.token_urlsafe(18))).rowcount
                if created:
                    added += 1
                else:
                    problems.append({'line': student['line'], 'student_id': student['student_id'],
                                     'error': 'Already on the dashboard, so it was not added again.'})
        problems.sort(key=lambda problem: problem['line'])
        log_activity('coordinator', 'Imported students', f'{added} added, {len(problems)} skipped of {total} rows')
        return jsonify(added=added, skipped=len(problems), total=total, problems=problems[:50])

    @app.get('/api/inventory/template')
    def import_template():
        output = io.StringIO()
        writer = csv.writer(output, lineterminator='\n')
        writer.writerow(IMPORT_COLUMNS)
        writer.writerow(['P202400001', 'Maria L. Santos', '1A', 'Pending', 'Purok 2, Brgy. Poblacion, Sample City', '09170000001'])
        writer.writerow(['P202400002', 'Juan D. Dela Cruz', '3E', 'Processing', '', ''])
        mem = io.BytesIO(output.getvalue().encode('utf-8-sig'))
        return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='student_import_template.csv')

    @app.get('/api/inventory/export')
    def export_inventory():
        with connect() as db:
            rows = [shape(row) for row in db.execute('SELECT * FROM inventory ORDER BY id DESC')]
        # The first six columns are the ones the import reads, so an export can be imported elsewhere.
        columns = [*IMPORT_COLUMNS, 'request_type', 'pending_reason', 'schedule_type', 'schedule_date',
                   'schedule_time', 'notes']
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            row['full_name'] = row['name']
            writer.writerow({key: csv_safe(row[key]) for key in columns})
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
            by_year = {f'Year {row[0]}': row[1] for row in db.execute(
                'SELECT year_level, COUNT(*) FROM inventory GROUP BY year_level ORDER BY year_level')}
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
            recent = [dict(row) for row in db.execute(
                'SELECT actor_role, action, detail, timestamp FROM activity_log ORDER BY id DESC LIMIT %s',
                (RECENT_ACTIVITY,))]
        return jsonify(total=total, by_status=by_status, by_year=by_year, recent_activity=recent,
                       not_yet_made=sum(by_status.get(name, 0) for name in NOT_YET_MADE),
                       by_request_type=by_request, upcoming_appointments=upcoming,
                       today=today, today_appointments=today_appointments,
                       overdue_appointments=overdue_appointments,
                       open_incidents=open_incidents, open_requests=open_requests,
                       new_feedback=new_feedback)

    @app.get('/api/queue')
    def appointment_queue():
        today = datetime.now().strftime('%Y-%m-%d')
        with connect() as db:
            rows = [shape(row) for row in db.execute(
                "SELECT id, student_id, name, section, year_level, status, schedule_type, schedule_date, schedule_time "
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
        # "Reynald Malabanan" finds "Reynald Jake L. Malabanan": every word must be in the name, in any order.
        likes = [like_pattern(word) for word in query.split()[:6]]
        name_match = ' AND '.join(["name ILIKE %s ESCAPE '\\'"] * len(likes))    # one placeholder per word
        with connect() as db:
            rows = [shape(row) for row in db.execute(
                f'''SELECT id, student_id, name, section, year_level, status, request_type, token
                    FROM inventory
                    WHERE LOWER(student_id) = LOWER(%s) OR token = %s OR ({name_match})
                    ORDER BY (LOWER(student_id) = LOWER(%s) OR token = %s) DESC, id
                    LIMIT 8''', (query, query, *likes, query, query))]
        for row in rows:
            row['status_note'] = STATUS_NOTES.get(row['status'], '')
            row['gate_note'] = gate_note(row['status'])
        add_incident_facts(rows)
        return jsonify(matches=rows)

    def write_gate(direction, student_id, name, note=''):
        with connect() as db:
            row = dict(db.execute(
                'INSERT INTO gate_logs (student_id,name,direction,note,timestamp) VALUES (%s,%s,%s,%s,%s) RETURNING *',
                (student_id, name or 'Visitor', direction, note, now_stamp())).fetchone())
        log_activity('guard', f'Gate {direction}', student_id or name)
        return row

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
        return jsonify(write_gate(direction, student_id, name, note)), 201

    # ─── Incidents: a student at the gate with no ID, or with a lost one ──────

    def false_statements_of(db, student_id):
        return db.execute(
            'SELECT COUNT(*) FROM incidents WHERE LOWER(student_id) = LOWER(%s) AND false_statement',
            (student_id,)).fetchone()[0]

    def recompute_penalties(db, student_id):
        """The penalty is charged at a student's second false statement. The count follows the order the
        incidents were filed, so withdrawing the first one moves the penalty to what is then the second."""
        flagged = db.execute(
            'SELECT id, penalty FROM incidents WHERE LOWER(student_id) = LOWER(%s) AND false_statement ORDER BY id',
            (student_id,)).fetchall()
        for number, row in enumerate(flagged, start=1):
            if row['penalty'] != penalty_for(number):
                db.execute('UPDATE incidents SET penalty=%s WHERE id=%s', (penalty_for(number), row['id']))

    def incident_row(db, incident_id):
        return dict(db.execute(
            'SELECT i.*, s.name AS student_name FROM incidents i '
            'LEFT JOIN inventory s ON LOWER(s.student_id) = LOWER(i.student_id) WHERE i.id = %s',
            (incident_id,)).fetchone())

    NO_RECORD = ('No student with this ID is on the dashboard yet. Ask the coordinator to add them (Add student '
                 'or Import) so their ID can be made.')

    def incident_rows(limit):
        """The latest incidents with the student's name, newest first, and how many are still open."""
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT i.*, s.name AS student_name FROM incidents i '
                'LEFT JOIN inventory s ON LOWER(s.student_id) = LOWER(i.student_id) ORDER BY i.id DESC LIMIT %s',
                (limit,))]
            open_count = db.execute("SELECT COUNT(*) FROM incidents WHERE status='Open'").fetchone()[0]
        return rows, open_count

    @app.get('/api/incidents')
    def list_incidents():
        rows, _ = incident_rows(50)
        return jsonify(incidents=rows, categories=list(INCIDENT_CATEGORIES), penalty=penalty_for(2),
                       penalty_text=PENALTY_TEXT, year_hint=YEAR_SECTION_HINT)

    @app.get('/api/incidents/check')
    def check_incident():
        """What the records say about a student, before an incident is filed."""
        category = request.args.get('category', '')
        student_id = request.args.get('student_id', '')
        if category not in INCIDENT_CATEGORIES:
            return jsonify(error='Choose No ID or Lost ID.'), 400
        if not student_id.strip() or len(student_id) > 40 or not clean_text(student_id):
            return jsonify(error='Enter a student ID.'), 400
        found = find_by_student_id(tidy(student_id))
        if found is None:
            return jsonify(found=False, message=NO_RECORD)
        row = add_incident_facts([shape(found)])[0]
        agrees, message = claim_check(category, row['status'])
        return jsonify(found=True,
                       student={'student_id': row['student_id'], 'name': row['name'], 'year_section': row['year_section'],
                                'status': row['status'], 'status_note': STATUS_NOTES.get(row['status'], '')},
                       claim={'agrees': agrees, 'message': message},
                       false_statements=row['false_statements'], penalty_due=row['penalty_due'],
                       next_penalty=penalty_for(row['false_statements'] + 1), penalty_text=PENALTY_TEXT)

    def file_incident(payload, actor):
        """Check and save an incident. Returns (the saved incident with its claim check and next steps, 201), or
        ({'error': message}, 400). `actor` is the role written to the activity log."""
        category = payload.get('category')
        if category not in INCIDENT_CATEGORIES:
            return {'error': 'Choose No ID or Lost ID.'}, 400
        detail = text_field(payload.get('detail'), max_len=500)
        if not detail:
            return {'error': 'Describe the incident (maximum 500 characters).'}, 400
        location = payload.get('location')
        location = '' if location is None else text_field(location, required=False)
        if location is None:
            return {'error': 'Location must be text of up to 100 characters.'}, 400
        raw_id = payload.get('student_id', '')
        student_id = ''
        if isinstance(raw_id, str) and raw_id.strip():
            student_id = known_student_id(raw_id)
            if student_id is None:
                return {'error': STUDENT_ID_HINT}, 400
        elif raw_id not in ('', None) and not isinstance(raw_id, str):
            return {'error': STUDENT_ID_HINT}, 400
        false_statement = payload.get('false_statement', False)
        if not isinstance(false_statement, bool):
            return {'error': 'false_statement must be true or false.'}, 400
        if false_statement and not student_id:
            return {'error': 'Enter the student ID of the student who gave false information.'}, 400
        with connect() as db:
            record = None
            if student_id:
                lock_student(db, student_id)          # two guards filing at once are counted one after the other
                record = db.execute('SELECT * FROM inventory WHERE LOWER(student_id) = LOWER(%s)',
                                    (student_id,)).fetchone()
            if false_statement and record is None:
                return {'error': 'No student with that ID is on the dashboard, so the false statement '
                                 'cannot be counted.'}, 400
            number = false_statements_of(db, student_id) + 1 if false_statement else 0
            penalty = penalty_for(number) if false_statement else 0
            new_id = db.execute(
                'INSERT INTO incidents (category,location,detail,student_id,status,timestamp,false_statement,penalty) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                (category, location, detail, student_id, 'Open', now_stamp(), false_statement, penalty)).fetchone()[0]
            incident = incident_row(db, new_id)
            total = false_statements_of(db, student_id) if student_id else 0
        log_activity(actor, 'Logged incident', category + (f' · {student_id}' if student_id else ''))
        if false_statement:
            log_activity(actor, 'False statement', f'{student_id}: number {number}'
                         + (f', penalty {PENALTY_TEXT}' if penalty else ''))
        if record is None:
            claim = {'agrees': None, 'message': NO_RECORD} if student_id else None
        else:
            agrees, message = claim_check(category, record['status'])
            claim = {'agrees': agrees, 'message': message}
        return {**incident, 'false_statements': total, 'claim': claim,
                'next_steps': incident_steps(category, penalty)}, 201

    @app.post('/api/incidents')
    def add_incident():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        body, status = file_incident(payload, 'guard')
        return jsonify(body), status

    def set_incident_status(incident_id, status, actor):
        """Mark one incident Open or Resolved (false statements and penalties are not touched). False when there
        is no such incident."""
        with connect() as db:
            probe = db.execute('SELECT student_id FROM incidents WHERE id=%s', (incident_id,)).fetchone()
            if probe is None:
                return False
            if probe['student_id']:
                lock_student(db, probe['student_id'])   # the same order as filing one: student first, then the row
            db.execute('UPDATE incidents SET status=%s WHERE id=%s', (status, incident_id))
        log_activity(actor, 'Updated incident', status)
        return True

    @app.patch('/api/incidents/<rowid:incident_id>')
    def update_incident(incident_id):
        """Mark an incident Open or Resolved, or confirm or withdraw its false statement."""
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not payload or set(payload) - {'status', 'false_statement'}:
            return jsonify(error='Choose Open or Resolved, or confirm or withdraw the false statement.'), 400
        if 'status' in payload and payload['status'] not in INCIDENT_STATUSES:
            return jsonify(error='Choose Open or Resolved.'), 400
        if 'false_statement' in payload and not isinstance(payload['false_statement'], bool):
            return jsonify(error='false_statement must be true or false.'), 400
        with connect() as db:
            probe = db.execute('SELECT student_id FROM incidents WHERE id=%s', (incident_id,)).fetchone()
            if probe is None:
                abort(404)
            if probe['student_id']:
                lock_student(db, probe['student_id'])   # the same order as filing one: student first, then rows
            row = dict(db.execute('SELECT * FROM incidents WHERE id=%s FOR UPDATE', (incident_id,)).fetchone())
            status = payload.get('status', row['status'])
            flag = payload.get('false_statement', row['false_statement'])
            if flag and not row['false_statement'] and (
                    not row['student_id'] or find_by_student_id(row['student_id']) is None):
                return jsonify(error='This incident has no student on the dashboard, so it cannot count as a '
                                     'false statement.'), 400
            db.execute('UPDATE incidents SET status=%s, false_statement=%s WHERE id=%s', (status, flag, incident_id))
            if row['student_id']:
                recompute_penalties(db, row['student_id'])
            if not flag:
                db.execute('UPDATE incidents SET penalty=0 WHERE id=%s', (incident_id,))
            updated = incident_row(db, incident_id)
        if 'status' in payload:
            log_activity('guard', 'Updated incident', payload['status'])
        if 'false_statement' in payload and flag != row['false_statement']:
            log_activity('coordinator', 'False statement' if flag else 'False statement withdrawn',
                         row['student_id'])
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
        # Staff notes, the QR token, the address and the contact number are not for students.
        return jsonify(record={k: v for k, v in shape(found).items() if k not in STUDENT_HIDDEN})

    def student_request_rows(student_id):
        """A student's own requests, newest first, without the free-text details."""
        with connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT id, request_type, status, timestamp FROM student_requests '
                'WHERE LOWER(student_id) = LOWER(%s) ORDER BY id DESC LIMIT 10', (student_id,))]

    @app.post('/api/student/requests')
    def student_requests():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        student_id = text_field(payload.get('student_id'))
        if not student_id:
            return jsonify(error='Enter your student ID.'), 400
        return jsonify(requests=student_request_rows(student_id))

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

    def request_fields(payload):
        """(fields, None) for a request a student filled in, or (None, message) when it cannot be filed."""
        student_id = known_student_id(payload.get('student_id'))
        if student_id is None:
            return None, STUDENT_ID_HINT
        request_type = payload.get('request_type')
        if request_type not in REQUEST_TYPES:
            return None, 'Choose a valid request type.'
        name = text_field(payload.get('name', ''), NAME_MAX, required=False)
        detail = text_field(payload.get('detail', ''), max_len=500, required=False)
        if name is None and payload.get('name', ''):
            return None, f'Name must be text of up to {NAME_MAX} characters.'
        if detail is None and payload.get('detail', ''):
            return None, 'Details must be text of up to 500 characters.'
        return {'student_id': student_id, 'request_type': request_type, 'name': name or '', 'detail': detail or ''}, None

    def file_student_request(fields):
        """Save a request as Submitted. Returns the saved row and the student's record (None when the ID is
        not on the list: a student may ask for help before they have been added)."""
        found = find_by_student_id(fields['student_id'])
        name = fields['name'] or (found['name'] if found else '')
        with connect() as db:
            row = dict(db.execute(
                'INSERT INTO student_requests (student_id,name,request_type,detail,status,timestamp) '
                'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                (fields['student_id'], name, fields['request_type'], fields['detail'], 'Submitted', now_stamp())).fetchone())
        log_activity('student', 'Submitted request', f'{row["request_type"]} · {row["student_id"]}')
        return row, found

    def request_steps(request_type):
        return list(STUDENT_LOST_STEPS) if request_type == 'Lost ID' else []

    @app.post('/api/requests')
    def add_request():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        fields, error = request_fields(payload)
        if error:
            return jsonify(error=error), 400
        row, _ = file_student_request(fields)
        return jsonify(**row, next_steps=request_steps(row['request_type'])), 201

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
            lost_note = ''
            if payload['status'] == 'Accepted' and current['request_type'] == 'Lost ID':
                lock_student(db, current['student_id'])       # same lock as "add a record": no duplicate record
                existing = db.execute(
                    'SELECT * FROM inventory WHERE LOWER(student_id) = LOWER(%s) FOR UPDATE',
                    (current['student_id'],)).fetchone()
                if existing:
                    existing = dict(existing)                    # a Released ID goes back to Pending
                    reset_after_lost(db, existing, current['detail'] or 'Lost ID request accepted')
                    lost_note = f"{existing['student_id']}: {existing['status']} → Pending"
                else:
                    db.execute(
                        'INSERT INTO inventory (student_id,name,section,year_level,status,request_type,pending_reason,token) '
                        'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                        (current['student_id'], current['name'] or current['student_id'], 'TBD', 1,
                         'Pending', 'Lost ID', current['detail'] or 'Lost ID request accepted', secrets.token_urlsafe(18)))
        log_activity('coordinator', 'Updated request', f"{current['student_id']} → {payload['status']}")
        if lost_note:
            log_activity('coordinator', 'Lost ID', lost_note)
        return jsonify(updated)

    # ─── Chat ─────────────────────────────────────────────────────

    def clean_session_id(value):
        return value if isinstance(value, str) and 0 < len(value) <= 120 and clean_text(value) else ''

    def save_chat_turn(session_id, workspace, user_text, answer, intent, confidence, about, now):
        """Keep one question and its answer in the chat history. Returns the answer's id (what a 👍 or 👎 is
        attached to), or None when the browser sent no session."""
        if not session_id:
            return None
        with connect() as db:
            db.execute(
                'INSERT INTO chat_history (session_id,role,message,intent,confidence,record_id,timestamp,workspace_role) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                (session_id, 'user', user_text, '', 0, about, now, workspace))
            message_id = db.execute(
                'INSERT INTO chat_history (session_id,role,message,intent,confidence,record_id,timestamp,workspace_role) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                (session_id, 'bot', answer, intent, confidence, about, now, workspace)).fetchone()[0]
            db.execute(
                'DELETE FROM chat_history WHERE session_id=%s AND id NOT IN '
                '(SELECT id FROM chat_history WHERE session_id=%s ORDER BY id DESC LIMIT 100)',
                (session_id, session_id))
        return message_id

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

    def resolve_student(text):
        """Work out whether a message names a student, by ID or by name. None when it does not.

        This is a lookup in the database, not the neural network: the network labels what a message is
        about, this finds who it is about. Returns {'intent', 'reply', 'student', 'chips'}.
        """
        words = id_candidates(text)
        if words:
            with connect() as db:
                rows = [shape(row) for row in db.execute(
                    'SELECT * FROM inventory WHERE LOWER(student_id) = ANY(%s)', ([w.lower() for w in words],))]
            if rows:
                order = {word.lower(): position for position, word in enumerate(words)}
                rows.sort(key=lambda row: order[row['student_id'].lower()])
                known = set(order) & {row['student_id'].lower() for row in rows}
                missing = [word.upper() for word in words
                           if STUDENT_ID_RE.fullmatch(word.upper()) and word.lower() not in known]
                return {'intent': 'student_lookup', 'rows': rows[:3], 'missing': missing}
            shaped = [word for word in words if STUDENT_ID_RE.fullmatch(word.upper())]
            only_word = len(words) == 1 and text.strip(' .,;:!?').lower() == words[0].lower()
            if shaped or only_word:
                return {'intent': 'student_not_found', 'asked': (shaped or words)[0].upper()}
        if name_query(text):
            with connect() as db:
                people = [dict(row) for row in db.execute('SELECT id, student_id, name FROM inventory')]
                matches = find_by_name(text, people)
                ids = [person['id'] for person in matches[:MAX_LOOKUP_LIST]]
                rows = {row['id']: shape(row) for row in db.execute(
                    'SELECT * FROM inventory WHERE id = ANY(%s::bigint[])', (ids,))} if ids else {}
            if matches:
                return {'intent': 'student_lookup', 'rows': [rows[i] for i in ids], 'matched': len(matches),
                        'by_name': True}
        return None

    MAX_RECORDS_LIST = 10      # how many "not yet made" records one chat answer lists
    DOUBLE_TAP_SECONDS = 20    # the same student logged the same way again within this time is one tap, not two

    def gate_log_answer():
        today = datetime.now().strftime('%Y-%m-%d')
        with connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT * FROM gate_logs WHERE timestamp >= %s ORDER BY id DESC LIMIT 15', (today,))]
            counts = {row[0]: row[1] for row in db.execute(
                'SELECT direction, COUNT(*) FROM gate_logs WHERE timestamp >= %s GROUP BY direction', (today,))}
        total = counts.get('in', 0) + counts.get('out', 0)
        if not total:
            return {'intent': 'gate_log', 'answer': '🚪 No gate entries today yet. To log one, type “check in” or '
                                                    '“check out” followed by a student ID or name.'}
        lines = [f'🚪 Gate log for {today}: {counts.get("in", 0)} in, {counts.get("out", 0)} out (latest first)']
        for entry in rows:
            who = entry['name'] + (f' · {entry["student_id"]}' if entry['student_id'] else '')
            lines.append(f'• {entry["timestamp"][11:16]} · {entry["direction"].upper()} · {who}')
        if total > len(rows):
            lines.append(f'…and {total - len(rows)} earlier entries today.')
        return {'intent': 'gate_log', 'answer': '\n'.join(lines)}

    VISITOR = re.compile(r'^\s*(?:visitor|bisita)\b\s*[:\-]?\s*(.*?)\s*$', re.IGNORECASE)

    def visitor_gate(direction, raw_name):
        """A visitor is not on the student list: only the name and the time are logged."""
        name = tidy(raw_name)
        if len(name) < 2 or len(name) > NAME_MAX or not clean_text(name):
            return {'intent': 'gate_not_logged',
                    'answer': 'Nothing was logged. Type the visitor’s name, for example '
                              '“check in visitor Juan Dela Cruz”.'}
        with connect() as db:
            last = db.execute(
                "SELECT timestamp FROM gate_logs WHERE student_id = '' AND LOWER(name) = LOWER(%s) AND direction = %s "
                'ORDER BY id DESC LIMIT 1', (name, direction)).fetchone()
        if last and (datetime.now() - datetime.strptime(last[0], '%Y-%m-%d %H:%M:%S')).total_seconds() < DOUBLE_TAP_SECONDS:
            lead = f'↩️ Visitor {name} was already logged {direction} a moment ago, so nothing was added.'
        else:
            entry = write_gate(direction, '', name, 'visitor, logged in the chat')
            lead = f'✅ Visitor {name} checked {direction} at {entry["timestamp"][11:16]}.'
        return {'intent': f'gate_check_{direction}',
                'answer': lead + '\nA visitor is not on the student list, so there is no ID record to check.'}

    def gate_command(direction, who, bare=False):
        """Log a student in or out, from "check in P202400230" or "out Alex Rivera", or a visitor, from
        "check in visitor Juan Dela Cruz". A finished answer, or None when the words after the verb name
        nobody at all (then it is an ordinary message).

        `bare` is a command that began with just "in" or "out", which also start ordinary sentences ("in case I
        lost my ID ..."). It counts only when it names a student, or a visitor by a name that is shaped like one."""
        visitor = VISITOR.match(who)
        if visitor:
            name = visitor.group(1)
            if bare and name and not looks_like_a_name(name) and not re.fullmatch(r"[^\W\d_][\w'’.-]{1,29}", name):
                return None                              # "in visitor parking, where do I go" is a sentence
            return visitor_gate(direction, name)
        found = resolve_student(who)
        if found is None:
            if bare or not (name_query(who) or id_candidates(who)):
                return None
            return {'intent': 'gate_not_logged',
                    'answer': f'Nothing was logged. I couldn’t find a student named “{who.strip()[:60]}”. '
                              'Type the student ID or the full name.'}
        if found['intent'] == 'student_not_found':
            return {'intent': 'gate_not_logged',
                    'answer': f'Nothing was logged. I couldn’t find a student with ID {found["asked"]}.'}
        if found.get('missing'):
            return {'intent': 'gate_not_logged',
                    'answer': f'Nothing was logged. I couldn’t find a student with ID {found["missing"][0]}. '
                              'Type one student at a time.'}
        rows, matched = found['rows'], found.get('matched', len(found['rows']))
        if len(rows) != 1 or matched != 1:
            if matched > MAX_LOOKUP_LIST:
                return {'intent': 'gate_not_logged',
                        'answer': f'Nothing was logged. “{who.strip()[:60]}” matches {matched} students. '
                                  'Type the student ID or the full name.'}
            listing = '\n'.join(f'• {row["name"]} · {row["student_id"]} · {row["year_section"]}' for row in rows)
            return {'intent': 'gate_not_logged', 'chips_title': 'Which student?',
                    'answer': f'Nothing was logged yet. Which student checked {direction}?\n{listing}',
                    'chips': [{'label': row['name'], 'question': f'check {direction} {row["student_id"]}'}
                              for row in rows]}
        row = add_incident_facts([rows[0]])[0]
        with connect() as db:
            last = db.execute(
                'SELECT timestamp FROM gate_logs WHERE LOWER(student_id) = LOWER(%s) AND direction = %s '
                'ORDER BY id DESC LIMIT 1', (row['student_id'], direction)).fetchone()
        if last and (datetime.now() - datetime.strptime(last[0], '%Y-%m-%d %H:%M:%S')).total_seconds() < DOUBLE_TAP_SECONDS:
            lead = f'↩️ {row["name"]} was already logged {direction} a moment ago, so nothing was added.'
        else:
            entry = write_gate(direction, row['student_id'], row['name'], 'logged in the chat')
            lead = f'✅ {row["name"]} ({row["student_id"]}) checked {direction} at {entry["timestamp"][11:16]}.'
        status = f'Year & Section: {row["year_section"]} · Status: **{row["status"]}** ({STATUS_NOTES.get(row["status"], "see the office")})'
        return {'intent': f'gate_check_{direction}', 'student': row,
                'answer': '\n'.join([lead, status, gate_note(row['status']), *staff_lines(row)])}

    def records_answer(workspace):
        """The IDs that are not made yet (Pending and Processing): "hindi pa nagagawang ID"."""
        if workspace == 'student':
            return {'intent': 'records_list',
                    'answer': 'The list of IDs that are not made yet is for the office and the guard. Type your '
                              'student ID or name to see whether your own ID is made.'}
        with connect() as db:
            rows = [shape(row) for row in db.execute(
                "SELECT * FROM inventory WHERE status = ANY(%s) ORDER BY (status = 'Pending') DESC, id DESC LIMIT %s",
                (list(NOT_YET_MADE), MAX_RECORDS_LIST))]
            counts = {row[0]: row[1] for row in db.execute(
                'SELECT status, COUNT(*) FROM inventory WHERE status = ANY(%s) GROUP BY status', (list(NOT_YET_MADE),))}
        total = sum(counts.values())
        if not total:
            return {'intent': 'records_list', 'answer': '📂 Every student on the dashboard has an ID that is made. '
                                                        'Nothing is waiting to be made.'}
        lines = [f'📂 IDs not yet made: {total} (' + ' · '.join(f'{name} {counts.get(name, 0)}' for name in NOT_YET_MADE) + ')']
        for row in rows:
            reason = f' — {row["pending_reason"]}' if row['status'] == 'Pending' and row['pending_reason'] else ''
            lines.append(f'• {row["name"]} · {row["student_id"]} · {row["year_section"]} · {row["status"]}{reason}')
        if total > len(rows):
            lines.append(f'…and {total - len(rows)} more.'
                         + (' Use the “Not yet made” filter on the Dashboard to see them all.' if workspace == 'coordinator' else ''))
        result = {'intent': 'records_list', 'answer': '\n'.join(lines), 'chips_title': 'Open a record:',
                  'chips': [{'label': row['name'], 'question': row['student_id']} for row in rows[:6]]}
        if workspace == 'coordinator':
            result['actions'] = [{'label': '📋 Open in Dashboard', 'type': 'show_not_made'}]
        return result

    def incidents_answer(workspace):
        """"incidents": the latest incidents and their status, with a Resolve button for the open ones."""
        rows, open_count = incident_rows(MAX_INCIDENTS_LIST)
        actions = [{'label': f'✔ Resolve #{row["id"]}', 'type': 'send', 'text': f'resolve incident {row["id"]}'}
                   for row in rows if row['status'] == 'Open'][:MAX_RESOLVE_BUTTONS]
        actions.append(incident_action())
        if workspace == 'coordinator':
            actions.append({'label': '📋 Open Incidents page', 'type': 'open_incidents'})
        return {'intent': 'incidents_list', 'answer': incidents_reply(rows, open_count), 'actions': actions}

    def incident_status_answer(workspace, status, incident_id):
        """"resolve incident 12" / "reopen incident 12"."""
        with connect() as db:
            row = db.execute('SELECT status FROM incidents WHERE id=%s', (incident_id,)).fetchone()
        list_button = {'label': '📋 Incidents', 'type': 'send', 'text': 'incidents'}
        if row is None:
            return {'intent': 'incident_status', 'actions': [list_button],
                    'answer': f'There is no incident #{incident_id}. Type “incidents” to see the list.'}
        if row['status'] == status:
            answer = f'Incident #{incident_id} is already {status}. Nothing was changed.'
        else:
            set_incident_status(incident_id, status, workspace)
            answer = f'✅ Incident #{incident_id} marked **{status}**.'
        return {'intent': 'incident_status', 'answer': answer, 'actions': [list_button]}

    def incident_help_answer(selected, category):
        """"file incident": the records for the student the chat is on, and the button that opens the form."""
        return {'intent': 'incident_help', 'answer': incident_help_reply(selected, category),
                'actions': [incident_action(selected, category), {'label': '📋 Incidents', 'type': 'send', 'text': 'incidents'}]}

    def my_requests_answer(text, selected):
        """"my requests": the type, status and time of one student's requests, as a chat answer. The student is
        the one the message names (ID or name), else the one the chat is already about."""
        subject = request_subject(text)
        found = resolve_student(subject) if subject else None
        if found is None and subject and (name_query(subject) or id_candidates(subject)):
            return {'intent': 'my_requests', 'actions': [help_action()],
                    'answer': f'I couldn’t find a student matching “{subject[:60]}”. Type your student ID, for '
                              f'example “my requests {STUDENT_ID_EXAMPLE}”.'}
        who = selected
        if found and found.get('rows'):
            rows, matched = found['rows'], found.get('matched', len(found['rows']))
            if len(rows) != 1 or matched != 1:
                if matched > MAX_LOOKUP_LIST:
                    return {'intent': 'my_requests',
                            'answer': f'“{subject[:60]}” matches {matched} students. Type your student ID, for '
                                      f'example “my requests {STUDENT_ID_EXAMPLE}”.'}
                listing = '\n'.join(f'• {row["name"]} · {row["student_id"]} · {row["year_section"]}' for row in rows)
                return {'intent': 'my_requests', 'chips_title': 'Whose requests?',
                        'answer': f'Whose requests do you want to see?\n{listing}',
                        'chips': [{'label': row['name'], 'question': f'my requests {row["student_id"]}'} for row in rows]}
            who = rows[0]
        elif found and found.get('asked'):             # an ID that is not on the list can still have made requests
            who = {'student_id': found['asked'], 'name': ''}
        if who is None:
            return {'intent': 'my_requests', 'actions': [help_action()],
                    'answer': f'Type your student ID first, for example “my requests {STUDENT_ID_EXAMPLE}”, and I will '
                              'list your requests.'}
        return {'intent': 'my_requests', 'actions': [help_action(who)],
                'answer': my_requests_reply(who['student_id'], who['name'], student_request_rows(who['student_id']))}

    def resolve_command(text, workspace, selected=None):
        """Orders typed into the chat rather than questions: log a student in or out (the guard), show the gate
        log (guard and coordinator), list the IDs that are not made yet (guard and coordinator), file, list and
        resolve incidents (guard and coordinator) and, for a student, follow their requests or ask for the Request
        help form. A finished answer, or None when the message is none of these."""
        if workspace == 'student':
            if asks_for_my_requests(text):
                return my_requests_answer(text, selected)
            if asks_to_request_help(text):
                return {'intent': 'request_help', 'actions': [help_action(selected)],
                        'answer': 'Tap Request help below and fill in the form. The office reviews every request, '
                                  'and you can follow yours by typing “my requests”.'}
        if workspace in ('guard', 'coordinator'):
            status_change = incident_status_command(text)
            if status_change:
                return incident_status_answer(workspace, *status_change)
            if asks_for_incidents(text):
                return incidents_answer(workspace)
            category = asks_to_file_incident(text)
            if category is not None:
                return incident_help_answer(selected, category)
        elif asks_for_incidents(text) or incident_status_command(text) or (
                asks_to_file_incident(text) is not None and 'incident' in text.lower()):
            return {'intent': 'incident_not_for_students', 'actions': [help_action(selected, 'Lost ID')],
                    'answer': 'Incidents are filed by the guard and the office. If you lost your ID, tap Request '
                              'help below and the office will take it from there.'}
        command = parse_gate_command(text)
        if command and workspace == 'guard':
            answer = gate_command(*command, bare=gate_verb_is_a_preposition(text))
            if answer is not None:
                return answer
        if workspace == 'guard' and is_bare_gate_verb(text):
            return {'intent': 'gate_not_logged',
                    'answer': 'Nothing was logged. Type check in or check out, then the student ID or name, '
                              f'for example “check in {STUDENT_ID_EXAMPLE}”.'}
        if asks_for_gate_log(text):
            if workspace == 'student':
                return {'intent': 'gate_log', 'answer': 'The gate log is kept by the guard. Type your student ID '
                                                        'or name to see your own ID status.'}
            return gate_log_answer()
        if asks_for_records(text):
            return records_answer(workspace)
        return None

    @app.post('/api/chat')
    def chat():
        payload = request.get_json(silent=True)
        if (not isinstance(payload, dict) or not isinstance(payload.get('message'), str)
                or not payload['message'].strip() or len(payload['message']) > 1000
                or not clean_text(payload['message'])):
            return jsonify(error='Enter a message of 1–1000 characters.'), 400

        user_text = payload['message']
        session_id = clean_session_id(payload.get('session_id'))
        workspace = payload.get('workspace_role', 'coordinator')
        if workspace not in ROLES:
            workspace = 'coordinator'
        if workspace == 'coordinator' and not coordinator_signed_in():
            workspace = 'student'                    # nobody can borrow the coordinator's answers by naming the role

        selected = None                    # the student the conversation is about, kept by the browser
        selected_missing = False
        if payload.get('record_id') is not None:
            if not valid_row_id(payload['record_id']):
                return jsonify(error='Choose a valid inventory record.'), 400
            with connect() as db:
                chosen = db.execute('SELECT * FROM inventory WHERE id=%s', (payload['record_id'],)).fetchone()
            if chosen is None:
                selected_missing = True    # deleted since it was picked: carry on as if none was picked
            else:
                selected = shape(chosen)

        found = resolve_command(user_text, workspace, selected) or resolve_student(user_text)
        small_talk = None if found else small_talk_intent(user_text)
        if found:
            intent, confidence, ranked = found['intent'], 0.0, []
        elif small_talk:
            # hello, thanks, "what can you do?" and "talk to a person" are rules, not topics of the dataset
            intent, confidence, ranked = small_talk, 0.0, []
        else:
            labels, confidences = classify(matcher.pipeline, [user_text], matcher.threshold)
            intent = str(labels[0])
            confidence = round(float(confidences[0]), 4)
            ranked = rank_intents(matcher.pipeline, user_text)
            # A few plain words the network cannot place are most likely the name of someone not on the list.
            if intent == 'out_of_scope' and looks_like_a_name(user_text):
                found = {'intent': 'student_not_found', 'asked_name': user_text.strip()[:60]}
                intent, ranked = 'student_not_found', []

        answers = {
            'greeting': {
                'coordinator': 'Hello, coordinator. Type a student ID or name and I will show that student’s record. I can also help with the queue, requests, imports and QR stickers.',
                'guard': 'Hello. Type a student ID or name and I will show whether that student’s ID is released. I can also help you log a gate entry or file an incident.',
                'student': 'Hi! Type a student ID or name and I will show that student’s ID status. You can also ask me about requirements, office hours or a lost ID.',
            },
            'thanks_goodbye': 'You’re welcome! Come back whenever you need help with school IDs. Have a great day!',
            'bot_help': {
                'coordinator': 'Coordinator tools:\n• Type a student ID or name to see that student’s record, with address and contact number\n• Type “records” to list the IDs that are not made yet\n• Dashboard: filter by status (including “Not yet made”), select all, change many statuses at once, import students from a CSV file\n• Needs attention shortcuts, the appointment queue (Mon–Fri, 8:00–12:00 and 1:00–5:00) and QR sticker sheets\n• Student requests: accepting a lost ID request sends a Released ID back to Pending\n• Incidents: tap File incident (or type “file incident”) for a No ID or Lost ID report, type “incidents” to list them; the Incidents page is where a false statement is marked or withdrawn, with the ' + PENALTY_TEXT + ' penalty for a second one\n• Review which answers people rated 👎 on the Feedback page',
                'guard': 'Guard tools:\n• Type a student ID or name to see whether that student’s ID is made or released\n• Type “records” to list the IDs that are not made yet\n• Gate log: tap Check in or Check out under a student, or type “check in P202400230” or “check out …” (for a visitor: “check in visitor Juan Dela Cruz”); type “gate log” to see today’s entries\n• Verify ID by student number, name, QR token, or camera scan\n• Incidents: tap File incident (or type “file incident”) to open the report form. Only two kinds: No ID or Lost ID. A lost ID goes to the Cashier first; a second false statement carries a ' + PENALTY_TEXT + ' penalty\n• Type “incidents” to see the latest ones and resolve them',
                'student': 'Student tools:\n• Type a student ID or name to see that student’s ID status, year and section, and appointment\n• Ask about requirements, office hours or the release schedule\n• Tell me you lost your ID and I will show a Request help button: it opens the form, right here in the chat (a lost ID is paid for at the Cashier first)\n• Type “my requests” to track a request you made\n• Tap 👍 or 👎 under my answers to tell the office how I did',
            },
            'talk_to_human': f'Please contact the Property Management Office.\n📍 {OFFICE["location"]}\n🕒 {OFFICE["hours"]}\n☎ {OFFICE["contact"]}',
            'lost_id_report': {
                'coordinator': 'A student reports a lost ID: send them to the Cashier first to pay the replacement fee. They then bring the Cashier’s receipt, an affidavit of loss and a 1×1 photo to this office. Open the student on the Dashboard and choose “Lost ID” in its menu, or accept the student’s request in the Requests inbox. Either way a Released ID goes back to Pending. This chat has not changed any record. ' + PENALTY_NOTE,
                'guard': 'If a student says their ID is lost, type the student ID or name here to check the records, tap File incident below to file a Lost ID incident, and send the student to the Cashier to pay the replacement fee. Then the student goes to the Property Management Office. ' + PENALTY_NOTE,
                'student': 'Go to the Cashier first and pay the replacement fee. Then bring the Cashier’s receipt, an affidavit of loss and a 1×1 photo to the Property Management Office, or tap “Request help” below to report it. When the office accepts your request, your ID goes back to Pending while the replacement is processed. This chat does not replace the official report until you send the request. ' + PENALTY_NOTE,
            },
            'id_requirements': (
                f'New ID: {OFFICE["requirements"]["New ID"]}\n'
                f'Lost ID: {OFFICE["requirements"]["Lost ID"]}\n'
                f'Pending ID: {OFFICE["requirements"]["Pending ID"]}'
            ),
            'office_hours_location': f'📍 {OFFICE["location"]}\n🕒 {OFFICE["hours"]}\n☎ {OFFICE["contact"]}',
            'qr_student_details': 'Scan the QR sticker with your phone camera. It opens the student’s name, year and section. Your phone must be able to reach this app’s URL.',
            'qr_sticker_request': {
                'coordinator': 'Use “Print sticker” on a student record. The label has a 30 mm QR code; print at actual size and test a scan before attaching it.',
                'guard': 'Ask the student to show the QR on their sticker or printed card. Use Verify ID with the student number if the scan fails.',
                'student': 'Type your student ID, then use “Print QR sticker” under my answer. Print at actual size (30 mm QR) and keep it with your card.',
            },
            'id_inventory': {
                'coordinator': 'Open the Dashboard to filter students by status, search them, select several and change their status, or import a CSV file of new students. Export CSV downloads everything.',
                'guard': 'Guards do not edit inventory. Use Verify ID to look up a student at the gate.',
                'student': 'I can show the ID status of a student when you type their student ID or name. Staff manage the full inventory.',
            },
            'out_of_scope': 'I’m an automated assistant for school IDs, so I can’t help with that. Try typing a student ID or name, or ask “When is my ID release?”, “Why is my ID pending?” or “How do I print a QR sticker?” For anything else, please contact the office.',
            'export_data': {
                'coordinator': 'Click “Export CSV” on the Dashboard to download all records as a spreadsheet.',
                'guard': 'CSV export is a coordinator tool. I can still help you verify a student or log a gate event.',
                'student': 'Inventory export is for staff. Type a student ID or name and I will show that student’s status.',
            },
        }

        def pick(value):
            if isinstance(value, dict):
                return value.get(workspace, value.get('coordinator', ''))
            return value

        lookups = ('id_processing_status', 'pending_id', 'id_release_schedule', 'pending_id_schedule', 'lost_id_schedule')
        lookup_chips, lookup_title = [], ''
        student = None                       # set when this reply is about exactly one student
        if found:
            if found.get('answer') is not None:          # a command that has already been carried out
                answer = found['answer']
                student = found.get('student')
                lookup_chips, lookup_title = found.get('chips', []), found.get('chips_title', '')
            elif intent == 'student_not_found':
                if found.get('asked'):
                    answer = (f'I couldn’t find a student with ID {found["asked"]}. Check the number and try again, '
                              'or type the student’s name. '
                              + ('' if STUDENT_ID_RE.fullmatch(found['asked'])
                                 else f'Student IDs look like {STUDENT_ID_EXAMPLE}. ')
                              + 'If the student should be on the list, please contact the office.')
                else:
                    answer = (f'I couldn’t find a student named “{found["asked_name"]}”. To look someone up, type the '
                              'student ID or the full name. If you meant a question, try “When is my ID release?” or '
                              '“What do I need for a new ID?”. If the student should be on the list, please contact '
                              'the office.')
            else:
                rows = found['rows']
                if workspace in ('guard', 'coordinator'):
                    add_incident_facts(rows)
                matched = found.get('matched', len(rows))
                if len(rows) == 1 and matched == 1:
                    student = rows[0]
                    answer = student_reply(student, workspace, RELEASE_HOURS)
                elif found.get('by_name') and matched > MAX_LOOKUP_LIST:
                    answer = (f'That matches {matched} students, which is too many to list. '
                              'Type the full name or the student ID.')
                elif found.get('by_name'):
                    listing = '\n'.join(f'• {row["name"]} · {row["student_id"]} · {row["year_section"]} · {row["status"]}'
                                        for row in rows)
                    answer = f'I found {matched} students that match “{user_text.strip()[:60]}”:\n{listing}\nTap a name, or type the student ID, to see the details.'
                    lookup_chips = [{'label': row['name'], 'question': row['student_id']} for row in rows]
                    lookup_title = 'Which student?'
                else:
                    answer = '\n\n'.join(student_reply(row, workspace, RELEASE_HOURS) for row in rows)
                for asked in found.get('missing', []):
                    answer += f'\n\nI couldn’t find a student with ID {asked}.'
            if student is not None:
                selected = student
        elif intent in lookups:
            if selected is None:
                answer = f'⚡ Type a student ID (for example {STUDENT_ID_EXAMPLE}) or a name first, then ask about status or schedule.'
            elif intent == 'id_processing_status':
                gate_hint = ''
                if workspace == 'guard':
                    gate_hint = ' ' + gate_note(selected['status'])
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
                              'Staff can set the date and time using “Update”. This chat has not booked an appointment.')
                if expected == 'Release':
                    answer += f'\nRelease hours: {RELEASE_HOURS}.'
        else:
            answer = pick(answers.get(intent, answers['out_of_scope']))

        # Second guesses when the match is weak, otherwise likely next questions. A lookup that
        # only asked the user to pick a student gets neither: the next step is typing one.
        if found:
            suggestions, suggestions_title, follow_ups = lookup_chips, lookup_title, []
        else:
            suggestions, suggestions_title, follow_ups = reply_chips(
                ranked, intent, confidence, workspace,
                with_follow_ups=not (intent in lookups and selected is None))
            if intent == 'out_of_scope' and suggestions:
                answer = UNSURE_REPLY

        actions = list(INTENT_ACTIONS.get(intent, []))
        if student is not None:                # the same card for everyone, with the buttons that fit the role
            actions = {
                'coordinator': [
                    {'label': '📋 Show in dashboard', 'type': 'view_record'},
                    {'label': '✏️ Update', 'type': 'update_record'},
                    {'label': '🖨️ Print sticker', 'type': 'print_sticker'},
                    {'label': '🪪 Preview ID card', 'type': 'preview_card'},
                ],
                'guard': [
                    {'label': '✅ Check in', 'type': 'send', 'text': f'check in {student["student_id"]}'},
                    {'label': '⬅️ Check out', 'type': 'send', 'text': f'check out {student["student_id"]}'},
                    incident_action(student),
                    {'label': '🪪 Preview ID card', 'type': 'preview_card'},
                ],
                'student': [
                    {'label': '🖨️ Print QR sticker', 'type': 'print_sticker'},
                    {'label': '🪪 Preview ID card', 'type': 'preview_card'},
                    help_action(student),
                ],
            }[workspace]
        elif found:
            actions = list(found.get('actions', []))
        elif workspace == 'guard':
            actions = [a for a in actions if a['type'] not in ('update_record', 'new_record', 'export_csv', 'reset_filters')]
            if intent in ('greeting', 'bot_help', 'id_inventory'):
                actions = [
                    {'label': '🔎 Verify ID', 'type': 'open_verify'},
                    {'label': '📜 Gate log', 'type': 'send', 'text': 'gate log'},
                    incident_action(selected),
                    {'label': '📋 Incidents', 'type': 'send', 'text': 'incidents'},
                ]
            if intent == 'lost_id_report':
                actions = [incident_action(selected, 'Lost ID')]
            if selected and intent in lookups:
                actions.append({'label': '✅ Check in', 'type': 'send', 'text': f'check in {selected["student_id"]}'})
        elif workspace == 'student':
            actions = [a for a in actions if a['type'] not in ('update_record', 'new_record', 'export_csv', 'reset_filters', 'view_record')]
            if intent in ('greeting', 'bot_help'):
                actions = [help_action(selected), {'label': '🕒 Office hours', 'type': 'ask_office'}]
                if selected:
                    actions.insert(1, {'label': '📋 My requests', 'type': 'send',
                                       'text': f'my requests {selected["student_id"]}'})
            if intent == 'lost_id_report':
                actions = [help_action(selected, 'Lost ID')]
            if intent == 'office_hours_location':
                actions = [{'label': '📞 Contact office', 'type': 'ask_human'}, help_action(selected)]
            if intent == 'talk_to_human':
                actions = [help_action(selected)]
            if selected:
                if intent in ('qr_sticker_request', 'qr_student_details', *lookups):
                    actions.append({'label': '🖨️ Print my sticker', 'type': 'print_sticker'})
                    actions.append({'label': '🪪 Preview ID card', 'type': 'preview_card'})
                if intent in lookups:                   # the form opens already set to what the answer was about
                    actions.append(help_action(selected, 'Status inquiry' if intent in lookups[:2] else 'Appointment help'))
        else:
            if intent in ('greeting', 'bot_help'):
                actions.extend([
                    {'label': '📅 Appointment queue', 'type': 'open_queue'},
                    {'label': '📥 Student requests', 'type': 'open_requests'},
                    {'label': '📂 IDs not yet made', 'type': 'send', 'text': 'records'},
                    {'label': '⚠️ Incidents', 'type': 'open_incidents'},
                    incident_action(selected),
                    {'label': '👍 Chatbot feedback', 'type': 'open_feedback'},
                ])
            if intent == 'lost_id_report':
                actions.append({'label': '📥 Open requests', 'type': 'open_requests'})
            if selected and intent in lookups:
                existing_types = {a['type'] for a in actions}
                if 'print_sticker' not in existing_types:
                    actions.append({'label': '🖨️ Print sticker', 'type': 'print_sticker'})
                actions.append({'label': '🪪 Preview ID card', 'type': 'preview_card'})

        if intent in ('out_of_scope', 'student_not_found'):
            actions.append({'label': '☎ Talk to the office', 'type': 'ask_human'})
            if workspace == 'student':
                actions.append(help_action(selected))

        seen = set()
        unique_actions = []
        for action in actions:
            key = (action['type'], action.get('text', ''), action.get('student_id', ''))
            if key not in seen:
                seen.add(key)
                unique_actions.append(action)
        actions = unique_actions

        now = now_stamp()
        about = selected['id'] if selected else None          # what the history remembers this message was about
        message_id = save_chat_turn(session_id, workspace, user_text, answer, intent, confidence, about, now)

        return jsonify(intent=intent, reply=answer, confidence=confidence, actions=actions, timestamp=now,
                       message_id=message_id, suggestions=suggestions, suggestions_title=suggestions_title,
                       follow_ups=follow_ups, selected_missing=selected_missing,
                       student=({'id': student['id'], 'student_id': student['student_id'], 'name': student['name']}
                                if student else None))

    @app.post('/api/chat/request')
    def chat_request():
        """The Request help form, sent from the pop-up in the student's chat. The request is filed exactly like one
        from /api/requests, and the confirmation comes back as a chat answer: it is kept in the chat history and can
        be rated like any other. What the student wrote in the details is not copied into the history."""
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        fields, error = request_fields(payload)
        if error:
            return jsonify(error=error), 400
        row, found = file_student_request(fields)
        steps = request_steps(row['request_type'])
        answer = request_sent_reply(row, steps)
        actions = [{'label': '📋 My requests', 'type': 'send', 'text': f'my requests {row["student_id"]}'}]
        now = now_stamp()
        message_id = save_chat_turn(clean_session_id(payload.get('session_id')), 'student',
                                    f'📝 Request help: {row["request_type"]}', answer, 'request_submitted', 0,
                                    found['id'] if found else None, now)
        return jsonify(intent='request_submitted', reply=answer, confidence=0, actions=actions, timestamp=now,
                       message_id=message_id, suggestions=[], suggestions_title='', follow_ups=[],
                       selected_missing=False, request={'id': row['id'], 'request_type': row['request_type'],
                                                        'status': row['status']},
                       student=({'id': found['id'], 'student_id': found['student_id'], 'name': found['name']}
                                if found else None)), 201

    @app.post('/api/chat/incident')
    def chat_incident():
        """The incident form, sent from the pop-up in the guard's (or the coordinator's) chat. The incident is filed
        exactly like one from /api/incidents, and the confirmation comes back as a chat answer that is kept in the
        chat history. What was written in "What happened?" is not copied into the history."""
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error='Send a JSON object.'), 400
        workspace = payload.get('workspace_role')
        if workspace not in ('guard', 'coordinator'):
            return jsonify(error='Incidents are filed by the guard and the office.'), 403
        body, status = file_incident(payload, workspace)
        if status != 201:
            return jsonify(body), status
        answer = incident_filed_reply(body, body['next_steps'])
        found = find_by_student_id(body['student_id']) if body['student_id'] else None
        now = now_stamp()
        message_id = save_chat_turn(clean_session_id(payload.get('session_id')), workspace,
                                    f'⚠️ File incident: {body["category"]}', answer, 'incident_filed', 0,
                                    found['id'] if found else None, now)
        return jsonify(intent='incident_filed', reply=answer, confidence=0, timestamp=now, message_id=message_id,
                       actions=[{'label': '📋 Incidents', 'type': 'send', 'text': 'incidents'}],
                       suggestions=[], suggestions_title='', follow_ups=[], selected_missing=False, incident=body,
                       student=({'id': found['id'], 'student_id': found['student_id'], 'name': found['name']}
                                if found else None)), 201

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
        # The topics to choose from are the ones the dataset teaches (plus out_of_scope), but every topic a
        # reply can carry gets its plain name, so a 👎 on a greeting still reads "Greeting".
        return jsonify(summary=summary, items=items, intents=matcher.topics,
                       intent_labels={name: intent_label(name) for name in (*matcher.topics, *SMALL_TALK_INTENTS)})

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
            if not isinstance(correct, str) or (correct and correct not in matcher.topics):
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
