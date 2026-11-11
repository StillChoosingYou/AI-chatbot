"""The admin panel: the owner's console above the two workspaces.

/admin               sign in with ADMIN_USERNAME / ADMIN_PASSWORD (their own password, not the coordinator's)
Inside the panel     the student link and its QR (the link to hand out), a switch that closes the student portal,
                     ticker news, PIN tools for student sign-in, chatbot insights and the questions it missed,
                     a security log, a full backup, a chat-history purge and system checks.
Switch role          "Open the coordinator workspace" signs the admin in as the coordinator and keeps the admin
                     session, so one click leads back to the panel.
"""
import csv
import os
import secrets
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

import captcha
from chat_support import intent_name
import chat_moderation
import moderation
import quality
import totp
from werkzeug.security import generate_password_hash
from student_auth import HASH_METHOD, hash_pin, new_pin, open_pin, seal_pin
from student_data import format_year_section

ADMIN_IDLE_SECONDS = 15 * 60
CAPTCHA_SECONDS = 5 * 60          # time allowed between the password step and the picture step
CAPTCHA_TRIES = 5
MAX_NEWS = 20
DEFAULT_PUBLIC_URL = 'https://pgpc-ai-chatbot.vercel.app'     # the live site: what the links show when the panel is opened on localhost
LOCAL_HOSTS = ('localhost', '127.0.0.1', '[::1]', '0.0.0.0')
SECURITY_ACTIONS = ('sign in', 'failed sign in', 'pin issued', 'pins issued', 'pin unlocked',
                    'admin sign in', 'admin failed sign in', 'admin failed captcha', 'two-factor', 'quality', 'portal', 'news', 'purge', 'backup', 'switch role',
                    'student sign in', 'student failed sign in', 'student locked', 'student sign out', 'student pin changed')


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
    @app.get('/', endpoint='home')
    def admin_page():
        """The home page is the admin sign-in; signed in, it is the admin panel."""
        if not admin_signed_in():
            return render_template('login.html', configured=login.configured, error='', role_title='ADMIN',
                                   action='/admin/login', page_title='Admin sign in', back=False)
        return render_template('admin.html', signed_in_as=login.username if login.enabled else 'local admin')

    captcha_on = os.environ.get('ADMIN_CAPTCHA', '1').strip() != '0'
    TOTP_SETTING = 'admin_totp'
    totp_allowed = os.environ.get('ADMIN_TOTP', '1').strip() != '0'      # ADMIN_TOTP=0 is the way back in if the phone is lost

    def login_page(error, status=200):
        return render_template('login.html', configured=login.configured, error=error, role_title='ADMIN',
                               action='/admin/login', page_title='Admin sign in', back=False), status

    def clear_captcha():
        for key in ('cap_until', 'cap_nonce', 'cap_seal', 'cap_tries'):
            session.pop(key, None)

    # ─── two-factor (TOTP): the authenticator app's 6-digit code, asked on the same step as the picture ───
    def parse_totp(text):
        try:
            data = json.loads(text or '{}')
        except ValueError:
            return {}
        return data if isinstance(data, dict) and data.get('secret') else {}

    def totp_state():
        return parse_totp(ctx.get_setting(TOTP_SETTING, ''))

    def totp_secret(state):
        return open_pin(state.get('secret', ''), app.secret_key)

    def totp_on():
        return totp_allowed and bool(totp_secret(totp_state()))

    def save_totp(state):
        ctx.set_setting(TOTP_SETTING, json.dumps(state))

    def check_second_factor(code):
        """'code', 'recovery' or '' (refused). A code is good once: its time step is stored and never accepted again.

        The record is read FOR UPDATE and written in the same transaction, so requests made at the same moment
        are checked one after another: only the first can spend a code or a recovery code.
        """
        with ctx.connect() as db:
            row = db.execute('SELECT value FROM app_settings WHERE key = %s FOR UPDATE', (TOTP_SETTING,)).fetchone()
            state = parse_totp(row[0] if row else '')
            secret = totp_secret(state)
            if not secret:
                return ''
            step = totp.verify(secret, code, state.get('last', 0))
            if step:
                state['last'] = step
                answer = 'code'
            else:
                used, rest = totp.use_recovery(code, state.get('recovery', []), app.secret_key)
                if not used:
                    return ''
                state['recovery'] = rest
                answer = 'recovery'
            db.execute('UPDATE app_settings SET value = %s, updated = %s WHERE key = %s',
                       (json.dumps(state), datetime.now().strftime('%Y-%m-%d %H:%M:%S'), TOTP_SETTING))
            return answer

    def second_step():
        return captcha_on or totp_on()

    def captcha_pending():
        return second_step() and time.time() < session.get('cap_until', 0)

    def verify_page(error='', status=200):
        return render_template('admin_captcha.html', error=error, need_captcha=captcha_on, need_code=totp_on()), status

    @app.post('/admin/login')
    def admin_login_post():
        who = address()
        if login.check(request.form.get('username', ''), request.form.get('password', ''), who):
            if second_step():                               # step 2 of 2: the picture and/or the authenticator code
                clear_captcha()
                session['cap_until'] = time.time() + CAPTCHA_SECONDS
                session['cap_tries'] = 0
                return redirect('/admin/verify')
            ctx.sign_in('admin')
            log('admin', 'admin sign in', f'from {who}'[:120])
            return redirect('/')
        log('admin', 'admin failed sign in', f'from {who}'[:120])
        error = ('Too many wrong tries. Wait a minute and try again.' if login.locked(who)
                 else 'Wrong username or password.')
        return login_page(error, 401)

    @app.get('/admin/verify')
    def admin_verify_page():
        if not captcha_pending():
            clear_captcha()
            return redirect('/')
        return verify_page()

    @app.get('/admin/captcha.png')
    def admin_captcha_image():
        if not captcha_pending() or not captcha_on:
            abort(404)
        code = captcha.new_code()
        nonce = secrets.token_hex(8)
        session['cap_nonce'] = nonce
        session['cap_seal'] = captcha.seal(code, nonce, app.secret_key)
        response = Response(captcha.render(code), mimetype='image/png')
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        return response

    @app.post('/admin/verify')
    def admin_verify_post():
        who = address()
        if not captcha_pending():
            clear_captcha()
            return redirect('/')
        how = ''
        picture_ok = (not captcha_on) or captcha.matches(request.form.get('captcha', ''), session.get('cap_seal'), session.get('cap_nonce', ''), app.secret_key)
        if picture_ok:                                      # the code is only looked at once the picture is right
            how = 'none' if not totp_on() else check_second_factor(request.form.get('code', ''))
        if picture_ok and how:
            clear_captcha()
            ctx.sign_in('admin')
            note = {'none': 'captcha passed', 'code': 'captcha and authenticator code passed', 'recovery': 'recovery code used'}[how]
            if not captcha_on and how == 'none':
                note = 'signed in'
            log('admin', 'admin sign in', f'from {who}, {note}'[:120])
            return redirect('/')
        tries = session.get('cap_tries', 0) + 1
        session['cap_tries'] = tries
        session.pop('cap_seal', None)                       # a guess burns the picture: a new one must be fetched
        detail = f'from {who}, try {tries}' + ('' if not picture_ok else ', wrong code')
        log('admin', 'admin failed captcha' if not picture_ok else 'two-factor', detail[:120])
        if tries >= CAPTCHA_TRIES:
            clear_captcha()
            return login_page('Too many wrong tries. Sign in again.', 401)
        message = ('Those letters did not match. Here is a new picture.' if not picture_ok
                   else 'That code was not accepted. Wait for the next one in your app, or use a recovery code.')
        return verify_page(message, 401)

    @app.get('/admin/cancel')
    def admin_verify_cancel():
        clear_captcha()
        return redirect('/')

    @app.post('/admin/logout')
    def admin_logout():
        ctx.sign_out('coordinator')                        # a coordinator workspace opened from this browser ends with it
        ctx.sign_out('admin')
        return redirect('/')

    @app.post('/admin/switch')
    def admin_switch():
        """Role switch: send the admin to the coordinator sign-in (the admin session is kept, the coordinator login is still required)."""
        if not admin_signed_in():
            return redirect('/')
        if request.form.get('to') == 'coordinator':
            ctx.sign_out('coordinator')                    # the coordinator workspace always asks for the coordinator sign-in
            log('admin', 'switch role', 'admin went to the coordinator sign-in')
            return redirect('/coordinator')
        return redirect('/')

    # ─── chat moderation ───
    @app.get('/api/admin/moderation')
    def admin_moderation():
        denied = need_admin()
        if denied:
            return denied
        status = request.args.get('status', 'new')
        return jsonify(**chat_moderation.overview(connect, status if status in ('new', 'urgent', 'review', 'all') else 'new'),
                       model=moderation.model_status())

    @app.post('/api/admin/moderation/report/<int:report_id>')
    def admin_moderation_report(report_id):
        denied = need_admin()
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        action = data.get('action')
        if action not in chat_moderation.REVIEW_ACTIONS:
            return jsonify(error='Choose confirm or dismiss.'), 400
        done = chat_moderation.review_report(connect, report_id, action, str(data.get('note', '')).strip(), 'admin')
        if done is None:
            return jsonify(error='That report no longer exists.'), 404
        log('admin', 'moderation', f'report {report_id} {done["status"]}'[:120])
        return jsonify(**done)

    @app.post('/api/admin/moderation/user')
    def admin_moderation_user():
        denied = need_admin()
        if denied:
            return denied
        data = request.get_json(silent=True) or {}
        action, subject = data.get('action'), str(data.get('subject', ''))[:80]
        if action not in chat_moderation.USER_ACTIONS:
            return jsonify(error='Unknown action.'), 400
        minutes = data.get('minutes', 30)
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 60 * 24 * 30:
            return jsonify(error='Pause for 1 minute up to 30 days.'), 400
        done = chat_moderation.user_action(connect, subject, action, minutes)
        if done is None:
            return jsonify(error='No such person in the moderation list.'), 404
        log('admin', 'moderation', f'{action} {subject}'[:120])
        return jsonify(**done)

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
            signed_today = db.execute('SELECT COUNT(*) FROM inventory WHERE last_signin >= %s', (datetime.now().strftime('%Y-%m-%d'),)).fetchone()[0]
        flagged, suspended = chat_moderation.new_count(connect)
        return jsonify(students=students, statuses=statuses, with_pin=with_pin, without_pin=students - with_pin,
                       locked=locked, open_requests=open_requests, open_incidents=open_incidents,
                       answers=turns, fallbacks=fallbacks, thumbs_up=up, thumbs_down=down,
                       portal_open=ctx.get_setting('student_portal_open', '1') == '1',
                       student_login=ctx.student_login_on, signed_in_today=signed_today, links=links(),
                       moderation_new=flagged, moderation_suspended=suspended)

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

    # ─── two-factor management (inside the panel) ───
    def totp_summary():
        state = totp_state()
        return {'enabled': bool(totp_secret(state)), 'switched_off_by_environment': not totp_allowed,
                'recovery_left': len(state.get('recovery', [])), 'pending': bool(session.get('totp_pending'))}

    def pending_secret():
        return open_pin(session.get('totp_pending', ''), app.secret_key)

    def spaced(secret):
        return ' '.join(secret[i:i + 4] for i in range(0, len(secret), 4))

    @app.get('/api/admin/totp')
    def admin_totp_status():
        denied = need_admin()
        if denied:
            return denied
        return jsonify(**totp_summary())

    @app.post('/api/admin/totp/begin')
    def admin_totp_begin():
        denied = need_admin()
        if denied:
            return denied
        if totp_secret(totp_state()):
            return jsonify(error='Two-factor sign-in is already on. Turn it off first to set it up again.'), 409
        secret = totp.new_secret()
        session['totp_pending'] = seal_pin(secret, app.secret_key)
        return jsonify(secret=spaced(secret), uri=totp.uri(secret, login.username or 'admin', 'School ID Admin'))

    @app.get('/api/admin/totp/qr.png')
    def admin_totp_qr():
        if not admin_signed_in():
            abort(401)
        secret = pending_secret()
        if not secret:
            abort(404)
        stream = io.BytesIO()
        code = qrcode.QRCode(box_size=8, border=3, error_correction=qrcode.constants.ERROR_CORRECT_M)
        code.add_data(totp.uri(secret, login.username or 'admin', 'School ID Admin'))
        code.make(fit=True)
        code.make_image(fill_color='black', back_color='white').save(stream, format='PNG')
        stream.seek(0)
        response = send_file(stream, mimetype='image/png')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.post('/api/admin/totp/enable')
    def admin_totp_enable():
        denied = need_admin()
        if denied:
            return denied
        secret = pending_secret()
        if not secret:
            return jsonify(error='Start the setup first.'), 400
        payload = request.get_json(silent=True) or {}
        step = totp.verify(secret, payload.get('code', ''), 0)
        if not step:
            return jsonify(error='That code is not right. Check the 6 digits in your app and try again.'), 400
        recovery = totp.new_recovery_codes()
        save_totp({'secret': seal_pin(secret, app.secret_key), 'last': step,
                   'recovery': [totp.recovery_hash(item, app.secret_key) for item in recovery]})
        session.pop('totp_pending', None)
        log('admin', 'two-factor', 'authenticator turned on')
        return jsonify(enabled=True, recovery=recovery)

    @app.post('/api/admin/totp/disable')
    def admin_totp_disable():
        denied = need_admin()
        if denied:
            return denied
        if not totp_secret(totp_state()):
            return jsonify(enabled=False)
        payload = request.get_json(silent=True) or {}
        if not check_second_factor(payload.get('code', '')):
            return jsonify(error='Type a current code from your app (or a recovery code) to turn this off.'), 400
        ctx.delete_setting(TOTP_SETTING)
        log('admin', 'two-factor', 'authenticator turned off')
        return jsonify(enabled=False)

    @app.post('/api/admin/totp/recovery')
    def admin_totp_recovery():
        denied = need_admin()
        if denied:
            return denied
        state = totp_state()
        if not totp_secret(state):
            return jsonify(error='Two-factor sign-in is off.'), 400
        payload = request.get_json(silent=True) or {}
        if check_second_factor(payload.get('code', '')) != 'code':
            return jsonify(error='Type a current code from your app to get new recovery codes.'), 400
        state = totp_state()                                 # check_second_factor saved the step: reload before changing
        recovery = totp.new_recovery_codes()
        state['recovery'] = [totp.recovery_hash(item, app.secret_key) for item in recovery]
        save_totp(state)
        log('admin', 'two-factor', 'new recovery codes made')
        return jsonify(recovery=recovery)

    # ─── quality scorecard (ISO/IEC 25010) ───
    def quality_report(deep=False):
        return quality.evaluate({
            'app': app, 'connect': connect, 'matcher': ctx.matcher, 'metrics': app.extensions['metrics'], 'on_vercel': ctx.on_vercel,
            'admin_configured': login.configured, 'captcha_on': captcha_on, 'totp_on': totp_on(), 'student_login_on': ctx.student_login_on,
            'coordinator_configured': ctx.coordinator_configured, 'secret_key_env': os.environ.get('SECRET_KEY', ''), 'build': ctx.build,
        }, samples=120 if deep else 30)

    @app.get('/api/admin/quality')
    def admin_quality():
        denied = need_admin()
        if denied:
            return denied
        deep = request.args.get('deep') == '1'
        report = quality_report(deep)
        if deep:
            log('admin', 'quality', f'scorecard measured: {report["overall"]}')
        return jsonify(report)

    @app.get('/admin/quality-report')
    def admin_quality_page():
        if not admin_signed_in():
            return redirect('/')
        response = app.make_response(render_template('quality_report.html', report=quality_report(True)))
        response.headers['Cache-Control'] = 'no-store'
        log('admin', 'quality', 'printable report opened')
        return response

    # ─── ticker news ───
    @app.get('/api/admin/news')
    def admin_news_get():
        denied = need_admin()
        if denied:
            return denied
        return jsonify(news=ctx.manual_news(), from_database=ctx.get_setting('news', None) is not None, max=MAX_NEWS,
                       auto=ctx.auto_news(), auto_on=ctx.auto_news_on(),
                       auto_forced_off=os.environ.get('AUTO_NEWS', '1').strip() == '0')

    @app.put('/api/admin/news/auto')
    def admin_news_auto():
        denied = need_admin()
        if denied:
            return denied
        on = (request.get_json(silent=True) or {}).get('on')
        if not isinstance(on, bool):
            return jsonify(error='Send {"on": true} or {"on": false}.'), 400
        ctx.set_setting('news_auto', '1' if on else '0')
        log('admin', 'news', 'calendar announcements ' + ('on' if on else 'off'))
        return jsonify(auto_on=on, auto=ctx.auto_news())

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
        return jsonify(news=ctx.manual_news())

    # ─── the coordinator's sign-in PIN ───
    @app.get('/api/admin/coordinator-pin')
    def admin_coordinator_pin():
        denied = need_admin()
        if denied:
            return denied
        mine = bool(ctx.coordinator_pin_hash())
        return jsonify(username=ctx.coordinator_login_username, source='admin' if mine else ('environment' if ctx.coordinator_configured else 'none'),
                       changed=ctx.get_setting('coordinator_pin_changed', '') if mine else '')

    @app.post('/api/admin/coordinator-pin')
    def admin_coordinator_pin_change():
        """A new sign-in PIN for the coordinator, from the panel (no redeploy). Shown once: only a hash is kept.
        Whoever is signed in as the coordinator is signed out within half a minute."""
        denied = need_admin()
        if denied:
            return denied
        wanted = str((request.get_json(silent=True) or {}).get('pin', '')).strip()
        if wanted and not 6 <= len(wanted) <= 64:
            return jsonify(error='The PIN must be 6 to 64 characters, or leave it empty for a random 6-digit PIN.'), 400
        pin = wanted or new_pin()
        ctx.set_setting('coordinator_pin_hash', generate_password_hash(pin, method=HASH_METHOD))
        ctx.set_setting('coordinator_pin_changed', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        log('admin', 'coordinator pin changed', 'new coordinator sign-in PIN set from the admin panel')
        return jsonify(pin=pin, username=ctx.coordinator_login_username)

    @app.delete('/api/admin/coordinator-pin')
    def admin_coordinator_pin_reset():
        """Go back to the password in the server settings (COORDINATOR_PASSWORD)."""
        denied = need_admin()
        if denied:
            return denied
        ctx.delete_setting('coordinator_pin_hash')
        ctx.delete_setting('coordinator_pin_changed')
        log('admin', 'coordinator pin reset', 'back to the server-settings password')
        return jsonify(source='environment' if ctx.coordinator_configured else 'none')

    # ─── PIN tools ───
    @app.get('/api/admin/pins')
    def admin_pins():
        denied = need_admin()
        if denied:
            return denied
        now = time.time()
        with connect() as db:
            rows = db.execute("SELECT student_id, name, pin_hash <> '', pin_failures, pin_locked_until, last_signin, "
                              'signin_count, pin_changed, pin_vault, year_level, section FROM inventory ORDER BY student_id').fetchall()
            today = datetime.now().strftime('%Y-%m-%d')
            recent = [{'time': r[0], 'action': r[1], 'detail': r[2]} for r in db.execute(
                "SELECT timestamp, action, detail FROM activity_log WHERE actor_role = 'student' ORDER BY id DESC LIMIT 15")]
            counts = dict(db.execute(
                "SELECT action, COUNT(*) FROM activity_log WHERE actor_role = 'student' AND timestamp >= %s GROUP BY 1", (today,)).fetchall())
        return jsonify(students=[{'student_id': r[0], 'name': r[1], 'has_pin': bool(r[2]), 'failures': r[3],
                                  'locked': r[4] > now, 'locked_minutes': max(0, int((r[4] - now) // 60) + 1) if r[4] > now else 0,
                                  'last_signin': r[5], 'signins': r[6], 'pin_changed': r[7],
                                  'pin': open_pin(r[8], app.secret_key), 'year_level': r[9], 'section': r[10],
                                  'year_section': format_year_section(r[9], r[10])}
                                 for r in rows],
                       recent=recent, today={'signed_in': counts.get('student sign in', 0),
                                             'failed': counts.get('student failed sign in', 0) + counts.get('student locked', 0),
                                             'pin_changes': counts.get('student pin changed', 0)})

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
                db.execute("UPDATE inventory SET pin_hash=%s, pin_failures=0, pin_locked_until=0, pin_changed='', pin_version=pin_version+1, "
                           'pin_vault=%s WHERE id=%s', (hash_pin(pin), seal_pin(pin, app.secret_key), rid))
                issued.append({'student_id': student_id, 'name': name, 'year_section': f'{year}{section}', 'pin': pin})
        log('admin', 'pins issued', f'{len(issued)} PIN(s), scope {scope}')
        return jsonify(issued=issued, scope=scope)

    @app.post('/api/admin/pins/issue')
    def admin_pin_issue():
        """A new PIN for one student. Shown once in this reply: only a hash is kept, so an existing PIN cannot be read back."""
        denied = need_admin()
        if denied:
            return denied
        student_id = str((request.get_json(silent=True) or {}).get('student_id', '')).strip()
        pin = new_pin()
        with connect() as db:
            row = db.execute("UPDATE inventory SET pin_hash=%s, pin_failures=0, pin_locked_until=0, pin_changed='', pin_version=pin_version+1, "
                             'pin_vault=%s WHERE LOWER(student_id)=LOWER(%s) RETURNING student_id, name',
                             (hash_pin(pin), seal_pin(pin, app.secret_key), student_id)).fetchone()
        if row is None:
            return jsonify(error='No student with that ID.'), 404
        log('admin', 'pins issued', f'1 PIN for {row[0]}'[:120])
        return jsonify(pin=pin, student_id=row[0], name=row[1])

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
            intents = [{'intent': r[0], 'label': intent_name(r[0]), 'count': r[1], 'avg_confidence': round(float(r[2] or 0), 2)} for r in db.execute(
                "SELECT intent, COUNT(*), AVG(NULLIF(confidence, 0)) FROM chat_history WHERE role = 'bot' AND intent <> '' "
                'GROUP BY intent ORDER BY 2 DESC LIMIT 20')]
            per_day = [{'day': r[0], 'count': r[1]} for r in db.execute(
                "SELECT SUBSTR(timestamp, 1, 10), COUNT(*) FROM chat_history WHERE role = 'user' AND timestamp >= %s "
                'GROUP BY 1 ORDER BY 1', (since,))]
            missed = [{'question': r[0][:200], 'intent': r[1], 'label': intent_name(r[1]), 'confidence': round(float(r[2]), 2), 'time': r[3], 'role': r[4]}
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
            failed_today = db.execute("SELECT COUNT(*) FROM activity_log WHERE action IN ('failed sign in','admin failed sign in','student failed sign in','student locked') "
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
