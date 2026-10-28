"""Pure helpers for the chat assistant: suggestion chips, feedback redaction, CSV safety.

Nothing here touches the database, Flask or the ANN, so every rule can be tested
on its own. The ANN still only labels the intent; these helpers decide which
follow-up questions to offer and how to treat text that a user chose to share.
"""
import re

from student_data import (
    PENALTY_AT, PENALTY_NOTE, PENALTY_TEXT, REQUEST_STATUS_NOTES, STATUS_NOTES, claim_check)

FEEDBACK_REASONS = ('Wrong answer', "Didn't understand me", 'Missing information', 'Other')

# intent -> (short chip label, question sent to the assistant when the chip is clicked).
# A test checks every question: the ANN maps it back to its own intent (or, for Help and
# Talk to the office, the small-talk rules do), so a chip never leads somewhere unexpected.
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
    'id_process': ('ID process', 'What is the process of making a school ID?'),
    'id_inventory': ('Inventory', 'Show the student ID inventory'),
}

# Intents whose answer only makes sense for coordinators; other roles are not offered them.
STAFF_ONLY_INTENTS = frozenset({'id_inventory'})
# Intents that are never useful as a "did you mean" suggestion.
NOT_SUGGESTED = frozenset({'out_of_scope', 'greeting', 'thanks_goodbye'})

# What a person most likely asks next, by the intent that was just answered.
FOLLOW_UPS = {
    'id_processing_status': ('id_process', 'id_release_schedule', 'pending_id'),
    'id_process': ('id_processing_status', 'id_release_schedule', 'id_requirements'),
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
UNRELATED_ABOVE = 0.50     # a top "out_of_scope" score at or above this means "clearly unrelated" (only when
#                            the ANN has been given out_of_scope examples; by default it has none)
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
    told they exist but are not shown them. The coordinator also sees false statements and
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
    if role == 'coordinator':
        lines.extend(staff_lines(row))
    if role != 'coordinator':
        lines.append(PRIVATE_NOTE)
    return '\n'.join(lines)


def staff_lines(row):
    """False statements and the penalty, for the coordinator only."""
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
# The network only labels what a message is about. A few things are not questions but orders (list the IDs
# that are not made yet), so plain patterns catch them first. Every pattern needs the whole intent to be spelled out; anything else goes on to the network.

_NOT_YET_MADE = re.compile(
    r'\bnot[\s-]+yet[\s-]+made\b|\bnot[\s-]+made[\s-]+yet\b|\bunmade\b'
    r'|\b(?:hindi|di)[\s-]+pa[\s-]+(?:nagagawa|nagawa|gawa)(?:ng)?\b', re.IGNORECASE)
_RECORDS_ONLY = re.compile(
    r'^\s*(?:show |list |view |open |see |display )?(?:me )?(?:the |all )?(?:student )?records?\s*[.!]?\s*$',
    re.IGNORECASE)


def asks_for_records(text):
    """True for "records" on its own and for "ID not yet made" in English or Tagalog."""
    return bool(_NOT_YET_MADE.search(text) or _RECORDS_ONLY.match(text))


# ─── Request help, inside the student's chat ──────────────────────────────
# A student does everything in the chat: the Request help button opens a form, and following a request is
# also a chat message. These are the few phrases that mean exactly that; anything else goes to the network.

_MY_REQUESTS = re.compile(
    r"\b(?:my\s+requests?|requests?\s+ko|(?:aking|akin)\s+requests?|track(?:\s+(?:my|the))?\s+requests?"
    r"|requests?\s+(?:status|tracker)|status\s+of\s+(?:my\s+|the\s+)?requests?|where(?:'s|\s+is)\s+my\s+requests?)\b",
    re.IGNORECASE)
_REQUEST_HELP = re.compile(
    r"^\s*(?:(?:please|pls)\s+)?(?:(?:i\s+)?(?:want|need|would\s+like)\s+(?:to\s+)?)?"
    r"(?:request(?:ing)?\s+help|(?:submit|make|file|send)\s+(?:a\s+|my\s+)?request|help\s+request|request\s+form)"
    r"(?:\s+(?:please|pls))?\s*[.!]?\s*$", re.IGNORECASE)
MAX_REQUEST_WORDS = 10      # "my requests P202400230" or "track my request Alex Rivera": a short order, not a story


def asks_for_my_requests(text):
    """True for "my requests", "track my request P202400230", "request ko", "status of my request"."""
    return len(text.split()) <= MAX_REQUEST_WORDS and bool(_MY_REQUESTS.search(text))


def request_subject(text):
    """What is left of a "my requests" message once that phrase is taken out: the ID or name it is about."""
    return _MY_REQUESTS.sub(' ', text).strip(' \t\n.,;:!?')


def asks_to_request_help(text):
    """True for "request help", "I need to submit a request", "request form"."""
    return bool(_REQUEST_HELP.match(text))


def request_sent_reply(row, steps):
    """The chat message that confirms a request: its number, its status and what to do next."""
    who = f' for {row["name"]}' if row.get('name') else ''
    lines = [f'✅ Request #{row["id"]} sent: **{row["request_type"]}**{who} ({row["student_id"]}). '
             f'Status: **{row["status"]}**.']
    if steps:
        lines.append('What to do next:')
        lines.extend(f'{number}. {step}' for number, step in enumerate(steps, 1))
        lines.append('When the office accepts your request, your ID goes back to Pending while the replacement is processed.')
    else:
        lines.append('The office will look at it during office hours.')
    lines.append('Type “my requests” whenever you want to follow it here in the chat.')
    return '\n'.join(lines)


def my_requests_reply(student_id, name, rows):
    """One student's requests, newest first: number, type, status and time. What the student wrote is not repeated."""
    who = f'{name} ({student_id})' if name else student_id
    if not rows:
        return (f'📋 No requests have been made with {student_id} yet. '
                'If you need the office’s help, tap Request help below.')
    lines = [f'📋 Requests for {who}, newest first:']
    for row in rows:
        note = REQUEST_STATUS_NOTES.get(row['status'], '')
        lines.append(f'• #{row["id"]} · {row["request_type"]} · **{row["status"]}** · {row["timestamp"][:16]}'
                     + (f' — {note}' if note else ''))
    return '\n'.join(lines)


# ─── Incidents, inside the chat (coordinator) ───────────────────
# The coordinator files an incident from a pop-up that a button in the chat opens, follows the list in the chat and
# resolves from it. Like the Request help phrases, these are the few messages that mean exactly that.

_FILE_INCIDENT = re.compile(
    r"^\s*(?:(?:please|pls)\s+)?(?:(?:i\s+)?(?:want|need|would\s+like)\s+(?:to\s+)?)?"
    r"(?:(?:file|report|log|make|create|open|submit|add)\s+(?:an?\s+|the\s+|new\s+)?incident(?:\s+report)?"
    r"|(?:new\s+)?incident(?:\s+report)?"
    r"|(?P<no_id>no\s+id(?:\s+incident)?|walang\s+id)"
    r"|(?P<lost_id>lost\s+id\s+incident|incident\s+(?:for\s+)?lost\s+id))"
    r"(?:\s+(?:please|pls))?\s*[.!]?\s*$", re.IGNORECASE)
_LIST_INCIDENTS = re.compile(
    r"^\s*(?:(?:please|pls)\s+)?(?:(?:show|list|see|view|open|check|display)\s+(?:me\s+)?)?"
    r"(?:(?:the|all|my|recent|latest|open|today'?s)\s+)*"
    r"(?:incidents|incident\s+(?:log|list|reports))\s*[.!]?\s*$", re.IGNORECASE)
_INCIDENT_STATUS = re.compile(
    r"^\s*(?P<verb>resolve|reopen|re-open)\s+(?:the\s+)?incident\s+#?(?P<number>\d{1,9})\s*[.!]?\s*$", re.IGNORECASE)


def asks_to_file_incident(text):
    """The category a message names ("No ID", "Lost ID", or "" for plain "file incident"), None when the
    message is not a request to file an incident."""
    match = _FILE_INCIDENT.match(text)
    if not match:
        return None
    if match.group('no_id'):
        return 'No ID'
    if match.group('lost_id'):
        return 'Lost ID'
    return ''


def asks_for_incidents(text):
    """True for "incidents", "show incidents", "incident log"."""
    return bool(_LIST_INCIDENTS.match(text))


def incident_status_command(text):
    """("Resolved" or "Open", number) for "resolve incident 12" and "reopen incident 12", else None."""
    match = _INCIDENT_STATUS.match(text)
    if not match:
        return None
    return ('Resolved' if match.group('verb').lower() == 'resolve' else 'Open'), int(match.group('number'))


def incident_help_reply(selected, category):
    """What the chat says before the pop-up opens: the records for the student the chat is on, and what the
    form is for."""
    parts = []
    if selected:
        parts.append(f'Selected: {selected["name"]} ({selected["student_id"]}), status **{selected["status"]}**.')
        if category:
            parts.append(claim_check(category, selected['status'])[1])
    parts.append('Tap File incident below to fill in the report. A lost ID goes to the Cashier first. '
                 f'{PENALTY_NOTE}')
    return ' '.join(parts)


def incident_filed_reply(incident, steps):
    """The chat message that confirms an incident: its number, who it is about, what the records say, the
    false statement and its penalty, and what to do next."""
    if incident.get('student_name'):
        who = f' for {incident["student_name"]} ({incident["student_id"]})'
    elif incident.get('student_id'):
        who = f' for {incident["student_id"]} (not on the dashboard)'
    else:
        who = ''
    lines = [f'✅ Incident #{incident["id"]} filed: **{incident["category"]}**{who}. Status: **{incident["status"]}**.']
    claim = incident.get('claim') or {}
    if claim.get('message'):
        lines.append(claim['message'])
    if incident.get('false_statement'):
        lines.append(f'⚠️ False statement no. {incident.get("false_statements") or 0} for this student'
                     + (f': penalty ₱{int(incident["penalty"]):,}.' if incident.get('penalty') else '.'))
    if steps:
        lines.append('What to do next:')
        lines.extend(f'{number}. {step}' for number, step in enumerate(steps, 1))
    lines.append('Type “incidents” to see the list.')
    return '\n'.join(lines)


def incidents_reply(rows, open_count):
    """The latest incidents, newest first: number, category, status, who, where and when. What the coordinator wrote
    in "What happened?" stays on the coordinator's Incidents page."""
    if not rows:
        return ('⚠️ No incidents have been filed yet. Tap File incident below when a student has no ID or has '
                'lost it.')
    lines = [f'⚠️ Incidents: {open_count} open. The latest {len(rows)}, newest first:']
    for row in rows:
        if row.get('student_name'):
            who = f'{row["student_name"]} ({row["student_id"]})'
        else:
            who = row.get('student_id') or 'no student ID'
        flag = ''
        if row.get('false_statement'):
            flag = ' · false statement' + (f' ₱{int(row["penalty"]):,}' if row.get('penalty') else '')
        where = f' · {row["location"]}' if row.get('location') else ''
        lines.append(f'• #{row["id"]} · {row["category"]} · **{row["status"]}** · {who}{where} · '
                     f'{row["timestamp"][:16]}{flag}')
    return '\n'.join(lines)
