"""Chat moderation with memory: what happens to a person after a message is flagged (moderation.py judges the message).

The ladder counts strikes in the last 30 days (a strike = a violation of severity 2 or 3 that the administrator has not
dismissed; a notice, severity 1, never counts):

  1st strike   warning
  2nd strike   chat paused 5 minutes
  3rd strike   chat paused 30 minutes
  4th strike   chat paused 24 hours
  5th strike   suspended until the administrator reviews it
  severe       a threat or sexual harassment pauses chat for 24 hours at once; a second severe one suspends

A suspension is never final by itself: it waits for the administrator, who can lift it, keep it (uphold) or dismiss the
violation that caused it. Every flagged message is stored as a report (table `violations`) with the rude words masked.
The coordinator signs in with one shared account, so a flagged coordinator message is recorded but never blocks the office.
"""
import time
from datetime import datetime, timedelta

WINDOW_DAYS = 30
LADDER = {1: ('warning', 0), 2: ('restricted', 5 * 60), 3: ('restricted', 30 * 60), 4: ('restricted', 24 * 3600)}
SUSPEND_AT = 5
SEVERE_PAUSE = 24 * 3600
STAMP = '%Y-%m-%d %H:%M:%S'
REVIEW_ACTIONS = ('confirm', 'dismiss')
USER_ACTIONS = ('lift', 'restrict', 'suspend', 'uphold', 'reset')


def decide(strikes, severe_before, severity):
    """('warning' | 'restricted' | 'suspended', pause in seconds) for a new strike. `strikes` includes the new one."""
    if severity >= 3:
        if severe_before or strikes >= SUSPEND_AT:
            return 'suspended', 0
        return 'restricted', SEVERE_PAUSE
    if strikes >= SUSPEND_AT:
        return 'suspended', 0
    return LADDER.get(strikes, LADDER[4])


def subject_for(workspace, own, session_id):
    """(subject key, label, enforce). Students are known by their student ID; the shared coordinator account is only logged."""
    if own is not None:
        return f'student:{own["student_id"].lower()}', f'{own["name"]} · {own["student_id"]}', True
    if workspace == 'coordinator':
        return 'coordinator', 'Coordinator (shared sign-in)', False
    return f'session:{(session_id or "none")[:40]}', 'Chat visitor (not signed in)', True


def minutes_text(seconds):
    minutes = max(1, round(seconds / 60))
    if minutes >= 120:
        return f'{round(minutes / 60)} hours'
    return f'{minutes} minute{"s" if minutes != 1 else ""}'


def _cutoff(user_reset_at, now_ts):
    start = datetime.fromtimestamp(now_ts) - timedelta(days=WINDOW_DAYS)
    return max(start.strftime(STAMP), user_reset_at or '')


def _strikes(db, subject, cutoff):
    row = db.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE severity >= 3) FROM violations "
        "WHERE subject = %s AND severity >= 2 AND status <> 'dismissed' AND timestamp >= %s", (subject, cutoff)).fetchone()
    return int(row[0]), int(row[1])


def gate(connect, subject, now_ts=None):
    """None when the person may chat, else {'state': 'restricted'|'suspended', 'until': seconds since epoch or 0}."""
    now_ts = time.time() if now_ts is None else now_ts
    with connect() as db:
        row = db.execute('SELECT state, restricted_until FROM moderation_users WHERE subject = %s', (subject,)).fetchone()
    if row is None:
        return None
    if row['state'] == 'suspended':
        return {'state': 'suspended', 'until': 0}
    if row['state'] == 'restricted' and row['restricted_until'] > now_ts:
        return {'state': 'restricted', 'until': row['restricted_until']}
    return None


def record(connect, subject, label, workspace, session_id, verdict, excerpt, enforce, now_ts=None):
    """Store one flagged message as a report and decide what happens to the person. Returns the outcome.

    The person's row is locked for the whole decision, so two flagged messages sent together are counted one by one.
    """
    now_ts = time.time() if now_ts is None else now_ts
    stamp = datetime.fromtimestamp(now_ts).strftime(STAMP)
    severity = int(verdict['severity'])
    with connect() as db:
        db.execute('INSERT INTO moderation_users (subject, subject_label, updated) VALUES (%s, %s, %s) '
                   'ON CONFLICT (subject) DO NOTHING', (subject, label, stamp))
        user = db.execute('SELECT * FROM moderation_users WHERE subject = %s FOR UPDATE', (subject,)).fetchone()
        before, severe_before = _strikes(db, subject, _cutoff(user['reset_at'], now_ts))
        seconds = 0
        if severity <= 1:
            action, strikes = 'notice', before
        else:
            strikes = before + 1
            action, seconds = decide(strikes, severe_before, severity)
            if not enforce:
                action, seconds = 'logged', 0
        report = db.execute(
            'INSERT INTO violations (subject, subject_label, workspace_role, session_id, categories, severity, excerpt, source, '
            'score, action, strikes, timestamp) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',
            (subject, label, workspace, (session_id or '')[:120], ','.join(verdict['categories']), severity, excerpt[:1000],
             verdict.get('source') or '', verdict.get('score'), action, strikes, stamp)).fetchone()[0]
        if action == 'restricted':
            db.execute("UPDATE moderation_users SET state = 'restricted', restricted_until = %s, review = '', subject_label = %s, "
                       'updated = %s WHERE subject = %s', (now_ts + seconds, label, stamp, subject))
        elif action == 'suspended':
            db.execute("UPDATE moderation_users SET state = 'suspended', restricted_until = 0, review = 'pending', "
                       'subject_label = %s, updated = %s WHERE subject = %s', (label, stamp, subject))
        else:
            db.execute('UPDATE moderation_users SET subject_label = %s, updated = %s WHERE subject = %s', (label, stamp, subject))
    return {'action': action, 'strikes': strikes, 'seconds': seconds, 'until': now_ts + seconds if seconds else 0,
            'severity': severity, 'report_id': report, 'categories': list(verdict['categories'])}


# ─────────────────────────── what the person is told ───────────────────────────
RULES = 'no offensive words, insults, threats or harassment'


def reply_for(outcome):
    """The chat answer for a flagged message."""
    action = outcome['action']
    if action == 'notice':
        return ('I know waiting for an ID can be frustrating, and I am here to help. Please keep our chat respectful. '
                'Pakiusap, igalang po natin ang isa’t isa. This was only a friendly reminder.')
    if action == 'warning':
        return (f'⚠️ Warning: that message goes against the chat rules ({RULES}). This is your first warning. '
                'If it happens again your chat will be paused, and every violation is reported to the administrator.')
    if action == 'restricted' and outcome['severity'] >= 3:
        return ('🚫 That message contains a threat or serious harassment. Your chat is paused for '
                f'{minutes_text(outcome["seconds"])} and the administrator has been told. '
                'Please visit the Property Management Office if you need help with your ID.')
    if action == 'restricted':
        return (f'⏸ Your chat is paused for {minutes_text(outcome["seconds"])} because the chat rules ({RULES}) were broken '
                f'again (strike {outcome["strikes"]}). The administrator has been told. The office can still help you in person.')
    if action == 'suspended':
        return ('🚫 Your chat access is suspended while the administrator reviews your reports. '
                'Please visit the Property Management Office. The suspension can be lifted after the review.')
    return f'⚠️ That message goes against the chat rules ({RULES}). It was recorded for the administrator.'   # logged


def blocked_reply(blocked, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    if blocked['state'] == 'suspended':
        return ('🚫 Your chat access is suspended while the administrator reviews your reports. '
                'Please visit the Property Management Office.')
    return f'⏸ Your chat is paused for another {minutes_text(max(60, blocked["until"] - now_ts))}. You can use the rest of the portal meanwhile.'


CRISIS_REPLY = ('I am really sorry you are feeling this way. You do not have to carry it alone: please talk to someone you trust or '
                'to the school guidance office as soon as you can. If you are in danger right now, call 911, the national '
                'emergency number in the Philippines. I am only a school ID assistant, but I am glad you told me.')


# ─────────────────────────── for the administrator ───────────────────────────
def overview(connect, status='new', limit=150, now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    since = (datetime.fromtimestamp(now_ts) - timedelta(days=WINDOW_DAYS)).strftime(STAMP)
    week = (datetime.fromtimestamp(now_ts) - timedelta(days=7)).strftime(STAMP)
    two_weeks = (datetime.fromtimestamp(now_ts) - timedelta(days=13)).strftime('%Y-%m-%d')
    where = {'new': "WHERE status = 'new' AND severity >= 2", 'urgent': "WHERE status = 'new' AND severity >= 3",
             'review': "WHERE status = 'new' AND source = 'model'", 'all': ''}.get(status, "WHERE status = 'new' AND severity >= 2")
    with connect() as db:
        reports = [dict(r) for r in db.execute(f'SELECT * FROM violations {where} ORDER BY id DESC LIMIT %s', (limit,))]
        users = [dict(r) for r in db.execute(
            'SELECT u.subject, u.subject_label, u.state, u.restricted_until, u.review, u.reset_at, '
            "(SELECT COUNT(*) FROM violations v WHERE v.subject = u.subject AND v.severity >= 2 AND v.status <> 'dismissed' "
            ' AND v.timestamp >= %s AND v.timestamp >= u.reset_at) AS strikes, '
            '(SELECT COUNT(*) FROM violations v WHERE v.subject = u.subject) AS total, '
            '(SELECT MAX(v.timestamp) FROM violations v WHERE v.subject = u.subject) AS last, '
            "(SELECT COUNT(*) FROM student_requests r WHERE r.request_type = 'Chat review' AND r.status IN ('Submitted', 'In review') "
            " AND u.subject = 'student:' || LOWER(r.student_id)) AS appeals "
            "FROM moderation_users u ORDER BY (u.state = 'suspended') DESC, strikes DESC, last DESC NULLS LAST LIMIT 200", (since,))]
        total, new, new_strikes, new_urgent, new_review, last7 = db.execute(
            "SELECT COUNT(*), COUNT(*) FILTER (WHERE status = 'new'), COUNT(*) FILTER (WHERE status = 'new' AND severity >= 2), "
            "COUNT(*) FILTER (WHERE status = 'new' AND severity >= 3), COUNT(*) FILTER (WHERE status = 'new' AND source = 'model'), "
            'COUNT(*) FILTER (WHERE timestamp >= %s) FROM violations', (week,)).fetchone()
        categories = {r[0]: r[1] for r in db.execute(
            "SELECT c, COUNT(*) FROM violations, unnest(string_to_array(categories, ',')) AS c WHERE c <> '' GROUP BY c ORDER BY 2 DESC")}
        days = {r[0]: r[1] for r in db.execute(
            'SELECT substr(timestamp, 1, 10), COUNT(*) FROM violations WHERE substr(timestamp, 1, 10) >= %s GROUP BY 1', (two_weeks,))}
    for user in users:
        user['pause_left'] = max(0, int(user['restricted_until'] - now_ts)) if user['state'] == 'restricted' else 0
        if user['state'] == 'restricted' and not user['pause_left']:
            user['state'] = 'ok'                                       # the pause is over
    series = []
    for offset in range(13, -1, -1):
        day = (datetime.fromtimestamp(now_ts) - timedelta(days=offset)).strftime('%Y-%m-%d')
        series.append({'day': day, 'count': days.get(day, 0)})
    return {'reports': reports, 'users': users,
            'stats': {'total': total, 'new': new, 'needs_attention': new_strikes, 'urgent': new_urgent, 'to_review': new_review, 'last_7_days': last7,
                      'restricted_now': sum(1 for u in users if u['state'] == 'restricted'),
                      'suspended_pending': sum(1 for u in users if u['state'] == 'suspended' and u['review'] == 'pending'),
                      'suspended_upheld': sum(1 for u in users if u['state'] == 'suspended' and u['review'] == 'upheld'),
                      'by_category': categories, 'by_day': series}}


def new_count(connect):
    """Reports that still need the administrator's eyes (for the tab's badge)."""
    with connect() as db:
        flagged, suspended = db.execute(
            "SELECT (SELECT COUNT(*) FROM violations WHERE status = 'new' AND severity >= 2), "
            "(SELECT COUNT(*) FROM moderation_users WHERE state = 'suspended' AND review = 'pending')").fetchone()
    return int(flagged), int(suspended)


def review_report(connect, report_id, action, note, who, now_ts=None):
    """Confirm a report or dismiss it as a false alarm (a dismissed strike stops counting). None when there is no such report."""
    now_ts = time.time() if now_ts is None else now_ts
    stamp = datetime.fromtimestamp(now_ts).strftime(STAMP)
    status = {'confirm': 'confirmed', 'dismiss': 'dismissed'}[action]
    with connect() as db:
        row = db.execute('SELECT subject FROM violations WHERE id = %s', (report_id,)).fetchone()
        if row is None:
            return None
        subject = row['subject']
        db.execute('SELECT 1 FROM moderation_users WHERE subject = %s FOR UPDATE', (subject,))
        db.execute('UPDATE violations SET status = %s, reviewed_by = %s, reviewed_at = %s, note = %s WHERE id = %s',
                   (status, who, stamp, note[:300], report_id))
        if action == 'dismiss':
            _settle_after_dismissal(db, subject, now_ts, stamp)
        return {'id': report_id, 'status': status, 'subject': subject}


def _settle_after_dismissal(db, subject, now_ts, stamp):
    user = db.execute('SELECT * FROM moderation_users WHERE subject = %s', (subject,)).fetchone()
    if user is None or user['state'] == 'ok' or (user['state'] == 'suspended' and user['review'] == 'upheld'):
        return
    strikes, severe = _strikes(db, subject, _cutoff(user['reset_at'], now_ts))
    if strikes == 0:
        wanted = ('ok', 0)
    else:
        kind, seconds = decide(strikes, max(severe - 1, 0), 3 if severe else 2)
        wanted = ('ok', 0) if kind == 'warning' else (kind, seconds)
    if wanted[0] == 'ok':
        db.execute("UPDATE moderation_users SET state = 'ok', restricted_until = 0, review = 'lifted', updated = %s WHERE subject = %s",
                   (stamp, subject))
    elif wanted[0] == 'restricted' and user['state'] == 'suspended':
        db.execute("UPDATE moderation_users SET state = 'restricted', restricted_until = %s, review = 'lifted', updated = %s WHERE subject = %s",
                   (now_ts + wanted[1], stamp, subject))


def user_action(connect, subject, action, minutes=0, now_ts=None):
    """An administrator's decision about a person. Returns the new state, or None when the person is unknown."""
    now_ts = time.time() if now_ts is None else now_ts
    stamp = datetime.fromtimestamp(now_ts).strftime(STAMP)
    with connect() as db:
        row = db.execute('SELECT * FROM moderation_users WHERE subject = %s FOR UPDATE', (subject,)).fetchone()
        if row is None:
            return None
        if action == 'lift':
            db.execute("UPDATE moderation_users SET state = 'ok', restricted_until = 0, review = 'lifted', updated = %s WHERE subject = %s", (stamp, subject))
        elif action == 'restrict':
            db.execute("UPDATE moderation_users SET state = 'restricted', restricted_until = %s, review = '', updated = %s WHERE subject = %s",
                       (now_ts + max(1, int(minutes)) * 60, stamp, subject))
        elif action == 'suspend':
            db.execute("UPDATE moderation_users SET state = 'suspended', restricted_until = 0, review = 'pending', updated = %s WHERE subject = %s", (stamp, subject))
        elif action == 'uphold':
            if row['state'] != 'suspended':
                return {'subject': subject, 'state': row['state'], 'unchanged': True}
            db.execute("UPDATE moderation_users SET review = 'upheld', updated = %s WHERE subject = %s", (stamp, subject))
        elif action == 'reset':
            db.execute("UPDATE moderation_users SET reset_at = %s, state = 'ok', restricted_until = 0, review = 'lifted', updated = %s WHERE subject = %s",
                       (stamp, stamp, subject))
        state = db.execute('SELECT state, review FROM moderation_users WHERE subject = %s', (subject,)).fetchone()
    return {'subject': subject, 'state': state['state'], 'review': state['review']}
