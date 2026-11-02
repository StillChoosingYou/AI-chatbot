import os
from unittest.mock import patch

import quality
from pg_support import DatabaseTestCase

ENV = {'STUDENT_LOGIN': '1', 'ADMIN_PASSWORD': 'admin-pass', 'COORDINATOR_PASSWORD': 'coord-pass'}
ALEX = 'P202400101'


class ScoringTests(DatabaseTestCase):
    def test_sus_score_follows_the_standard(self):
        self.assertEqual(quality.sus_score([5, 1] * 5), 100)
        self.assertEqual(quality.sus_score([1, 5] * 5), 0)
        self.assertEqual(quality.sus_score([3] * 10), 50)
        self.assertEqual(quality.sus_score([4, 2, 4, 2, 4, 2, 4, 2, 4, 2]), 75)

    def test_sus_score_rejects_bad_answers(self):
        for bad in (None, [], [3] * 9, [3] * 11, [0] * 10, [6] * 10, ['3'] * 10, [True] * 10, [3.5] * 10):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    quality.sus_score(bad)

    def test_adjectives(self):
        self.assertEqual([quality.sus_adjective(n) for n in (90, 70, 55, 30)], ['Excellent', 'Good', 'OK', 'Poor'])

    def test_survey_identity_is_hashed_and_stable(self):
        one = quality.who_answered('P1', 'k')
        self.assertEqual(one, quality.who_answered('p1', 'k'))
        self.assertNotEqual(one, quality.who_answered('P1', 'other'))
        self.assertNotIn('P1', one)

    def test_scores_are_weighted_and_skip_unmeasured_checks(self):
        checks = [quality.check('a', 1, ''), quality.check('b', 0, '', weight=3), quality.check('c', None, '')]
        self.assertEqual(quality.score_of(checks), 25.0)
        self.assertIsNone(quality.score_of([quality.check('c', None, '')]))
        self.assertEqual([quality.grade(n) for n in (95, 80, 65, 40, None)], ['Excellent', 'Good', 'Fair', 'Needs work', 'Not measured'])
        self.assertEqual(quality.falling(50, 100, 1000), 1)
        self.assertEqual(quality.falling(1000, 100, 1000), 0)
        self.assertAlmostEqual(quality.falling(550, 100, 1000), .5)


class HealthAndHeadersTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()
        self.client = self.app.test_client()

    def test_healthz_is_public_and_says_what_is_up(self):
        response = self.client.get('/healthz')
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json['status'], response.json['database'], response.json['model']), ('ok', True, True))
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertNotIn('password', response.get_data(as_text=True).lower())

    def test_every_response_carries_the_security_headers(self):
        for path in ('/healthz', '/student', '/coordinator', '/static/ux.js'):
            headers = self.client.get(path).headers
            with self.subTest(path=path):
                csp = headers['Content-Security-Policy']
                for part in ("default-src 'self'", "object-src 'none'", "frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'"):
                    self.assertIn(part, csp)
                self.assertNotIn('http', csp)                       # no outside source is trusted
                self.assertEqual(headers['Cross-Origin-Opener-Policy'], 'same-origin')
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')

    def test_requests_are_timed_but_polling_and_static_files_are_not(self):
        metrics = self.app.extensions['metrics']
        self.client.get('/student')
        self.client.get('/static/ux.js')
        self.client.get('/healthz')
        summary = metrics.summary()
        self.assertEqual(summary['requests'], 1)
        self.assertEqual(list(summary['groups']), ['page'])
        self.assertEqual(summary['errors'], 0)


class ScorecardTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()
        self.admin = self.app.test_client()
        self.admin.post('/admin/login', data={'username': 'Admin', 'password': 'admin-pass'})
        self.coordinator = self.app.test_client()
        self.coordinator.post('/coordinator/login', data={'username': 'Coordinator', 'password': 'coord-pass'})

    def student(self):
        pin = self.admin.post('/api/admin/pins/issue', json={'student_id': ALEX}).json['pin']
        client = self.app.test_client()
        client.post('/student/login', data={'student_id': ALEX, 'pin': pin})
        return client

    def test_the_scorecard_is_for_the_admin_only(self):
        for client in (self.app.test_client(), self.coordinator):
            self.assertEqual(client.get('/api/admin/quality').status_code, 401)
            self.assertEqual(client.get('/admin/quality-report').status_code, 302)

    def test_all_eight_characteristics_are_scored_with_evidence(self):
        report = self.admin.get('/api/admin/quality').json
        self.assertEqual([c['name'] for c in report['characteristics']],
                         ['Functional Suitability', 'Performance Efficiency', 'Compatibility', 'Interaction Capability',
                          'Reliability', 'Security', 'Maintainability', 'Flexibility'])
        self.assertTrue(0 <= report['overall'] <= 100)
        for c in report['characteristics']:
            with self.subTest(c['name']):
                self.assertTrue(c['checks'])
                self.assertTrue(c['score'] is None or 0 <= c['score'] <= 100)
                for check in c['checks']:
                    self.assertIn(check['kind'], ('measured', 'inspected', 'declared', 'gap'))
                    self.assertTrue(check['points'] is None or 0 <= check['points'] <= 1)
                    self.assertTrue(check['detail'])

    def test_a_gap_scores_zero_instead_of_being_hidden(self):
        gap = quality.check('Not built yet', 0, 'Planned.', 'gap')
        self.assertEqual(gap['points'], 0)
        self.assertEqual(gap['kind'], 'gap')

    def test_security_reports_the_strict_policy_and_two_factor(self):
        security = next(c for c in self.admin.get('/api/admin/quality').json['characteristics'] if c['id'] == 'security')
        by_name = {k['name']: k for k in security['checks']}
        csp = by_name['Content-Security-Policy refuses inline scripts']
        self.assertEqual(csp['points'], 1)
        self.assertIn('Two-factor sign-in for the admin (TOTP) is available', by_name)
        self.assertFalse([k for k in security['checks'] if k['kind'] == 'gap'])

    def test_measured_timings_are_real_numbers(self):
        report = self.admin.get('/api/admin/quality').json
        bench = report['benchmark']
        self.assertGreater(bench['samples'], 0)
        self.assertGreater(bench['inference_p95'], 0)
        self.assertGreater(bench['db_p95'], 0)
        deep = self.admin.get('/api/admin/quality?deep=1').json['benchmark']
        self.assertGreater(deep['samples'], bench['samples'])

    def test_too_little_data_is_not_counted_as_a_pass_or_a_fail(self):
        interaction = next(c for c in self.admin.get('/api/admin/quality').json['characteristics'] if c['id'] == 'interaction')
        sus = next(k for k in interaction['checks'] if 'System Usability' in k['name'])
        self.assertIsNone(sus['points'])
        self.assertIn('needs 5', sus['detail'])

    def test_five_answers_turn_the_usability_check_into_a_score(self):
        database = self.app.extensions['database']
        with database.connect() as db:
            for n in range(5):
                db.execute("INSERT INTO sus_responses (who, answers, score, timestamp) VALUES (%s, '[]', %s, '')", (f'w{n}', 80))
        interaction = next(c for c in self.admin.get('/api/admin/quality').json['characteristics'] if c['id'] == 'interaction')
        sus = next(k for k in interaction['checks'] if 'System Usability' in k['name'])
        self.assertEqual(sus['kind'], 'measured')
        self.assertGreater(sus['points'], .95)
        self.assertIn('SUS 80.0', sus['detail'])

    def test_survey_needs_sign_in_and_valid_answers(self):
        self.assertEqual(self.app.test_client().get('/api/student/survey').status_code, 401)
        self.assertEqual(self.app.test_client().post('/api/student/survey', json={'answers': [3] * 10}).status_code, 401)
        client = self.student()
        data = client.get('/api/student/survey').json
        self.assertEqual((len(data['items']), data['answered']), (10, False))
        for bad in ({}, {'answers': [3] * 9}, {'answers': [9] * 10}, {'answers': 'x'}):
            self.assertEqual(client.post('/api/student/survey', json=bad).status_code, 400)

    def test_one_answer_per_student_and_the_id_is_not_stored_with_it(self):
        client = self.student()
        client.post('/api/student/survey', json={'answers': [5, 1] * 5})
        client.post('/api/student/survey', json={'answers': [3] * 10})
        self.assertTrue(client.get('/api/student/survey').json['answered'])
        with self.app.extensions['database'].connect() as db:
            rows = db.execute('SELECT who, answers, score FROM sus_responses').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['score'], 50)
        self.assertNotIn(ALEX.lower(), (rows[0]['who'] + rows[0]['answers']).lower())

    def test_printable_report_has_every_characteristic(self):
        page = self.admin.get('/admin/quality-report')
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        for name in ('Functional Suitability', 'Performance Efficiency', 'Compatibility', 'Interaction Capability',
                     'Reliability', 'Security', 'Maintainability', 'Flexibility', 'ISO/IEC 25010', 'Print / Save as PDF'):
            self.assertIn(name, text)
        self.assertEqual(page.headers['Cache-Control'], 'no-store')

    def test_the_admin_panel_has_the_quality_tab(self):
        script = self.admin.get('/static/admin.js').get_data(as_text=True)
        self.assertIn("['quality', 'Quality']", script)
        self.assertIn('/api/admin/quality', script)
        self.assertIn('set-survey', self.app.test_client().get('/static/app.js').get_data(as_text=True))

