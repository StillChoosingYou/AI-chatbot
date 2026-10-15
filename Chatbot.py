"""Local school ID management prototype. Run with .venv/bin/python Chatbot.py."""
import io
import os
import re
import secrets
import unicodedata
import sqlite3
import warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import qrcode
from flask import Flask, abort, g, jsonify, render_template, request, send_file, url_for

from PIL import Image, ImageOps, UnidentifiedImageError

from access import install_access
from id_cards import sample_card
from intent_model import ANNIntentModel

ROOT = Path(__file__).resolve().parent
STATUSES = ('Pending', 'Processing', 'Ready for release', 'Released')
ID_STATUSES = ('Not issued', 'Active', 'Lost')
KINDS = ('New ID', 'Pending ID', 'Lost ID')
SCHEDULES = ('Release', 'Pending follow-up', 'Lost ID replacement')


def create_app(database=None):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 12 * 1024 * 1024
    app.config['DATABASE'] = str(database or os.environ.get('SCHOOL_ID_DATABASE') or ROOT / 'data' / 'inventory.sqlite3')
    public_base = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
    if public_base and (urlparse(public_base).scheme not in ('http', 'https') or not urlparse(public_base).netloc):
        raise ValueError('PUBLIC_BASE_URL must be an absolute HTTP(S) URL')
    matcher = ANNIntentModel()

    def connect():
        connection = sqlite3.connect(app.config['DATABASE'])
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
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
        db.execute('''CREATE TABLE IF NOT EXISTS id_images (
            record_id INTEGER NOT NULL REFERENCES inventory(id) ON DELETE CASCADE,
            side TEXT NOT NULL CHECK(side IN ('front','back')),
            image BLOB NOT NULL, PRIMARY KEY(record_id,side))''')
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

    # Preserve existing inventory, images and QR tokens while separating ID validity.
    with connect() as db:
        columns = {row['name'] for row in db.execute('PRAGMA table_info(inventory)')}
        if 'id_status' not in columns:
            db.execute("ALTER TABLE inventory ADD COLUMN id_status TEXT NOT NULL DEFAULT 'Not issued'")
            db.execute("UPDATE inventory SET id_status=CASE WHEN request_type='Lost ID' THEN 'Lost' WHEN status='Released' THEN 'Active' ELSE 'Not issued' END")
    can_read = install_access(app, connect)

    def record(record_id):
        with connect() as db:
            row = db.execute('SELECT * FROM inventory WHERE id=?', (record_id,)).fetchone()
        if row is None:
            abort(404)
        if not can_read(row):
            abort(403)
        return dict(row)

    def scan_url(token):
        path = url_for('profile', token=token)
        return public_base + path if public_base else url_for('profile', token=token, _external=True)

    @app.get('/')
    def home():
        return render_template('index.html', statuses=STATUSES, kinds=KINDS, schedules=SCHEDULES, id_statuses=ID_STATUSES)

    @app.get('/api/inventory')
    def inventory():
        with connect() as db:
            rows = [dict(row) for row in db.execute('''SELECT inventory.*,
                EXISTS(SELECT 1 FROM id_images WHERE record_id=inventory.id AND side='front') AS has_front_image,
                EXISTS(SELECT 1 FROM id_images WHERE record_id=inventory.id AND side='back') AS has_back_image
                FROM inventory WHERE (? IS NULL OR inventory.id=?) ORDER BY id DESC''',
                (g.user['record_id'], g.user['record_id']))]
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
                cursor = db.execute('INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,token,id_status) VALUES (?,?,?,?,?,?,?,?,?)', (*fields.values(),int(year),'Pending',kind,secrets.token_urlsafe(18),'Lost' if kind == 'Lost ID' else 'Not issued'))
                new_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            return jsonify(error='That student ID already exists.'), 409
        return jsonify(record(new_id)), 201

    @app.patch('/api/inventory/<int:record_id>')
    def update_record(record_id):
        current = record(record_id)
        payload = request.get_json(silent=True)
        allowed = ('id_status', 'status', 'pending_reason', 'schedule_type', 'schedule_date', 'schedule_time')
        if not isinstance(payload, dict) or not payload or set(payload)-set(allowed):
            return jsonify(error='Supply status, pending reason, or schedule fields.'), 400
        if any(not isinstance(v, str) or len(v) > 500 for v in payload.values()):
            return jsonify(error='Fields must be text of up to 500 characters.'), 400
        current.update(payload)
        if current['id_status'] not in ID_STATUSES or current['status'] not in STATUSES or current['schedule_type'] not in ('', *SCHEDULES):
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
            db.execute('UPDATE inventory SET id_status=?,status=?,pending_reason=?,schedule_type=?,schedule_date=?,schedule_time=? WHERE id=?', (*[current[k] for k in allowed],record_id))
        return jsonify(record(record_id))

    def scanned_record(token):
        with connect() as db:
            row = db.execute('SELECT * FROM inventory WHERE token=?', (token,)).fetchone()
        if row is None:
            abort(404)
        return row

    @app.get('/student/<token>')
    def profile(token):
        row = scanned_record(token)
        with connect() as db:
            uploaded = {image['side'] for image in db.execute('SELECT side FROM id_images WHERE record_id=?', (row['id'],))}
        return render_template('profile.html', student=row, token=token, uploaded=uploaded)

    @app.get('/student/<token>/id/<side>.png')
    def id_image(token, side):
        if side not in ('front','back'):
            abort(404)
        row = scanned_record(token)
        with connect() as db:
            stored = db.execute('SELECT image FROM id_images WHERE record_id=? AND side=?', (row['id'],side)).fetchone()
        if stored:
            stream = io.BytesIO(stored['image'])
        else:
            stream = io.BytesIO()
            sample_card(row, side, scan_url(token)).save(stream, format='PNG')
            stream.seek(0)
        return send_file(stream, mimetype='image/png')

    @app.post('/api/inventory/<int:record_id>/images')
    def upload_id_images(record_id):
        record(record_id)
        if set(request.files) - {'front','back'} or request.form:
            return jsonify(error='Supply front and/or back image files.'), 400
        images = {}
        # Validate every file before writing either side; a bad back cannot replace a good front.
        for side in ('front','back'):
            files = request.files.getlist(side)
            if len(files) > 1:
                return jsonify(error='Choose only one image per side.'), 400
            if not files or not files[0].filename:
                continue
            content = files[0].read(5 * 1024 * 1024 + 1)
            if len(content) > 5 * 1024 * 1024:
                return jsonify(error=f'{side.title()} image must be at most 5 MB.'), 400
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(content)) as image:
                        if image.format not in ('JPEG','PNG') or getattr(image,'n_frames',1) != 1:
                            raise ValueError('Use a single JPEG or PNG image.')
                        if image.width * image.height > 20_000_000:
                            raise ValueError('Image dimensions exceed 20 megapixels.')
                        image.load()
                        normalized = ImageOps.exif_transpose(image).convert('RGB')
                        normalized.thumbnail((1600,2400))
                        stream = io.BytesIO()
                        # Recreate pixels to strip EXIF, text chunks, and embedded metadata.
                        clean = Image.new('RGB', normalized.size)
                        clean.paste(normalized)
                        clean.save(stream, format='PNG')
                        images[side] = stream.getvalue()
            except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                return jsonify(error=f'{side.title()} image must be a valid JPEG or PNG, up to 20 megapixels.'), 400
        if not images:
            return jsonify(error='Choose a front or back ID image to upload.'), 400
        with connect() as db:
            db.executemany('INSERT INTO id_images (record_id,side,image) VALUES (?,?,?) ON CONFLICT(record_id,side) DO UPDATE SET image=excluded.image',
                           [(record_id,side,content) for side,content in images.items()])
        return jsonify(saved=list(images))

    @app.delete('/api/inventory/<int:record_id>/images/<side>')
    def remove_id_image(record_id, side):
        record(record_id)
        if side not in ('front','back'):
            abort(404)
        with connect() as db:
            db.execute('DELETE FROM id_images WHERE record_id=? AND side=?', (record_id,side))
        return jsonify(removed=side)

    @app.errorhandler(403)
    @app.errorhandler(404)
    def access_error(error):
        if request.path.startswith('/api/'):
            return jsonify(error='You cannot access that student record.' if error.code == 403 else 'Student record not found.'), error.code
        return error

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error='Upload exceeds the 12 MB request limit. Use images up to 5 MB each.'), 413

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

    @app.get('/sticker/<int:record_id>')
    def sticker(record_id):
        row = record(record_id)
        return render_template('sticker.html', student=row, scan_url=scan_url(row['token']))

    @app.after_request
    def private_pages(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.post('/api/chat')
    def chat():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get('message'), str) or not payload['message'].strip() or len(payload['message']) > 1000:
            return jsonify(error='Enter a message of 1–1000 characters.'), 400
        text = payload['message']
        intent = matcher.match(text)
        if re.search(r"\b(status|active|lost)\b", text, re.I) and re.search(r"\bID\b", text, re.I) and not re.search(r"\b(report|schedule|appointment|lost my)\b", text, re.I):
            intent = 'id_processing_status'
        def normalize(value):
            value = unicodedata.normalize('NFKC', value).casefold().replace('’', "'")
            value = re.sub(r"'s\b", '', value)
            return ' '.join(re.findall(r"[^\W_]+", value))
        query = normalize(text)
        with connect() as db:
            visible = [dict(row) for row in db.execute('SELECT * FROM inventory WHERE (? IS NULL OR id=?)',
                       (g.user['record_id'], g.user['record_id']))]
        # A supplied name takes precedence over a stale selected inventory card.
        matches = [row for row in visible if (' ' + normalize(row['name']) + ' ') in (' ' + query + ' ')
                   or (' ' + normalize(row['student_id']) + ' ') in (' ' + query + ' ')]
        named_query = re.search(r"\b(?:of|for)\s+(.+?)(?:['’]s)?\s+(?:student\s+)?ID\b", text, re.I)
        if not named_query:
            named_query = re.search(r"\b(?:is|what['’]s|what is)\s+(.+?)(?:['’]s)?\s+ID\s+(?:active|lost|status|processing)\b", text, re.I)
        if not named_query:
            named_query = re.search(r"\bID\s+(?:processing\s+)?status\s+(?:of|for)\s+(.+?)[?.!]*$", text, re.I)
        if not matches and named_query:
            fragment = normalize(named_query.group(1))
            if fragment not in ('my', 'the', 'a', 'our', 'this', 'my student'):
                matches = [row for row in visible if fragment and ((' ' + fragment + ' ') in (' ' + normalize(row['name']) + ' ')
                           or fragment == normalize(row['student_id']))]
                if not matches:
                    return jsonify(intent=intent, reply='No matching student was found in the records you can access. Check the full name or student ID.')
        if len(matches) > 1:
            return jsonify(intent=intent, reply='Several students match. Choose the correct student before checking the ID.',
                           candidates=[{key: row[key] for key in ('id','name','student_id','course','section','year_level')} for row in matches])

        selected = matches[0] if matches else None
        if payload.get('record_id') is not None:
            if not isinstance(payload['record_id'], int) or isinstance(payload['record_id'], bool):
                return jsonify(error='Choose a valid inventory record.'), 400
            supplied = record(payload['record_id'])
            if selected is None:
                selected = supplied
        if g.user['role'] == 'student' and selected is None:
            selected = record(g.user['record_id'])
        answers = {
            'greeting': 'Hello! I can help with school ID status, release schedules, pending or lost IDs, inventory, and QR stickers.',
            'thanks_goodbye': 'You’re welcome! Come back whenever you need help with your school ID.',
            'bot_help': 'Ask about a student by full name or student ID, or select a record to check its status or appointment. You can also filter the inventory or print a QR sticker.',
            'talk_to_human': 'Please contact the Property Management Office staff for assistance. Office contact details have not been configured yet.',
            'lost_id_report': 'Please report the missing ID to the Property Management Office. Staff can record a Lost ID request and arrange a replacement appointment. This chat has not submitted a report.',
            'id_requirements': 'Ask the Property Management Office to confirm the required documents, photo specifications, and any fees. School requirements have not been configured yet.',
            'office_hours_location': 'Please confirm the office hours and pickup location with the Property Management Office. These details have not been configured yet.',
            'qr_student_details': 'Scan the QR sticker with your phone camera. It opens the front and back ID images, plus the student’s name, course, section, and year level. Your phone must be able to reach this app’s URL.',
            'qr_sticker_request': 'Use “Print sticker” on the student’s inventory card. The label has a 30 mm QR code; print at actual size and test a scan before attaching it.',
            'id_inventory': 'Use the search box and filters above to find records by name or student ID, course, section, year, request type, or processing status.',
            'out_of_scope': 'I can help with school IDs. Try “When is my ID release?”, “Why is my ID pending?”, or “How do I print a QR sticker?”',
        }
        lookups = ('id_processing_status', 'pending_id', 'id_release_schedule', 'pending_id_schedule', 'lost_id_schedule')
        if intent in lookups:
            if selected is None:
                answer = 'Select a student record using “Ask about ID” or ask by full name or student ID so I can check its status or appointment.'
            elif intent == 'id_processing_status':
                answer = f'{selected["name"]}: ID status: {selected["id_status"]}. Processing status: {selected["status"]} ({selected["request_type"]}). No ID, No Entry. A status QR does not authorize entry; follow the school’s entry procedure.'
            elif intent == 'pending_id':
                answer = f'{selected["name"]}: status is {selected["status"]}. ' + (selected['pending_reason'] or 'No pending reason has been recorded. Ask staff to confirm any missing requirements.')
            else:
                expected = {'id_release_schedule':'Release','pending_id_schedule':'Pending follow-up','lost_id_schedule':'Lost ID replacement'}[intent]
                if selected['schedule_type'] == expected:
                    answer = f'{selected["name"]}: {expected} is scheduled for {selected["schedule_date"]} at {selected["schedule_time"]} (school local time).'
                else:
                    answer = f'No {expected.lower()} appointment is recorded for {selected["name"]}. Staff can set the date and time using “Update / schedule”. This chat has not booked an appointment.'
        else:
            answer = answers.get(intent, answers['out_of_scope'])
        result = {'intent': intent, 'reply': answer}
        if selected is not None and intent in lookups:
            result.update(student_name=selected['name'], id_status=selected['id_status'],
                          processing_status=selected['status'], qr_url=url_for('qr_image', record_id=selected['id']),
                          scan_url=scan_url(selected['token']))
        return jsonify(result)

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
