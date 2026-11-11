"""Chat moderation: the rules and model judge a message; the strike ladder, the admin review and the chat itself act on it."""
import os
import time
import unittest
from unittest.mock import patch

import chat_moderation as cm
import moderation
from pg_support import DatabaseTestCase, run_together

ALEX = 'P202400101'
ENV = {'STUDENT_LOGIN': '1', 'ADMIN_PASSWORD': 'admin-pass', 'COORDINATOR_PASSWORD': 'coord-pass', 'ADMIN_CAPTCHA': '0'}
CREDS = {'username': 'Admin', 'password': 'admin-pass'}
RUDE = 'putang ina mo bobo ka'
THREAT = 'papatayin kita, i will kill you'
SEVERE = {'severity': 3, 'categories': ['threat'], 'source': 'rules', 'score': None}
STRIKE = {'severity': 2, 'categories': ['insult'], 'source': 'rules', 'score': None}


class JudgementTests(unittest.TestCase):
    def test_school_questions_are_never_flagged(self):
        for text in ('When is my ID release?', 'I lost my ID, what do I do?', 'ang tagal ng ID ko', 'magkano ang replacement fee',
                     'will I beat the traffic if I go to the office at 8am?', 'Hello po'):
            self.assertFalse(moderation.check(text)['flagged'], text)

    def test_profanity_insults_and_threats_are_flagged_with_their_category(self):
        self.assertGreaterEqual(moderation.check(RUDE)['severity'], 2)
        verdict = moderation.check(THREAT)
        self.assertEqual(verdict['severity'], 3)
        self.assertIn('threat', verdict['categories'])

    def test_disguised_words_are_still_caught_and_the_excerpt_is_masked(self):
        verdict = moderation.check('p u t a n g  i n a  m o')
        self.assertTrue(verdict['flagged'])
        self.assertNotIn('putang', verdict['masked'].lower().replace(' ', ''))

    def test_a_rude_word_typed_together_with_ka_or_mo_is_still_caught(self):
        for text in ('hayopka', 'Hayop ka', 'hayop ka!', 'gagoka', 'bobomo', 'ulolka', 'tangaka!!'):
            verdict = moderation.check(text)
            self.assertTrue(verdict['flagged'], text)
            self.assertGreaterEqual(verdict['severity'], 2, text)
        for text in ('tamaka', 'salamat ka', 'pwedeng ka mag-ask', 'Thank you!', 'thankyou po'):
            self.assertFalse(moderation.check(text)['flagged'], text)

    def test_a_cry_for_help_is_never_punished(self):
        verdict = moderation.check('gusto ko nang mamatay')
        self.assertTrue(verdict['crisis'])
        self.assertFalse(verdict['flagged'])


class LadderTests(unittest.TestCase):
    def test_strikes_climb_from_warning_to_suspension(self):
        steps = [cm.decide(n, 0, 2) for n in range(1, 6)]
        self.assertEqual(steps, [('warning', 0), ('restricted', 300), ('restricted', 1800), ('restricted', 86400), ('suspended', 0)])

    def test_a_threat_pauses_at_once_and_a_second_one_suspends(self):
        self.assertEqual(cm.decide(1, 0, 3), ('restricted', 86400))
        self.assertEqual(cm.decide(2, 1, 3), ('suspended', 0))


class StoreTests(DatabaseTestCase):
    def go(self, verdict, subject='student:p1', **kw):
        return cm.record(self.connect, subject, 'Test', 'student', 's1', verdict, 'x ***', True, **kw)

    def setUp(self):
        super().setUp()
        self.connect = self.make_app().extensions['database'].connect

    def test_the_ladder_is_followed_and_reports_are_stored(self):
        now = time.time()
        actions = [self.go(STRIKE, now_ts=now + i)['action'] for i in range(5)]
        self.assertEqual(actions, ['warning', 'restricted', 'restricted', 'restricted', 'suspended'])
        self.assertEqual(cm.gate(self.connect, 'student:p1', now + 10)['state'], 'suspended')
        with self.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM violations').fetchone()[0], 5)

    def test_a_notice_is_recorded_but_never_counts(self):
        for _ in range(4):
            out = self.go({**STRIKE, 'severity': 1})
            self.assertEqual(out['action'], 'notice')
        self.assertIsNone(cm.gate(self.connect, 'student:p1'))
        self.assertEqual(self.go(STRIKE)['action'], 'warning')

    def test_the_pause_ends_by_itself(self):
        now = time.time()
        self.go(STRIKE, now_ts=now)
        self.go(STRIKE, now_ts=now + 1)
        self.assertEqual(cm.gate(self.connect, 'student:p1', now + 60)['state'], 'restricted')
        self.assertIsNone(cm.gate(self.connect, 'student:p1', now + 400))

    def test_the_shared_coordinator_account_is_logged_but_never_blocked(self):
        out = cm.record(self.connect, 'coordinator', 'Coordinator', 'coordinator', 's', SEVERE, 'x', False)
        self.assertEqual(out['action'], 'logged')
        self.assertIsNone(cm.gate(self.connect, 'coordinator'))

    def test_dismissing_a_false_alarm_gives_the_strike_back_and_lifts_the_pause(self):
        now = time.time()
        self.go(STRIKE, now_ts=now)
        second = self.go(STRIKE, now_ts=now + 1)
        self.assertEqual(cm.gate(self.connect, 'student:p1', now + 5)['state'], 'restricted')
        cm.review_report(self.connect, second['report_id'], 'dismiss', 'sarcasm', 'admin', now_ts=now + 3)
        self.assertIsNone(cm.gate(self.connect, 'student:p1', now + 5))

    def test_admin_decisions_about_a_person(self):
        self.go(SEVERE)
        self.go(SEVERE)
        self.assertEqual(cm.gate(self.connect, 'student:p1')['state'], 'suspended')
        self.assertEqual(cm.user_action(self.connect, 'student:p1', 'uphold')['review'], 'upheld')
        self.assertEqual(cm.user_action(self.connect, 'student:p1', 'lift')['state'], 'ok')
        self.assertIsNone(cm.gate(self.connect, 'student:p1'))
        self.assertIsNone(cm.user_action(self.connect, 'student:nobody', 'lift'))

    def test_flagged_messages_sent_together_are_counted_one_by_one(self):
        results = run_together([(lambda: self.go(STRIKE)['strikes']) for _ in range(6)])
        self.assertEqual([r for r in results if isinstance(r, BaseException)], [])
        self.assertEqual(sorted(results), [1, 2, 3, 4, 5, 6])


class ChatTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.dict(os.environ, ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = self.make_app()
        self.admin = self.app.test_client()
        self.admin.post('/admin/login', data=CREDS)
        pin = self.admin.post('/api/admin/pins/issue', json={'student_id': ALEX}).json['pin']
        self.student = self.app.test_client()
        self.student.post('/student/login', data={'student_id': ALEX, 'pin': pin})

    def say(self, text, client=None):
        return (client or self.student).post('/api/chat', json={'message': text, 'workspace_role': 'student', 'session_id': 'sess-1'})

    def test_a_clean_question_is_answered_as_before(self):
        reply = self.say('What do I need for a new ID?').json
        self.assertNotEqual(reply['intent'].split('_')[0], 'moderation')
        self.assertIsNone(reply.get('moderation'))

    def test_the_first_offence_is_a_warning_then_the_chat_pauses(self):
        first = self.say(RUDE).json
        self.assertEqual(first['moderation']['action'], 'warning')
        self.assertIn('first warning', first['reply'])
        second = self.say(RUDE).json
        self.assertEqual(second['moderation']['action'], 'restricted')
        later = self.say('When is my ID release?').json
        self.assertEqual(later['intent'], 'moderation_blocked')

    def test_the_stored_history_holds_the_masked_text_not_the_insult(self):
        self.say(RUDE)
        with self.db() as db:
            stored = ' '.join(r[0] for r in db.execute("SELECT message FROM chat_history WHERE role = 'user'"))
        self.assertNotIn('putang', stored.lower())

    def test_a_long_flagged_message_is_kept_in_full_for_the_administrator(self):
        long_text = ('ang tagal ng ID ko at ' * 30) + 'putang ina mo bobo ka ' + ('salamat po ' * 20)
        self.assertLess(len(long_text), 1000)
        self.say(long_text)
        excerpt = self.admin.get('/api/admin/moderation').json['reports'][0]['excerpt']
        self.assertGreater(len(excerpt), 600)
        self.assertTrue(excerpt.endswith('salamat po'))
        self.assertNotIn('putang', excerpt.lower())

    def test_the_administrator_sees_the_report_and_can_dismiss_it(self):
        self.say(THREAT)
        data = self.admin.get('/api/admin/moderation').json
        self.assertEqual(data['stats']['urgent'], 1)
        self.assertEqual(data['users'][0]['subject'], f'student:{ALEX.lower()}')
        self.assertEqual(self.admin.get('/api/admin/overview').json['moderation_new'], 1)
        report = data['reports'][0]
        self.assertEqual(self.admin.post(f'/api/admin/moderation/report/{report["id"]}', json={'action': 'dismiss'}).status_code, 200)
        self.assertEqual(self.say('When is my ID release?').json['intent'].startswith('moderation'), False)

    def test_a_suspension_can_be_lifted_by_the_administrator(self):
        self.say(THREAT)
        self.say(THREAT)
        self.assertEqual(self.say('hello').json['intent'], 'moderation_blocked')
        done = self.admin.post('/api/admin/moderation/user', json={'subject': f'student:{ALEX.lower()}', 'action': 'lift'})
        self.assertEqual(done.json['state'], 'ok')
        self.assertFalse(self.say('hello').json['intent'].startswith('moderation'))

    def test_only_the_administrator_can_use_the_moderation_api(self):
        for method, url in (('get', '/api/admin/moderation'), ('post', '/api/admin/moderation/user')):
            self.assertEqual(getattr(self.student, method)(url).status_code, 401)

    def test_a_crisis_message_gets_support_and_no_strike(self):
        reply = self.say('gusto ko nang mamatay').json
        self.assertEqual(reply['intent'], 'crisis_support')
        self.assertEqual(self.admin.get('/api/admin/moderation?status=all').json['stats']['total'], 0)

    def test_a_message_only_the_model_doubts_is_recorded_but_still_answered(self):
        verdict = {'flagged': True, 'severity': 1, 'categories': ['insult'], 'terms': [], 'masked': 'maybe rude words here',
                   'crisis': False, 'source': 'model', 'score': 0.9}
        with patch('moderation.check', return_value=verdict):
            reply = self.say('What do I need for a new ID?').json
        self.assertFalse(reply['intent'].startswith('moderation'))
        self.assertEqual(self.admin.get('/api/admin/moderation?status=review').json['stats']['to_review'], 1)
        self.assertEqual(self.admin.get('/api/admin/moderation').json['reports'], [])          # not in "needs review"

    def test_a_name_is_not_flagged_by_the_model_and_a_model_doubt_never_stops_a_request(self):
        self.assertFalse(moderation.check('Alex')['flagged'])
        self.assertFalse(self.say('talk to a staff').json['intent'].startswith('moderation'))

    def test_moderation_can_be_switched_off(self):
        with patch.dict(os.environ, {'CHAT_MODERATION': '0'}):
            self.assertFalse(self.say(RUDE).json['intent'].startswith('moderation'))


if __name__ == '__main__':
    unittest.main()


class IntentNameTests(unittest.TestCase):
    """The admin screens show readable names, never the raw identifiers."""

    def test_every_topic_and_every_moderation_answer_has_a_proper_name(self):
        import csv
        from chat_support import INTENT_NAMES, intent_name
        with open('property_management_dataset.csv', encoding='utf-8') as f:
            topics = {row['intent'] for row in csv.DictReader(f)}
        for intent in topics | {'out_of_scope', 'student_lookup', 'own_record_only', 'crisis_support',
                                'moderation_notice', 'moderation_warning', 'moderation_restricted',
                                'moderation_suspended', 'moderation_blocked', 'moderation_logged'}:
            self.assertIn(intent, INTENT_NAMES, intent)
            self.assertNotIn('_', intent_name(intent))

    def test_an_unknown_answer_type_is_still_made_readable(self):
        from chat_support import intent_name
        self.assertEqual(intent_name('some_new_thing'), 'Some new thing')
