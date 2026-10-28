"""The ID process guide: which step a question is about, and that answers never repeat in a conversation."""
import unittest

from chat_support import INTENT_PROMPTS
from id_process import STEPS, chips_for, detect_step, process_answer
from intent_model import ANNIntentModel
from pg_support import AppTestCase

HOURS = 'Mon–Fri, 8 AM–5 PM'
ASKS = {
    'production': 'What happens during production of my school ID?',
    'signature': 'What is the signature step?',
    'canva': 'Do you edit it in Canva?',
    'printing': 'Is it printed already?',
    'case_lace': 'Ano ang case at lace?',
    'releasing': 'How does releasing work?',
}


class StepDetectionTests(unittest.TestCase):
    def test_every_step_is_found_by_its_own_word(self):
        for key, question in ASKS.items():
            self.assertEqual(detect_step(question)['key'], key, question)

    def test_a_question_about_the_whole_process_has_no_step(self):
        self.assertIsNone(detect_step('What is the process of making a school ID?'))
        self.assertIsNone(detect_step('Ano ang mga hakbang?'))

    def test_the_first_step_named_wins(self):
        self.assertEqual(detect_step('after printing, how is the release done?')['key'], 'printing')

    def test_overview_lists_all_six_steps_in_order(self):
        answer, _ = process_answer('what is the process', None, [], HOURS)
        positions = [answer.index(step['title']) for step in STEPS]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(STEPS), 6)

    def test_status_of_the_selected_student_is_mentioned(self):
        student = {'name': 'Ana Cruz', 'status': 'For release'}
        answer, _ = process_answer('what is the process', student, [], HOURS)
        self.assertIn('Ana Cruz', answer)
        self.assertIn('step 6', answer)


class NoRepeatTests(unittest.TestCase):
    def test_a_later_question_never_gets_an_earlier_answer(self):
        for question in list(ASKS.values()) + ['What is the process of making a school ID?']:
            given = []
            for _ in range(4):
                answer, _ = process_answer(question, None, given, HOURS)
                self.assertNotIn(answer, given, question)
                given.append(answer)

    def test_different_steps_have_different_answers(self):
        answers = [process_answer(question, None, [], HOURS)[0] for question in ASKS.values()]
        self.assertEqual(len(set(answers)), len(answers))

    def test_chips_lead_to_another_step_and_name_a_valid_question(self):
        for step in STEPS:
            chips = chips_for(step)
            self.assertTrue(chips)
            self.assertLessEqual(len(chips), 3)
            self.assertNotIn(step['chip'], [chip['label'] for chip in chips])


class ProcessRouteTests(AppTestCase):
    def ask(self, text, session='proc-1', **extra):
        return self.client.post('/api/chat', json={'message': text, 'session_id': session, **extra}).json

    def test_chip_questions_route_to_the_process_intent(self):
        matcher = ANNIntentModel()
        self.assertEqual(matcher.match(INTENT_PROMPTS['id_process'][1]), 'id_process')
        for step in STEPS:
            self.assertEqual(matcher.match(f'What happens during {step["title"].lower()} of my school ID?'), 'id_process', step['title'])

    def test_asking_the_process_twice_gives_different_replies(self):
        first = self.ask('What is the process of making a school ID?')
        second = self.ask('What is the process of making a school ID?')
        self.assertEqual((first['intent'], second['intent']), ('id_process', 'id_process'))
        self.assertNotEqual(first['reply'], second['reply'])

    def test_follow_up_chips_walk_through_the_steps(self):
        reply = self.ask('What happens during canva edit of my school ID?')
        self.assertEqual(reply['intent'], 'id_process')
        self.assertIn('Canva edit', reply['reply'])
        labels = [chip['label'] for chip in reply['follow_ups']]
        self.assertIn('Printing', labels)
        nxt = self.ask(next(chip['question'] for chip in reply['follow_ups'] if chip['label'] == 'Printing'))
        self.assertEqual(nxt['intent'], 'id_process')
        self.assertIn('Printing', nxt['reply'])
        self.assertNotEqual(nxt['reply'], reply['reply'])

    def test_a_new_conversation_may_repeat_but_a_cleared_one_starts_over(self):
        a = self.ask('What happens during signature of my school ID?', session='proc-a')
        b = self.ask('What happens during signature of my school ID?', session='proc-b')
        self.assertEqual(a['reply'], b['reply'])


class NewsTickerForEveryoneTests(AppTestCase):
    def test_news_is_public_and_the_student_page_carries_the_ticker(self):
        self.assertEqual(self.client.get('/api/news').status_code, 200)       # no sign-in needed
        page = self.client.get('/student').get_data(as_text=True)
        self.assertIn('id="ticker"', page)
        self.assertIn('PGPC NEWS', page)
        script = self.client.get('/static/app.js').get_data(as_text=True)
        self.assertIn("view-assistant').querySelector('.heading').after(ticker)", script)   # not just the coordinator's Dashboard
        self.assertNotIn("workspaceRole !== 'coordinator' || !newsItems.length", script)


if __name__ == '__main__':
    unittest.main()
