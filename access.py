"""Local accounts, sessions and role enforcement for the school ID workspace."""
import secrets
from datetime import timedelta
from pathlib import Path

import click
from flask import g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

ROLES = ('student', 'coordinator', 'guard')
ROLE_LABELS = {'student': 'Student', 'coordinator': 'Property management coordinator', 'guard': 'Guard'}


def install_access(app, connect):
    key_path = Path(app.config['DATABASE']).parent / '.session-key'
    try:
        with key_path.open('x') as key_file:
            key_path.chmod(0o600)
            key_file.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    app.config.update(SECRET_KEY=key_path.read_text().strip(), SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE='Lax', PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('student','coordinator','guard')),
            record_id INTEGER REFERENCES inventory(id),
            CHECK((role='student' AND record_id IS NOT NULL) OR (role!='student' AND record_id IS NULL)))''')

    def csrf_token():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        return session['csrf']

    app.jinja_env.globals.update(csrf_token=csrf_token, role_labels=ROLE_LABELS)

    @app.before_request
    def authorize():
        g.user = None
        if session.get('account_id'):
            with connect() as db:
                row = db.execute('SELECT id,username,role,record_id FROM accounts WHERE id=?', (session['account_id'],)).fetchone()
            g.user = dict(row) if row else None
        protected = request.path.startswith(('/api/', '/qr/', '/sticker/')) or request.path == '/' or request.path.startswith('/student/')
        if protected and not g.user:
            if request.path.startswith('/api/'):
                return jsonify(error='Sign in to use the ID workspace.'), 401
            return redirect(url_for('login',next=request.path if request.path.startswith('/student/') and '/id/' not in request.path else ''))
        if request.path.startswith('/student/') and g.user['role'] not in ('guard','coordinator'):
            return 'Only signed-in guards or staff can view QR scan details. Students can use their own ID workspace.',403
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
            if not session.get('csrf') or not secrets.compare_digest(session['csrf'], supplied):
                return jsonify(error='Refresh the page and try again.'), 400
            student_booking = request.endpoint == 'reschedule_lost_id' and g.user['role'] == 'student'
            staff_claim=request.endpoint=='claim_found_id' and g.user['role']=='guard'
            if request.path.startswith('/api/inventory') and g.user['role'] != 'coordinator' and not student_booking and not staff_claim:
                return jsonify(error='Only the property management coordinator can change records.'), 403
            if request.path.startswith('/api/pickup-slots') and g.user['role'] != 'coordinator':
                return jsonify(error='Only the coordinator can publish pickup availability.'), 403

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = ''
        if request.method == 'POST':
            with connect() as db:
                user = db.execute('SELECT * FROM accounts WHERE username=?', (request.form.get('username', '').strip().casefold(),)).fetchone()
            if user and check_password_hash(user['password_hash'], request.form.get('password', '')):
                session.clear()
                session['account_id'] = user['id']
                session.permanent = True
                csrf_token()
                next_path=request.args.get('next','')
                return redirect(next_path if next_path.startswith('/student/') and not next_path.startswith('//') else url_for('home'))
            error = 'Incorrect username or password.'
        return render_template('login.html', error=error), 401 if error else 200

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.cli.command('create-account')
    @click.option('--username', prompt=True)
    @click.option('--role', type=click.Choice(ROLES), prompt=True)
    @click.option('--student-id', default=None, help='Required for a student account; must exist in inventory.')
    @click.option('--password', prompt=True, hide_input=True, confirmation_prompt=True)
    def create_account(username, role, student_id, password):
        """Provision a local account. Run on the coordinator's computer."""
        username = username.strip().casefold()
        if not username or len(username) > 100 or len(password) < 10:
            raise click.ClickException('Use a username of 1–100 characters and a password of at least 10 characters.')
        with connect() as db:
            if db.execute('SELECT 1 FROM accounts WHERE username=?', (username,)).fetchone():
                raise click.ClickException('That username already exists.')
            row = db.execute('SELECT id FROM inventory WHERE student_id=?', (student_id,)).fetchone() if student_id else None
            if role == 'student' and not row:
                raise click.ClickException('Provide --student-id for an existing inventory record.')
            if role != 'student' and student_id:
                raise click.ClickException('Only student accounts use --student-id.')
            db.execute('INSERT INTO accounts (username,password_hash,role,record_id) VALUES (?,?,?,?)',
                       (username, generate_password_hash(password, method='pbkdf2:sha256:600000'), role, row['id'] if row else None))
        click.echo(f'Created {ROLE_LABELS[role]} account: {username}')

    def can_read(row):
        return g.user['role'] != 'student' or g.user['record_id'] == row['id']

    return can_read
