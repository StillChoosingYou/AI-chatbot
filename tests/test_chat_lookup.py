"""The chat is the way to look a student up: type an ID or a name and the chat answers with that student.

The coordinator can also ask for the IDs that are not made yet. None of this uses the neural network, only the
database.
"""
import json
import unittest
from unittest import mock

from chat_support import PRIVATE_NOTE
from pg_support import AppTestCase
from student_data import RELEASE_HOURS, STUDENT_ID_EXAMPLE

ROLES = ('student', 'coordinator')


class ChatTestCase(AppTestCase):
    def add(self, student_id, name, year_section='1A', **extra):
        response = self.client.post('/api/inventory', json={
            'student_id': student_id, 'name': name, 'year_section': year_section, **extra})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def types(self, reply):
        return [action['type'] for action in reply['actions']]


class LookupByIdTests(ChatTestCase):
    def test_typing_a_student_id_shows_that_student_to_every_role(self):
        alex = self.student('P202400101')
        for role in ROLES:
            with self.subTest(role=role):
                reply = self.ask('P202400101', role)
                self.assertEqual((reply['intent'], reply['confidence']), ('student_lookup', 0.0))
                for line in ('🪪 **Alex M. Rivera**', 'Student ID: P202400101', 'Year & Section: 1A',
                             'Status: **For release** (ID is made, ready to claim)', 'Request: New ID',
                             'Appointment: none scheduled yet', f'Release hours: {RELEASE_HOURS}'):
                    self.assertIn(line, reply['reply'])
                self.assertEqual(reply['student'], {'id': alex['id'], 'student_id': 'P202400101', 'name': 'Alex M. Rivera'})
                self.assertEqual((reply['suggestions'], reply['follow_ups'], reply['selected_missing']), ([], [], False))

    def test_every_status_comes_with_a_plain_note_and_the_reason_when_pending(self):
        expected = {'P202400102': ('Pending', 'ID not yet made'), 'P202400103': ('Processing', 'ID is being made'),
                    'P202400104': ('Released', 'ID already claimed'), 'P202400101': ('For release', 'ID is made, ready to claim')}
        for student_id, (status, note) in expected.items():
            text = self.ask(student_id, 'student')['reply']
            self.assertIn(f'Status: **{status}** ({note})', text, student_id)
        pending = self.ask('P202400102', 'student')['reply']
        self.assertIn('Why pending: Photo verification needed', pending)
        self.assertNotIn('Release hours', pending)
        self.assertNotIn('Why pending', self.ask('P202400104', 'student')['reply'])

    def test_an_appointment_is_part_of_the_answer(self):
        record_id = self.student('P202400101')['id']
        self.client.patch(f'/api/inventory/{record_id}', json={
            'schedule_type': 'Release', 'schedule_date': '2030-01-07', 'schedule_time': '09:30'})
        self.assertIn('Appointment: Release, **2030-01-07 at 09:30**', self.ask('P202400101', 'student')['reply'])

    def test_the_address_and_contact_number_are_for_the_coordinator_alone(self):
        for role in ROLES:
            with self.subTest(role=role):
                text = self.ask('P202400101', role)['reply']
                if role == 'coordinator':
                    self.assertIn('Address: Purok 3, Brgy. San Isidro, Sample City', text)
                    self.assertIn('Contact number: 09000000101', text)
                    self.assertNotIn(PRIVATE_NOTE, text)
                else:
                    self.assertNotIn('Purok 3', text)
                    self.assertNotIn('09000000101', text)
                    self.assertIn(PRIVATE_NOTE, text)
        self.add('P202499010', 'Bare Student')
        text = self.ask('P202499010', 'coordinator')['reply']
        self.assertIn('Address: not recorded', text)
        self.assertIn('Contact number: not recorded', text)

    def test_staff_notes_and_the_qr_token_are_never_in_an_answer(self):
        alex = self.student('P202400101')
        self.client.patch(f'/api/inventory/{alex["id"]}', json={'notes': 'Internal: the parent teaches here'})
        for role in ROLES:
            for message in ('P202400101', 'Alex Rivera', 'P202400101 P202400102', 'records'):
                reply = self.ask(message, role, record_id=alex['id'])
                dump = json.dumps(reply, ensure_ascii=False)
                self.assertNotIn(alex['token'], dump, (role, message))
                self.assertNotIn('Internal: the parent', dump, (role, message))

    def test_the_id_can_be_in_any_case_or_inside_a_sentence(self):
        for text in ('p202400101', '  P202400101  ', 'P202400101?', 'status of P202400101 please',
                     'Pakicheck ng p202400101', 'what is the status of student P202400101', 'P202400101.'):
            reply = self.ask(text, 'student')
            self.assertEqual(reply['intent'], 'student_lookup', text)
            self.assertEqual(reply['student']['student_id'], 'P202400101', text)

    def test_two_ids_give_two_cards_in_the_order_typed_and_never_more_than_three(self):
        reply = self.ask('P202400104 and P202400101', 'coordinator')
        self.assertEqual(reply['intent'], 'student_lookup')
        self.assertIsNone(reply['student'])                               # the chat is not about one student now
        self.assertLess(reply['reply'].index('Taylor A. Cruz'), reply['reply'].index('Alex M. Rivera'))
        self.assertEqual(reply['reply'].count('Student ID:'), 2)
        every = self.ask('P202400101 P202400102 P202400103 P202400104 P202400105 P202400106', 'coordinator')
        self.assertEqual(every['reply'].count('Student ID:'), 3)
        partly = self.ask('P202400101 and P202499999', 'student')            # the ID that is not there is named, not ignored
        self.assertEqual(partly['reply'].count('Student ID:'), 1)
        self.assertTrue(partly['reply'].endswith('I couldn’t find a student with ID P202499999.'))
        self.assertEqual(partly['student']['student_id'], 'P202400101')

    def test_an_id_that_is_not_on_the_list_says_so_and_offers_the_office(self):
        for role in ROLES:
            reply = self.ask('P202499999', role)
            self.assertEqual(reply['intent'], 'student_not_found', role)
            self.assertIn('find a student with ID P202499999', reply['reply'])
            self.assertNotIn('Student IDs look like', reply['reply'])        # it is shaped like an ID already
            self.assertIn('ask_human', self.types(reply))
            self.assertIsNone(reply['student'])

    def test_something_that_is_not_shaped_like_an_id_is_told_what_one_looks_like(self):
        for text in ('P2024', 'p2024.', '12345'):
            reply = self.ask(text, 'student')
            self.assertEqual(reply['intent'], 'student_not_found', text)
            self.assertIn(f'Student IDs look like {STUDENT_ID_EXAMPLE}', reply['reply'], text)

    def test_numbers_inside_an_ordinary_question_go_on_to_the_assistant(self):
        for text in ('I need 2 copies of my ID in 2024', 'Is the office open at 9am?'):
            reply = self.ask(text, 'student')
            self.assertNotIn(reply['intent'], ('student_lookup', 'student_not_found'), text)
        reply = self.ask('What is the status of my student ID?', 'student')
        self.assertEqual(reply['intent'], 'id_processing_status')
        self.assertTrue(reply['reply'].startswith('⚡ Type a student ID'))

    def test_odd_text_never_breaks_the_lookup(self):
        for text in ("'; DROP TABLE inventory;--", 'P202400101%', '%', '_', 'P_0240010_', '\\', 'P202400101​', '😀 P202400101',
                     'x' * 900, ' '.join(['P20240010' + str(n) for n in range(10)])):
            response = self.client.post('/api/chat', json={'message': text, 'workspace_role': 'coordinator'})
            self.assertEqual(response.status_code, 200, text[:30])
        self.assertEqual(len(self.client.get('/api/inventory').json['records']), 6)
        self.assertEqual(self.client.post('/api/chat', json={'message': 'P2024\x00', 'workspace_role': 'coordinator'}).status_code, 400)


class LookupByNameTests(ChatTestCase):
    def test_a_name_finds_the_student_however_it_is_typed(self):
        for text in ('Alex Rivera', 'alex rivera', 'ALEX M. RIVERA', 'Rivera Alex', 'rivera', 'Alex', 'Rive',
                     'Pakisearch si Alex Rivera', 'status of Alex Rivera please', 'Ano ang status ng ID ni Alex Rivera?',
                     'may record ba si Alex Rivera?'):
            for role in ROLES:
                reply = self.ask(text, role)
                self.assertEqual(reply['intent'], 'student_lookup', (text, role))
                self.assertEqual(reply['student']['student_id'], 'P202400101', (text, role))

    def test_accents_do_not_get_in_the_way(self):
        self.add('P202499020', 'José Muñoz', '2C')
        for text in ('jose munoz', 'José Muñoz', 'MUÑOZ', 'munoz jose'):
            self.assertEqual(self.ask(text, 'student')['student']['student_id'], 'P202499020', text)

    def test_may_is_a_name_first_and_an_everyday_word_second(self):
        self.add('P202499021', 'May Anne Lopez', '1B')
        self.assertEqual(self.ask('May Anne Lopez', 'student')['student']['student_id'], 'P202499021')
        self.assertEqual(self.ask('may Lopez', 'student')['student']['student_id'], 'P202499021')
        self.assertEqual(self.ask('may Alex Rivera', 'student')['student']['student_id'], 'P202400101')

    def test_a_name_typed_in_full_wins_over_longer_names_that_contain_it(self):
        self.add('P202499022', 'Alexander Rivera', '3B')
        self.assertEqual(self.ask('Alex Rivera', 'student')['student']['student_id'], 'P202400101')
        reply = self.ask('Rivera', 'student')                                     # but the family name alone is vague
        self.assertIsNone(reply['student'])
        self.assertIn('I found 2 students', reply['reply'])

    def test_several_matches_are_listed_and_a_tap_on_a_name_opens_that_student(self):
        self.add('P202499023', 'Ana Rivera', '2A')
        for role in ROLES:
            reply = self.ask('Rivera', role)
            self.assertEqual(reply['intent'], 'student_lookup', role)
            self.assertIsNone(reply['student'])
            self.assertIn('I found 2 students that match “Rivera”', reply['reply'])
            self.assertIn('• Ana Rivera · P202499023 · 2A · Pending', reply['reply'])
            self.assertIn('• Alex M. Rivera · P202400101 · 1A · For release', reply['reply'])
            self.assertEqual(reply['suggestions_title'], 'Which student?')
            self.assertEqual({chip['question'] for chip in reply['suggestions']}, {'P202499023', 'P202400101'})
            self.assertNotIn('Address', reply['reply'])                           # a list never carries private details
            chosen = self.ask(reply['suggestions'][0]['question'], role)
            self.assertEqual(chosen['intent'], 'student_lookup')
            self.assertIsNotNone(chosen['student'])

    def test_a_name_that_matches_too_many_students_is_not_listed(self):
        for number, word in enumerate(('One', 'Two', 'Three', 'Four', 'Five', 'Six'), start=1):
            self.add(f'P2024993{number:02d}', f'{word} Villanueva', '1A')
        reply = self.ask('Villanueva', 'student')
        self.assertEqual(reply['intent'], 'student_lookup')
        self.assertIn('matches 6 students, which is too many to list', reply['reply'])
        self.assertEqual(reply['suggestions'], [])
        self.assertNotIn('Two Villanueva', reply['reply'])

    def test_a_name_that_is_not_on_the_list_says_so_instead_of_guessing(self):
        for text in ('Maria Clara', 'Juan Dela Cruz'):
            reply = self.ask(text, 'student')
            self.assertEqual(reply['intent'], 'student_not_found', text)
            self.assertIn(f'find a student named “{text}”', reply['reply'])
            self.assertIn('ask_human', self.types(reply))

    def test_a_student_who_is_added_can_be_found_at_once_and_one_who_is_deleted_cannot(self):
        created = self.add('P202499024', 'Reynald Jake L. Malabanan', '3E')
        self.assertEqual(self.ask('Reynald Malabanan', 'student')['student']['student_id'], 'P202499024')
        self.assertIn('Year & Section: 3E', self.ask('P202499024', 'student')['reply'])
        self.client.delete(f'/api/inventory/{created["id"]}')
        self.assertEqual(self.ask('P202499024', 'student')['intent'], 'student_not_found')
        self.assertEqual(self.ask('Reynald Malabanan', 'student')['intent'], 'student_not_found')

    def test_questions_with_no_name_in_them_are_not_taken_for_one(self):
        for text, intent in (('When can I collect my school ID?', 'id_release_schedule'),
                             ('What documents are needed for a new student ID?', 'id_requirements'),
                             ('Where is the Property Management Office?', 'office_hours_location'),
                             ('How can I print a small QR sticker?', 'qr_sticker_request'),
                             ('I lost my school ID', 'lost_id_report')):
            self.assertEqual(self.ask(text, 'student')['intent'], intent, text)


class FollowUpTests(ChatTestCase):
    """After a lookup the browser sends the student's record_id with the next question."""

    def test_a_question_after_a_lookup_is_about_that_student(self):
        record_id = self.student('P202400101')['id']
        self.client.patch(f'/api/inventory/{record_id}', json={
            'schedule_type': 'Release', 'schedule_date': '2030-01-07', 'schedule_time': '09:30'})
        for role in ROLES:
            reply = self.ask('When can I collect my school ID?', role, record_id=record_id)
            self.assertEqual(reply['intent'], 'id_release_schedule', role)
            self.assertIn('**2030-01-07 at 09:30**', reply['reply'])
            self.assertIn(f'Release hours: {RELEASE_HOURS}', reply['reply'])

    def test_the_coordinator_and_the_student_are_told_the_status(self):
        jamie = self.student('P202400102')
        coordinator = self.ask('What is the status of my student ID?', 'coordinator', record_id=jamie['id'])['reply']
        self.assertIn('Jamie L. Santos: ID status is **Pending**', coordinator)
        reason = self.ask('Why is my student ID pending?', 'student', record_id=jamie['id'])['reply']
        self.assertIn('Photo verification needed', reason)

    def test_without_a_student_the_chat_asks_for_one_and_a_deleted_student_counts_as_none(self):
        reply = self.ask('When can I collect my school ID?', 'student')
        self.assertTrue(reply['reply'].startswith(f'⚡ Type a student ID (for example {STUDENT_ID_EXAMPLE})'))
        record = self.add('P202499030', 'Temporary Student')
        self.client.delete(f'/api/inventory/{record["id"]}')
        gone = self.ask('When can I collect my school ID?', 'student', record_id=record['id'])
        self.assertTrue(gone['selected_missing'])
        self.assertTrue(gone['reply'].startswith('⚡ Type a student ID'))
        self.assertEqual(self.client.post('/api/chat', json={'message': 'hi', 'record_id': 'abc'}).status_code, 400)

    def test_the_history_remembers_which_student_each_message_was_about(self):
        alex = self.student('P202400101')
        self.ask('P202400101', 'student')
        messages = self.client.get('/api/chat/history', query_string={'session_id': self.session}).json['messages']
        self.assertEqual([(m['role'], m['intent'], m['record_id']) for m in messages],
                         [('user', '', alex['id']), ('bot', 'student_lookup', alex['id'])])
        self.assertIn('Alex M. Rivera', messages[-1]['message'])

    def test_a_lookup_answer_can_be_rated_like_any_other(self):
        reply = self.ask('P202400101', 'student')
        self.assertEqual(self.rate(reply, 'up').status_code, 200)
        self.assertEqual(self.rate(reply, 'down', reason='Wrong answer', share_text=True).status_code, 200)
        item = self.client.get('/api/feedback').json['items'][0]
        self.assertEqual((item['rating'], item['intent']), ('down', 'student_lookup'))
        self.assertEqual(item['shared_question'], '[student ID]')                  # the shared question is redacted


class LookupDoesNotUseTheNetworkTests(ChatTestCase):
    """The neural network only labels what a question is about. Finding a student is a database search."""

    def test_lookups_commands_and_lists_work_even_if_the_network_is_broken(self):
        broken = mock.patch('Chatbot.classify', side_effect=RuntimeError('the network must not be asked'))
        with broken:
            for role in ROLES:
                self.assertEqual(self.ask('P202400101', role)['intent'], 'student_lookup', role)
                self.assertEqual(self.ask('Alex Rivera', role)['intent'], 'student_lookup', role)
                self.assertEqual(self.ask('P202499999', role)['intent'], 'student_not_found', role)
                self.assertEqual(self.ask('records', role)['intent'], 'records_list', role)
            # an ordinary question does need it: the failure is a clean error, not a crash
            with self.assertLogs(self.app.logger, 'ERROR'):
                response = self.client.post('/api/chat', json={'message': 'When is my ID release?'})
            self.assertEqual(response.status_code, 500)
            self.assertIn('error', response.json)


class RecordsListTests(ChatTestCase):
    def test_records_lists_the_ids_that_are_not_made_yet_for_the_coordinator(self):
        for role in ('coordinator',):
            reply = self.ask('records', role)
            self.assertEqual(reply['intent'], 'records_list', role)
            lines = reply['reply'].splitlines()
            self.assertEqual(lines[0], '📂 IDs not yet made: 4 (Pending 2 · Processing 2)')
            self.assertEqual(lines[1:], ['• Jordan F. Flores · P202400106 · 2A · Pending — Enrollment verification needed',
                                         '• Jamie L. Santos · P202400102 · 2B · Pending — Photo verification needed',
                                         '• Casey P. Garcia · P202400105 · 1A · Processing',
                                         '• Sam D. Reyes · P202400103 · 3A · Processing'])
            self.assertEqual(reply['suggestions_title'], 'Open a record:')
            self.assertEqual([chip['question'] for chip in reply['suggestions']],
                             ['P202400106', 'P202400102', 'P202400105', 'P202400103'])
            self.assertNotIn('Address', reply['reply'])
        self.assertEqual(self.types(self.ask('records', 'coordinator')), ['show_not_made'])

    def test_it_is_asked_for_in_english_and_in_tagalog(self):
        for text in ('Records', 'list records', 'show me the student records', 'Hindi pa nagagawang ID', 'hindi pa nagawa',
                     'di pa nagagawang ID', 'ID not yet made', 'IDs not made yet', 'unmade IDs', 'Hindi-pa-nagagawa'):
            self.assertEqual(self.ask(text, 'coordinator')['intent'], 'records_list', text)

    def test_a_student_is_not_given_the_list(self):
        for text in ('records', 'hindi pa nagagawang ID'):
            reply = self.ask(text, 'student')
            self.assertEqual(reply['intent'], 'records_list')
            self.assertIn('is for the office', reply['reply'])
            for name in ('Jordan', 'Jamie', 'Casey', 'Sam D. Reyes'):
                self.assertNotIn(name, reply['reply'])
            self.assertEqual(reply['suggestions'], [])

    def test_a_tap_on_a_name_in_the_list_opens_that_student(self):
        reply = self.ask('records', 'coordinator')
        opened = self.ask(reply['suggestions'][0]['question'], 'coordinator')
        self.assertEqual(opened['student']['name'], 'Jordan F. Flores')

    def test_when_every_id_is_made_the_list_says_so(self):
        ids = [r['id'] for r in self.records]
        self.client.patch('/api/inventory/bulk', json={'record_ids': ids, 'status': 'Released'})
        reply = self.ask('records', 'coordinator')
        self.assertIn('Every student on the dashboard has an ID that is made', reply['reply'])
        self.assertEqual((reply['suggestions'], reply['actions']), ([], []))

    def test_a_long_list_stops_at_ten_and_the_coordinator_is_pointed_to_the_dashboard(self):
        for number, word in enumerate(('Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot', 'Golf', 'Hotel')):
            self.add(f'P2024992{number:02d}', f'Filler {word}')
        coordinator = self.ask('records', 'coordinator')['reply']
        self.assertEqual(coordinator.splitlines()[0], '📂 IDs not yet made: 12 (Pending 10 · Processing 2)')
        self.assertEqual(coordinator.count('• '), 10)
        self.assertIn('…and 2 more. Use the “Not yet made” filter on the Dashboard to see them all.', coordinator)
        self.assertEqual(len(self.ask('records', 'coordinator')['suggestions']), 6)

    def test_the_word_records_inside_a_real_question_does_not_trigger_the_list(self):
        for text in ('Where are the student records kept?', 'Does the office keep records of lost IDs?'):
            reply = self.ask(text, 'coordinator')
            self.assertNotEqual(reply['intent'], 'records_list', text)


class WelcomeTests(ChatTestCase):
    def test_every_role_is_welcomed_with_the_same_invitation_to_type_a_student(self):
        for role in ROLES:
            reply = self.ask('hello', role)
            self.assertEqual(reply['intent'], 'greeting', role)
            self.assertIn('Type a student ID or name', reply['reply'], role)
        self.assertEqual(self.types(self.ask('hello', 'student')), ['open_student_request', 'ask_office'])
        coordinator = self.ask('hello', 'coordinator')
        self.assertEqual(self.types(coordinator),
                         ['open_queue', 'open_requests', 'send', 'open_incidents', 'open_incident', 'open_feedback'])
        self.assertEqual([a['text'] for a in coordinator['actions'] if a['type'] == 'send'], ['records'])

    def test_the_help_text_of_each_role_mentions_what_that_role_can_do(self):
        expected = {'coordinator': ('records', 'import students', 'Not yet made', 'Lost ID'),
                    'student': ('student ID or name', 'Request help', 'Cashier')}
        for role, words in expected.items():
            text = self.ask('What can you do?', role)['reply']
            for word in words:
                self.assertIn(word, text, (role, word))


if __name__ == '__main__':
    unittest.main()
