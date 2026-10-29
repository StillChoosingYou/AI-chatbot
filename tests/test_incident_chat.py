"""The coordinator files incidents in the chat.

"File incident" is a small button under an answer that opens a pop-up form; sending the form puts the
confirmation in the chat; "incidents" lists the latest ones with a Resolve button for the open ones. The
Incidents page is the list where a false statement is marked or withdrawn.
"""
import unittest

from Chatbot import BUILD
from chat_support import (
    asks_for_incidents, asks_to_file_incident, incident_filed_reply, incident_help_reply, incident_status_command,
    incidents_reply)
from pg_support import AppTestCase

TAYLOR = 'P202400104'                      # demo student whose ID is Released
JAMIE = 'P202400102'                       # demo student whose ID is Pending
SESSION = 'test-session:coordinator'
NOTE = 'Says it is in the locker. PRIVATE-GATE-NOTE-123'
INCIDENT_INTENTS = {'incident_help', 'incidents_list', 'incident_status', 'incident_filed', 'incident_not_for_students'}


class IncidentChatCase(AppTestCase):
    def types(self, reply):
        return [action['type'] for action in reply['actions']]

    def file(self, **change):
        body = {'category': 'No ID', 'student_id': JAMIE, 'location': 'Main gate', 'detail': NOTE,
                'false_statement': False, 'session_id': SESSION, 'workspace_role': 'coordinator', **change}
        return self.client.post('/api/chat/incident', json=body)

    def activity(self):
        return [(e['actor_role'], e['action'], e['detail']) for e in self.client.get('/api/activity').json['events']]

    def stored(self):
        return {row['id']: row for row in self.client.get('/api/incidents').json['incidents']}


class PhraseTests(unittest.TestCase):
    def test_filing_phrases_name_the_category_they_mention(self):
        for text in ('file incident', 'File an incident', 'report an incident', 'incident report', 'new incident',
                     'log incident', 'I need to file an incident please', 'Please open an incident report', 'incident'):
            self.assertEqual(asks_to_file_incident(text), '', text)
        for text in ('no id', 'No ID incident', 'walang id', 'NO ID.'):
            self.assertEqual(asks_to_file_incident(text), 'No ID', text)
        for text in ('lost id incident', 'incident for lost ID', 'Incident lost id'):
            self.assertEqual(asks_to_file_incident(text), 'Lost ID', text)

    def test_ordinary_sentences_are_not_orders(self):
        for text in ('There was an incident at the gate yesterday', 'what is an incident report?', 'no id? what do I do',
                     'file incident later when I am free', 'the student has no id', 'incidents at the gate are rare',
                     'resolve incident', 'resolve incident twelve', 'resolve incident 12 and 13'):
            self.assertIsNone(asks_to_file_incident(text), text)
            self.assertFalse(asks_for_incidents(text), text)
            self.assertIsNone(incident_status_command(text), text)

    def test_list_phrases(self):
        for text in ('incidents', 'Incidents', 'show incidents', 'list the latest incidents', 'incident log',
                     'open incidents', 'recent incident reports', 'see all incidents.'):
            self.assertTrue(asks_for_incidents(text), text)
        self.assertFalse(asks_for_incidents('incident'))
        self.assertFalse(asks_for_incidents('how many incidents were there today?'))

    def test_status_phrases(self):
        self.assertEqual(incident_status_command('resolve incident 12'), ('Resolved', 12))
        self.assertEqual(incident_status_command('Resolve the incident #7.'), ('Resolved', 7))
        self.assertEqual(incident_status_command('reopen incident 3'), ('Open', 3))
        self.assertEqual(incident_status_command('re-open incident 3'), ('Open', 3))

    def test_the_replies_are_plain_text_the_chat_can_draw(self):
        self.assertIn('Tap File incident below', incident_help_reply(None, ''))
        row = {'id': 4, 'category': 'No ID', 'status': 'Open', 'student_id': 'P202400102', 'student_name': 'Jamie L. Santos',
               'false_statement': True, 'false_statements': 2, 'penalty': 1500, 'claim': {'message': 'Records agree.'}}
        reply = incident_filed_reply(row, ['Go to the Cashier.'])
        for part in ('Incident #4', 'Jamie L. Santos', 'Records agree.', 'no. 2', '₱1,500', '1. Go to the Cashier.'):
            self.assertIn(part, reply)
        self.assertIn('No incidents have been filed', incidents_reply([], 0))


class IncidentButtonTests(IncidentChatCase):
    def test_the_typed_phrases_open_the_form_for_the_coordinator(self):
        for role in ('coordinator',):
            for text in ('file incident', 'Report an incident', 'incident report', 'I need to file an incident please'):
                reply = self.ask(text, role)
                self.assertEqual((reply['intent'], self.types(reply), reply['confidence']),
                                 ('incident_help', ['open_incident', 'send'], 0.0), (role, text))
                button = reply['actions'][0]
                self.assertEqual((button['label'], 'category' in button, 'student_id' in button),
                                 ('⚠️ File incident', False, False))
                self.assertEqual(reply['actions'][1]['text'], 'incidents')

    def test_no_id_and_lost_id_phrases_set_the_category(self):
        self.assertEqual(self.ask('no id', 'coordinator')['actions'][0]['category'], 'No ID')
        self.assertEqual(self.ask('walang id', 'coordinator')['actions'][0]['category'], 'No ID')
        self.assertEqual(self.ask('lost id incident', 'coordinator')['actions'][0]['category'], 'Lost ID')

    def test_with_a_student_in_the_chat_the_answer_gives_the_records_and_the_button_brings_the_student(self):
        taylor, jamie = self.student(TAYLOR)['id'], self.student(JAMIE)['id']
        reply = self.ask('lost id incident', 'coordinator', record_id=taylor)
        for part in ('Taylor A. Cruz', TAYLOR, 'Released', 'Records agree: this ID was released'):
            self.assertIn(part, reply['reply'])
        self.assertEqual({k: v for k, v in reply['actions'][0].items() if k != 'label'},
                         {'type': 'open_incident', 'student_id': TAYLOR, 'category': 'Lost ID'})
        self.assertIn('Records do not agree', self.ask('lost id incident', 'coordinator', record_id=jamie)['reply'])
        self.assertIn('Ask the student where the card is', self.ask('no id', 'coordinator', record_id=taylor)['reply'])
        plain = self.ask('file incident', 'coordinator', record_id=taylor)
        self.assertNotIn('category', plain['actions'][0])
        self.assertEqual(plain['actions'][0]['student_id'], TAYLOR)

    def test_ordinary_sentences_still_go_to_the_assistant(self):
        for text in ('There was an incident at the gate yesterday', 'what is an incident report?', 'no id? what do I do'):
            self.assertNotIn(self.ask(text, 'coordinator')['intent'], INCIDENT_INTENTS, text)

    def test_a_student_is_pointed_to_request_help_instead(self):
        for text in ('file incident', 'incidents', 'resolve incident 1', 'incident report'):
            reply = self.ask(text, 'student')
            self.assertEqual((reply['intent'], self.types(reply)), ('incident_not_for_students', ['open_student_request']), text)
            self.assertEqual(reply['actions'][0]['request_type'], 'Lost ID')
            self.assertIn('Request help', reply['reply'])
        self.assertNotEqual(self.ask('no id', 'student')['intent'], 'incident_not_for_students')   # an ordinary sentence for them

    def test_a_lost_id_answer_points_the_coordinator_to_the_dashboard_and_the_requests(self):
        reply = self.ask('I lost my school ID', 'coordinator')
        self.assertEqual((reply['intent'], self.types(reply)), ('lost_id_report', ['open_dashboard', 'open_requests']))
        self.assertIn('Cashier', reply['reply'])

    def test_the_welcome_and_the_help_point_to_the_chat_forms(self):
        coordinator = self.ask('hello', 'coordinator')
        self.assertIn('open_incident', self.types(coordinator))
        self.assertIn('open_incidents', self.types(coordinator))               # the page is still there for the coordinator
        for role in ('coordinator',):
            text = self.ask('What can you do?', role)['reply']
            self.assertIn('file incident', text, role)
            self.assertIn('“incidents”', text, role)


class FilingFromTheChatTests(IncidentChatCase):
    def test_an_incident_comes_back_as_a_chat_answer(self):
        response = self.file()
        self.assertEqual(response.status_code, 201, response.json)
        data = response.json
        self.assertEqual((data['intent'], data['confidence']), ('incident_filed', 0))
        incident = data['incident']
        self.assertEqual((incident['category'], incident['student_id'], incident['status'], incident['penalty']),
                         ('No ID', JAMIE, 'Open', 0))
        for part in (f'Incident #{incident["id"]}', 'No ID', 'Jamie L. Santos', JAMIE, 'Open', 'Records agree',
                     'Type “incidents”'):
            self.assertIn(part, data['reply'])
        self.assertIsInstance(data['message_id'], int)
        self.assertEqual(data['student']['student_id'], JAMIE)
        self.assertEqual(data['actions'], [{'label': '📋 Incidents', 'type': 'send', 'text': 'incidents'}])
        self.assertEqual((data['suggestions'], data['follow_ups'], data['selected_missing']), ([], [], False))

    def test_a_lost_id_incident_sends_the_student_to_the_cashier_first(self):
        reply = self.file(category='Lost ID', student_id=TAYLOR).json['reply']
        self.assertIn('Records agree: this ID was released', reply)
        self.assertIn('What to do next', reply)
        self.assertLess(reply.index('Cashier'), reply.index('Property Management Office'))

    def test_the_second_false_statement_carries_the_penalty_and_the_first_does_not(self):
        first = self.file(student_id=TAYLOR, false_statement=True).json
        second = self.file(student_id=TAYLOR, false_statement=True).json
        third = self.file(student_id=TAYLOR, false_statement=True).json
        self.assertIn('False statement no. 1', first['reply'])
        self.assertNotIn('₱', first['reply'])
        self.assertIn('False statement no. 2 for this student: penalty ₱1,500.', second['reply'])
        self.assertIn('second false statement', second['reply'])
        self.assertEqual((first['incident']['penalty'], second['incident']['penalty'], third['incident']['penalty']), (0, 1500, 0))
        self.assertIn('False statement no. 3', third['reply'])
        self.assertNotIn('₱1,500', third['reply'])

    def test_the_chat_history_keeps_the_confirmation_but_not_what_the_coordinator_wrote(self):
        self.file()
        history = self.client.get('/api/chat/history', query_string={'session_id': SESSION}).json['messages']
        self.assertEqual([(m['role'], m['intent']) for m in history], [('user', ''), ('bot', 'incident_filed')])
        self.assertEqual(history[0]['message'], '⚠️ File incident: No ID')
        self.assertNotIn('PRIVATE-GATE-NOTE-123', ' '.join(m['message'] for m in history))
        self.assertEqual(self.stored()[1]['detail'], NOTE)                  # it is on the Incidents page, for the coordinator

    def test_the_activity_log_names_who_filed_it(self):
        self.file(workspace_role='coordinator', student_id=TAYLOR, false_statement=True)
        self.file(student_id=TAYLOR, false_statement=True)
        events = self.activity()
        self.assertIn(('coordinator', 'Logged incident', f'No ID · {TAYLOR}'), events)
        self.assertIn(('coordinator', 'False statement', f'{TAYLOR}: number 2, penalty ₱1,500'), events)

    def test_only_the_office_can_use_the_form(self):
        for role in ('student', '', None, 'visitor'):
            response = self.file(workspace_role=role)
            self.assertEqual(response.status_code, 403, role)
            self.assertIn('filed by the office', response.json['error'])
        self.assertEqual(self.raw_post('/api/chat/incident', '[]').status_code, 400)
        self.assertEqual(self.stored(), {})                                    # nothing was filed

    def test_what_cannot_be_filed_is_refused_with_a_reason_and_nothing_is_saved(self):
        cases = (({'category': 'Stolen'}, 'No ID or Lost ID'), ({'category': None}, 'No ID or Lost ID'),
                 ({'detail': ''}, 'Describe the incident'), ({'detail': 'x' * 501}, 'Describe the incident'),
                 ({'detail': 5}, 'Describe the incident'), ({'student_id': 'DEMO-9'}, 'letter P and 9 digits'),
                 ({'student_id': 12}, 'letter P and 9 digits'), ({'location': 'x' * 101}, 'Location must be text'),
                 ({'false_statement': 'yes'}, 'must be true or false'),
                 ({'student_id': '', 'false_statement': True}, 'Enter the student ID'),
                 ({'student_id': 'P202499555', 'false_statement': True}, 'No student with that ID'))
        for change, words in cases:
            response = self.file(**change)
            self.assertEqual(response.status_code, 400, change)
            self.assertIn(words, response.json['error'], change)
        self.assertEqual(self.stored(), {})

    def test_a_student_who_is_not_on_the_list_can_still_be_reported(self):
        data = self.file(student_id='p202499555').json
        self.assertIsNone(data['student'])
        self.assertIn('P202499555 (not on the dashboard)', data['reply'])
        nobody = self.file(student_id='').json                                   # no ID given at all
        self.assertIn('Incident #', nobody['reply'])
        self.assertNotIn(' for ', nobody['reply'].splitlines()[0])

    def test_the_confirmation_can_be_rated_like_any_other_answer(self):
        data = self.file().json
        self.assertEqual(self.rate(data, 'up', session=SESSION).status_code, 200)
        self.assertEqual(self.rate(data, 'up', session='another-session').status_code, 404)

    def test_the_older_incident_api_still_works_and_still_logs_the_coordinator(self):
        response = self.client.post('/api/incidents', json={'category': 'Lost ID', 'student_id': TAYLOR, 'detail': NOTE})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['next_steps'][0], 'Send the student to the Cashier to pay the replacement fee.')
        self.assertIn(('coordinator', 'Logged incident', f'Lost ID · {TAYLOR}'), self.activity())


class IncidentListTests(IncidentChatCase):
    def test_an_empty_list_says_so_and_offers_the_form(self):
        reply = self.ask('incidents', 'coordinator')
        self.assertEqual((reply['intent'], self.types(reply)), ('incidents_list', ['open_incident', 'open_incidents']))
        self.assertIn('No incidents have been filed', reply['reply'])

    def test_the_list_is_newest_first_with_a_resolve_button_for_the_open_ones(self):
        for _ in range(5):
            self.file()
        self.assertEqual(self.ask('resolve incident 4', 'coordinator')['reply'], '✅ Incident #4 marked **Resolved**.')
        reply = self.ask('show incidents', 'coordinator')
        lines = [line for line in reply['reply'].splitlines() if line.startswith('• ')]
        self.assertEqual([line.split(' · ')[0] for line in lines], ['• #5', '• #4', '• #3', '• #2', '• #1'])
        self.assertIn('4 open', reply['reply'])
        self.assertIn('**Resolved**', lines[1])
        self.assertIn('**Open**', lines[0])
        resolve = [a for a in reply['actions'] if a['type'] == 'send']
        self.assertEqual([a['text'] for a in resolve], ['resolve incident 5', 'resolve incident 3', 'resolve incident 2'])
        self.assertEqual(self.types(reply)[-2:], ['open_incident', 'open_incidents'])

    def test_only_the_latest_eight_are_listed(self):
        for _ in range(10):
            self.file()
        reply = self.ask('incidents', 'coordinator')
        self.assertEqual(len([line for line in reply['reply'].splitlines() if line.startswith('• ')]), 8)
        self.assertIn('10 open', reply['reply'])

    def test_the_list_leaves_out_what_the_coordinator_wrote(self):
        self.file()
        reply = self.ask('incidents', 'coordinator')['reply']
        self.assertNotIn('PRIVATE-GATE-NOTE-123', reply)
        for part in ('No ID', 'Jamie L. Santos', JAMIE, 'Main gate'):
            self.assertIn(part, reply)

    def test_the_list_tags_a_false_statement_and_its_penalty(self):
        self.file(student_id=TAYLOR, false_statement=True)
        self.file(student_id=TAYLOR, false_statement=True)
        lines = [line for line in self.ask('incidents', 'coordinator')['reply'].splitlines() if line.startswith('• ')]
        self.assertIn('false statement ₱1,500', lines[0])
        self.assertTrue(lines[1].endswith('false statement'), lines[1])

    def test_resolving_and_reopening_from_the_chat(self):
        self.file()
        reply = self.ask('resolve incident 1', 'coordinator')
        self.assertEqual((reply['intent'], reply['reply']), ('incident_status', '✅ Incident #1 marked **Resolved**.'))
        self.assertEqual(reply['actions'], [{'label': '📋 Incidents', 'type': 'send', 'text': 'incidents'}])
        self.assertEqual(self.stored()[1]['status'], 'Resolved')
        self.assertIn(('coordinator', 'Updated incident', 'Resolved'), self.activity())
        self.assertIn('already Resolved', self.ask('resolve incident 1', 'coordinator')['reply'])
        self.assertEqual(self.ask('reopen incident 1', 'coordinator')['reply'], '✅ Incident #1 marked **Open**.')
        self.assertEqual(self.stored()[1]['status'], 'Open')
        self.assertIn(('coordinator', 'Updated incident', 'Open'), self.activity())
        self.assertIn('There is no incident #99', self.ask('resolve incident 99', 'coordinator')['reply'])

    def test_resolving_leaves_false_statements_and_penalties_alone(self):
        self.file(student_id=TAYLOR, false_statement=True)
        self.file(student_id=TAYLOR, false_statement=True)
        self.ask('resolve incident 2', 'coordinator')
        second = self.stored()[2]
        self.assertEqual((second['status'], second['false_statement'], second['penalty']), ('Resolved', True, 1500))

    def test_a_student_cannot_resolve_anything(self):
        self.file()
        self.assertEqual(self.ask('resolve incident 1', 'student')['intent'], 'incident_not_for_students')
        self.assertEqual(self.stored()[1]['status'], 'Open')

    def test_the_other_ways_to_ask_for_the_list(self):
        for text in ('Incidents', 'incident log', 'open incidents', 'list the latest incidents', 'recent incident reports'):
            self.assertEqual(self.ask(text, 'coordinator')['intent'], 'incidents_list', text)


class PageTests(AppTestCase):
    def test_the_form_is_a_pop_up_and_the_incidents_page_is_the_coordinators_list(self):
        html = self.client.get('/coordinator').get_data(as_text=True)
        self.assertIn('<dialog id="incident-dialog"', html)
        self.assertEqual(html.count('id="incident-form"'), 1)                   # one form, not one on the page and one in the pop-up
        self.assertIn('data-workspace="coordinator" id="view-incidents"', html)
        self.assertNotIn('id="incident-result"', html)

    def test_the_footer_says_which_build_this_is(self):
        html = self.client.get('/coordinator').get_data(as_text=True)
        self.assertIn(f'build {BUILD}', html)


if __name__ == '__main__':
    unittest.main()
