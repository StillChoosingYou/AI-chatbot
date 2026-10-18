"""Pure helpers for the chat assistant: suggestion chips, feedback redaction, CSV safety.

Nothing here touches the database, Flask or the ANN, so every rule can be tested
on its own. The ANN still only labels the intent; these helpers decide which
follow-up questions to offer and how to treat text that a user chose to share.
"""
import re

FEEDBACK_REASONS = ('Wrong answer', "Didn't understand me", 'Missing information', 'Other')

# intent -> (short chip label, question sent to the assistant when the chip is clicked).
# Every question is a training utterance, and a test checks that the ANN maps each one
# back to its own intent, so a chip never leads somewhere unexpected.
INTENT_PROMPTS = {
    'bot_help': ('Help', 'What can you do?'),
    'talk_to_human': ('Talk to the office', 'I want to talk to a real person'),
    'id_processing_status': ('ID status', 'What is the status of my student ID?'),
    'id_release_schedule': ('Release date', 'When can I collect my school ID?'),
    'pending_id': ('Pending reason', 'Why is my student ID pending?'),
    'pending_id_schedule': ('Follow-up date', 'When is my pending ID follow-up appointment?'),
    'lost_id_report': ('Lost ID', 'I lost my school ID'),
    'lost_id_schedule': ('Replacement date', 'When is my lost ID replacement schedule?'),
    'id_requirements': ('Requirements', 'What documents are needed for a new student ID?'),
    'office_hours_location': ('Office hours', 'Where is the Property Management Office?'),
    'qr_student_details': ('QR scan', 'What information appears after scanning the QR sticker?'),
    'qr_sticker_request': ('QR sticker', 'How can I print a small QR sticker?'),
    'id_inventory': ('Inventory', 'Show the student ID inventory'),
}

# Intents whose answer only makes sense for coordinators; other roles are not offered them.
STAFF_ONLY_INTENTS = frozenset({'id_inventory'})
# Intents that are never useful as a "did you mean" suggestion.
NOT_SUGGESTED = frozenset({'out_of_scope', 'greeting', 'thanks_goodbye'})

# What a person most likely asks next, by the intent that was just answered.
FOLLOW_UPS = {
    'id_processing_status': ('id_release_schedule', 'pending_id', 'qr_sticker_request'),
    'pending_id': ('pending_id_schedule', 'id_requirements', 'talk_to_human'),
    'id_release_schedule': ('id_requirements', 'office_hours_location', 'qr_sticker_request'),
    'pending_id_schedule': ('pending_id', 'office_hours_location'),
    'lost_id_schedule': ('id_requirements', 'office_hours_location'),
    'lost_id_report': ('id_requirements', 'lost_id_schedule', 'office_hours_location'),
    'id_requirements': ('office_hours_location', 'lost_id_report'),
    'office_hours_location': ('id_requirements', 'talk_to_human'),
    'qr_sticker_request': ('qr_student_details', 'id_processing_status'),
    'qr_student_details': ('qr_sticker_request', 'id_processing_status'),
    'talk_to_human': ('office_hours_location', 'id_requirements'),
    'id_inventory': ('qr_sticker_request',),
}

# Scores are uncalibrated softmax values, so these cut-offs are tuned by eye against the
# deployed model (see tests) and are meant to be revisited after retraining.
SURE_ABOVE = 0.60          # at or above this the answer is shown without second guesses
UNRELATED_ABOVE = 0.50     # a top "out_of_scope" score at or above this means "clearly unrelated"
FALLBACK_TOP_MIN = 0.25    # fallback: the best candidate must reach this to offer anything
FALLBACK_OTHER_MIN = 0.15  # fallback: further candidates must reach this
UNSURE_ALT_MIN = 0.10      # unsure answer: a runner-up must reach this to be offered
SURE_ALT_MIN = 0.25        # confident answer: a runner-up must reach this to be offered


_OTHER_LABELS = {
    'greeting': 'Greeting',
    'thanks_goodbye': 'Thanks / goodbye',
    'out_of_scope': 'Outside what I can help with',
}


def intent_label(intent):
    """Plain-language name of a topic, for the review screen."""
    if intent in INTENT_PROMPTS:
        return INTENT_PROMPTS[intent][0]
    return _OTHER_LABELS.get(intent) or str(intent).replace('_', ' ').capitalize()


def prompt_chips(intents, workspace):
    """Turn intent names into chip dicts, dropping unknown and role-inappropriate ones."""
    chips = []
    for intent in intents:
        if intent not in INTENT_PROMPTS:
            continue
        if intent in STAFF_ONLY_INTENTS and workspace != 'coordinator':
            continue
        label, question = INTENT_PROMPTS[intent]
        chips.append({'intent': intent, 'label': label, 'question': question})
    return chips


def reply_chips(ranked, intent, confidence, workspace, with_follow_ups=True):
    """Pick the chips shown under a reply.

    ranked is [(intent, score)] best first (empty when no known word was typed).
    Returns (suggestions, suggestions_title, follow_ups). At most one of the two chip
    lists is filled: second guesses win over follow-ups.
    """
    candidates = [(label, score) for label, score in ranked
                  if label not in NOT_SUGGESTED and label != intent]
    top_label, top_score = ranked[0] if ranked else ('', 0.0)

    if intent == 'out_of_scope':
        if not ranked or (top_label == 'out_of_scope' and top_score >= UNRELATED_ABOVE):
            return [], '', []
        if not candidates or candidates[0][1] < FALLBACK_TOP_MIN:
            return [], '', []
        names = [label for label, score in candidates if score >= FALLBACK_OTHER_MIN or label == candidates[0][0]]
        chips = prompt_chips(names, workspace)[:3]
        return chips, 'Did you mean:', []

    floor = UNSURE_ALT_MIN if confidence < SURE_ABOVE else SURE_ALT_MIN
    chips = prompt_chips([label for label, score in candidates if score >= floor], workspace)[:2]
    if chips:
        return chips, 'Not what you meant? Try:', []
    if not with_follow_ups:
        return [], '', []
    return [], '', prompt_chips(FOLLOW_UPS.get(intent, ()), workspace)[:3]


_EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_PHONE = re.compile(r'\+?\d[\d\s().-]{6,}\d')
_LONG_NUMBER = re.compile(r'\d{5,}')


def _alternatives(words):
    """Regex for 'any of these whole tokens', longest first so a full name beats a part of it."""
    return r'(?<!\w)(?:' + '|'.join(re.escape(w) for w in sorted(set(words), key=len, reverse=True)) + r')(?!\w)'


def redact_text(text, student_ids=(), names=()):
    """Remove what we can recognise as personal before a question is stored for review.

    Registered student IDs, registered names (full and each part of four or more
    letters), e-mail addresses, phone-like and other long numbers are replaced.
    Everything is replaced in a single pass, left to right, so one piece of text is
    never rewritten twice (an e-mail such as jamie.s@school.example becomes one
    [email], not "[name]" plus a leftover). This is a safety net, not a guarantee:
    the review screen still tells staff to check the text.
    """
    ids = [s.strip() for s in student_ids if s and s.strip()]
    parts = []
    for name in names:
        name = (name or '').strip()
        if len(name) >= 4:
            parts.append(name)
        parts.extend(piece for piece in re.split(r'[\s,.-]+', name) if len(piece) >= 4 and piece.isalpha())
    # Order matters: at one position the first alternative that matches wins.
    groups = [('email', _EMAIL.pattern)]
    if ids:
        groups.append(('student_id', _alternatives(ids)))
    if parts:
        groups.append(('name', _alternatives(parts)))
    groups += [('phone', _PHONE.pattern), ('number', _LONG_NUMBER.pattern)]
    pattern = re.compile('|'.join(f'(?P<{label}>{regex})' for label, regex in groups), re.IGNORECASE)
    replacement = {'email': '[email]', 'student_id': '[student ID]', 'name': '[name]',
                   'phone': '[number]', 'number': '[number]'}
    return pattern.sub(lambda match: replacement[match.lastgroup], str(text)).strip()[:500]


def csv_safe(value):
    """Stop spreadsheet programs from running text that starts like a formula."""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value


def clean_text(value):
    """True when a string can be stored. Lone surrogates (valid JSON, invalid UTF-8) and NUL
    characters (PostgreSQL text cannot hold them) cannot."""
    if '\x00' in value:
        return False
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True
