"""Short topic words by rules: "status", "canva", "qr" and the like."""
import unittest

from pg_support import AppTestCase
from topic_words import PHRASE_TO_TOPIC, TOPIC_PHRASES, topic_word_intent


class TopicWordRuleTests(unittest.TestCase):
    def test_every_table_phrase_gives_its_own_topic(self):
        for topic, phrases in TOPIC_PHRASES.items():
            for phrase in phrases:
                with self.subTest(phrase=phrase):
                    self.assertEqual(topic_word_intent(phrase), topic)

    def test_case_punctuation_and_polite_words_do_not_matter(self):
        for text, topic in (('STATUS?', 'id_processing_status'), ('Canva po', 'id_process'), ('  release   date!! ', 'id_release_schedule'),
                            ('Nawala po', 'lost_id_report'), ('QR code, please', 'qr_student_details')):
            with self.subTest(text=text):
                self.assertEqual(topic_word_intent(text), topic)

    def test_a_real_question_is_left_to_the_ann(self):
        for text in ('status of my ID', 'when is the release date', 'hello', 'what is a canva edit of my ID', 'purple asteroid',
                     'canva banana', '', '   ', '???', 'status ng ID ko ngayon'):
            with self.subTest(text=text):
                self.assertIsNone(topic_word_intent(text))

    def test_no_phrase_is_in_two_topics(self):
        self.assertEqual(len(PHRASE_TO_TOPIC), sum(len(v) for v in TOPIC_PHRASES.values()))


class TopicWordsInTheChatTests(AppTestCase):
    def test_one_word_questions_get_the_topic_answer(self):
        for text, intent in (('canva', 'id_process'), ('requirements', 'id_requirements'), ('nawala', 'lost_id_report'),
                             ('office hours', 'office_hours_location'), ('sticker', 'qr_sticker_request'), ('qr', 'qr_student_details'),
                             ('release date', 'id_release_schedule'), ('pending', 'pending_id'), ('status', 'id_processing_status')):
            with self.subTest(text=text):
                reply = self.ask(text)
                self.assertEqual(reply['intent'], intent)
                self.assertTrue(reply['reply'])

    def test_unrelated_single_words_still_get_the_fallback(self):
        for text in ('banana', 'weather', 'asteroid'):
            with self.subTest(text=text):
                self.assertEqual(self.ask(text)['intent'], 'out_of_scope')


if __name__ == '__main__':
    unittest.main()
