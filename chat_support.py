"""Pure helpers for the chat assistant: suggestion chips, feedback redaction, CSV safety.

Nothing here touches the database, Flask or the ANN, so every rule can be tested
on its own. The ANN still only labels the intent; these helpers decide which
follow-up questions to offer and how to treat text that a user chose to share.
"""
import re

from student_data import PENALTY_AT, PENALTY_TEXT, STATUS_NOTES, gate_note

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


# ─── Looking a student up from the chat ───────────────────────────────────

_ID_WORD = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{2,39}')


def id_candidates(text, limit=5):
    """The words of a message that could be a student ID: four or more characters with a digit in them."""
    seen, found = set(), []
    for word in _ID_WORD.findall(str(text)):
        key = word.lower()
        if len(word) >= 4 and any(char.isdigit() for char in word) and key not in seen:
            seen.add(key)
            found.append(word)
    return found[:limit]


PRIVATE_NOTE = '🔒 Address and contact number are shown to the coordinator only.'


def student_reply(row, role, release_hours):
    """What the chat tells one workspace role about one student.

    row is a record with 'year_section' already worked out. Everyone sees the ID, name, year and section,
    status and appointment. The address and contact number are for the coordinator; the other roles are
    told they exist but are not shown them. The guard and the coordinator also see false statements and
    any penalty (row['false_statements'], row['penalty_due']). Staff notes and the QR token are never
    part of a chat answer.
    """
    lines = [f'🪪 **{row["name"]}**',
             f'Student ID: {row["student_id"]}',
             f'Year & Section: {row["year_section"]}',
             f'Status: **{row["status"]}** ({STATUS_NOTES.get(row["status"], "see the office")})']
    if row.get('request_type'):
        lines.append(f'Request: {row["request_type"]}')
    if row.get('schedule_date'):
        lines.append(f'Appointment: {row["schedule_type"]}, **{row["schedule_date"]} at {row["schedule_time"]}**')
    else:
        lines.append('Appointment: none scheduled yet')
    if row['status'] == 'Pending' and row.get('pending_reason'):
        lines.append(f'Why pending: {row["pending_reason"]}')
    if row['status'] == 'For release':
        lines.append(f'Release hours: {release_hours}')
    if role == 'coordinator':
        lines.append(f'Address: {row.get("address") or "not recorded"}')
        lines.append(f'Contact number: {row.get("contact_number") or "not recorded"}')
    if role in ('coordinator', 'guard'):
        lines.extend(staff_lines(row))
    if role == 'guard':
        lines.append(gate_note(row['status']))
    if role != 'coordinator':
        lines.append(PRIVATE_NOTE)
    return '\n'.join(lines)


def staff_lines(row):
    """False statements and the penalty, for the guard and the coordinator only."""
    lines = []
    count = row.get('false_statements', 0)
    if count:
        lines.append(f'False statements on record: {count}')
    if row.get('penalty_due'):
        lines.append(f'⚠️ Penalty due: ₱{row["penalty_due"]:,}. Send the student to the Cashier, then mark the '
                     'incident Resolved once it is paid.')
    elif count == PENALTY_AT - 1:
        lines.append(f'A second false statement carries a {PENALTY_TEXT} penalty.')
    if count > PENALTY_AT:
        lines.append('More than two false statements: the written rule only sets the penalty at the second, '
                     'so refer this student to the coordinator.')
    return lines


# ─── Commands typed into the chat ─────────────────────────────────────────
# The network only labels what a message is about. A few things are not questions but orders (log a
# student in or out, show the gate log, list the IDs that are not made yet), so plain patterns catch them
# first. Every pattern needs the whole intent to be spelled out; anything else goes on to the network.

_GATE_COMMAND = re.compile(
    r'^\s*(?:please\s+|pls\s+)?(check[\s-]*in|check[\s-]*out|log[\s-]*in|log[\s-]*out|time[\s-]*in|time[\s-]*out'
    r'|in|out|pasok|labas)\s*[:\-]?\s+(.+?)\s*$', re.IGNORECASE)
_GATE_BARE = re.compile(
    r'^\s*(?:please\s+|pls\s+)?(?:check[\s-]*in|check[\s-]*out|log[\s-]*in|log[\s-]*out|time[\s-]*in|time[\s-]*out'
    r'|pasok|labas)\s*[.!]?\s*$', re.IGNORECASE)
_GATE_IN_WORDS = {'checkin', 'login', 'timein', 'in', 'pasok'}
_GATE_LOG = re.compile(
    r"\bgate[\s-]*(?:logs?|entries|records?|history|list)\b"
    r"|\b(?:logs?|entries|records?) (?:at|of|sa) (?:the )?gate\b"
    r"|\bwho(?:'s| is| are)? (?:inside|in the campus|in school)\b", re.IGNORECASE)
_NOT_YET_MADE = re.compile(
    r'\bnot[\s-]+yet[\s-]+made\b|\bnot[\s-]+made[\s-]+yet\b|\bunmade\b'
    r'|\b(?:hindi|di)[\s-]+pa[\s-]+(?:nagagawa|nagawa|gawa)(?:ng)?\b', re.IGNORECASE)
_RECORDS_ONLY = re.compile(
    r'^\s*(?:show |list |view |open |see |display )?(?:me )?(?:the |all )?(?:student )?records?\s*[.!]?\s*$',
    re.IGNORECASE)


def parse_gate_command(text):
    """('in' or 'out', who) for "check in P202400230", "out Alex Rivera"; None for any other message."""
    if '?' in text:
        return None
    match = _GATE_COMMAND.match(text)
    if not match:
        return None
    verb = re.sub(r'[\s-]+', '', match.group(1).lower())
    return ('in' if verb in _GATE_IN_WORDS else 'out'), match.group(2)


def is_bare_gate_verb(text):
    """True for "check in" or "check out" typed alone, with no student after it."""
    return bool(_GATE_BARE.match(text))


def asks_for_gate_log(text):
    return bool(_GATE_LOG.search(text))


def asks_for_records(text):
    """True for "records" on its own and for "ID not yet made" in English or Tagalog."""
    return bool(_NOT_YET_MADE.search(text) or _RECORDS_ONLY.match(text))
