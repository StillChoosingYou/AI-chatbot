"""Builds moderation_dataset.csv, the training data of the chat-moderation model. Run: python build_moderation_dataset.py

The rows are made from templates and word lists (moderation.py), written by the team, so the file is synthetic.
To measure what the model really learned, the test split holds out:
  * 30% of the rude words (the model must catch words it never saw in training), and
  * 20% of the sentence frames (it must catch known words in sentences it never saw).
The clean rows are the 351 real questions of property_management_dataset.csv plus hand-written everyday messages,
including angry-but-polite complaints and words that merely look rude (names such as Dickson, "kill the queue").
A quarter of the rude rows are disguised (g@go, putaaa, p.u.t.a) so the model learns the usual tricks.
"""
import csv
import random
from pathlib import Path

import moderation as m

ROOT = Path(__file__).resolve().parent
RNG = random.Random(20261103)

CLEAN_HAND = [
    'Hello po, good morning', 'Good afternoon, I have a question about my ID', 'Thank you so much for your help',
    'Salamat po sa tulong', 'Maraming salamat, ang bilis ng sagot', 'Okay po, noted', 'Pwede po ba magtanong?',
    'Can I ask something about my ID?', 'Sige po, pupunta ako bukas', 'I will go to the office tomorrow morning',
    'I will bring my receipt and my photo', 'Dadalhin ko po ang resibo at litrato ko', 'Kill the queue, I will come early',
    'Shoot, I forgot my receipt at home', 'Oops I think I typed the wrong student ID', 'I will beat the traffic and arrive before 5',
    'I am dying to get my new ID, it has been weeks', 'My phone died so I could not take a picture of the form',
    'This is taking forever, can you tell me when it will be ready?', 'Ang tagal na po ng ID ko, kailan po ba ito matatapos?',
    'I am very frustrated because I already waited a month', 'Nakakainis po kasi ilang beses na akong bumalik',
    'Sobrang init dito sa pila, kailan po ba kami matatawag?', 'Grabe ang haba ng pila ngayon', 'Hay nako, wala pa rin ang ID ko',
    'I am disappointed with the delay but I understand', 'Pasensya na po sa abala', 'Sorry for asking again',
    'The cashier was very kind', 'The staff here are helpful', 'You are very helpful, thank you', 'Ikaw ang pinaka-matulungin na assistant',
    'You are so fast', 'You are right, I misread the schedule', 'Ang galing mo naman', 'Ang ganda ng sistema niyo',
    'I like this assistant', 'This system is easy to use', 'The new design looks great', 'This is crazy, I never expected it to be this quick',
    'My name is Dickson Cruz and I lost my ID', 'I am Cass Hancock from BSIT 2A', 'Assunta Reyes here, what is my ID status?',
    'Dick Santos here, when can I claim?', 'Hello, this is Cummings from section B', 'My friend Moran asked me to check his ID',
    'My classmate Pass Rivera lost her ID', 'I am in room 455 of the main building', 'My assignment is due tomorrow so I need the ID today',
    'The title of my thesis is about school records', 'I attend taekwondo class so I can only come after 4', 'I can only come at 3:30 PM',
    'Mamatay na ang printer', 'The printer died again', 'I will die of boredom in this queue lol', 'Patay na ang ilaw sa office',
    'Pwede bang bumalik bukas? Wala akong dalang requirements', 'I hate Mondays, but I will come on Tuesday', 'I hate waiting but I can wait',
    'Ayoko ng mahabang pila pero sige', 'Hindi ko gusto ang kulay ng ID pero okay lang', 'It is a bit slow today',
    'Mabagal ang internet kaya hindi ko maisend', 'Wrong number, sorry', 'Nagkamali po ako ng pindot', 'Please help me, I am in a hurry',
    'Paki-check naman po ng status ng ID ko', 'Ano po ang pwede kong gawin kung nawala ko ang ID ko?', 'My ID was stolen, what do I do?',
    'Ninakaw po ang bag ko pati ang ID ko', 'Someone took my wallet and my school ID was inside', 'Nahulog ang ID ko sa jeep',
    'Can you scan my QR code?', 'Paano po mag-print ng QR sticker?', 'What time does the office close?', 'Bukas po ba kayo sa Sabado?',
    'Pwede po bang ibang tao ang kumuha ng ID ko?', 'May bayad po ba ang replacement?', 'Magkano po ang replacement fee?',
    'Okay, thanks. Bye!', 'Paalam po, salamat', 'Have a nice day', 'God bless po sa inyo', 'Great job, assistant', 'That answer was clear',
    'Hindi ko po maintindihan, pwede po ulit?', 'Can you explain that again, please?', 'I do not understand step 3', 'Mali po yata ang sagot',
    'That is not what I asked', 'Hindi po iyan ang tinatanong ko', 'You did not answer my question', 'Hindi mo sinagot ang tanong ko',
    'This bot did not help me', 'Hindi nakatulong ang sagot', 'I think the schedule on the page is wrong', 'Pakitama po ang petsa',
    'Your answer is wrong, please check again', 'Mali ang sagot mo, paki-check ulit', 'I am angry because my ID has not arrived',
    'Galit na galit na ako sa tagal ng ID ko', 'Asar na asar na ako sa pila', 'I am sad because I lost my ID', 'Malungkot ako kasi nawala ang ID ko',
    'I am so stressed with enrollment', 'Stress na stress na ako sa requirements', 'The deadline is killing me', 'Pagod na pagod na ako kakapila',
    'I want to go home already', 'Gusto ko nang umuwi', 'Pwede po bang magreklamo sa office?', 'I want to file a complaint about the long wait',
    'Gusto kong magreklamo sa matagal na proseso', 'Where can I report a rude guard?', 'Paano po mag-report ng bastos na staff?',
]
CLEAN_FRAMES_NAMES = ['Dickson', 'Cass', 'Hancock', 'Assunta', 'Cummings', 'Moran', 'Pass', 'Sexton', 'Cockburn', 'Bassett', 'Shitara', 'Dyke']
CLEAN_NAME_FRAMES = ['My name is {n} and I lost my ID', 'Hi, this is {n}. When can I claim my ID?', 'Student {n} is asking about the status',
                     'Si {n} po ito, kailan ko makukuha ang ID ko?', 'Please check the record of {n}', 'Tell {n} to come to the office']

PROFANITY_FRAMES = ['{t} I lost my ID', '{t} ang tagal ng ID ko', 'ano ba yan {t}', '{t}!!', 'this is so {t}', 'what the {t}', '{t} naman',
                    'ayoko na, {t}', 'ang tagal tagal, {t}', 'oh {t} nawala ko na naman', 'i said {t}', '{t} wala pa rin ang ID ko']
INSULT_FRAMES = ['{t} ka', 'you are a {t}', 'ang {t} mo', '{t} ka talaga', 'you {t}', 'wow {t} naman ng staff', 'ikaw yung {t}', 'what a {t} you are',
                 'ang {t} niyo', 'you are such a {t}', '{t} ka ba?', 'hey {t}, answer me', 'the staff here are {t}', 'sir you are {t}']
ADJ_FRAMES = ['you are {t}', 'ang {t} mo', '{t} ka', 'this bot is {t}', 'you are so {t}', 'ang {t} mo talaga', 'the staff here are {t}', 'you are really {t}']
THREAT_FRAMES = ['I will {v} you', "I'll {v} you after class", 'i am going to {v} you', 'we will {v} you all', 'one more time and I will {v} you',
                 "gonna {v} you", 'wait for me outside, I will {v} you']
THREAT_VERBS = ['kill', 'hurt', 'stab', 'beat up', 'punch', 'shoot', 'strangle', 'murder', 'slap']
THREAT_FIL = ['papatayin kita', 'sasaktan kita', 'bubugbugin kita', 'susuntukin kita', 'sasampalin kita', 'sasaksakin kita', 'babarilin kita',
              'mamamatay ka sa akin', 'pagsisisihan mo ito', 'abangan kita sa labas', 'alam ko kung saan ka nakatira', 'patayin ka sana',
              'i know where you live', 'you will regret this', 'watch your back', 'you are dead', 'there is a bomb in the school',
              'i will bomb the school', 'sunugin ko ang school', 'may bomba sa school bukas', 'pasasabugin ko ang office', 'kill yourself',
              'hintayin mo ako sa labas', 'tatadyakan kita', 'babasagin ko mukha mo', 'ipapapatay kita', 'sirain ko buhay mo']
HARASS = ['send nudes', 'show me your body', 'send me your pics naked', 'sexy mo naman', 'ang sexy mo', 'sex tayo', 'kantutin kita', 'jakulin kita',
          'i want to sleep with you', 'have sex with me', 'hubad ka', 'magpakita ka ng katawan mo', 'kama tayo mamaya', 'ikama kita',
          'sarap mo siguro sa kama', 'ang sexy ng staff dyan', 'show me your boobs', 'i will keep messaging you until you date me',
          'susundan kita pag-uwi mo', 'i am watching you', 'i am stalking you', 'i know your schedule, i will wait for you',
          'give me your number or else', 'sumama ka sa akin mamaya', 'let me touch you', 'hipuan kita', 'tikman kita', 'ang landi mo naman',
          'ang hot mo, pwede ba kitang makita sa gabi', 'send me a pic of your legs', 'pa-kiss naman', 'you owe me a date', 'pa-sex naman']
# The same frames with harmless words, so the frame itself says nothing: only the word decides.
NEUTRAL_PROFANITY = ['okay', 'hello', 'please', 'sorry', 'salamat', 'thanks', 'naman', 'po', 'sige', 'ayos', 'grabe', 'hay', 'ano', 'wow', 'hmm',
                     'oops', 'ay', 'sayang', 'pasensya', 'opo']
NEUTRAL_INSULT = ['lifesaver', 'genius', 'star', 'hero', 'angel', 'helpful', 'kind', 'smart', 'mabait', 'magaling', 'matulungin', 'matalino',
                  'maaasahan', 'sweet', 'friend', 'real person', 'bot', 'robot', 'student', 'nice', 'busy', 'right', 'tama', 'late', 'new here']
NEUTRAL_VERBS = ['call', 'thank', 'text', 'message', 'see', 'help', 'pay', 'visit', 'meet', 'email', 'remind', 'find', 'wait for', 'ask', 'teach']
CLEAN_LOOKALIKES = ['send me the form', 'show me the schedule', 'send me a pic of the receipt', 'show me your ID requirements', 'send me the list of requirements',
                    'show me where the office is', 'can you send me the steps', 'show me my status', 'send me my QR code', 'hello can you show me the form',
                    'you can send it to the cashier', 'I will send you the picture', 'I will see you tomorrow at the office', 'I will call the office tomorrow',
                    'I know where the office is', 'I know where to go now', 'watch the video for the steps', 'I am watching the queue number',
                    'you are right', 'you are welcome', 'are you a real person?', 'are you a bot?', 'who are you?', 'you answered quickly', 'you helped me a lot']
OBF = {'a': '@4', 'e': '3', 'i': '1!', 'o': '0', 's': '$5', 't': '7'}


def obfuscate(text):
    """A disguised copy: letters swapped for symbols, repeated letters, dots or spaces between letters."""
    words = text.split(' ')
    for index, word in enumerate(words):
        if len(word) < 4 or RNG.random() > 0.5:
            continue
        mode = RNG.choice(('leet', 'repeat', 'dots', 'spaces', 'star'))
        if mode == 'leet':
            word = ''.join(RNG.choice(OBF[c]) if c in OBF and RNG.random() < 0.7 else c for c in word)
        elif mode == 'repeat':
            position = RNG.randrange(len(word))
            word = word[:position] + word[position] * RNG.randint(2, 4) + word[position + 1:]
        elif mode == 'dots':
            word = '.'.join(word)
        elif mode == 'spaces':
            word = ' '.join(word)
        else:
            position = RNG.randrange(1, len(word) - 1)
            word = word[:position] + '*' + word[position + 1:]
        words[index] = word
    return ' '.join(words)


def split_terms(terms):
    ordered = sorted(set(terms))
    RNG.shuffle(ordered)
    cut = max(1, int(len(ordered) * 0.3))
    return ordered[cut:], ordered[:cut]


def split_frames(frames):
    ordered = list(frames)
    RNG.shuffle(ordered)
    cut = max(1, int(len(ordered) * 0.2))
    return ordered[cut:], ordered[:cut]


def rude_rows(label, terms, frames, rows):
    train_terms, test_terms = split_terms(terms)
    train_frames, test_frames = split_frames(frames)
    seen = set()

    def add(text, split):
        if text in seen:
            return
        seen.add(text)
        rows.append((text, label, split))
        if RNG.random() < 0.25:
            twisted = obfuscate(text)
            if twisted != text and twisted not in seen:
                seen.add(twisted)
                rows.append((twisted, label, split))

    for term in train_terms:
        for frame in train_frames:
            add(frame.format(t=term, v=term), 'train')
    for term in test_terms:
        for frame in frames:
            add(frame.format(t=term, v=term), 'test')
    for term in train_terms:
        for frame in test_frames:
            add(frame.format(t=term, v=term), 'test')


def main():
    rows = []
    # clean: the real questions, then everyday messages
    questions = [r['text'] for r in csv.DictReader((ROOT / 'property_management_dataset.csv').open(encoding='utf-8'))]
    for index, text in enumerate(questions):
        rows.append((text, 'ok', 'test' if index % 5 == 0 else 'train'))
    for index, text in enumerate(CLEAN_HAND):
        rows.append((text, 'ok', 'test' if index % 5 == 0 else 'train'))
    for name in CLEAN_FRAMES_NAMES:
        for index, frame in enumerate(CLEAN_NAME_FRAMES):
            rows.append((frame.format(n=name), 'ok', 'test' if (index + len(name)) % 5 == 0 else 'train'))
    for family, fillers in ((PROFANITY_FRAMES, NEUTRAL_PROFANITY), (INSULT_FRAMES, NEUTRAL_INSULT), (ADJ_FRAMES, NEUTRAL_INSULT), (THREAT_FRAMES, NEUTRAL_VERBS)):
        for filler in fillers:
            for frame in family:
                text = frame.format(t=filler, v=filler)
                rows.append((text, 'ok', 'test' if sum(map(ord, text)) % 5 == 0 else 'train'))
    for index, text in enumerate(CLEAN_LOOKALIKES):
        rows.append((text, 'ok', 'test' if index % 5 == 0 else 'train'))
    # profanity: vulgar words in everyday sentences, not aimed at anyone
    rude_rows('profanity', [*m.STRONG, *m.MILD], PROFANITY_FRAMES, rows)
    # insult: names a person insultingly; adjectives are insults when aimed
    rude_rows('insult', [t for t in m.PERSON if len(t) > 3], INSULT_FRAMES, rows)
    rude_rows('insult', [t for t in m.ADJ if len(t) > 3 and ' ' not in t], ADJ_FRAMES, rows)
    # threat
    train_verbs, test_verbs = split_terms(THREAT_VERBS)
    train_frames, test_frames = split_frames(THREAT_FRAMES)
    for verb in train_verbs:
        for frame in train_frames:
            rows.append((frame.format(v=verb), 'threat', 'train'))
    for verb in test_verbs:
        for frame in THREAT_FRAMES:
            rows.append((frame.format(v=verb), 'threat', 'test'))
    for verb in train_verbs:
        for frame in test_frames:
            rows.append((frame.format(v=verb), 'threat', 'test'))
    for index, text in enumerate(THREAT_FIL):
        rows.append((text, 'threat', 'test' if index % 4 == 0 else 'train'))
        if index % 4 and RNG.random() < 0.5:
            rows.append((obfuscate(text), 'threat', 'train'))
    # harassment
    for index, text in enumerate(HARASS):
        rows.append((text, 'harassment', 'test' if index % 4 == 0 else 'train'))
        if index % 4:
            rows.append((obfuscate(text), 'harassment', 'train'))
    seen, final = set(), []
    for text, label, split in rows:
        if text not in seen:
            seen.add(text)
            final.append((text, label, split))
    RNG.shuffle(final)
    with (ROOT / 'moderation_dataset.csv').open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['text', 'label', 'split'])
        writer.writerows(final)
    counts = {}
    for _, label, split in final:
        counts[(label, split)] = counts.get((label, split), 0) + 1
    print(len(final), 'rows', dict(sorted(counts.items())))


if __name__ == '__main__':
    main()
