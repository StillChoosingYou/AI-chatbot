"""Incidents: "No ID" and "Lost ID" only, the Cashier first for a lost ID, and the ₱1,500 penalty for
a student's second false statement."""
import unittest

from pg_support import AppTestCase, run_together
from student_data import CASHIER_STEP, OFFICE_STEP, PENALTY_AMOUNT, PENALTY_TEXT, STUDENT_ID_HINT

JAMIE = 'P202400102'     # Pending: the ID is not made yet
SAM = 'P202400103'       # Processing
ALEX = 'P202400101'      # For release
TAYLOR = 'P202400104'    # Released


class IncidentTestCase(AppTestCase):
    def file(self, category='Lost ID', student_id=JAMIE, false=False, detail='Said the ID was lost', **extra):
        payload = {'category': category, 'detail': detail, **extra}
        if student_id is not None:
            payload['student_id'] = student_id
        if false:
            payload['false_statement'] = True
        return self.client.post('/api/incidents', json=payload)

    def incidents(self):
        return self.client.get('/api/incidents').json['incidents']

    def penalties(self, student_id=JAMIE):
        """The penalty on each of a student's incidents, oldest first."""
        return [i['penalty'] for i in reversed(self.incidents()) if i['student_id'] == student_id]

    def activity(self):
        return [(e['action'], e['detail']) for e in self.client.get('/api/activity').json['events']]


class CategoryAndFieldTests(IncidentTestCase):
    def test_only_no_id_and_lost_id_can_be_filed(self):
        listing = self.client.get('/api/incidents').json
        self.assertEqual((listing['categories'], listing['penalty'], listing['penalty_text']), (['No ID', 'Lost ID'], 1500, '₱1,500'))
        for category in ('No ID', 'Lost ID'):
            self.assertEqual(self.file(category, None).status_code, 201, category)
        for category in ('Visitor', 'Damaged ID', 'Suspicious', 'Other', 'lost id', 'LOST ID', '', None, 5, ['Lost ID']):
            response = self.file(category, None)
            self.assertEqual(response.status_code, 400, repr(category))
            self.assertEqual(response.json['error'], 'Choose No ID or Lost ID.')
        self.assertEqual(len(self.incidents()), 2)

    def test_incidents_filed_before_with_other_categories_are_still_listed_and_can_be_resolved(self):
        with self.db() as db:
            db.execute("INSERT INTO incidents (category,location,detail,student_id,status,timestamp) VALUES "
                       "('Visitor','Main gate','Visitor without a pass','','Open','2026-10-01 08:00:00')")
        old = next(i for i in self.incidents() if i['category'] == 'Visitor')
        self.assertEqual((old['false_statement'], old['penalty'], old['student_name']), (False, 0, None))
        self.assertEqual(self.client.patch(f'/api/incidents/{old["id"]}', json={'status': 'Resolved'}).json['status'], 'Resolved')

    def test_the_description_is_required_and_the_other_fields_are_checked(self):
        for detail in (None, '', '   ', 'x' * 501, 7, ['x']):
            response = self.client.post('/api/incidents', json={'category': 'No ID', 'detail': detail})
            self.assertEqual(response.status_code, 400, repr(detail))
        self.assertEqual(self.client.post('/api/incidents', json={'category': 'No ID'}).status_code, 400)
        self.assertEqual(self.file('No ID', None, detail='x' * 500).status_code, 201)
        self.assertEqual(self.file('No ID', None, location='x' * 100).status_code, 201)
        for location in ('x' * 101, 5, ['gate']):
            response = self.file('No ID', None, location=location)
            self.assertEqual(response.status_code, 400, repr(location))
            self.assertIn('Location', response.json['error'])
        self.assertEqual(self.file('No ID', None, location=None).json['location'], '')
        self.assertEqual(self.raw_post('/api/incidents', '[1]').status_code, 400)
        self.assertEqual(self.raw_post('/api/incidents', 'not json').status_code, 400)

    def test_the_student_id_is_optional_but_must_be_well_formed_when_given(self):
        none = self.file('No ID', None)
        self.assertEqual((none.status_code, none.json['student_id'], none.json['student_name'], none.json['claim']), (201, '', None, None))
        blank = self.file('No ID', '   ')
        self.assertEqual((blank.status_code, blank.json['student_id']), (201, ''))
        for bad in ('P2024', 'abc', 'DEMO-9', 12345, ['P202400102'], 'P2024\x0000102'):
            response = self.file('No ID', bad)
            self.assertEqual(response.status_code, 400, repr(bad))
            self.assertEqual(response.json['error'], STUDENT_ID_HINT)
        known = self.file('No ID', ' p202400102 ')
        self.assertEqual((known.json['student_id'], known.json['student_name']), (JAMIE, 'Jamie L. Santos'))

    def test_an_id_that_is_not_on_the_dashboard_is_kept_as_typed_and_the_claim_cannot_be_checked(self):
        response = self.file('No ID', 'p202499999')
        self.assertEqual(response.status_code, 201)
        self.assertEqual((response.json['student_id'], response.json['student_name']), ('P202499999', None))
        self.assertIsNone(response.json['claim']['agrees'])
        self.assertIn('No student with this ID is on the dashboard yet', response.json['claim']['message'])
        self.assertEqual(response.json['false_statements'], 0)

    def test_the_server_decides_the_penalty_not_the_browser(self):
        response = self.client.post('/api/incidents', json={
            'category': 'Lost ID', 'detail': 'x', 'student_id': JAMIE, 'penalty': 99999, 'status': 'Resolved', 'id': 1})
        self.assertEqual((response.status_code, response.json['penalty'], response.json['status']), (201, 0, 'Open'))
        self.assertEqual(self.client.patch(f'/api/incidents/{response.json["id"]}', json={'penalty': 0}).status_code, 400)


class ClaimCheckTests(IncidentTestCase):
    """What the student says is compared with what the records show."""

    CASES = (('Lost ID', TAYLOR, True, 'was released, so it can have been lost'),
             ('Lost ID', JAMIE, False, 'never released (status Pending: ID not yet made)'),
             ('Lost ID', SAM, False, 'never released (status Processing: ID is being made)'),
             ('Lost ID', ALEX, False, 'never released (status For release: ID is made, ready to claim)'),
             ('No ID', JAMIE, True, 'the ID is not made yet (status Pending'),
             ('No ID', SAM, True, 'the ID is not made yet (status Processing'),
             ('No ID', ALEX, True, 'the card is made but the student has not claimed it yet'),
             ('No ID', TAYLOR, None, 'Records show this ID was released'))

    def test_the_check_before_filing_compares_the_claim_with_the_records(self):
        for category, student_id, agrees, words in self.CASES:
            with self.subTest(category=category, student=student_id):
                result = self.client.get('/api/incidents/check', query_string={'category': category, 'student_id': student_id}).json
                self.assertTrue(result['found'])
                self.assertEqual(result['claim']['agrees'], agrees)
                self.assertIn(words, result['claim']['message'])
                self.assertEqual(result['student']['student_id'], student_id)

    def test_filing_gives_the_same_verdict(self):
        for category, student_id, agrees, words in self.CASES:
            with self.subTest(category=category, student=student_id):
                claim = self.file(category, student_id).json['claim']
                self.assertEqual(claim['agrees'], agrees)
                self.assertIn(words, claim['message'])

    def test_the_check_shows_the_student_and_what_the_next_false_statement_would_cost(self):
        url = '/api/incidents/check'
        first = self.client.get(url, query_string={'category': 'Lost ID', 'student_id': ' p202400102 '}).json
        self.assertEqual(first['student'], {'student_id': JAMIE, 'name': 'Jamie L. Santos', 'year_section': '2B',
                                           'status': 'Pending', 'status_note': 'ID not yet made'})
        self.assertEqual((first['false_statements'], first['penalty_due'], first['next_penalty'], first['penalty_text']),
                         (0, 0, 0, PENALTY_TEXT))
        self.file(false=True)
        second = self.client.get(url, query_string={'category': 'Lost ID', 'student_id': JAMIE}).json
        self.assertEqual((second['false_statements'], second['penalty_due'], second['next_penalty']), (1, 0, PENALTY_AMOUNT))
        self.file(false=True)
        third = self.client.get(url, query_string={'category': 'Lost ID', 'student_id': JAMIE}).json
        self.assertEqual((third['false_statements'], third['penalty_due'], third['next_penalty']), (2, PENALTY_AMOUNT, 0))

    def test_an_unknown_student_or_a_bad_question_is_answered_without_a_crash(self):
        url = '/api/incidents/check'
        unknown = self.client.get(url, query_string={'category': 'No ID', 'student_id': 'P202499999'}).json
        self.assertFalse(unknown['found'])
        self.assertIn('No student with this ID is on the dashboard yet', unknown['message'])
        for query in ({'category': 'Visitor', 'student_id': JAMIE}, {'student_id': JAMIE}, {'category': 'No ID'},
                      {'category': 'No ID', 'student_id': '   '}, {'category': 'No ID', 'student_id': 'x' * 41},
                      {'category': 'No ID', 'student_id': 'P2024\x00'}):
            self.assertEqual(self.client.get(url, query_string=query).status_code, 400, str(query))


class NextStepsTests(IncidentTestCase):
    def test_a_lost_id_sends_the_student_to_the_cashier_first(self):
        steps = self.file('Lost ID', TAYLOR).json['next_steps']
        self.assertEqual(steps, [CASHIER_STEP, OFFICE_STEP])
        self.assertIn('Cashier', steps[0])
        self.assertIn('Property Management Office', steps[1])
        self.assertIn('affidavit of loss', steps[1])
        self.assertEqual(self.file('Lost ID', None).json['next_steps'], [CASHIER_STEP, OFFICE_STEP])

    def test_a_missing_id_has_no_steps_until_a_penalty_applies(self):
        self.assertEqual(self.file('No ID', JAMIE).json['next_steps'], [])
        self.assertEqual(self.file('No ID', None).json['next_steps'], [])

    def test_no_amount_is_made_up_for_the_replacement_fee(self):
        for steps in (self.file('Lost ID', TAYLOR).json['next_steps'], self.client.get('/api/office').json['requirements'].values()):
            for step in steps:
                self.assertNotRegex(step, r'[₱$]\s?\d|PHP|\d\s?pesos', step)


class PenaltyTests(IncidentTestCase):
    def test_the_penalty_is_charged_once_at_the_second_false_statement(self):
        first = self.file(false=True).json
        self.assertEqual((first['false_statement'], first['penalty'], first['false_statements']), (True, 0, 1))
        self.assertEqual(first['next_steps'], [CASHIER_STEP, OFFICE_STEP])
        second = self.file(false=True).json
        self.assertEqual((second['penalty'], second['false_statements']), (PENALTY_AMOUNT, 2))
        self.assertEqual(len(second['next_steps']), 3)
        self.assertIn(PENALTY_TEXT, second['next_steps'][-1])
        self.assertIn('Cashier', second['next_steps'][-1])
        third = self.file(false=True).json                      # the written rule says nothing about a third time
        self.assertEqual((third['penalty'], third['false_statements']), (0, 3))
        self.assertEqual(third['next_steps'], [CASHIER_STEP, OFFICE_STEP])
        fourth = self.file(false=True).json
        self.assertEqual((fourth['penalty'], fourth['false_statements']), (0, 4))
        self.assertEqual(self.penalties(), [0, PENALTY_AMOUNT, 0, 0])

    def test_a_no_id_incident_can_carry_the_penalty_too(self):
        self.file('No ID', ALEX, false=True)
        second = self.file('No ID', ALEX, false=True).json
        self.assertEqual((second['penalty'], second['next_steps'][0] == CASHIER_STEP), (PENALTY_AMOUNT, False))
        self.assertIn(PENALTY_TEXT, second['next_steps'][0])

    def test_an_incident_that_is_not_marked_as_a_false_statement_never_counts(self):
        for _ in range(4):
            response = self.file(false=False)
            self.assertEqual((response.json['penalty'], response.json['false_statement'], response.json['false_statements']), (0, False, 0))
        first = self.file(false=True).json
        self.assertEqual((first['penalty'], first['false_statements']), (0, 1))          # the first one, not the fifth
        self.assertEqual(self.penalties(), [0, 0, 0, 0, 0])

    def test_each_student_is_counted_on_their_own_whatever_the_case_of_the_id(self):
        self.file(student_id=JAMIE, false=True)
        other = self.file(student_id=SAM, false=True).json
        self.assertEqual((other['penalty'], other['false_statements']), (0, 1))
        again = self.file(student_id=JAMIE.lower(), false=True).json
        self.assertEqual((again['student_id'], again['penalty'], again['false_statements']), (JAMIE, PENALTY_AMOUNT, 2))
        self.assertEqual(self.penalties(SAM), [0])

    def test_a_false_statement_needs_a_student_who_is_on_the_dashboard(self):
        for student_id, words in ((None, 'Enter the student ID'), ('P202499999', 'No student with that ID is on the dashboard'),
                                  ('   ', 'Enter the student ID')):
            response = self.file(student_id=student_id, false=True)
            self.assertEqual(response.status_code, 400, student_id)
            self.assertIn(words, response.json['error'])
        for bad in ('yes', 1, 0, None, 'true', [True]):
            response = self.client.post('/api/incidents', json={
                'category': 'Lost ID', 'detail': 'x', 'student_id': JAMIE, 'false_statement': bad})
            self.assertEqual(response.status_code, 400, repr(bad))
            self.assertIn('true or false', response.json['error'])
        self.assertEqual(self.incidents(), [])                                          # nothing half-saved

    def test_resolving_the_incident_clears_the_penalty_due_and_reopening_brings_it_back(self):
        self.file(false=True)
        second = self.file(false=True).json
        check = lambda: self.client.get('/api/incidents/check', query_string={'category': 'Lost ID', 'student_id': JAMIE}).json
        self.assertEqual(check()['penalty_due'], PENALTY_AMOUNT)
        resolved = self.client.patch(f'/api/incidents/{second["id"]}', json={'status': 'Resolved'})
        self.assertEqual((resolved.json['status'], resolved.json['penalty']), ('Resolved', PENALTY_AMOUNT))   # kept as a record
        self.assertEqual((check()['penalty_due'], check()['false_statements']), (0, 2))
        reopened = self.client.patch(f'/api/incidents/{second["id"]}', json={'status': 'Open'})
        self.assertEqual(reopened.json['status'], 'Open')
        self.assertEqual(check()['penalty_due'], PENALTY_AMOUNT)

    def test_withdrawing_a_false_statement_moves_the_penalty_to_what_is_then_the_second(self):
        a, b, c = (self.file(false=True).json['id'] for _ in range(3))
        self.assertEqual(self.penalties(), [0, PENALTY_AMOUNT, 0])
        withdrawn = self.client.patch(f'/api/incidents/{a}', json={'false_statement': False})
        self.assertEqual((withdrawn.status_code, withdrawn.json['false_statement'], withdrawn.json['penalty']), (200, False, 0))
        self.assertEqual(self.penalties(), [0, 0, PENALTY_AMOUNT])                      # b is now the first, c the second
        restored = self.client.patch(f'/api/incidents/{a}', json={'false_statement': True})
        self.assertEqual(restored.json['false_statement'], True)
        self.assertEqual(self.penalties(), [0, PENALTY_AMOUNT, 0])
        self.client.patch(f'/api/incidents/{b}', json={'false_statement': False})       # withdrawing the penalised one
        self.assertEqual(self.penalties(), [0, 0, PENALTY_AMOUNT])
        self.client.patch(f'/api/incidents/{c}', json={'false_statement': False})
        self.assertEqual(self.penalties(), [0, 0, 0])
        self.assertEqual(self.client.get('/api/incidents/check', query_string={
            'category': 'Lost ID', 'student_id': JAMIE}).json['false_statements'], 1)

    def test_flagging_an_older_incident_later_renumbers_by_the_order_they_were_filed(self):
        old = self.file(false=False).json['id']
        self.file(false=True)
        self.file(false=True)
        self.assertEqual(self.penalties(), [0, 0, PENALTY_AMOUNT])
        self.client.patch(f'/api/incidents/{old}', json={'false_statement': True})
        self.assertEqual(self.penalties(), [0, PENALTY_AMOUNT, 0])

    def test_changing_only_the_status_leaves_the_flag_and_the_penalty_alone(self):
        self.file(false=True)
        second = self.file(false=True).json['id']
        self.client.patch(f'/api/incidents/{second}', json={'status': 'Resolved'})
        row = next(i for i in self.incidents() if i['id'] == second)
        self.assertEqual((row['false_statement'], row['penalty'], row['status']), (True, PENALTY_AMOUNT, 'Resolved'))

    def test_an_incident_without_a_student_on_the_dashboard_cannot_become_a_false_statement(self):
        for student_id in (None, 'P202499999'):
            incident = self.file('No ID', student_id).json
            response = self.client.patch(f'/api/incidents/{incident["id"]}', json={'false_statement': True})
            self.assertEqual(response.status_code, 400, student_id)
            self.assertIn('no student on the dashboard', response.json['error'])
            self.assertEqual(self.client.patch(f'/api/incidents/{incident["id"]}', json={'false_statement': False}).status_code, 200)

    def test_what_the_coordinator_sees_in_the_chat_grows_with_the_record(self):
        def staff(role):
            return self.ask(JAMIE, role)['reply']
        clean = staff('coordinator')
        self.assertNotIn('False statements', clean)
        self.assertNotIn('Penalty', clean)
        self.file(false=True)
        for role in ('coordinator',):
            text = staff(role)
            self.assertIn('False statements on record: 1', text, role)
            self.assertIn(f'A second false statement carries a {PENALTY_TEXT} penalty.', text, role)
        second = self.file(false=True).json
        for role in ('coordinator',):
            text = staff(role)
            self.assertIn('False statements on record: 2', text, role)
            self.assertIn(f'⚠️ Penalty due: {PENALTY_TEXT}. Send the student to the Cashier, then mark the incident '
                          'Resolved once it is paid.', text, role)
            self.assertNotIn('A second false statement carries', text, role)
        self.client.patch(f'/api/incidents/{second["id"]}', json={'status': 'Resolved'})
        text = staff('coordinator')
        self.assertIn('False statements on record: 2', text)
        self.assertNotIn('Penalty due', text)
        self.file(false=True)
        self.assertIn('More than two false statements', staff('coordinator'))
        self.assertNotIn('Penalty due', staff('coordinator'))

    def test_a_student_never_sees_false_statements_or_penalties(self):
        self.file(false=True)
        self.file(false=True)
        for message in (JAMIE, 'Jamie Santos', f'{JAMIE} {SAM}'):
            dump = str(self.ask(message, 'student'))
            for word in ('False statement', 'Penalty', 'penalty', '1,500'):
                self.assertNotIn(word, dump, (message, word))

    def test_the_scan_lookup_carries_the_same_facts(self):
        self.file(false=True)
        self.file(false=True)
        match = self.client.get('/api/verify', query_string={'q': 'Jamie Santos'}).json['matches'][0]
        self.assertEqual((match['false_statements'], match['penalty_due']), (2, PENALTY_AMOUNT))
        self.assertEqual(match['status_note'], 'ID not yet made')
        clean = self.client.get('/api/verify', query_string={'q': TAYLOR}).json['matches'][0]
        self.assertEqual((clean['false_statements'], clean['penalty_due']), (0, 0))

    def test_every_role_is_told_about_the_cashier_and_the_penalty_when_an_id_is_lost(self):
        for role in ('student', 'coordinator'):
            reply = self.ask('I lost my school ID', role)
            self.assertEqual(reply['intent'], 'lost_id_report', role)
            self.assertIn('Cashier', reply['reply'], role)
            self.assertIn(PENALTY_TEXT, reply['reply'], role)
        self.assertIn('Property Management Office', self.ask('I lost my school ID', 'student')['reply'])
        self.assertIn('Cashier', self.client.get('/api/office').json['requirements']['Lost ID'])


class ConcurrencyTests(IncidentTestCase):
    def test_false_statements_filed_at_the_same_moment_are_counted_one_after_another(self):
        def job():
            return self.app.test_client().post('/api/incidents', json={
                'category': 'Lost ID', 'detail': 'Said it was lost', 'student_id': JAMIE, 'false_statement': True})
        results = run_together([job] * 6)
        self.assertEqual([r for r in results if isinstance(r, BaseException)], [])
        self.assertEqual([r.status_code for r in results], [201] * 6)
        self.assertEqual(sorted(r.json['false_statements'] for r in results), [1, 2, 3, 4, 5, 6])
        self.assertEqual(sorted(r.json['penalty'] for r in results), [0, 0, 0, 0, 0, PENALTY_AMOUNT])
        self.assertEqual(sorted(self.penalties()), [0, 0, 0, 0, 0, PENALTY_AMOUNT])         # exactly one penalty

    def test_flagging_several_incidents_at_once_still_charges_only_the_second(self):
        ids = [self.file(false=False).json['id'] for _ in range(4)]
        jobs = [lambda i=i: self.app.test_client().patch(f'/api/incidents/{i}', json={'false_statement': True}) for i in ids]
        results = run_together(jobs)
        self.assertEqual([r for r in results if isinstance(r, BaseException)], [])
        self.assertEqual([r.status_code for r in results], [200] * 4)
        self.assertEqual(self.penalties(), [0, PENALTY_AMOUNT, 0, 0])

    def test_filing_and_withdrawing_at_the_same_moment_leave_a_consistent_record(self):
        first = self.file(false=True).json['id']
        self.file(false=True)
        jobs = [lambda: self.app.test_client().patch(f'/api/incidents/{first}', json={'false_statement': False}),
                lambda: self.app.test_client().post('/api/incidents', json={
                    'category': 'No ID', 'detail': 'again', 'student_id': JAMIE, 'false_statement': True}),
                lambda: self.app.test_client().post('/api/incidents', json={
                    'category': 'No ID', 'detail': 'again', 'student_id': JAMIE, 'false_statement': True})]
        results = run_together(jobs)
        self.assertEqual([r for r in results if isinstance(r, BaseException)], [])
        flagged = [i for i in reversed(self.incidents()) if i['false_statement']]
        self.assertEqual(len(flagged), 3)                                                # 1 withdrawn, 2 added
        self.assertEqual([i['penalty'] for i in flagged], [0, PENALTY_AMOUNT, 0])


class ActivityTests(IncidentTestCase):
    def test_filing_flagging_and_resolving_are_written_to_the_activity_log(self):
        self.file('Lost ID', TAYLOR)
        self.file(false=True)
        second = self.file(false=True).json
        self.client.patch(f'/api/incidents/{second["id"]}', json={'status': 'Resolved'})
        self.client.patch(f'/api/incidents/{second["id"]}', json={'false_statement': False})
        events = self.activity()
        self.assertIn(('Logged incident', f'Lost ID · {TAYLOR}'), events)
        self.assertIn(('False statement', f'{JAMIE}: number 1'), events)
        self.assertIn(('False statement', f'{JAMIE}: number 2, penalty {PENALTY_TEXT}'), events)
        self.assertIn(('Updated incident', 'Resolved'), events)
        self.assertIn(('False statement withdrawn', JAMIE), events)


if __name__ == '__main__':
    unittest.main()
