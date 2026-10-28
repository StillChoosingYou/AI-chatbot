"""The student does everything in the chat.

"Request help" is a small button under an answer (after "I lost my ID", say) that opens a pop-up form; sending
the form puts the confirmation in the chat; "my requests" follows a request there too. Students are limited
to years 1 to 4, and the dashboard's ticker gets its numbers from /api/stats.
"""
import unittest

from chat_support import (
    asks_for_my_requests, asks_to_request_help, my_requests_reply, request_sent_reply, request_subject)
from pg_support import NEW, AppTestCase
from student_data import STUDENT_LOST_STEPS

TAYLOR = 'P202400104'                      # a demo student whose ID is Released
SESSION = 'test-session:student'
NOTE = 'I lost it on the jeep. PRIVATE-NOTE-123'


class StudentChatCase(AppTestCase):
    def types(self, reply):
        return [action['type'] for action in reply['actions']]

    def button(self, reply):
        return next((a for a in reply['actions'] if a['type'] == 'open_student_request'), None)

    def add(self, student_id, name, year_section='1A'):
        response = self.client.post('/api/inventory', json={'student_id': student_id, 'name': name, 'year_section': year_section})
        self.assertEqual(response.status_code, 201, response.json)

    def send_request(self, **change):
        body = {'student_id': TAYLOR, 'request_type': 'Lost ID', 'detail': NOTE, 'session_id': SESSION,
                'workspace_role': 'student', **change}
        return self.client.post('/api/chat/request', json=body)


class RequestHelpButtonTests(StudentChatCase):
    def test_telling_the_chat_you_lost_your_id_offers_a_small_request_help_button(self):
        reply = self.ask('I lost my school ID', 'student')
        self.assertEqual((reply['intent'], self.types(reply)), ('lost_id_report', ['open_student_request']))
        button = self.button(reply)
        self.assertEqual((button['label'], button['request_type']), ('📝 Request help', 'Lost ID'))
        self.assertNotIn('student_id', button)            # nobody is picked yet: the form asks for the ID
        self.assertIn('tap “Request help”', reply['reply'])
        self.assertLess(reply['reply'].index('Cashier'), reply['reply'].index('Property Management Office'))

    def test_with_a_student_in_the_chat_the_button_brings_that_student(self):
        reply = self.ask('I lost my school ID', 'student', record_id=self.student(TAYLOR)['id'])
        button = self.button(reply)
        self.assertEqual((button['student_id'], button['name'], button['request_type']), (TAYLOR, 'Taylor A. Cruz', 'Lost ID'))

    def test_the_form_opens_set_to_what_the_answer_was_about(self):
        taylor = self.student(TAYLOR)['id']
        for question, expected in (('What is the status of my student ID?', 'Status inquiry'),
                                   ('Why is my student ID pending?', 'Status inquiry'),
                                   ('When can I collect my school ID?', 'Appointment help')):
            reply = self.ask(question, 'student', record_id=taylor)
            self.assertEqual(self.button(reply)['request_type'], expected, question)

    def test_the_card_of_a_student_has_the_button_for_that_student(self):
        reply = self.ask(TAYLOR, 'student')
        self.assertEqual(self.button(reply)['student_id'], TAYLOR)
        self.assertNotIn('request_type', self.button(reply))       # the form asks what kind of help

    def test_the_button_is_also_there_when_the_chat_cannot_help(self):
        unknown = self.ask('P202400999', 'student')
        self.assertEqual((unknown['intent'], sorted(self.types(unknown))), ('student_not_found', ['ask_human', 'open_student_request']))
        self.assertEqual(self.types(self.ask('I want to talk to a real person', 'student')), ['open_student_request'])
        self.assertEqual(self.types(self.ask('Where is the Property Management Office?', 'student')),
                         ['ask_human', 'open_student_request'])

    def test_typing_request_help_gets_the_button(self):
        for text in ('request help', 'Request Help', 'I need to submit a request', 'make a request please', 'request form'):
            reply = self.ask(text, 'student')
            self.assertEqual((reply['intent'], self.types(reply), reply['confidence']), ('request_help', ['open_student_request'], 0.0), text)

    def test_the_welcome_offers_the_form_and_then_my_requests_once_a_student_is_in_the_chat(self):
        self.assertEqual(self.types(self.ask('hello', 'student')), ['open_student_request', 'ask_office'])
        reply = self.ask('hello', 'student', record_id=self.student(TAYLOR)['id'])
        self.assertEqual(self.types(reply), ['open_student_request', 'send', 'ask_office'])
        self.assertEqual(reply['actions'][1]['text'], f'my requests {TAYLOR}')

    def test_the_other_roles_never_get_the_button(self):
        for role in ('coordinator',):
            for text in ('I lost my school ID', 'hello', 'I want to talk to a real person', 'request help', 'P202400999', TAYLOR):
                self.assertNotIn('open_student_request', self.types(self.ask(text, role)), (role, text))


class SendingTheFormTests(StudentChatCase):
    def test_a_lost_id_request_comes_back_as_a_chat_answer(self):
        response = self.send_request()
        self.assertEqual(response.status_code, 201)
        data, ticket = response.json, response.json['request']
        self.assertEqual((data['intent'], data['confidence'], ticket['request_type'], ticket['status']),
                         ('request_submitted', 0, 'Lost ID', 'Submitted'))
        for part in (f'Request #{ticket["id"]}', 'Lost ID', 'Taylor A. Cruz', TAYLOR, 'Submitted', 'my requests'):
            self.assertIn(part, data['reply'])
        self.assertLess(data['reply'].index('Cashier'), data['reply'].index('Property Management Office'))
        self.assertIsInstance(data['message_id'], int)
        self.assertEqual(data['student']['student_id'], TAYLOR)
        self.assertEqual(data['actions'], [{'label': '📋 My requests', 'type': 'send', 'text': f'my requests {TAYLOR}'}])
        self.assertEqual((data['suggestions'], data['follow_ups'], data['selected_missing']), ([], [], False))

    def test_the_coordinator_gets_the_request_with_what_the_student_wrote(self):
        ticket = self.send_request().json['request']
        mine = next(r for r in self.client.get('/api/requests').json['requests'] if r['id'] == ticket['id'])
        self.assertEqual((mine['student_id'], mine['name'], mine['request_type'], mine['status'], mine['detail']),
                         (TAYLOR, 'Taylor A. Cruz', 'Lost ID', 'Submitted', NOTE))
        self.assertIn(('Submitted request', f'Lost ID · {TAYLOR}'),
                      [(e['action'], e['detail']) for e in self.client.get('/api/activity').json['events']])

    def test_the_chat_history_keeps_the_confirmation_but_not_the_details(self):
        self.send_request()
        history = self.client.get('/api/chat/history', query_string={'session_id': SESSION}).json['messages']
        self.assertEqual([(m['role'], m['intent']) for m in history], [('user', ''), ('bot', 'request_submitted')])
        self.assertEqual(history[0]['message'], '📝 Request help: Lost ID')
        self.assertNotIn('PRIVATE-NOTE-123', ' '.join(m['message'] for m in history))

    def test_the_confirmation_can_be_rated_like_any_other_answer(self):
        data = self.send_request().json
        self.assertEqual(self.rate(data, 'up', session=SESSION).status_code, 200)
        self.assertEqual(self.rate(data, 'up', session='another-session').status_code, 404)    # not someone else's

    def test_another_kind_of_request_has_no_steps(self):
        data = self.send_request(request_type='Status inquiry', detail='').json
        self.assertNotIn('Cashier', data['reply'])
        self.assertIn('The office will look at it', data['reply'])

    def test_a_student_who_is_not_on_the_list_yet_can_still_ask(self):
        data = self.send_request(student_id='p202499777', name='Pat Lee', request_type='Other').json
        self.assertIsNone(data['student'])
        self.assertIn('Pat Lee (P202499777)', data['reply'])

    def test_what_cannot_be_filed_is_refused_with_a_reason(self):
        for change, words in (({'student_id': ''}, 'letter P and 9 digits'), ({'student_id': 'DEMO-9'}, 'letter P and 9 digits'),
                              ({'request_type': 'Refund'}, 'valid request type'), ({'request_type': ''}, 'valid request type'),
                              ({'detail': 'x' * 501}, 'Details must be text of up to 500'),
                              ({'name': 'n' * 101}, 'Name must be text of up to 100'),
                              ({'detail': 7}, 'Details must be text'), ({'name': ['Pat']}, 'Name must be text')):
            response = self.send_request(**change)
            self.assertEqual(response.status_code, 400, change)
            self.assertIn(words, response.json['error'], change)
        self.assertEqual(self.raw_post('/api/chat/request', '[]').status_code, 400)
        self.assertEqual(self.client.get('/api/requests').json['requests'], [])               # nothing was filed

    def test_the_older_request_form_api_refuses_the_same_things_and_still_files_the_rest(self):
        response = self.client.post('/api/requests', json={'student_id': TAYLOR, 'request_type': 'Other', 'detail': 'x' * 501})
        self.assertEqual(response.status_code, 400)
        ok = self.client.post('/api/requests', json={'student_id': TAYLOR, 'request_type': 'Other', 'detail': NOTE})
        self.assertEqual((ok.status_code, ok.json['name'], ok.json['detail']), (201, 'Taylor A. Cruz', NOTE))

    def test_without_a_session_the_request_is_filed_but_no_history_is_kept(self):
        response = self.send_request(session_id='')
        self.assertEqual((response.status_code, response.json['message_id']), (201, None))
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM chat_history').fetchone()[0], 0)

    def test_accepting_a_lost_id_request_made_in_the_chat_sends_a_released_id_back_to_pending(self):
        ticket = self.send_request().json['request']
        self.assertIn('Status: **Released**', self.ask(TAYLOR, 'student')['reply'])
        self.assertEqual(self.client.patch(f'/api/requests/{ticket["id"]}', json={'status': 'Accepted'}).status_code, 200)
        self.assertIn('Status: **Pending**', self.ask(TAYLOR, 'student')['reply'])


class MyRequestsTests(StudentChatCase):
    def test_my_requests_lists_type_status_and_time_but_not_what_the_student_wrote(self):
        ticket = self.send_request().json['request']
        self.send_request(request_type='Status inquiry', detail='second private note')
        reply = self.ask(f'my requests {TAYLOR}', 'student')
        text = reply['reply']
        self.assertEqual(reply['intent'], 'my_requests')
        self.assertIn('Taylor A. Cruz (P202400104)', text)
        self.assertIn(f'#{ticket["id"]} · Lost ID · **Submitted**', text)
        self.assertIn('waiting for the office to look at it', text)
        self.assertLess(text.index('Status inquiry'), text.index('Lost ID'))               # newest first
        self.assertNotIn('PRIVATE-NOTE', text)
        self.assertNotIn('second private', text)
        self.assertEqual(self.button(reply)['student_id'], TAYLOR)

    def test_the_status_follows_what_the_coordinator_does(self):
        ticket = self.send_request().json['request']
        self.client.patch(f'/api/requests/{ticket["id"]}', json={'status': 'In review'})
        self.assertIn('**In review** ', self.ask(f'my requests {TAYLOR}', 'student')['reply'])
        self.assertIn('staff are looking at it', self.ask(f'my requests {TAYLOR}', 'student')['reply'])

    def test_the_student_can_be_named_by_id_by_name_or_by_the_chat_itself(self):
        self.send_request()
        taylor = self.student(TAYLOR)['id']
        for text, extra in (('my requests', {'record_id': taylor}), ('track my request taylor cruz', {}),
                            ('my requests p202400104', {}), ('where is my request?', {'record_id': taylor}),
                            ('nasaan na ang request ko', {'record_id': taylor})):
            reply = self.ask(text, 'student', **extra)
            self.assertEqual(reply['intent'], 'my_requests', text)
            self.assertIn('Lost ID', reply['reply'], text)

    def test_without_a_student_it_asks_for_the_id(self):
        reply = self.ask('my requests', 'student')
        self.assertEqual(reply['intent'], 'my_requests')
        self.assertIn('Type your student ID first', reply['reply'])
        self.assertEqual(self.types(reply), ['open_student_request'])

    def test_an_id_without_requests_says_so_and_offers_the_form(self):
        reply = self.ask('my requests P202400101', 'student')
        self.assertIn('No requests have been made with P202400101 yet', reply['reply'])
        self.assertEqual(self.button(reply)['student_id'], 'P202400101')
        self.assertIn('No requests have been made with P202499555 yet', self.ask('my requests P202499555', 'student')['reply'])

    def test_a_name_nobody_has_is_not_answered_with_someone_elses_requests(self):
        self.send_request()
        reply = self.ask('my requests Zacharias Quimby', 'student', record_id=self.student(TAYLOR)['id'])
        self.assertIn('couldn’t find a student matching', reply['reply'])
        self.assertNotIn('Lost ID', reply['reply'])

    def test_a_name_shared_by_several_students_asks_which_one(self):
        self.add('P202499601', 'Patrick Lopez')
        self.add('P202499602', 'Patrick Lim')
        reply = self.ask('my requests Patrick', 'student')
        self.assertEqual(reply['suggestions_title'], 'Whose requests?')
        self.assertEqual(sorted(c['question'] for c in reply['suggestions']), ['my requests P202499601', 'my requests P202499602'])

    def test_only_the_latest_ten_are_listed(self):
        for _ in range(12):
            self.send_request(request_type='Other', detail='')
        self.assertEqual(self.ask(f'my requests {TAYLOR}', 'student')['reply'].count('\n• '), 10)

    def test_only_the_student_chat_has_these_commands(self):
        for role in ('coordinator',):
            for text in ('my requests', 'request help'):
                self.assertNotIn(self.ask(text, role)['intent'], ('my_requests', 'request_help'), (role, text))

    def test_a_long_question_that_mentions_a_request_is_left_to_the_network(self):
        question = 'How do I submit my request for a replacement after I paid at the cashier and brought everything?'
        self.assertNotEqual(self.ask(question, 'student')['intent'], 'my_requests')
        self.assertNotEqual(self.ask('What is the status of my student ID?', 'student')['intent'], 'my_requests')


class RequestWordsTests(unittest.TestCase):
    def test_what_counts_as_following_a_request(self):
        for text in ('my requests', 'My request', 'track my request P202400230', 'track requests', 'request status',
                     'status of my request', "where's my request?", 'where is my request', 'request ko',
                     'nasaan na ang request ko', 'aking requests'):
            self.assertTrue(asks_for_my_requests(text), text)
        for text in ('What is the status of my student ID?', 'I lost my school ID', 'request help', 'requests',
                     'Requirements for a new ID',
                     'How do I submit my request for a replacement after I paid at the cashier and brought everything to the office today?'):
            self.assertFalse(asks_for_my_requests(text), text)

    def test_what_counts_as_asking_for_the_form(self):
        for text in ('request help', 'Request Help', 'requesting help', 'I need to request help', 'I want to submit a request',
                     'make a request', 'please file a request', 'help request', 'request form', 'submit my request.'):
            self.assertTrue(asks_to_request_help(text), text)
        for text in ('How do I request a new ID?', 'request', 'I lost my ID and need help', 'my requests', 'can you help'):
            self.assertFalse(asks_to_request_help(text), text)

    def test_the_subject_is_what_is_left_of_the_message(self):
        self.assertEqual(request_subject('track my request P202400230'), 'P202400230')
        self.assertEqual(request_subject('my requests'), '')
        self.assertEqual(request_subject('Taylor Cruz my requests'), 'Taylor Cruz')

    def test_the_two_chat_answers(self):
        row = {'id': 12, 'name': 'Taylor A. Cruz', 'student_id': TAYLOR, 'request_type': 'Lost ID', 'status': 'Submitted'}
        sent = request_sent_reply(row, list(STUDENT_LOST_STEPS))
        self.assertTrue(sent.startswith(f'✅ Request #12 sent: **Lost ID** for Taylor A. Cruz ({TAYLOR}). Status: **Submitted**.'))
        self.assertIn('1. Go to the Cashier first', sent)
        self.assertIn('2. Then bring', sent)
        self.assertNotIn('1.', request_sent_reply(row, []))
        listing = my_requests_reply(TAYLOR, 'Taylor A. Cruz', [{**row, 'timestamp': '2026-10-22 16:30:11'}])
        self.assertIn('• #12 · Lost ID · **Submitted** · 2026-10-22 16:30 — waiting for the office to look at it', listing)
        self.assertIn('No requests have been made with P202400230 yet', my_requests_reply('P202400230', '', []))


class YearLevelTests(StudentChatCase):
    def test_only_years_one_to_four_can_be_added_or_changed(self):
        for written in ('5A', '6C', '5-1', '7A'):
            response = self.client.post('/api/inventory', json={**NEW, 'year_section': written})
            self.assertEqual(response.status_code, 400, written)
            self.assertIn('from 1 to 4', response.json['error'])
        for year in range(1, 5):
            created = self.client.post('/api/inventory', json={**NEW, 'student_id': f'P20249900{year}', 'year_section': f'{year}A'})
            self.assertEqual(created.status_code, 201, year)
        row = self.student('P202499001')
        self.assertEqual(self.client.patch(f'/api/inventory/{row["id"]}', json={'year_section': '5B'}).status_code, 400)
        self.assertEqual(self.client.patch(f'/api/inventory/{row["id"]}', json={'year_section': '4B'}).status_code, 200)

    def test_a_record_from_before_in_year_five_still_shows_and_can_be_edited(self):
        with self.db() as db:
            db.execute("INSERT INTO inventory (student_id,name,course,section,year_level,status,request_type,token) "
                       "VALUES ('P202499005','Old Fifth','BSIT','A',5,'Pending','New ID','tok-legacy-5')")
        row = self.student('P202499005')
        self.assertEqual(row['year_section'], '5A')
        self.assertIn('Year & Section: 5A', self.ask('P202499005', 'student')['reply'])
        edited = self.client.patch(f'/api/inventory/{row["id"]}', json={'address': 'New street'})
        self.assertEqual((edited.status_code, edited.json['year_section']), (200, '5A'))
        self.assertEqual(self.client.patch(f'/api/inventory/{row["id"]}', json={'year_section': '6A'}).status_code, 400)


class TickerDataTests(StudentChatCase):
    def test_stats_carry_the_latest_events_for_the_dashboard_ticker(self):
        from Chatbot import RECENT_ACTIVITY
        for _ in range(RECENT_ACTIVITY + 3):
            self.client.post('/api/incidents', json={'category': 'No ID', 'detail': 'x'})
        recent = self.client.get('/api/stats').json['recent_activity']
        self.assertEqual(len(recent), RECENT_ACTIVITY)
        self.assertEqual(set(recent[0]), {'actor_role', 'action', 'detail', 'timestamp'})
        self.assertEqual(recent, self.client.get('/api/activity').json['events'][:RECENT_ACTIVITY])    # newest first


if __name__ == '__main__':
    unittest.main()
