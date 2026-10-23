"""Tests for chat feedback, suggestion chips, request tracking and the hardening around them."""
import csv
import io
import re
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import numpy as np
from markupsafe import escape

from Chatbot import MAX_STICKERS, UNSURE_REPLY
from chat_support import FEEDBACK_REASONS, FOLLOW_UPS, INTENT_PROMPTS, csv_safe, prompt_chips, redact_text, reply_chips
from intent_model import ANNIntentModel, rank_intents
from pg_support import NEW, AppTestCase

MODEL = ANNIntentModel()


def fake_classifier(label, confidence):
    return patch('Chatbot.classify', return_value=(np.array([label], dtype=object), np.array([confidence])))


class ChatFeedbackTests(AppTestCase):
    def test_reply_has_message_id_and_history_carries_the_rating(self):
        reply = self.ask('What can you do?')
        self.assertIsInstance(reply['message_id'], int)
        self.assertEqual(self.rate(reply, 'up').status_code, 200)
        messages = self.client.get('/api/chat/history', query_string={'session_id': self.session}).json['messages']
        bot = [m for m in messages if m['role'] == 'bot'][-1]
        user = [m for m in messages if m['role'] == 'user'][-1]
        self.assertEqual((bot['id'], bot['rating']), (reply['message_id'], 'up'))
        self.assertIsNone(user['rating'])

    def test_without_a_session_there_is_nothing_to_rate(self):
        reply = self.client.post('/api/chat', json={'message': 'hello'}).json
        self.assertIsNone(reply['message_id'])

    def test_thumbs_down_shares_the_question_only_with_consent_and_redacts_it(self):
        text = (f'Where is the ID of {self.row["name"]}, {self.row["student_id"].lower()}? '
                'Call 0917 123 4567 or mail juan@example.com, ref 99887766')
        shared = self.ask(text)
        private = self.ask(text)
        self.assertEqual(self.rate(shared, 'down', reason='Wrong answer', share_text=True,
                                   comment=f'I am {self.row["name"]}').status_code, 200)
        self.assertEqual(self.rate(private, 'down', reason='Wrong answer').status_code, 200)
        items = self.client.get('/api/feedback').json['items']
        with_text = next(i for i in items if i['shared_question'])
        without_text = next(i for i in items if not i['shared_question'])
        for leaked in (self.row['name'], self.row['student_id'], '0917', 'juan@example.com', '99887766'):
            self.assertNotIn(leaked.lower(), with_text['shared_question'].lower())
            self.assertNotIn(leaked.lower(), with_text['comment'].lower())
        for marker in ('[student ID]', '[name]', '[email]', '[number]'):
            self.assertIn(marker, with_text['shared_question'])
        self.assertEqual(with_text['comment'], 'I am [name]')
        self.assertEqual((without_text['shared_question'], without_text['reason']), ('', 'Wrong answer'))
        self.assertEqual(self.client.get('/api/feedback').json['summary']['shared'], 1)

    def test_feedback_is_bound_to_the_session_that_received_the_reply(self):
        reply = self.ask('What can you do?')
        self.assertEqual(self.rate(reply, 'down', session='someone-else:coordinator', share_text=True).status_code, 404)
        user_row = self.client.get('/api/chat/history', query_string={'session_id': self.session}).json['messages'][0]
        self.assertEqual(user_row['role'], 'user')
        self.assertEqual(self.client.post('/api/chat/feedback', json={
            'message_id': user_row['id'], 'session_id': self.session, 'rating': 'up'}).status_code, 404)
        self.assertEqual(self.client.post('/api/chat/feedback', json={
            'message_id': 987654, 'session_id': self.session, 'rating': 'up'}).status_code, 404)
        self.assertEqual(self.client.get('/api/feedback').json['summary']['total'], 0)
        self.assertEqual(self.client.delete('/api/chat/feedback', query_string={
            'message_id': reply['message_id'], 'session_id': 'someone-else:coordinator'}).json['removed'], False)

    def test_feedback_validation(self):
        reply = self.ask('What can you do?')
        base = {'message_id': reply['message_id'], 'session_id': self.session, 'rating': 'down'}
        bad_bodies = [
            {**base, 'rating': 'meh'}, {**base, 'rating': None}, {**base, 'reason': 'Because'},
            {**base, 'comment': 'x' * 201}, {**base, 'comment': 7}, {**base, 'share_text': 'yes'},
            {**base, 'message_id': True}, {**base, 'message_id': 0}, {**base, 'message_id': 2 ** 63},
            {**base, 'message_id': '3'}, {**base, 'session_id': ''}, {**base, 'session_id': 's' * 121},
            {k: v for k, v in base.items() if k != 'session_id'}]
        for body in bad_bodies:
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/api/chat/feedback', json=body).status_code, 400)
        self.assertEqual(self.client.post('/api/chat/feedback', json=[1]).status_code, 400)
        self.assertEqual(self.raw_post('/api/chat/feedback', '{"message_id": 1, "session_id": "\\ud800", "rating": "up"}').status_code, 400)
        self.assertEqual(self.client.get('/api/feedback').json['summary']['total'], 0)

    def test_thumbs_up_ignores_reason_comment_and_sharing(self):
        reply = self.ask('When can I collect my school ID?')
        self.assertEqual(self.rate(reply, 'up', reason='Wrong answer', comment='nope', share_text=True).json['shared'], False)
        item = self.client.get('/api/feedback').json['items'][0]
        self.assertEqual((item['rating'], item['reason'], item['comment'], item['shared_question']), ('up', '', '', ''))

    def test_a_rating_can_change_and_be_removed(self):
        reply = self.ask('What can you do?')
        self.rate(reply, 'up')
        self.rate(reply, 'down', reason="Didn't understand me")
        data = self.client.get('/api/feedback').json
        self.assertEqual((data['summary']['total'], data['summary']['down']), (1, 1))
        self.assertEqual(data['items'][0]['reason'], "Didn't understand me")
        query = {'message_id': reply['message_id'], 'session_id': self.session}
        self.assertTrue(self.client.delete('/api/chat/feedback', query_string=query).json['removed'])
        self.assertFalse(self.client.delete('/api/chat/feedback', query_string=query).json['removed'])
        self.assertEqual(self.client.get('/api/feedback').json['summary']['total'], 0)
        self.assertEqual(self.client.delete('/api/chat/feedback', query_string={'message_id': 'x'}).status_code, 400)
        self.assertEqual(self.client.delete('/api/chat/feedback', query_string={'message_id': '²', 'session_id': 's'}).status_code, 400)

    def test_review_workflow_filters_stats_and_export(self):
        first, second, third = (self.ask(t) for t in ('What can you do?', 'Where is the office?', 'hello'))
        self.rate(first, 'down', reason='Wrong answer', comment='=HYPERLINK("http://example.com")')
        self.rate(second, 'down', reason='Other')
        self.rate(third, 'up')
        data = self.client.get('/api/feedback').json
        self.assertEqual((data['summary']['total'], data['summary']['up'], data['summary']['down']), (3, 1, 2))
        self.assertEqual(data['summary']['helpful_percent'], 33)
        self.assertEqual(data['summary']['new_down'], 2)
        self.assertEqual(data['summary']['by_reason'], {'Other': 1, 'Wrong answer': 1})
        self.assertIn('id_inventory', data['intents'])
        self.assertEqual(self.client.get('/api/stats').json['new_feedback'], 2)
        down = self.client.get('/api/feedback', query_string={'rating': 'down'}).json['items']
        self.assertEqual(len(down), 2)
        target = down[-1]['id']
        reviewed = self.client.patch(f'/api/feedback/{target}', json={'status': 'Reviewed', 'correct_intent': 'id_inventory'})
        self.assertEqual((reviewed.status_code, reviewed.json['status'], reviewed.json['correct_intent']), (200, 'Reviewed', 'id_inventory'))
        self.assertEqual(self.client.get('/api/stats').json['new_feedback'], 1)
        self.assertEqual(len(self.client.get('/api/feedback', query_string={'status': 'New', 'rating': 'down'}).json['items']), 1)
        for body in ({}, {'status': 'Done'}, {'correct_intent': 'made_up'}, {'correct_intent': 3}, {'other': 1}):
            self.assertEqual(self.client.patch(f'/api/feedback/{target}', json=body).status_code, 400)
        self.assertEqual(self.client.patch('/api/feedback/9999', json={'status': 'Reviewed'}).status_code, 404)
        self.assertEqual(self.client.patch(f'/api/feedback/{target}', json={'correct_intent': ''}).json['correct_intent'], '')
        self.assertEqual(self.client.get('/api/feedback', query_string={'rating': 'sideways'}).status_code, 400)
        self.assertEqual(self.client.get('/api/feedback', query_string={'status': 'Old'}).status_code, 400)
        export = self.client.get('/api/feedback/export')
        self.assertEqual(export.status_code, 200)
        rows = list(csv.reader(io.StringIO(export.data.decode('utf-8-sig'))))
        self.assertEqual(rows[0][:4], ['id', 'timestamp', 'rating', 'reason'])
        self.assertEqual(len(rows), 4)
        self.assertTrue(any(cell.startswith("'=HYPERLINK") for row in rows for cell in row))
        self.assertFalse(any(cell.startswith('=') for row in rows for cell in row))
        log = [e['action'] for e in self.client.get('/api/activity').json['events']]
        self.assertIn('Reviewed chat feedback', log)

    def test_feedback_outlives_trimmed_chat_history(self):
        reply = self.ask('What can you do?')
        self.rate(reply, 'down', reason='Wrong answer')
        for _ in range(60):
            self.ask('hello')
        messages = self.client.get('/api/chat/history', query_string={'session_id': self.session}).json['messages']
        self.assertNotIn(reply['message_id'], [m['id'] for m in messages])
        self.assertEqual(self.client.get('/api/feedback').json['summary']['total'], 1)


class SuggestionLogicTests(unittest.TestCase):
    def labels(self, chips):
        return [chip['intent'] for chip in chips]

    def test_unsure_fallback_offers_up_to_three_second_guesses(self):
        ranked = [('lost_id_report', .25), ('id_release_schedule', .19), ('id_requirements', .15), ('office_hours_location', .11)]
        chips, title, follow = reply_chips(ranked, 'out_of_scope', .25, 'student')
        self.assertEqual(self.labels(chips), ['lost_id_report', 'id_release_schedule', 'id_requirements'])
        self.assertEqual((title, follow), ('Did you mean:', []))
        self.assertTrue(all(c['question'] and c['label'] for c in chips))

    def test_fallback_offers_nothing_when_there_is_nothing_sensible_to_offer(self):
        cases = {
            'no known word': [],
            'clearly unrelated': [('out_of_scope', .98), ('id_release_schedule', .01)],
            'weak best guess': [('qr_sticker_request', .21), ('bot_help', .12)],
            'only chit-chat': [('thanks_goodbye', .29), ('out_of_scope', .09)],
        }
        for name, ranked in cases.items():
            with self.subTest(name):
                self.assertEqual(reply_chips(ranked, 'out_of_scope', .2, 'coordinator'), ([], '', []))

    def test_unsure_out_of_scope_top_still_offers_guesses(self):
        ranked = [('out_of_scope', .30), ('id_processing_status', .28), ('pending_id', .20)]
        chips, _, _ = reply_chips(ranked, 'out_of_scope', .30, 'student')
        self.assertEqual(self.labels(chips), ['id_processing_status', 'pending_id'])

    def test_unsure_answer_shows_runner_up_and_confident_answer_shows_follow_ups(self):
        chips, title, follow = reply_chips([('pending_id_schedule', .50), ('pending_id', .49)], 'pending_id_schedule', .50, 'student')
        self.assertEqual((self.labels(chips), title, follow), (['pending_id'], 'Not what you meant? Try:', []))
        chips, title, follow = reply_chips([('id_processing_status', .97), ('pending_id', .01)], 'id_processing_status', .97, 'student')
        self.assertEqual((chips, title), ([], ''))
        self.assertEqual(self.labels(follow), ['id_release_schedule', 'pending_id', 'qr_sticker_request'])
        chips, _, follow = reply_chips([('lost_id_schedule', .66), ('pending_id_schedule', .27)], 'lost_id_schedule', .66, 'student')
        self.assertEqual((self.labels(chips), follow), (['pending_id_schedule'], []))

    def test_staff_only_topics_are_never_offered_to_other_roles(self):
        ranked = [('id_processing_status', .45), ('id_inventory', .40)]
        self.assertEqual(self.labels(reply_chips(ranked, 'id_processing_status', .45, 'coordinator')[0]), ['id_inventory'])
        for role in ('guard', 'student'):
            self.assertEqual(reply_chips(ranked, 'id_processing_status', .45, role)[0], [])
        # Follow-ups are filtered the same way, for every answered intent and every role.
        for intent in FOLLOW_UPS:
            for role in ('guard', 'student'):
                _, _, follow = reply_chips([(intent, .99)], intent, .99, role)
                self.assertNotIn('id_inventory', self.labels(follow))
        self.assertEqual(self.labels(prompt_chips(['id_inventory', 'bot_help'], 'coordinator')), ['id_inventory', 'bot_help'])
        self.assertEqual(self.labels(prompt_chips(['id_inventory', 'bot_help', 'not_an_intent'], 'student')), ['bot_help'])

    def test_follow_ups_can_be_switched_off_and_conversation_intents_have_none(self):
        self.assertEqual(reply_chips([('pending_id', .95)], 'pending_id', .95, 'student', with_follow_ups=False), ([], '', []))
        for intent in ('greeting', 'thanks_goodbye', 'bot_help'):
            self.assertEqual(reply_chips([(intent, .99)], intent, .99, 'coordinator'), ([], '', []))

    def test_every_chip_question_leads_back_to_its_own_topic(self):
        known = set(MODEL.intents)
        self.assertLessEqual(set(INTENT_PROMPTS), known)
        for intent, targets in FOLLOW_UPS.items():
            self.assertIn(intent, known)
            self.assertLessEqual(set(targets), set(INTENT_PROMPTS))
        for intent, (label, question) in INTENT_PROMPTS.items():
            with self.subTest(intent=intent):
                self.assertTrue(label)
                self.assertEqual(MODEL.match(question), intent)

    def test_rank_intents(self):
        self.assertEqual(rank_intents(MODEL.pipeline, 'purple asteroid quantum banana'), [])
        ranked = rank_intents(MODEL.pipeline, 'When is my ID release date?')
        self.assertEqual(ranked[0][0], 'id_release_schedule')
        self.assertEqual(len(ranked), len(MODEL.intents))
        self.assertEqual([s for _, s in ranked], sorted((s for _, s in ranked), reverse=True))
        self.assertAlmostEqual(sum(s for _, s in ranked), 1.0, places=5)
        self.assertEqual(len(rank_intents(MODEL.pipeline, 'When is my ID release date?', 3)), 3)


class SuggestionRouteTests(AppTestCase):
    def test_unsure_message_gets_chips_automated_notice_and_office_action(self):
        ranked = [('lost_id_report', .25), ('id_release_schedule', .19), ('id_requirements', .15)]
        with fake_classifier('out_of_scope', .25), patch('Chatbot.rank_intents', return_value=ranked):
            reply = self.ask('id', role='student')
        self.assertEqual(reply['reply'], UNSURE_REPLY)
        self.assertEqual([c['intent'] for c in reply['suggestions']], ['lost_id_report', 'id_release_schedule', 'id_requirements'])
        self.assertEqual(reply['suggestions_title'], 'Did you mean:')
        self.assertIn('ask_human', [a['type'] for a in reply['actions']])
        history = self.client.get('/api/chat/history', query_string={'session_id': self.session}).json['messages']
        self.assertEqual(history[-1]['message'], UNSURE_REPLY)

    def test_unrelated_or_empty_vocabulary_message_gets_the_plain_redirect(self):
        for text in ('tell me a joke', 'what is the weather today?'):
            with self.subTest(text):
                reply = self.ask(text)
                self.assertEqual((reply['intent'], reply['suggestions'], reply['follow_ups']), ('out_of_scope', [], []))
                self.assertIn('automated assistant', reply['reply'])
                self.assertIn('ask_human', [a['type'] for a in reply['actions']])

    def test_a_few_plain_words_nobody_matches_are_answered_as_a_student_who_was_not_found(self):
        # Two to five words with no question or filler words look like a name. The network cannot tell a
        # name it has never seen from nonsense, so the answer covers both and points to the office.
        for text in ('purple asteroid quantum banana', 'Juan Dela Cruz'):
            for role in ('student', 'guard', 'coordinator'):
                with self.subTest(text=text, role=role):
                    reply = self.ask(text, role=role)
                    self.assertEqual((reply['intent'], reply['suggestions'], reply['follow_ups']), ('student_not_found', [], []))
                    self.assertIn(f'couldn’t find a student named “{text}”', reply['reply'])
                    self.assertIn('ask_human', [a['type'] for a in reply['actions']])
                    self.assertIsNone(reply['student'])

    def test_confident_answers_come_with_follow_up_chips(self):
        reply = self.ask('What is the status of my student ID?', record_id=self.row['id'])
        self.assertEqual(reply['intent'], 'id_processing_status')
        self.assertEqual(reply['suggestions'], [])
        self.assertTrue(reply['follow_ups'])
        for chip in reply['follow_ups']:
            self.assertEqual(MODEL.match(chip['question']), chip['intent'])

    def test_a_lookup_that_only_asks_for_a_record_has_no_follow_ups(self):
        reply = self.ask('What is the status of my student ID?')
        self.assertIn('Type a student ID', reply['reply'])
        self.assertEqual((reply['suggestions'], reply['follow_ups']), ([], []))

    def test_inventory_answer_now_offers_export(self):
        reply = self.ask('Download the inventory as CSV')
        self.assertIn('Export CSV', reply['reply'])
        self.assertIn('export_csv', [a['type'] for a in reply['actions']])
        guard = self.ask('Download the inventory as CSV', role='guard')
        self.assertNotIn('export_csv', [a['type'] for a in guard['actions']])

    def test_unexpected_errors_are_logged_and_never_leak_details(self):
        with patch('Chatbot.classify', side_effect=RuntimeError('secret detail')), \
                self.assertLogs(self.app.logger, level='ERROR'):
            response = self.client.post('/api/chat', json={'message': 'hello'})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.content_type, 'application/json')
        self.assertNotIn('secret detail', response.get_data(as_text=True))


class HardeningTests(AppTestCase):
    NEW = NEW

    def test_year_and_section_accept_only_a_year_from_one_to_six_and_a_section(self):
        for value in ('7A', '0A', 'A', '3', '', '   ', None, 3, ['3E'], True, '9' * 5000, '³A', '②A', '१A', '3E!!', '3 E E'):
            with self.subTest(value=str(value)[:12]):
                response = self.client.post('/api/inventory', json={**self.NEW, 'year_section': value})
                self.assertEqual(response.status_code, 400)
                self.assertIn('Year & Section', response.json['error'])
        for number, (written, shown) in enumerate((('4B', '4B'), ('6-c', '6C'), (' 3e ', '3E'), ('2 1', '2-1'))):
            student = {**self.NEW, 'student_id': f'P20249{number + 1:04d}', 'year_section': written}
            created = self.client.post('/api/inventory', json=student)
            self.assertEqual((created.status_code, created.json['year_section']), (201, shown), written)
        older = {**self.NEW, 'student_id': 'P202499100', 'section': 'A', 'year_level': 2}      # the older two-field form
        del older['year_section']
        self.assertEqual(self.client.post('/api/inventory', json=older).json['year_section'], '2A')
        for bad in (7, 0, '2.5', None, True):
            self.assertEqual(self.client.post('/api/inventory', json={**older, 'student_id': 'P202499101', 'year_level': bad}).status_code, 400)

    def test_text_that_cannot_be_stored_is_rejected_not_a_crash(self):
        body = '{"student_id":"P202499002","name":"\\ud800","year_section":"1A"}'
        self.assertEqual(self.raw_post('/api/inventory', body).status_code, 400)
        self.assertEqual(self.raw_post('/api/incidents', '{"category":"No ID","detail":"\\ud800"}').status_code, 400)
        self.assertEqual(self.raw_post('/api/chat', '{"message": "hi \\ud800"}').status_code, 400)
        self.assertEqual(self.raw_post('/api/chat', '{"message": "hi", "session_id": "\\ud800"}').json['message_id'], None)
        patch_bad = self.client.patch(f'/api/inventory/{self.row["id"]}', data='{"notes":"\\ud800"}', content_type='application/json')
        self.assertEqual(patch_bad.status_code, 400)
        self.assertEqual(self.raw_post('/api/gate', '{"direction":"in","name":"\\ud800"}').status_code, 400)

    def test_huge_record_numbers_are_not_found_or_invalid_never_a_crash(self):
        huge = '9' * 25
        for method, url in (('get', f'/qr/{huge}.png'), ('get', f'/sticker/{huge}'), ('get', f'/card/{huge}'),
                            ('patch', f'/api/inventory/{huge}'), ('delete', f'/api/inventory/{huge}'),
                            ('patch', f'/api/incidents/{huge}'), ('patch', f'/api/requests/{huge}'),
                            ('patch', f'/api/feedback/{huge}'), ('patch', '/api/inventory/0'), ('get', '/qr/0.png'),
                            ('patch', '/api/inventory/9223372036854775807')):
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, json={'status': 'Pending'})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.patch('/api/inventory/bulk', json={'record_ids': [2 ** 63], 'status': 'Pending'}).status_code, 400)
        self.assertEqual(self.client.patch('/api/inventory/bulk', json={'record_ids': [-1], 'status': 'Pending'}).status_code, 400)
        for bad in (2 ** 70, -1, 0):
            self.assertEqual(self.client.post('/api/chat', json={'message': 'hi', 'record_id': bad}).status_code, 400)

    def test_api_errors_are_json_and_pages_keep_their_html(self):
        missing = self.client.patch('/api/inventory/99999', json={'status': 'Pending'})
        self.assertEqual((missing.status_code, missing.content_type), (404, 'application/json'))
        self.assertIn('error', missing.json)
        too_big = self.client.post('/api/chat', data='{"message": "' + 'x' * 40000 + '"}', content_type='application/json')
        self.assertEqual((too_big.status_code, too_big.content_type), (413, 'application/json'))
        self.assertIn('32 KB', too_big.json['error'])
        wrong = self.client.delete('/api/stats')
        self.assertEqual((wrong.status_code, wrong.content_type), (405, 'application/json'))
        self.assertIn('GET', wrong.headers['Allow'])
        unknown = self.client.get('/api/nothing-here')
        self.assertEqual((unknown.status_code, unknown.content_type), (404, 'application/json'))
        page = self.client.get('/no-such-page')
        self.assertEqual(page.status_code, 404)
        self.assertTrue(page.content_type.startswith('text/html'))
        self.assertEqual(page.headers['Cache-Control'], 'no-store')

    def test_student_ids_are_unique_whatever_the_letter_case(self):
        self.assertEqual(self.client.post('/api/inventory', json={**self.NEW, 'student_id': 'p202400101'}).status_code, 409)
        self.assertEqual(self.client.post('/api/inventory', json={**self.NEW, 'student_id': ' P202400101 '}).status_code, 409)
        self.assertEqual(self.client.post('/api/inventory', json=self.NEW).status_code, 201)
        self.assertEqual(self.client.post('/api/inventory', json={**self.NEW, 'student_id': 'p202499001'}).status_code, 409)
        self.assertEqual(len(self.client.get('/api/inventory').json['records']), len(self.records) + 1)

    def test_student_lookup_leaves_out_staff_notes_and_the_qr_token(self):
        self.client.patch(f'/api/inventory/{self.row["id"]}', json={'notes': 'Called guardian'})
        record = self.client.post('/api/student/lookup', json={'student_id': self.row['student_id'].lower()}).json['record']
        self.assertNotIn('notes', record)
        self.assertNotIn('token', record)
        for field in ('id', 'student_id', 'name', 'year_section', 'status', 'request_type',
                      'pending_reason', 'schedule_type', 'schedule_date', 'schedule_time'):
            self.assertIn(field, record)
        self.assertNotIn('address', record)               # for the coordinator only
        self.assertNotIn('contact_number', record)
        guard = self.client.get('/api/verify', query_string={'q': self.row['student_id']}).json['matches'][0]
        self.assertIn('token', guard)

    def test_verify_finds_tokens_with_underscores_and_treats_wildcards_literally(self):
        # token_urlsafe() output contains "_" and "-"; about one token in three has an underscore.
        token = 'abc_DEF-ghi_JKL'
        with self.db() as db:
            db.execute('UPDATE inventory SET token=%s WHERE id=%s', (token, self.row['id']))
        found = self.client.get('/api/verify', query_string={'q': token}).json['matches']
        self.assertEqual([m['id'] for m in found], [self.row['id']])
        self.assertEqual(self.client.get('/api/verify', query_string={'q': 'abcDEF-ghiJKL'}).json['matches'], [])
        for wildcard in ('_', '%', '%%', '_____'):
            with self.subTest(wildcard=wildcard):
                self.assertEqual(self.client.get('/api/verify', query_string={'q': wildcard}).json['matches'], [])
        self.assertTrue(self.client.get('/api/verify', query_string={'q': self.row['name'][:4].lower()}).json['matches'])
        self.assertEqual([m['id'] for m in self.client.get(
            '/api/verify', query_string={'q': self.row['student_id'].lower()}).json['matches']], [self.row['id']])
        for bad in ('', '   ', 'x' * 101):
            with self.subTest(bad=bad[:5]):
                self.assertEqual(self.client.get('/api/verify', query_string={'q': bad}).status_code, 400)

    def test_exports_neutralise_spreadsheet_formulas(self):
        self.client.post('/api/inventory', json={**self.NEW, 'name': '=cmd|calc'})
        text = self.client.get('/api/inventory/export').data.decode('utf-8-sig')
        names = [row['full_name'] for row in csv.DictReader(io.StringIO(text))]
        self.assertIn("'=cmd|calc", names)
        self.assertNotIn('=cmd|calc', names)
        self.assertEqual(csv_safe('=1+1'), "'=1+1")
        self.assertEqual(csv_safe('-5'), "'-5")
        self.assertEqual((csv_safe('Alex'), csv_safe(3), csv_safe(None), csv_safe('')), ('Alex', 3, None, ''))


class RequestTrackingTests(AppTestCase):
    def submit(self, **extra):
        body = {'student_id': 'P202400103', 'request_type': 'Lost ID', 'detail': 'Wallet stolen near the gate', **extra}
        return self.client.post('/api/requests', json=body).json

    def test_students_see_only_type_status_and_time_of_their_own_requests(self):
        first = self.submit()
        self.submit(request_type='Status inquiry', student_id='p202400103', detail='private words')
        self.submit(student_id='P202400104')
        mine = self.client.post('/api/student/requests', json={'student_id': 'p202400103'}).json['requests']
        self.assertEqual(len(mine), 2)
        self.assertEqual(mine[-1]['id'], first['id'])
        for item in mine:
            self.assertEqual(set(item), {'id', 'request_type', 'status', 'timestamp'})
        self.assertEqual(self.client.post('/api/student/requests', json={'student_id': 'NOBODY'}).json['requests'], [])
        self.assertEqual(self.client.post('/api/student/requests', json={}).status_code, 400)
        self.assertEqual(self.client.post('/api/student/requests', json=[]).status_code, 400)

    def test_inbox_filter_and_counts(self):
        a, b = self.submit(), self.submit(request_type='Other')
        self.client.patch(f'/api/requests/{a["id"]}', json={'status': 'In review'})
        data = self.client.get('/api/requests').json
        self.assertEqual(data['counts'], {'Submitted': 1, 'In review': 1, 'Accepted': 0, 'Closed': 0})
        only = self.client.get('/api/requests', query_string={'status': 'Submitted'}).json['requests']
        self.assertEqual([r['id'] for r in only], [b['id']])
        self.assertEqual(self.client.get('/api/requests', query_string={'status': 'Lost'}).status_code, 400)

    def test_accepting_a_lost_id_request_changes_the_record_only_once(self):
        ticket = self.submit()
        rid = next(r['id'] for r in self.records if r['student_id'] == 'P202400103')
        self.assertEqual(self.client.patch(f'/api/requests/{ticket["id"]}', json={'status': 'Accepted'}).status_code, 200)
        self.client.patch(f'/api/inventory/{rid}', json={'status': 'Processing'})
        again = self.client.patch(f'/api/requests/{ticket["id"]}', json={'status': 'Accepted'})
        self.assertEqual((again.status_code, again.json['status']), (200, 'Accepted'))
        record = next(r for r in self.client.get('/api/inventory').json['records'] if r['id'] == rid)
        self.assertEqual(record['status'], 'Processing')
        self.assertEqual(self.client.patch(f'/api/requests/{ticket["id"]}', json={'status': 'Closed'}).status_code, 200)
        reopen = self.client.patch(f'/api/requests/{ticket["id"]}', json={'status': 'Accepted'})
        self.assertEqual(reopen.status_code, 409)
        self.assertIn('Closed', reopen.json['error'])
        record = next(r for r in self.client.get('/api/inventory').json['records'] if r['id'] == rid)
        self.assertEqual(record['status'], 'Processing')

    def test_allowed_and_blocked_moves(self):
        ticket = self.submit(request_type='Other')
        path = f'/api/requests/{ticket["id"]}'
        self.assertEqual(self.client.patch(path, json={'status': 'In review'}).status_code, 200)
        self.assertEqual(self.client.patch(path, json={'status': 'Submitted'}).status_code, 409)
        self.assertEqual(self.client.patch(path, json={'status': 'Accepted'}).status_code, 200)
        self.assertEqual(self.client.patch(path, json={'status': 'In review'}).status_code, 409)
        self.assertEqual(self.client.patch(path, json={'status': 'Bogus'}).status_code, 400)


class DashboardAndToolTests(AppTestCase):
    def schedule(self, record_id, when, status=None):
        """Set an appointment directly in the database: the counts below must not depend on the release
        hours (which refuse weekends), so the test cannot depend on the day it runs."""
        with self.db() as db:
            db.execute("UPDATE inventory SET schedule_type='Release', schedule_date=%s, schedule_time='09:30' WHERE id=%s",
                       (when.strftime('%Y-%m-%d'), record_id))
            if status:
                db.execute('UPDATE inventory SET status=%s WHERE id=%s', (status, record_id))

    def test_stats_count_today_overdue_and_only_future_appointments(self):
        now = datetime.now()
        ids = [r['id'] for r in self.records]
        self.schedule(ids[0], now)
        self.schedule(ids[1], now - timedelta(days=2), 'Pending')
        self.schedule(ids[2], now - timedelta(days=3), 'Released')
        self.schedule(ids[3], now + timedelta(days=4))
        stats = self.client.get('/api/stats').json
        self.assertEqual(stats['today'], now.strftime('%Y-%m-%d'))
        self.assertEqual((stats['today_appointments'], stats['overdue_appointments']), (1, 1))
        dates = [row['schedule_date'] for row in stats['upcoming_appointments']]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(len(dates), 2)
        self.assertTrue(all(d >= stats['today'] for d in dates))

    def test_gate_list_counts_todays_events(self):
        for direction in ('in', 'in', 'out'):
            self.client.post('/api/gate', json={'student_id': self.row['student_id'], 'direction': direction})
        with self.db() as db:
            db.execute("INSERT INTO gate_logs (student_id,name,direction,note,timestamp) VALUES ('X','Old','in','','2020-01-01 08:00:00')")
        data = self.client.get('/api/gate').json
        self.assertEqual(data['today'], {'in': 2, 'out': 1})
        self.assertEqual(len(data['logs']), 4)

    def test_sticker_sheet(self):
        ids = [r['id'] for r in self.records]
        page = self.client.get('/stickers', query_string={'ids': f'{ids[0]},{ids[1]},{ids[0]}'})
        text = page.get_data(as_text=True)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(text.count('<img'), 2)
        for record in (r for r in self.records if r['id'] in ids[:2]):
            self.assertIn(f'/qr/{record["id"]}.png', text)
            self.assertIn(record['student_id'], text)
        self.assertNotIn(self.records[0]['token'], text)
        self.assertIn('30 × 30 mm', text)
        self.assertEqual(page.headers['Cache-Control'], 'no-store')
        mixed = self.client.get('/stickers', query_string={'ids': f'{ids[0]},424242'}).get_data(as_text=True)
        self.assertEqual(mixed.count('<img'), 1)
        self.assertIn('no longer exist', mixed)
        for bad in ('', 'abc', '1,two', '0', '-3', '9' * 30, '1,' + '9' * 5000):
            with self.subTest(ids=bad[:12]):
                self.assertEqual(self.client.get('/stickers', query_string={'ids': bad}).status_code, 400)
        self.assertEqual(self.client.get('/stickers', query_string={'ids': '424242'}).status_code, 404)
        too_many = ','.join(str(n) for n in range(1, MAX_STICKERS + 2))
        self.assertEqual(self.client.get('/stickers', query_string={'ids': too_many}).status_code, 400)
        exactly = ','.join(str(n) for n in range(1, MAX_STICKERS + 1))
        self.assertIn(self.client.get('/stickers', query_string={'ids': exactly}).status_code, (200, 404))

    def test_help_and_inventory_answers_mention_the_new_tools(self):
        coordinator = self.ask('What can you do?')['reply']
        for word in ('Feedback', 'Needs attention', 'Not yet made', 'import', 'Incidents'):
            self.assertIn(word, coordinator)
        guard = self.ask('What can you do?', role='guard')['reply']
        for word in ('camera', 'Check in', 'gate log', 'No ID or Lost ID'):
            self.assertIn(word, guard)
        student = self.ask('What can you do?', role='student')['reply']
        for word in ('track', 'Cashier'):
            self.assertIn(word, student)


class PageAndContractTests(AppTestCase):
    """What the page and the browser code rely on the server to provide."""

    def test_page_ships_the_feedback_form_built_from_the_server_side_reasons(self):
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('<template id="feedback-template">', page)
        for reason in FEEDBACK_REASONS:
            self.assertIn(f'value="{escape(reason)}"', page)
        self.assertIn('maxlength="200"', page)                 # same cap the server enforces
        self.assertIn(f'data-max="{MAX_STICKERS}"', page)
        self.assertIn('id="scan-dialog"', page)

    def test_the_feedback_comment_cap_in_the_form_is_the_cap_on_the_server(self):
        reply = self.ask('What can you do?')
        self.assertEqual(self.rate(reply, 'down', comment='x' * 200).status_code, 200)
        self.assertEqual(self.rate(reply, 'down', comment='x' * 201).status_code, 400)

    def test_scanner_library_is_served_from_this_app_and_never_from_a_cdn(self):
        for name in ('jsQR.js', 'jsQR.LICENSE.txt', 'README.md'):
            with self.client.get(f'/static/vendor/{name}') as response:      # closes the file handle
                self.assertEqual(response.status_code, 200, name)
                self.assertGreater(len(response.data), 100, name)
        with self.client.get('/static/app.js') as response:
            script = response.get_data(as_text=True)
        self.assertIn("script.src = '/static/vendor/jsQR.js'", script)     # loaded on demand, from this app
        page = self.client.get('/').get_data(as_text=True)
        references = re.findall(r'<(?:script|link)\b[^>]*?\b(?:src|href)="([^"]+)"', page)
        self.assertTrue(references)
        self.assertTrue(all(ref.startswith('/static/') for ref in references), references)

    def test_every_topic_the_model_knows_has_a_display_name(self):
        labels = self.client.get('/api/feedback').json['intent_labels']
        self.assertEqual(set(labels), set(MODEL.intents))
        self.assertTrue(all(isinstance(name, str) and name.strip() for name in labels.values()))

    def test_request_inbox_lists_statuses_in_workflow_order_and_only_moves_forward(self):
        data = self.client.get('/api/requests').json
        self.assertEqual(data['statuses'], ['Submitted', 'In review', 'Accepted', 'Closed'])
        self.assertEqual(data['flow']['Closed'], [])
        for status, options in data['flow'].items():
            for later in options:
                self.assertGreater(data['statuses'].index(later), data['statuses'].index(status))

    def test_the_server_enforces_exactly_the_flow_it_advertises(self):
        flow = self.client.get('/api/requests').json['flow']
        for current in flow:
            for target in flow:
                if target == current:
                    continue
                with self.subTest(move=f'{current} -> {target}'):
                    ticket = self.client.post('/api/requests', json={
                        'student_id': 'P202400103', 'request_type': 'Other', 'detail': 'Question about my ID'}).json
                    path = f'/api/requests/{ticket["id"]}'
                    if current != 'Submitted':
                        self.assertEqual(self.client.patch(path, json={'status': current}).status_code, 200)
                    expected = 200 if target in flow[current] else 409
                    self.assertEqual(self.client.patch(path, json={'status': target}).status_code, expected)

    def test_only_the_coordinator_is_pointed_to_the_feedback_page(self):
        def offered(role):
            return {action['type'] for action in self.ask('What can you do?', role=role)['actions']}
        self.assertIn('open_feedback', offered('coordinator'))
        self.assertNotIn('open_feedback', offered('guard'))
        self.assertNotIn('open_feedback', offered('student'))


class RedactionTests(unittest.TestCase):
    def test_known_people_and_contact_details_are_removed(self):
        text = 'Hi, I am Jamie Santos (demo-002), my mail is jamie.s@school.example and my number is +63 917 123 4567'
        cleaned = redact_text(text, ['DEMO-002'], ['Jamie Santos'])
        self.assertEqual(cleaned, 'Hi, I am [name] ([student ID]), my mail is [email] and my number is [number]')

    def test_parts_of_registered_names_are_removed_but_short_words_stay(self):
        cleaned = redact_text('Sam asked Reyes about the ID', [], ['Sam Reyes'])
        self.assertEqual(cleaned, 'Sam asked [name] about the ID')
        self.assertEqual(redact_text('Cruzade is not Cruz', [], ['Taylor Cruz']), 'Cruzade is not [name]')

    def test_text_is_replaced_in_one_pass_so_placeholders_are_never_rewritten(self):
        # "Student" is part of a registered name AND of the placeholder "[student ID]".
        cleaned = redact_text('demo-001 is New Student, mail new.student@school.example', ['DEMO-001'], ['New Student'])
        self.assertEqual(cleaned, '[student ID] is [name], mail [email]')
        # A name that is also the start of an ID: the whole ID wins.
        self.assertEqual(redact_text('DEMO-001 and Demo', ['DEMO-001'], ['Demo Lee']), '[student ID] and [name]')

    def test_ordinary_questions_are_untouched_and_length_is_capped(self):
        question = 'When can I collect my school ID in year 2?'
        self.assertEqual(redact_text(question, ['DEMO-001'], ['Alex Rivera']), question)
        self.assertEqual(len(redact_text('x' * 900)), 500)
        self.assertEqual(redact_text('  spaced  '), 'spaced')
        self.assertEqual(redact_text('regex (chars) [ok] ?', ['(chars)', ''], ['A.*B']), 'regex [student ID] [ok] ?')


if __name__ == '__main__':
    unittest.main()
