"""Small talk by rules: what they recognise, and what they leave to the ANN.

The first four lists are the questions that used to be rows S001-S060 of the dataset, before it was cut down to
school ID topics. They stay here as the examples the rules must keep recognising.
"""
import csv
import unittest

from intent_model import ROOT
from small_talk import GREETING, HELP, HUMAN, THANKS, small_talk_intent, words

OLD_ROWS = {
    GREETING: ['Hello', 'Hi there', 'Good morning', 'Hey, are you there?', 'Good evening', 'Hello po',
               'Hi po, good morning', 'Hey, musta?', 'gud am po', 'Hi bot, kumusta', 'Magandang umaga po',
               'Magandang hapon po', 'Kumusta po', 'Magandang gabi po', 'Mabuhay po'],
    THANKS: ['Thank you', 'Thanks for your help', 'Bye', "That's all, thanks", 'Goodbye, have a nice day',
             'Thank you po', 'Salamat po sa help', 'Ok na po, thanks', 'Sige po, bye na', 'Thanks bot, ge na',
             'Salamat po', 'Maraming salamat po sa tulong', 'Paalam po', 'Sige po, salamat', 'Hanggang sa muli po'],
    HELP: ['What can you do?', 'How do I use this chatbot?', 'What kind of concerns can you help me with?',
           'Can you explain how this works?', 'What are your features?', 'Ano ang kaya mong gawin as a chatbot?',
           'Paano ko ito i-use?', 'Anong mga concerns ang pwede kong i-report dito?',
           'Pwede mo bang i-explain how this works?', 'Ano ang mga features mo?', 'Ano ang maitutulong mo sa akin?',
           'Paano po gamitin ang chatbot na ito?', 'Anu-ano ang mga bagay na kaya mong sagutin?',
           'Ipaliwanag mo nga kung paano ka gumagana', 'Anong mga serbisyo ang meron ka?'],
    HUMAN: ['I want to talk to a real person', 'Can I speak with a staff member?', 'Connect me to a human agent',
            'Please transfer me to someone from the help desk', 'I would rather talk to a human, not a bot',
            'Gusto ko makausap ang real person', 'Pwede ba akong mag-talk sa staff?', 'Paki-connect mo ako sa human agent',
            'Ayoko na sa bot, staff na lang please', 'Can I speak sa isang tao dito?', 'Gusto kong makausap ang isang tao',
            'Pwede po bang makausap ang isang kawani?', 'Ilipat mo ako sa tunay na tao',
            'Hindi mo ako naiintindihan, tao na lang ang kausapin ko', 'Pakitawag po ang nakatalagang kawani rito'],
}
NEW_VARIANTS = {
    GREETING: ['hi', 'Hello!', 'hello bot', 'Hi, good afternoon', 'Good morning po', 'Magandang umaga po sir', 'Kamusta po?',
               'Hello everyone', "Good evening ma'am", 'Hello po, kumusta?'],
    THANKS: ['thanks', 'Thank you so much', 'thank u po', 'salamat', 'Salamat po ulit', 'ok thanks bye', 'bye bye',
             'Thanks a lot!', 'tysm', 'Maraming salamat po', 'See you!', 'Take care po'],
    HELP: ['help', 'Help me please', 'can you help me?', 'what can i ask you?', 'How does this chatbot work?',
           'Ano ang magagawa mo?', 'Paano gamitin ito?', 'who are you?', 'What are your commands?', 'Hi, what can you do?',
           'What is your name?'],
    HUMAN: ['I need to talk to a human', 'let me speak to a staff', 'Can I call someone from the office?',
            'Connect me to the staff please', 'Gusto ko makausap ang staff', 'Can I talk to the person in charge of IDs?',
            'I want a human agent', 'How do I contact the staff?'],
}
QUESTIONS_THAT_MUST_REACH_THE_ANN = [
    'Hi, when can I get my ID?', 'Thanks! Where is the office?', 'Hello, what is the status of my ID?',
    'Good morning, I lost my ID', 'Salamat po, kailan ang release ng ID ko?', 'What can you do about my lost ID?',
    'How do I use my QR sticker?', 'Paano gamitin ang QR sticker?', 'Can you help me with my math homework?',
    'Help me find my pending ID', 'I need help with my pending ID', 'Where is the Property Management Office?',
    'Can someone else claim my ID for me?', 'Can I send a person to claim my ID?', 'Does the office talk to students on weekends?',
    'Tell me a joke', 'What is the weather like today?', 'May girlfriend ka na ba, bot?',
]


class SmallTalkTests(unittest.TestCase):
    def check(self, examples):
        for intent, texts in examples.items():
            for text in texts:
                with self.subTest(intent=intent, text=text):
                    self.assertEqual(small_talk_intent(text), intent)

    def test_the_questions_that_used_to_be_dataset_rows_are_still_recognised(self):
        self.check(OLD_ROWS)

    def test_new_wordings_are_recognised_too(self):
        self.check(NEW_VARIANTS)

    def test_questions_stay_with_the_ann(self):
        for text in QUESTIONS_THAT_MUST_REACH_THE_ANN:
            with self.subTest(text=text):
                self.assertIsNone(small_talk_intent(text))

    def test_no_dataset_question_is_taken_for_small_talk(self):
        with (ROOT / 'property_management_dataset.csv').open() as file:
            for row in csv.DictReader(file):
                with self.subTest(row['id']):
                    self.assertIsNone(small_talk_intent(row['text']))

    def test_empty_symbols_and_long_messages_are_not_small_talk(self):
        for text in ('', '   ', '???', '👍', 'hello ' * 20, 'x'):
            with self.subTest(text=text[:12]):
                self.assertIsNone(small_talk_intent(text))

    def test_words_drops_punctuation_accents_and_one_letter_words(self):
        self.assertEqual(words("That's all, thanks!"), ['that', 'all', 'thanks'])
        self.assertEqual(words('Paano ko ito i-use?'), ['paano', 'ko', 'ito', 'use'])
        self.assertEqual(words('Kumustá'), ['kumusta'])


if __name__ == '__main__':
    unittest.main()
