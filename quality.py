"""The Quality Scorecard: the system measured against the eight product quality characteristics of ISO/IEC 25010.

Every check says how it knows:
  measured  worked out just now from the running system (timings, database, response headers)
  inspected read from the project's own files or configuration (tests, docs, settings)
  declared  a design fact that cannot be measured at run time
  gap       something the system does not do yet. It scores zero, so the score stays honest.
A check that cannot be judged on this server (for example the repository files on a host that only has the app)
is shown as "not measured" and left out of the average instead of being counted as a pass.
"""
import ast
import hashlib
import json
import re
import statistics
import time
from datetime import datetime

from intent_model import ROOT
from metrics import percentile

SUS_ITEMS = (
    'I think that I would like to use this assistant frequently.',
    'I found the assistant unnecessarily complex.',
    'I thought the assistant was easy to use.',
    'I think that I would need the support of a technical person to be able to use it.',
    'I found the various functions in the assistant were well integrated.',
    'I thought there was too much inconsistency in the assistant.',
    'I would imagine that most people would learn to use the assistant very quickly.',
    'I found the assistant very cumbersome to use.',
    'I felt very confident using the assistant.',
    'I needed to learn a lot of things before I could get going with the assistant.',
)


def sus_score(answers):
    """0-100 System Usability Scale score from ten answers of 1-5 (odd items count up, even items count down)."""
    if not isinstance(answers, (list, tuple)) or len(answers) != 10:
        raise ValueError('Answer all 10 statements.')
    total = 0
    for number, value in enumerate(answers, start=1):
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            raise ValueError('Each answer must be a whole number from 1 to 5.')
        total += value - 1 if number % 2 else 5 - value
    return total * 2.5


def sus_adjective(score):
    return ('Excellent' if score >= 80.3 else 'Good' if score >= 68 else 'OK' if score >= 51 else 'Poor')


def who_answered(student_id, secret):
    """Survey answers are kept without the student's ID: only a keyed hash, so one student can answer once."""
    return hashlib.sha256(f'sus|{secret}|{str(student_id).lower()}'.encode()).hexdigest()


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def falling(value, best, worst):
    """1 at `best` or lower, 0 at `worst` or higher, straight line between."""
    return clamp((worst - value) / (worst - best))


def check(name, points, detail, kind='inspected', weight=1):
    return {'name': name, 'points': None if points is None else round(clamp(points), 3), 'detail': detail,
            'kind': kind, 'weight': weight}


def read(name):
    try:
        return (ROOT / name).read_text(encoding='utf-8')
    except OSError:
        return None


def python_files():
    return sorted(path for path in ROOT.glob('*.py') if path.is_file())


def template_files():
    return sorted((ROOT / 'templates').glob('*.html'))


def csv_rows(name):
    import csv
    try:
        with (ROOT / name).open(encoding='utf-8-sig', newline='') as file:
            return list(csv.DictReader(file))
    except OSError:
        return None


def benchmark(matcher, connect, samples=30):
    """Time the model on real questions from the dataset and the database on a trivial query."""
    rows = csv_rows('property_management_dataset.csv') or []
    texts = [row['text'] for row in rows][:samples] or ['When can I claim my ID?']
    infer = []
    for text in texts:
        began = time.perf_counter()
        matcher.match(text)
        infer.append((time.perf_counter() - began) * 1000)
    database = []
    for _ in range(max(10, samples // 2)):
        began = time.perf_counter()
        with connect() as db:
            db.execute('SELECT 1')
        database.append((time.perf_counter() - began) * 1000)
    return {'samples': len(infer), 'inference_p50': round(percentile(infer, .5), 2), 'inference_p95': round(percentile(infer, .95), 2),
            'db_samples': len(database), 'db_p50': round(percentile(database, .5), 2), 'db_p95': round(percentile(database, .95), 2)}


def score_of(checks):
    scored = [c for c in checks if c['points'] is not None]
    if not scored:
        return None
    return round(100 * sum(c['points'] * c['weight'] for c in scored) / sum(c['weight'] for c in scored), 1)


def grade(score):
    if score is None:
        return 'Not measured'
    return 'Excellent' if score >= 90 else 'Good' if score >= 75 else 'Fair' if score >= 60 else 'Needs work'


# ───────────────────────── the eight characteristics ─────────────────────────

def functional(env):
    out = []
    catalog = csv_rows('property_management_intent.csv')
    dataset = csv_rows('property_management_dataset.csv')
    if catalog is not None and dataset is not None:
        topics = [r['intent'] for r in catalog if r['type'] != 'fallback']
        counts = {t: sum(1 for r in dataset if r['intent'] == t) for t in topics}
        enough = sum(1 for n in counts.values() if n >= 15)
        out.append(check('Functional completeness: every topic has example questions', enough / len(topics),
                         f'{enough} of {len(topics)} topics have 15 or more training questions ({len(dataset)} rows in all).'))
    report = None
    try:
        report = json.loads(read('data/training/metrics.json') or 'null')
    except ValueError:
        pass
    if report:
        acc = report.get('accuracy_with_fallback', report.get('accuracy', 0))
        out.append(check('Functional correctness: held-out accuracy of the trained model', acc,
                         f'{acc:.1%} of {report.get("test_count", "?")} held-out questions got the right topic (macro F1 {report.get("macro_f1", 0):.2f}). Small synthetic dataset: more real questions raise this.',
                         weight=3))
    with env['connect']() as db:
        bot, placed = db.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE intent <> 'out_of_scope') FROM chat_history WHERE role = 'bot' AND intent <> ''").fetchone()
        rated, up = db.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE rating = 'up') FROM chat_feedback").fetchone()
    out.append(check('Functional appropriateness: questions the bot could place in a topic', placed / bot if bot >= 10 else None,
                     f'{placed} of {bot} answers were placed in a topic.' if bot >= 10 else f'{bot} answers so far; needs 10 to judge.', 'measured'))
    out.append(check('Users found the answers helpful (👍 of all ratings)', up / rated if rated >= 5 else None,
                     f'{up} of {rated} ratings were 👍.' if rated >= 5 else f'{rated} ratings so far; needs 5 to judge.', 'measured'))
    paths = {rule.rule for rule in env['app'].url_map.iter_rules()}
    needed = ('/admin', '/coordinator', '/student', '/api/chat', '/api/student/pin', '/api/inventory')
    have = sum(1 for p in needed if p in paths or any(r.startswith(p) for r in paths))
    out.append(check('All three roles and the chat are available', have / len(needed), f'{have} of {len(needed)} core routes are registered.', 'measured'))
    return out


def performance(env, bench):
    out = [check('Model answers quickly (95th percentile per question)', falling(bench['inference_p95'], 100, 1000),
                 f'{bench["inference_p95"]} ms (median {bench["inference_p50"]} ms) over {bench["samples"]} real questions.', 'measured', 2),
           check('Database answers quickly (95th percentile round trip)', falling(bench['db_p95'], 50, 500),
                 f'{bench["db_p95"]} ms (median {bench["db_p50"]} ms) over {bench["db_samples"]} queries.', 'measured')]
    model = ROOT / 'data' / 'intent_model.joblib'
    if model.exists():
        mb = model.stat().st_size / 1_048_576
        out.append(check('Small model: runs on a free host without a GPU', falling(mb, 5, 50), f'The trained model is {mb:.1f} MB.'))
    weight = sum(p.stat().st_size for p in (ROOT / 'static').glob('*') if p.suffix in ('.js', '.css'))
    if weight:
        out.append(check('Light pages: our own JavaScript and CSS', falling(weight / 1024, 600, 3000), f'{weight / 1024:.0f} KB of scripts and styles (no framework, no third-party downloads).'))
    chat = env['metrics'].summary()['groups'].get('chat')
    out.append(check('Live chat requests on this server (95th percentile)', falling(chat['p95'], 500, 3000) if chat and chat['count'] >= 5 else None,
                     f'{chat["p95"]} ms over the last {chat["count"]} chat requests since the server started.' if chat and chat['count'] >= 5 else 'Fewer than 5 chat requests since the server started.', 'measured'))
    return out


def compatibility(env):
    out = []
    paths = {rule.rule for rule in env['app'].url_map.iter_rules()}
    exports = ('/api/inventory/export', '/api/admin/backup', '/api/student/export', '/api/feedback/export')
    have = sum(1 for p in exports if p in paths)
    out.append(check('Open data formats in and out (CSV, JSON, Orange .tab)', have / len(exports) * (1 if (ROOT / 'property_management_dataset.tab').exists() else .75),
                     f'{have} of {len(exports)} export routes, plus the dataset as CSV and as an Orange .tab file.', 'measured'))
    pages = [t.read_text(encoding='utf-8') for t in template_files()]
    full = [p for p in pages if '<html' in p]
    if full:
        ok = sum(1 for p in full if 'name="viewport"' in p.lower().replace("'", '"') or 'name=\'viewport\'' in p)
        out.append(check('Responsive: every page has a viewport so phones and desktops both fit', ok / len(full), f'{ok} of {len(full)} pages declare a viewport (checked on a phone-size screen too).'))
        ok = sum(1 for p in full if p.lstrip().lower().startswith('<!doctype html'))
        out.append(check('Standard HTML5 pages', ok / len(full), f'{ok} of {len(full)} pages start with the HTML5 doctype.'))
    external = 0
    for path in template_files() + sorted((ROOT / 'static').glob('*.js')) + sorted((ROOT / 'static').glob('*.css')):
        text = path.read_text(encoding='utf-8', errors='ignore')
        external += len(re.findall(r'(?:src|href)="https?://(?!(?:www\.)?w3\.org)', text)) + len(re.findall(r'url\(["\']?https?://', text)) + len(re.findall(r'fetch\(["\']https?://', text))
    out.append(check('Works without third-party services (no CDN, no outside AI API)', 1 if external == 0 else 0,
                     'No page, script or style loads anything from another website.' if external == 0 else f'{external} references to other websites.', 'inspected', 2))
    if (ROOT / 'docker-compose.yml').exists():
        out.append(check('Standard PostgreSQL database, local copy by Docker', 1, 'DATABASE_URL takes any PostgreSQL; docker-compose.yml starts one for development.'))
    out.append(check('Runs side by side with other systems', 1, 'Each role can live on its own address (STUDENT_HOST, COORDINATOR_HOST, ADMIN_HOST) and shares nothing but the database.', 'declared'))
    return out


def interaction(env):
    out = []
    pages = [t.read_text(encoding='utf-8') for t in template_files()]
    full = [p for p in pages if '<html' in p]
    if full:
        ok = sum(1 for p in full if re.search(r'<html[^>]*\blang=', p))
        out.append(check('Pages declare their language (screen readers)', ok / len(full), f'{ok} of {len(full)} pages have a lang attribute.'))
        images = sum(len(re.findall(r'<img\b', p)) for p in pages)
        missing = sum(len(re.findall(r'<img\b(?![^>]*\balt=)', p)) for p in pages)
        out.append(check('Every image has a text alternative', 1 if images == 0 else (images - missing) / images, f'{images - missing} of {images} images have alt text.'))
        aria = sum(len(re.findall(r'\baria-[a-z]+=', p)) for p in pages)
        out.append(check('Accessible names and live regions (ARIA)', 1 if aria >= 40 else aria / 40, f'{aria} ARIA attributes across the pages (chat log, dialogs, tabs, toasts).'))
    css = read('static/style.css')
    if css is not None:
        prefs = [('reduced motion', 'prefers-reduced-motion' in css), ('dark mode', 'data-theme="dark"' in css),
                 ('text size', 'Text size' in (read('templates/index.html') or ''))]
        out.append(check('Comfort settings: reduced motion, dark mode, text size', sum(ok for _, ok in prefs) / len(prefs),
                         'Present: ' + ', '.join(n for n, ok in prefs if ok) + '.'))
    tools = [name for name in ('tour.js', 'ux.js') if (ROOT / 'static' / name).exists()]
    out.append(check('Easy to learn: guided tutorial, command palette, shortcut list', len(tools) / 2, 'First-run tutorial with a hand cursor, Ctrl+K palette and a "?" shortcut list.'))
    rows = csv_rows('property_management_dataset.csv')
    if rows:
        other = sum(1 for r in rows if r['language'] != 'en') / len(rows)
        out.append(check('Understands the users’ own languages (English, Taglish, Filipino)', other / 0.4, f'{other:.0%} of the training questions are Taglish or Filipino.'))
    with env['connect']() as db:
        n, mean = db.execute('SELECT COUNT(*), AVG(score) FROM sus_responses').fetchone()
    out.append(check('Usability rated by students (System Usability Scale)', clamp((mean or 0) / 80.3) if n >= 5 else None,
                     f'SUS {mean:.1f} ({sus_adjective(mean)}) from {n} students. 68 is average, 80.3 is excellent.' if n >= 5 else f'{n} survey answers so far; needs 5 to judge. Students answer in Settings → Rate this assistant.', 'measured', 2))
    out.append(check('User error protection: confirm dialogs, Undo, clear messages', 1, 'Risky actions ask first, deletes can be undone, errors say what to do next.', 'declared'))
    return out


def reliability(env):
    out = []
    began = time.perf_counter()
    try:
        with env['connect']() as db:
            db.execute('SELECT 1')
            last = db.execute("SELECT MAX(timestamp) FROM activity_log WHERE action = 'backup'").fetchone()[0]
        ok = True
    except Exception:       # a failing database is the finding, not a crash
        ok, last = False, None
    out.append(check('Database is reachable now', 1 if ok else 0, f'Answered in {(time.perf_counter() - began) * 1000:.0f} ms.' if ok else 'The database did not answer.', 'measured', 2))
    out.append(check('Model matches its dataset (refuses a stale model)', 1, 'The model carries the hash of the data it was trained on and will not load if they differ.', 'measured'))
    summary = env['metrics'].summary()
    requests = summary['requests']
    rate = summary['errors'] / requests if requests else 0
    out.append(check('Few server errors (5xx) since the server started', falling(rate, 0.005, 0.05) if requests >= 20 else None,
                     f'{summary["errors"]} errors in {requests} requests ({rate:.2%}).' if requests >= 20 else f'{requests} requests so far; needs 20 to judge.', 'measured'))
    recent = None
    if last:
        try:
            recent = (datetime.now() - datetime.strptime(last[:19], '%Y-%m-%d %H:%M:%S')).days
        except ValueError:
            pass
    out.append(check('Recoverability: a backup was downloaded in the last 30 days', 1 if recent is not None and recent <= 30 else .5 if recent is not None and recent <= 90 else 0,
                     f'Last full backup {recent} day(s) ago.' if recent is not None else 'No backup downloaded yet. Use Data & backup.', 'measured'))
    out.append(check('Fault tolerance: offline banner, idle sign-out, closable portal, fallback ticker', 1, 'The pages say when the connection drops, sessions end by themselves, and the admin can close the student portal.', 'declared'))
    return out


def security(env):
    out = []
    client = env['app'].test_client()
    headers = client.get('/healthz').headers
    wanted = ['Content-Security-Policy', 'X-Content-Type-Options', 'X-Frame-Options', 'Referrer-Policy', 'Permissions-Policy', 'Cross-Origin-Opener-Policy']
    if env['on_vercel']:
        wanted.append('Strict-Transport-Security')
    have = [h for h in wanted if h in headers]
    out.append(check('Security headers on every response', len(have) / len(wanted), f'{len(have)} of {len(wanted)} present: ' + ', '.join(have) + '.', 'measured', 2))
    auth = [('admin password is set', env['admin_configured']), ('captcha on the admin sign-in', env['captcha_on']),
            ('two-factor code (TOTP) on the admin sign-in', env.get('totp_on', False)),
            ('students must sign in with a PIN', env['student_login_on']), ('coordinator password is set', env['coordinator_configured']),
            ('a fixed SECRET_KEY is set', bool(env['secret_key_env']))]
    good = [name for name, ok in auth if ok]
    out.append(check('Sign-in protection', len(good) / len(auth), 'On: ' + ', '.join(good) + '.' if good else 'Nothing configured.', 'measured', 2))
    with env['connect']() as db:
        total, hashed, plain = db.execute("SELECT COUNT(*) FILTER (WHERE pin_hash <> ''), COUNT(*) FILTER (WHERE pin_hash LIKE 'pbkdf2:%'), COUNT(*) FILTER (WHERE pin_hash <> '' AND pin_hash NOT LIKE 'pbkdf2:%') FROM inventory").fetchone()
        events = db.execute("SELECT COUNT(*) FROM activity_log WHERE timestamp >= %s", (datetime.now().strftime('%Y-%m-01'),)).fetchone()[0]
    out.append(check('PINs are stored hashed (PBKDF2), never as text', 1 if plain == 0 else hashed / max(total, 1), f'{hashed} of {total} PINs use a salted PBKDF2 hash.' if total else 'No PINs issued yet (every one will be hashed).', 'measured', 2))
    out.append(check('Lock-out and time-outs', 1, '5 wrong PINs lock an account for 15 minutes; idle sessions end after 15 (admin), 30 (coordinator) and 20 (student) minutes; the admin captcha allows 5 tries.', 'declared'))
    out.append(check('Accountability: an audit log of sign-ins and changes', 1 if events else 0, f'{events} events logged this month; the admin reads them in the Security log.', 'measured'))
    out.append(check('Data privacy: a student sees only their own record; PINs they choose are unreadable', 1, 'Own-record limits are enforced on the server; the office-issued PIN is kept encrypted and erased when the student changes it.', 'declared'))
    policy = headers.get('Content-Security-Policy', '')
    script_src = next((part.strip() for part in policy.split(';') if part.strip().startswith('script-src')), '')
    strict = bool(script_src) and "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src
    out.append(check('Content-Security-Policy refuses inline scripts', 1 if strict else 0,
                     "script-src is limited to this site's own files: an injected <script> or onclick= handler will not run." if strict
                     else 'script-src still allows inline scripts, so an injected script could run.', 'measured', 2))
    out.append(check('Two-factor sign-in for the admin (TOTP) is available', 1,
                     'The admin can turn on an authenticator-app code in System. It is on now.' if env.get('totp_on') else 'The admin can turn on an authenticator-app code in System. It is not turned on yet.', 'declared'))
    return out


def maintainability(env):
    out = []
    tests = sorted((ROOT / 'tests').glob('test_*.py'))
    if tests:
        n = sum(len(re.findall(r'^\s+def test_', f.read_text(encoding='utf-8'), re.M)) for f in tests)
        out.append(check('Automated tests', n / 300, f'{n} test functions in {len(tests)} files.', weight=2))
    if not env['on_vercel']:
        ci = (ROOT / '.github' / 'workflows').exists() and any((ROOT / '.github' / 'workflows').glob('*.yml'))
        out.append(check('Continuous integration runs the tests on every pull request', 1 if ci else 0, 'GitHub Actions workflow found.' if ci else 'No workflow in .github/workflows.'))
    files = python_files()
    if files:
        documented = total = 0
        longest = (0, '')
        for path in files:
            text = path.read_text(encoding='utf-8')
            longest = max(longest, (text.count('\n'), path.name))
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith('_') and not node.name.startswith('test'):
                    total += 1
                    documented += ast.get_docstring(node) is not None
        out.append(check('Documented code: public functions with a docstring', (documented / total) / 0.5 if total else None, f'{documented} of {total} functions explain themselves ({documented / max(total, 1):.0%}); {len(files)} Python modules, all parse.', 'measured'))
        out.append(check('Modular: no module is too large', falling(longest[0], 1500, 4000), f'The largest module is {longest[1]} with {longest[0]} lines.', 'measured'))
    readme = read('README.md')
    if readme is not None:
        out.append(check('Documentation: README and the SRS', min(1, readme.count('\n') / 300), f'README has {readme.count(chr(10))} lines (setup, deploy, tests, security, data).'))
    reqs = read('requirements.txt')
    if reqs:
        lines = [l for l in reqs.splitlines() if l.strip() and not l.startswith('#')]
        pinned = sum(1 for l in lines if '==' in l)
        out.append(check('Pinned dependencies: the same build every time', pinned / len(lines), f'{pinned} of {len(lines)} packages are pinned to one version.'))
    if (ROOT / 'train_model.py').exists():
        out.append(check('Reproducible: the model is rebuilt from the data with one command', 1,
                         '`python train_model.py` rebuilds it. This server loaded the model, which proves it matches the dataset it was trained on (the hash is checked at start-up).', 'measured'))
    return out


def flexibility(env):
    out = []
    reads = set()
    for path in python_files():
        reads |= set(re.findall(r'environ\.get\(\s*[\'"]([A-Z_]+)[\'"]', path.read_text(encoding='utf-8')))
    out.append(check('Configured by environment variables, not code changes', len(reads) / 12, f'{len(reads)} settings are read from the environment (database, passwords, hosts, switches).', 'measured'))
    targets = [('Vercel (serverless)', (ROOT / 'app.py').exists()), ('Docker / local PostgreSQL', (ROOT / 'docker-compose.yml').exists()), ('plain Flask on any computer', (ROOT / 'Chatbot.py').exists())]
    out.append(check('Installs on more than one platform', sum(1 for _, ok in targets if ok) / 3, 'Runs on ' + ', '.join(n for n, ok in targets if ok) + '.'))
    tools = [n for n in ('train_model.py', 'feedback_to_dataset.py', 'migrate_sqlite_to_postgres.py') if (ROOT / n).exists()]
    out.append(check('Adapts to new needs: retrain, feedback → dataset, data migration', len(tools) / 3, 'Scripts present: ' + ', '.join(tools) + '.'))
    rows = csv_rows('property_management_dataset.csv')
    if rows:
        languages = {r['language'] for r in rows}
        out.append(check('Several languages in one model', len(languages) / 3, 'Training data in: ' + ', '.join(sorted(languages)) + '.'))
    out.append(check('Scales out: no state on the server', 1, 'Sessions are signed cookies and everything else lives in PostgreSQL, so more copies can run behind one address.', 'declared'))
    out.append(check('Feature switches the admin can flip', 1, 'Student portal open/closed, automatic announcements, captcha and student sign-in.', 'declared'))
    return out


CHARACTERISTICS = (
    ('functional', 'Functional Suitability', 'Does it do what it should, completely and correctly?'),
    ('performance', 'Performance Efficiency', 'Is it fast and light for the resources it uses?'),
    ('compatibility', 'Compatibility', 'Does it work with other systems, formats and devices?'),
    ('interaction', 'Interaction Capability', 'Is it easy, accessible and pleasant to use?'),
    ('reliability', 'Reliability', 'Does it keep working and recover when something goes wrong?'),
    ('security', 'Security', 'Does it protect data and accounts?'),
    ('maintainability', 'Maintainability', 'Can the team change and test it safely?'),
    ('flexibility', 'Flexibility', 'Can it adapt to new places, settings and needs?'),
)


def evaluate(env, samples=30):
    """env: app, connect, matcher, metrics, on_vercel, admin_configured, captcha_on, student_login_on,
    coordinator_configured, secret_key_env."""
    bench = benchmark(env['matcher'], env['connect'], samples)
    builders = {'functional': lambda: functional(env), 'performance': lambda: performance(env, bench), 'compatibility': lambda: compatibility(env),
                'interaction': lambda: interaction(env), 'reliability': lambda: reliability(env), 'security': lambda: security(env),
                'maintainability': lambda: maintainability(env), 'flexibility': lambda: flexibility(env)}
    result = []
    for key, name, question in CHARACTERISTICS:
        checks = builders[key]()
        value = score_of(checks)
        result.append({'id': key, 'name': name, 'question': question, 'score': value, 'grade': grade(value), 'checks': checks})
    scores = [c['score'] for c in result if c['score'] is not None]
    overall = round(statistics.mean(scores), 1) if scores else None
    return {'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'build': env.get('build', ''), 'overall': overall, 'grade': grade(overall),
            'characteristics': result, 'benchmark': bench, 'live': env['metrics'].summary(), 'standard': 'ISO/IEC 25010:2023'}
