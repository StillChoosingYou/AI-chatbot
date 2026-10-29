"""The admin panel: the owner's console above the two workspaces.

/admin               sign in with ADMIN_USERNAME / ADMIN_PASSWORD (their own password, not the coordinator's)
Inside the panel     the student link and its QR (the link to hand out), a switch that closes the student portal,
                     ticker news, PIN tools for student sign-in, chatbot insights and the questions it missed,
                     a security log, a full backup, a chat-history purge and system checks.
Switch role          "Open the coordinator workspace" signs the admin in as the coordinator and keeps the admin
                     session, so one click leads back to the panel.
"""
import csv
import io
import json
import platform
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import qrcode
import sklearn
from flask import Response, abort, jsonify, redirect, render_template, request, send_file, session

from student_auth import hash_pin, new_pin

ADMIN_IDLE_SECONDS = 15 * 60
MAX_NEWS = 20
DEFAULT_PUBLIC_URL = 'https://pgpc-ai-chatbot.vercel.app'     # the live site: what the links show when the panel is opened on localhost
LOCAL_HOSTS = ('localhost', '127.0.0.1', '[::1]', '0.0.0.0')
SECURITY_ACTIONS = ('sign in', 'failed sign in', 'pin issued', 'pins issued', 'pin unlocked',
                    'admin sign in', 'admin failed sign in', 'portal', 'news', 'purge', 'backup', 'switch role')


def register_admin(app, ctx):
    """Add the admin routes. ctx carries what lives inside create_app: login, connect, log_activity, settings, etc."""
    login, connect, log = ctx.admin_login, ctx.connect, ctx.log_activity

    def admin_signed_in():
        return not login.enabled or session.get('admin') is True

    def need_admin():
        if not admin_signed_in():
            response = jsonify(error='Sign in to the admin panel first.', sign_in='/admin')
            response.status_code = 401
            return response
        return None

    def address():
        return request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()

    def url_for_role(host, path):
        if host:
            return f'https://{host}{path}'
        return f'{public_base()}{path}'

    def public_base():
        """The address in the links: the one the admin saved, else PUBLIC_BASE_URL, else this site's own address
        (the live site's when the panel is opened on localhost)."""
        saved = (ctx.get_setting('public_url', '') or '').rstrip('/')
        if saved:
            return saved
        if ctx.public_base:
            return ctx.public_base
        if request.host.split(':')[0].lower() in LOCAL_HOSTS:
            return DEFAULT_PUBLIC_URL
        return request.url_root.rstrip('/')

    def links():
        return {'student': url_for_role(ctx.student_host, '/student'),
                'coordinator': url_for_role(ctx.coordinator_host, '/coordinator'),
                'admin': url_for_role(ctx.admin_host, '/admin'),
                'separate_hosts': bool(ctx.student_host and ctx.coordinator_host)}

    # ─── pages ───
    @app.get('/admin', strict_slashes=False)
    def admin_page():
        if not admin_signed_in():
            return render_template('login.html', configured=login.configured, error='', role_title='ADMIN',
                                   action='/admin/login', page_title='Admin sign in', back=False)
        return render_template('admin.html', signed_in_as=login.username if login.enabled else 'local admin')

    @app.post('/admin/login')
    def admin_login_post():
        who = address()
        if login.check(request.form.get('username', ''), request.form.get('password', ''), who):
            session.clear()
            session['admin'] = True
            session['seen'] = time.time()
            session.permanent = True
            log('admin', 'admin sign in', f'from {who}'[:120])
            return redirect('/admin')
        log('admin', 'admin failed sign in', f'from {who}'[:120])
        error = ('Too many wrong tries. Wait a minute and try again.' if login.locked(who)
                 else 'Wrong username or password.')
        return render_template('login.html', configured=login.configured, error=error, role_title='ADMIN',
                               action='/admin/login', page_title='Admin sign in', back=False), 401

    @app.post('/admin/logout')
    def admin_logout():
        session.clear()
        return redirect('/admin')

    @app.post('/admin/switch')
    def admin_switch():
        """Role switch: the admin steps into the coordinator workspace (the admin session is kept)."""
        if not admin_signed_in():
            return redirect('/admin')
        if request.form.get('to') == 'coordinator':
            session['coordinator'] = True
            session['seen'] = time.time()
            log('admin', 'switch role', 'admin opened the coordinator workspace')
            return redirect('/coordinator')
        return redirect('/admin')

    # ─── numbers ───
    @app.get('/api/admin/overview')
    def admin_overview():
        denied = need_admin()
        if denied:
            return denied
        with connect() as db:
            statuses = {row[0]: row[1] for row in db.execute('SELECT status, COUNT(*) FROM inventory GROUP BY 1')}
            students, with_pin, locked = db.execute(
                "SELECT COUNT(*), COUNT(*) FILTER (WHERE pin_hash <> ''), "
                "COUNT(*) FILTER (WHERE pin_locked_until > %s) FROM inventory", (time.time(),)).fetchone()
            open_requests = db.execute("SELECT COUNT(*) FROM student_requests WHERE status IN ('Submitted','In review')").fetchone()[0]
            open_incidents = db.execute("SELECT COUNT(*) FROM incidents WHERE status = 'Open'").fetchone()[0]
            turns, fallbacks = db.execute(
                "SELECT COUNT(*) FILTER (WHERE role = 'bot'), COUNT(*) FILTER (WHERE role = 'bot' AND intent = 'out_of_scope') "
                'FROM chat_history').fetchone()
            up, down = db.execute("SELECT COUNT(*) FILTER (WHERE rating = 'up'), COUNT(*) FILTER (WHERE rating = 'down') FROM chat_feedback").fetchone()
        return jsonify(students=students, statuses=statuses, with_pin=with_pin, without_pin=students - with_pin,
                       locked=locked, open_requests=open_requests, open_incidents=open_incidents,
                       answers=turns, fallbacks=fallbacks, thumbs_up=up, thumbs_down=down,
                       portal_open=ctx.get_setting('student_portal_open', '1') == '1',
                       student_login=ctx.student_login_on, links=links())

    @app.put('/api/admin/public-url')
    def admin_public_url():
        """Save the website address the links are built from. An empty value goes back to the automatic one."""
        denied = need_admin()
        if denied:
            return denied
        value = str((request.get_json(silent=True) or {}).get('url', '')).strip().rstrip('/')
        if value:
            parsed = urlparse(value)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path or parsed.query or len(value) > 200:
                return jsonify(error='Use the site address only, for example https://pgpc-ai-chatbot.vercel.app'), 400
            ctx.set_setting('public_url', value)
        else:
            ctx.delete_setting('public_url')
        log('admin', 'portal', f'public address set to {value or "automatic"}'[:120])
        return jsonify(**links(), public_url=value)

    @app.get('/api/admin/links')
    def admin_links():
        denied = need_admin()
        if denied:
            return denied
        return jsonify(**links(), student_login=ctx.student_login_on, public_url=ctx.get_setting('public_url', '') or '',
                       portal_open=ctx.get_setting('student_portal_open', '1') == '1')

    @app.get('/api/admin/qr.png')
    def admin_qr():
        if not admin_signed_in():
            abort(401)
        which = request.args.get('which', 'student')
        target = links().get(which if which in ('student', 'coordinator') else 'student')
        stream = io.BytesIO()
        code = qrcode.QRCode(box_size=10, border=3, error_correction=qrcode.constants.ERROR_CORRECT_M)
        code.add_data(target)
        code.make(fit=True)
        code.make_image(fill_color='black', back_color='white').save(stream, format='PNG')
        stream.seek(0)
        return send_file(stream, mimetype='image/png')

    @app.post('/api/admin/portal')
    def admin_portal():
        denied = need_admin()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload.get('open'), bool):
            return jsonify(error='Send {"open": true} or {"open": false}.'), 400
        ctx.set_setting('student_portal_open', '1' if payload['open'] else '0')
        log('admin', 'portal', 'student portal opened' if payload['open'] else 'student portal closed')
        return jsonify(open=payload['open'])

    # ─── ticker news ───
    @app.get('/api/admin/news')
    def admin_news_get():
        denied = need_admin()
        if denied:
            return denied
        return jsonify(news=ctx.news_items(), from_database=ctx.get_setting('news', None) is not None, max=MAX_NEWS)

    @app.put('/api/admin/news')
    def admin_news_put():
        denied = need_admin()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        entries = payload.get('news')
        if not isinstance(entries, list) or len(entries) > MAX_NEWS:
            return jsonify(error=f'Send a list of at most {MAX_NEWS} announcements.'), 400
        clean = ctx.clean_news(entries)
        ctx.set_setting('news', json.dumps(clean, ensure_ascii=False))
        log('admin', 'news', f'ticker set to {len(clean)} announcement(s)')
        return jsonify(news=clean)

    @app.delete('/api/admin/news')
    def admin_news_reset():
        denied = need_admin()
        if denied:
            return denied
        ctx.delete_setting('news')
        log('admin', 'news', 'ticker back to data/pgpc_news.json')
        return jsonify(news=ctx.news_items())

    # ─── PIN tools ───
    @app.get('/api/admin/pins')
    def admin_pins():
        denied = need_admin()
        if denied:
            return denied
        now = time.time()
        with connect() as db:
            rows = db.execute("SELECT student_id, name, pin_hash <> '', pin_failures, pin_locked_until "
                              'FROM inventory ORDER BY student_id').fetchall()
        return jsonify(students=[{'student_id': r[0], 'name': r[1], 'has_pin': bool(r[2]), 'failures': r[3],
                                  'locked': r[4] > now, 'locked_minutes': max(0, int((r[4] - now) // 60) + 1) if r[4] > now else 0}
                                 for r in rows])

    @app.post('/api/admin/pins/bulk')
    def admin_pins_bulk():
        """New PINs for every student without one ('missing') or for everyone ('all'). Shown once, in this reply."""
        denied = need_admin()
        if denied:
            return denied
        scope = (request.get_json(silent=True) or {}).get('scope', 'missing')
        if scope not in ('missing', 'all'):
            return jsonify(error='scope must be "missing" or "all".'), 400
        issued = []
        with connect() as db:
            rows = db.execute("SELECT id, student_id, name, year_level, section FROM inventory "
                              + ("WHERE pin_hash = '' " if scope == 'missing' else '') + 'ORDER BY student_id').fetchall()
            for rid, student_id, name, year, section in rows:
                pin = new_pin()
                db.execute('UPDATE inventory SET pin_hash=%s, pin_failures=0, pin_locked_until=0 WHERE id=%s', (hash_pin(pin), rid))
                issued.append({'student_id': student_id, 'name': name, 'year_section': f'{year}{section}', 'pin': pin})
        log('admin', 'pins issued', f'{len(issued)} PIN(s), scope {scope}')
        return jsonify(issued=issued, scope=scope)

    @app.post('/api/admin/pins/unlock')
    def admin_pins_unlock():
        denied = need_admin()
        if denied:
            return denied
        student_id = str((request.get_json(silent=True) or {}).get('student_id', '')).strip()
        with connect() as db:
            changed = db.execute('UPDATE inventory SET pin_failures=0, pin_locked_until=0 WHERE LOWER(student_id)=LOWER(%s)',
                                 (student_id,)).rowcount
        if not changed:
            return jsonify(error='No student with that ID.'), 404
        log('admin', 'pin unlocked', f'sign-in unlocked for {student_id}'[:120])
        return jsonify(unlocked=student_id)

    # ─── chatbot insights ───
    MISSED_SQL = (
        'SELECT u.message, b.intent, b.confidence, b.timestamp, b.workspace_role FROM chat_history b '
        'JOIN LATERAL (SELECT message FROM chat_history c WHERE c.session_id = b.session_id AND c.role = %s '
        'AND c.id < b.id ORDER BY c.id DESC LIMIT 1) u ON TRUE '
        "WHERE b.role = %s AND (b.intent = 'out_of_scope' OR (b.confidence > 0 AND b.confidence < 0.5)) ")

    @app.get('/api/admin/insights')
    def admin_insights():
        denied = need_admin()
        if denied:
            return denied
        since = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
        with connect() as db:
            intents = [{'intent': r[0], 'count': r[1], 'avg_confidence': round(float(r[2] or 0), 2)} for r in db.execute(
                "SELECT intent, COUNT(*), AVG(NULLIF(confidence, 0)) FROM chat_history WHERE role = 'bot' AND intent <> '' "
                'GROUP BY intent ORDER BY 2 DESC LIMIT 20')]
            per_day = [{'day': r[0], 'count': r[1]} for r in db.execute(
                "SELECT SUBSTR(timestamp, 1, 10), COUNT(*) FROM chat_history WHERE role = 'user' AND timestamp >= %s "
                'GROUP BY 1 ORDER BY 1', (since,))]
            missed = [{'question': r[0][:200], 'intent': r[1], 'confidence': round(float(r[2]), 2), 'time': r[3], 'role': r[4]}
                      for r in db.execute(MISSED_SQL + 'ORDER BY b.id DESC LIMIT 60', ('user', 'bot'))]
            reasons = [{'reason': r[0] or 'none', 'count': r[1]} for r in db.execute(
                "SELECT reason, COUNT(*) FROM chat_feedback WHERE rating = 'down' GROUP BY 1 ORDER BY 2 DESC LIMIT 8")]
            by_role = {r[0]: r[1] for r in db.execute("SELECT workspace_role, COUNT(*) FROM chat_history WHERE role = 'user' GROUP BY 1")}
        return jsonify(intents=intents, per_day=per_day, missed=missed, thumbs_down_reasons=reasons, by_role=by_role)

    @app.get('/api/admin/missed.csv')
    def admin_missed_csv():
        """The questions the bot could not place, most asked first: the raw material for the next dataset."""
        if not admin_signed_in():
            abort(401)
        with connect() as db:
            rows = db.execute(
                'SELECT LOWER(TRIM(m.message)), COUNT(*), MAX(m.intent) FROM (' + MISSED_SQL.replace('SELECT u.message, b.intent, b.confidence, b.timestamp, b.workspace_role', 'SELECT u.message, b.intent') +
                ') m GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 500', ('user', 'bot')).fetchall()
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['question', 'times_asked', 'bot_intent', 'correct_intent (fill in)', 'good_answer (fill in)'])
        for question, count, intent in rows:
            writer.writerow([question, count, intent, '', ''])
        log('admin', 'backup', f'missed-questions CSV, {len(rows)} row(s)')
        return Response(out.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition': 'attachment; filename=missed_questions.csv'})

    # ─── security log ───
    @app.get('/api/admin/security')
    def admin_security():
        denied = need_admin()
        if denied:
            return denied
        with connect() as db:
            rows = db.execute('SELECT timestamp, actor_role, action, detail FROM activity_log WHERE action = ANY(%s) '
                              'ORDER BY id DESC LIMIT 120', (list(SECURITY_ACTIONS),)).fetchall()
            failed_today = db.execute("SELECT COUNT(*) FROM activity_log WHERE action IN ('failed sign in','admin failed sign in') "
                                      'AND timestamp >= %s', (datetime.now().strftime('%Y-%m-%d'),)).fetchone()[0]
        return jsonify(events=[{'time': r[0], 'role': r[1], 'action': r[2], 'detail': r[3]} for r in rows],
                       failed_today=failed_today)

    # ─── data tools ───
    @app.get('/api/admin/backup')
    def admin_backup():
        """Every school table as one JSON file. PIN hashes are never included."""
        if not admin_signed_in():
            abort(401)
        include_chat = request.args.get('chat') == '1'
        tables = {'inventory': 'SELECT id, student_id, name, course, section, year_level, status, request_type, pending_reason, '
                               'schedule_type, schedule_date, schedule_time, token, notes, address, contact_number FROM inventory ORDER BY id',
                  'student_requests': 'SELECT * FROM student_requests ORDER BY id', 'incidents': 'SELECT * FROM incidents ORDER BY id',
                  'activity_log': 'SELECT * FROM activity_log ORDER BY id', 'chat_feedback': 'SELECT * FROM chat_feedback ORDER BY id'}
        if include_chat:
            tables['chat_history'] = 'SELECT * FROM chat_history ORDER BY id'
        data = {'exported': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'tables': {}}
        with connect() as db:
            for name, sql in tables.items():
                cursor = db.execute(sql)
                columns = [c.name for c in cursor.description]
                data['tables'][name] = [dict(zip(columns, row)) for row in cursor.fetchall()]
        log('admin', 'backup', 'full JSON backup' + (' with chat history' if include_chat else ''))
        return Response(json.dumps(data, ensure_ascii=False, default=str, indent=1), mimetype='application/json',
                        headers={'Content-Disposition': f'attachment; filename=school_id_backup_{datetime.now():%Y%m%d_%H%M}.json'})

    @app.post('/api/admin/purge')
    def admin_purge():
        """Delete chat history older than N days. dry_run (the default) only counts."""
        denied = need_admin()
        if denied:
            return denied
        payload = request.get_json(silent=True) or {}
        days = payload.get('days')
        if not isinstance(days, int) or isinstance(days, bool) or not 7 <= days <= 3650:
            return jsonify(error='days must be a whole number from 7 to 3650.'), 400
        dry_run = payload.get('dry_run', True) is not False
        cutoff = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        with connect() as db:
            if dry_run:
                count = db.execute('SELECT COUNT(*) FROM chat_history WHERE timestamp < %s', (cutoff,)).fetchone()[0]
            else:
                count = db.execute('DELETE FROM chat_history WHERE timestamp < %s', (cutoff,)).rowcount
        if not dry_run:
            log('admin', 'purge', f'{count} chat message(s) older than {days} days deleted')
        return jsonify(count=count, dry_run=dry_run, cutoff=cutoff)

    @app.get('/api/admin/system')
    def admin_system():
        denied = need_admin()
        if denied:
            return denied
        started = time.perf_counter()
        with connect() as db:
            db.execute('SELECT 1')
            version = db.execute('SHOW server_version').fetchone()[0]
        return jsonify(database={'ok': True, 'version': version, 'latency_ms': round((time.perf_counter() - started) * 1000, 1)},
                       model={'threshold': ctx.matcher.threshold, 'intents': sorted(ctx.matcher.intents)},
                       versions={'python': sys.version.split()[0], 'scikit_learn': sklearn.__version__, 'platform': platform.system()},
                       build=ctx.build,
                       config={'student_login': ctx.student_login_on, 'student_host': ctx.student_host or '(not set)',
                               'coordinator_host': ctx.coordinator_host or '(not set)', 'admin_host': ctx.admin_host or '(not set)',
                               'on_vercel': ctx.on_vercel, 'coordinator_password_set': ctx.coordinator_configured,
                               'admin_password_set': login.configured})
