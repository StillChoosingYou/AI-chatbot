"""One- and two-word topic questions, by rules: "status", "release date", "canva", "qr".

The ANN learns from whole questions. A single word such as "status" or "canva" is a poor fit for it: the word
appears in several topics, so the network guesses, or the fallback turns the word away. For these few short
messages a person means one thing, so a small fixed table gives the answer. The table is deliberately short and
exact: the message must be nothing but one of these phrases (polite words aside), so a real question, even a short
one such as "status of my ID", still goes to the ANN.

Nothing here touches the database, Flask or the ANN, so every row can be tested on its own.
"""
from small_talk import words

POLITE = frozenset('po opo ho please pls plz kindly nga naman paki ba ng ang ko'.split())

TOPIC_PHRASES = {
    'id_processing_status': ('status', 'id status', 'status id', 'my status', 'status ko', 'progress', 'id progress'),
    'id_release_schedule': ('release', 'release date', 'release schedule', 'pickup', 'pick up', 'claim', 'kuha', 'kuha id'),
    'id_requirements': ('requirements', 'requirement', 'fee', 'fees', 'bayad', 'documents', 'id requirements'),
    'lost_id_report': ('lost', 'lost id', 'nawala', 'nawala id', 'nawalang id', 'missing id'),
    'pending_id': ('pending', 'pending id', 'on hold', 'hold'),
    'qr_student_details': ('qr', 'qr code', 'scan', 'scan qr'),
    'qr_sticker_request': ('sticker', 'qr sticker', 'qr label', 'label'),
    'office_hours_location': ('office', 'office hours', 'hours', 'location', 'office location', 'oras'),
    'id_process': ('process', 'id process', 'steps', 'canva', 'canva edit', 'case and lace', 'lace', 'signature', 'printing', 'production', 'proseso'),
}
PHRASE_TO_TOPIC = {phrase: topic for topic, items in TOPIC_PHRASES.items() for phrase in items}
assert len(PHRASE_TO_TOPIC) == sum(len(items) for items in TOPIC_PHRASES.values()), 'a phrase belongs to one topic'


def topic_word_intent(text):
    """The topic when the whole message is one of the table's short phrases, else None."""
    spoken = [word for word in words(text) if word not in POLITE]
    if not spoken or len(spoken) > 3:
        return None
    return PHRASE_TO_TOPIC.get(' '.join(spoken))
