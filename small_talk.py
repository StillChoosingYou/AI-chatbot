"""Small talk by rules: hello, thanks, "what can you do?" and "I want to talk to a person".

These four are not school ID topics, so they are not in the training dataset and the ANN has never seen them.
A few hand-written rules recognise them instead. The rules are strict on purpose: a greeting or a thanks counts
only when it is the whole message, and the other two need a clear phrase. Everything else, "Hi, when can I get my
ID?" included, goes on to the ANN as a question about IDs.

The rules read English, Filipino and the mix of both (Taglish), because that is how the questions are written.
Nothing here touches the database, Flask or the ANN, so every rule can be tested on its own.
"""
import re
import unicodedata

GREETING = 'greeting'
THANKS = 'thanks_goodbye'
HELP = 'bot_help'
HUMAN = 'talk_to_human'
SMALL_TALK_INTENTS = (GREETING, THANKS, HELP, HUMAN)

MAX_WORDS = 14            # a longer message is a real question, not small talk


def words(text):
    """Lower-case words without accents or punctuation. One-letter words ("a", "i", the "s" of "that's") are
    dropped, so "i-use" and "that's" come out as "use" and "that"."""
    folded = unicodedata.normalize('NFKD', str(text).lower())
    folded = ''.join(char for char in folded if not unicodedata.combining(char))
    return [word for word in re.findall(r'[a-z0-9]+', folded) if len(word) > 1]


def phrases(*items):
    """One pattern that finds any of the phrases as whole words, the longest first."""
    ordered = sorted(items, key=len, reverse=True)
    return re.compile(r'(?<!\S)(?:' + '|'.join(re.escape(item) for item in ordered) + r')(?!\S)')


def only(sentence, anchors, fillers):
    """True when the sentence is nothing but anchor phrases and filler words, with at least one anchor."""
    rest, found = anchors.subn(' ', sentence)
    return bool(found) and all(word in fillers for word in rest.split())


# ─── hello ────────────────────────────────────────────────────────────────

GREETINGS = phrases(
    'hello', 'hi', 'hey', 'hiya', 'heya', 'hallo', 'hola', 'yo', 'howdy', 'greetings', 'sup', 'wassup',
    'good morning', 'good afternoon', 'good evening', 'good day', 'good am', 'good pm',
    'gud morning', 'gud afternoon', 'gud evening', 'gud day', 'gud am', 'gud pm',
    'how are you', 'how are you doing', 'what up', 'are you there', 'anyone there', 'is anyone there',
    'anybody there',
    'magandang umaga', 'magandang tanghali', 'magandang hapon', 'magandang gabi', 'magandang araw',
    'mabuhay', 'kumusta', 'kamusta', 'musta', 'mustah')
GREETING_FILLERS = frozenset('''
po opo ho bot chatbot assistant there everyone everybody all guys sir madam ma am maam miss kuya ate boss admin
coordinator guard staff office pmo team ka kayo na naman ba ulit again please pls
'''.split())

# ─── thanks and goodbye ──────────────────────────────────────────────────

THANKS_ANCHORS = phrases(
    'thanks', 'thank', 'thx', 'thnx', 'tnx', 'ty', 'tysm', 'bye', 'byebye', 'goodbye', 'good bye', 'cya',
    'see you', 'see ya', 'take care', 'good night', 'goodnight', 'that all', 'until next time',
    'salamat', 'paalam', 'ingat', 'hanggang sa muli', 'hanggang sa susunod')
THANKS_FILLERS = frozenset('''
you very much so many lot lots po opo ho bot chatbot assistant for your the help helping helped assistance tulong
sa ang ng maraming sige ge ok okay na naman lang ha have nice great good wonderful day night one ahead all that now
later again ulit everything anything
'''.split())

# ─── "what can you do?" ──────────────────────────────────────────────────

# Said alone, these ask for help in general. With more words after them ("can you help me with my homework")
# they are a request for something else, so only the whole message counts.
HELP_ALONE = re.compile(r'(?:can you |how can you )?help(?: me)?|need help|tulong|saklolo')
# Phrases about the chatbot itself. They count anywhere in a message, unless it also mentions school IDs.
ABOUT_THE_BOT = phrases(
    'what can you do', 'what do you do', 'what are you able to do', 'what can ask', 'what should ask',
    'what can do here', 'what else can you do',
    'what are your features', 'what are your functions', 'what are your capabilities', 'what are your commands',
    'what are your services', 'what are your abilities', 'your features', 'your functions', 'your capabilities',
    'what features', 'what functions', 'what commands',
    'what kind of concerns', 'what kind of questions', 'what kind of things', 'what concerns',
    'how do use this', 'how to use this', 'how do use you', 'how to use you',
    'how does this work', 'how does this chatbot work', 'how does the chatbot work', 'how does chatbot work', 'how does it work', 'how do you work', 'how this works', 'how it works', 'how you work',
    'explain how this works', 'explain how you work', 'who are you', 'what is your name', 'what your name',
    'are you bot', 'are you robot', 'are you human', 'are you real',
    'kaya mong gawin', 'kaya mo bang gawin', 'maitutulong mo', 'magagawa mo', 'features mo', 'serbisyo mo',
    'mga features', 'mga serbisyo', 'kaya mong sagutin', 'paano gamitin', 'paano ito gamitin', 'paano ito use',
    'paano ko ito use', 'paano ka gumagana', 'gumagana ka', 'paano gumagana', 'concerns ang pwede',
    'pwede kong itanong', 'pwedeng itanong', 'pwede kong tanungin')
# A message with one of these is about an ID, so the ANN reads it, whatever else it says.
ID_WORDS = frozenset('''
id ids student students qr sticker stickers pending lost nawala nawawala naiwala release released requirement
requirements office pmo hours status schedule appointment inventory card cards replacement fee fees record records
estudyante
'''.split())

# ─── "I want to talk to a person" ────────────────────────────────────────

HUMAN_VERBS = frozenset('''
talk speak chat connect transfer call contact reach escalate makausap kausap kausapin usap makipag ilipat ikonekta
tawag tawagin pakitawag
'''.split())
HUMAN_TARGETS = frozenset('''
human person people staff agent representative rep operator someone somebody officer personnel employee admin
administrator tao kawani empleyado helpdesk
'''.split())
HUMAN_PHRASES = phrases('human agent', 'live agent', 'ayoko sa bot', 'ayaw ko sa bot', 'staff na lang', 'tao na lang',
                        'human na lang', 'staff nalang', 'tao nalang', 'human nalang')

POLITE = frozenset('po opo ho please pls plz kindly nga naman paki'.split())
OPENERS = frozenset('hi hello hey'.split())


def small_talk_intent(text):
    """greeting, thanks_goodbye, bot_help or talk_to_human when the message is small talk, else None."""
    spoken = words(text)
    if not spoken or len(spoken) > MAX_WORDS:
        return None
    sentence = ' '.join(spoken).replace('help desk', 'helpdesk')

    plain = [word for word in sentence.split() if word not in POLITE]
    while plain and plain[0] in OPENERS:                  # "hello, what can you do?" is a question
        plain = plain[1:]
    asked = ' '.join(plain)
    if any(word in HUMAN_VERBS for word in plain) and any(word in HUMAN_TARGETS for word in plain):
        return HUMAN
    if HUMAN_PHRASES.search(asked):
        return HUMAN
    if HELP_ALONE.fullmatch(asked):
        return HELP
    if ABOUT_THE_BOT.search(asked) and not ID_WORDS.intersection(plain):
        return HELP
    if only(sentence, THANKS_ANCHORS, THANKS_FILLERS):
        return THANKS
    if only(sentence, GREETINGS, GREETING_FILLERS):
        return GREETING
    return None
