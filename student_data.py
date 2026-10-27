"""Pure helpers for student records: formats, release hours, CSV import and name matching.

Nothing here touches Flask or the database, so every rule can be tested on its own.
"""
import csv
import io
import re
import unicodedata
from datetime import time

STATUSES = ('Pending', 'Processing', 'For release', 'Released')
# Other ways a status has been written (older versions of this app, spreadsheets). It is always stored
# under one of the names in STATUSES.
STATUS_ALIASES = {
    'pending': 'Pending',
    'processing': 'Processing', 'in process': 'Processing', 'in progress': 'Processing',
    'for release': 'For release', 'for released': 'For release', 'ready for release': 'For release',
    'ready': 'For release', 'ready for pickup': 'For release',
    'released': 'Released', 'claimed': 'Released',
}

# ─── Formats ──────────────────────────────────────────────────────────────

# A school ID is the letter P, the four digits of the year and a five-digit number: P202400230.
# This one line decides which IDs the dashboard, the import and the add form accept.
STUDENT_ID_RE = re.compile(r'P[0-9]{9}')          # ASCII digits only; use .fullmatch
STUDENT_ID_EXAMPLE = 'P202400230'
STUDENT_ID_HINT = 'Student ID must be the letter P and 9 digits, for example P202400230.'
MAX_YEAR = 4                                       # four year levels: this one line decides what every form, the import and the chat accept
YEAR_SECTION_RE = re.compile(rf'([1-{MAX_YEAR}])\s*[-/]?\s*([A-Za-z0-9]{{1,10}})')
YEAR_SECTION_HINT = f'Year & Section is a year from 1 to {MAX_YEAR} followed by the section, for example 3E.'
CONTACT_HINT = 'Contact number must be a phone number, for example 09171234567.'
NAME_MAX = 100
ADDRESS_MAX = 200


def tidy(value):
    """Text from a form or spreadsheet cell: no stray spaces, non-breaking spaces included."""
    return ' '.join(str(value).replace('\u00a0', ' ').split())


def clean_student_id(value):
    """The student ID in the form it is stored (upper case), or None when it is not a valid one."""
    if not isinstance(value, str):
        return None
    value = tidy(value).upper()
    return value if STUDENT_ID_RE.fullmatch(value) else None


def parse_year_section(value):
    """(year level, section) from text such as '3E', '3-E' or '3 e'; None when it does not fit."""
    if not isinstance(value, str):
        return None
    match = YEAR_SECTION_RE.fullmatch(tidy(value))
    return (int(match.group(1)), match.group(2).upper()) if match else None


def format_year_section(year, section):
    """'3E', or '1-2' when the section is a number. Records made without a section show 'TBD'."""
    section = str(section or '').strip()
    if not section or section.upper() == 'TBD':
        return 'TBD'
    return f'{year}-{section}' if section[0].isdigit() else f'{year}{section}'


def clean_contact(value):
    """The contact number in the form it is stored ('' when blank); None when it is not a phone number.

    Spaces, dashes and brackets are removed. A mobile number may be written 09xx, +63 9xx or 9xx (a
    spreadsheet drops the leading zero) and is stored as 09xx; anything that starts like a mobile number
    has to be a whole one. Any other number of 7 to 13 digits is kept as typed, for landlines.
    """
    if not isinstance(value, str):
        return None
    digits = re.sub(r'[\s().-]', '', tidy(value))
    if not digits:
        return ''
    if re.match(r'(?:\+?63|0)?9', digits):
        mobile = re.fullmatch(r'(?:\+?63|0)?(9[0-9]{9})', digits)
        return '0' + mobile.group(1) if mobile else None
    return digits if re.fullmatch(r'\+?[0-9]{7,13}', digits) else None


def clean_status(value, default='Pending'):
    """A status in the form it is stored; `default` when blank; None when it is not one we know."""
    if value is None:
        return default
    if not isinstance(value, str):
        return None
    key = ' '.join(re.sub(r'[^a-z]+', ' ', value.lower()).split())
    return STATUS_ALIASES.get(key) if key else default


# ─── Release hours ────────────────────────────────────────────────────────

OFFICE_WINDOWS = ((time(8, 0), time(12, 0)), (time(13, 0), time(17, 0)))     # Monday to Friday
RELEASE_HOURS = 'Mon–Fri, 8:00 AM–12:00 PM and 1:00–5:00 PM (lunch break 12:00–1:00 PM)'


def office_hours_error(when):
    """Why an appointment cannot start at this date and time, or None when it is inside the release hours."""
    if when.weekday() >= 5:
        return 'The office is closed on Saturdays and Sundays. Choose a day from Monday to Friday.'
    clock = when.time()
    if time(12, 0) <= clock < time(13, 0):
        return 'The office is closed for lunch from 12:00 to 1:00 PM. Choose a time before 12:00 PM or from 1:00 PM.'
    if not any(start <= clock < end for start, end in OFFICE_WINDOWS):
        return 'The office is open from 8:00 AM to 5:00 PM. Choose a start time in those hours.'
    return None


# ─── CSV import ───────────────────────────────────────────────────────────

IMPORT_COLUMNS = ('student_id', 'full_name', 'year_section', 'status', 'address', 'contact_number')
REQUIRED_COLUMNS = ('student_id', 'full_name', 'year_section')
COLUMN_ALIASES = {
    'student_id': {'student_id', 'studentid', 'id', 'id_number', 'id_no', 'student_number', 'student_no',
                   'student_id_number'},
    'full_name': {'full_name', 'fullname', 'name', 'student_name'},
    'year_section': {'year_section', 'year_and_section', 'yearsection', 'year_sec', 'yr_sec', 'yr_section',
                     'year_level_section'},
    'status': {'status', 'id_status'},
    'address': {'address', 'home_address'},
    'contact_number': {'contact_number', 'contact', 'contact_no', 'contact_num', 'phone', 'phone_number',
                       'mobile', 'mobile_number', 'cellphone', 'cellphone_number'},
}
MAX_IMPORT_BYTES = 1024 * 1024
MAX_IMPORT_ROWS = 2000


class ImportFileError(ValueError):
    """The file as a whole cannot be imported (not a CSV, columns missing, too big). Nothing was added."""


def column_key(header):
    return re.sub(r'[^a-z0-9]+', '_', str(header).lower()).strip('_')


def decode_upload(data):
    """Text of an uploaded file. Excel's "CSV UTF-8" starts with a marker that utf-8-sig removes; the
    older "CSV (Comma delimited)" is Windows-1252."""
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    return data.decode('latin-1')


def check_student(raw):
    """Validate one student given as {'student_id', 'full_name', 'year_section', ...}.

    Returns (fields, None) with the values as they are stored, or (None, message).
    """
    student_id = clean_student_id(raw.get('student_id', ''))
    if student_id is None:
        return None, STUDENT_ID_HINT
    name = tidy(raw.get('full_name', ''))
    if not name or len(name) > NAME_MAX:
        return None, f'Full name is required (maximum {NAME_MAX} characters).'
    year_section = parse_year_section(raw.get('year_section', ''))
    if year_section is None:
        return None, YEAR_SECTION_HINT
    status = clean_status(raw.get('status'))
    if status is None:
        return None, 'Status must be Pending, Processing, For release or Released.'
    address = tidy(raw.get('address', ''))
    if len(address) > ADDRESS_MAX:
        return None, f'Address is too long (maximum {ADDRESS_MAX} characters).'
    contact = clean_contact(raw.get('contact_number', ''))
    if contact is None:
        return None, CONTACT_HINT
    return {'student_id': student_id, 'name': name, 'year_level': year_section[0], 'section': year_section[1],
            'status': status, 'address': address, 'contact_number': contact}, None


def unprotect(cell):
    """The export puts an apostrophe before a value that starts like a spreadsheet formula (+6328881234 becomes
    '+6328881234). Reading an exported file back takes that apostrophe off again."""
    return cell[1:] if cell[:1] == "'" and cell[1:2] in ('=', '+', '-', '@') else cell


def read_student_csv(data):
    """Read an uploaded CSV of students.

    Returns (students, problems, total): the rows that can be added (each with the 'line' it came from),
    the rows that cannot ({'line', 'student_id', 'error'}) and how many rows the file had.
    Raises ImportFileError when the file itself is unusable.
    """
    text = decode_upload(data)
    if '\x00' in text:
        raise ImportFileError('This does not look like a CSV text file.')
    text = text.replace('\r\n', '\n').replace('\r', '\n')          # every kind of line end becomes one
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ImportFileError('The file is empty.')
    delimiter = max(',;\t', key=lines[0].count)          # Excel in some countries separates with ";"
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = None
    for cells in reader:
        if any(cell.strip() for cell in cells):
            header = cells
            break
    mapping = {}
    for position, cell in enumerate(header):
        key = column_key(cell)
        for column, aliases in COLUMN_ALIASES.items():
            if key in aliases and column not in mapping:
                mapping[column] = position
    missing = [column for column in REQUIRED_COLUMNS if column not in mapping]
    if missing:
        raise ImportFileError(
            f'The first row must name the columns. Missing: {", ".join(missing)}. '
            f'Expected: {", ".join(IMPORT_COLUMNS)}.')
    students, problems, seen, total = [], [], {}, 0
    try:
        for cells in reader:
            if not any(cell.strip() for cell in cells):
                continue
            total += 1
            if total > MAX_IMPORT_ROWS:
                raise ImportFileError(f'The file has more than {MAX_IMPORT_ROWS} students. Split it into smaller files.')
            raw = {column: unprotect(cells[position]) if position < len(cells) else ''
                   for column, position in mapping.items()}
            fields, error = check_student(raw)
            shown_id = tidy(raw.get('student_id', ''))[:20]
            if fields is not None and fields['student_id'] in seen:
                fields, error = None, f'Repeated: this student is already on line {seen[fields["student_id"]]}.'
            if fields is None:
                problems.append({'line': reader.line_num, 'student_id': shown_id, 'error': error})
                continue
            seen[fields['student_id']] = reader.line_num
            students.append({**fields, 'line': reader.line_num})
    except csv.Error as error:
        raise ImportFileError(f'The file could not be read as CSV ({error}).') from None
    return students, problems, total


# ─── Finding a student by name ────────────────────────────────────────────

# Words that are not part of a name. What is left of a message after removing them has to match a
# student's name for the message to count as a name search.
NAME_STOPWORDS = frozenset('''
a an the of for to in on at by with from and or as is are was were am be been being it its this that these
those there here i me my mine we us our you your yours he him his she her they them their what which who
whom whose where when why how can could would should will shall do does did please pls kindly hi hello hey
thanks thank ok okay yes no not about info information details detail data record records status state
check checking search searching find finding look looking lookup show tell give get view see want need
know let lets id ids student students name names number num school card cards pending processing release
released ready schedule schedules appointment appointments qr sticker address contact phone
ako ko mo ka ikaw ang ng sa na ba po ni si kay kina nila niya ano sino nasaan kailan paano bakit mayroon
meron yung yong iyong iyung nito dito doon daw lang pa at saan alam gusto paki pakicheck pakisearch
hanapin hanap tingnan tignan ipakita alamin kung
'''.split())
# "may" is both a first name and an everyday word (English, and Tagalog for "there is"). It is a name
# first; only when that finds nobody is it treated like any other filler word.
OPTIONAL_WORDS = frozenset({'may'})
MAX_NAME_WORDS = 6


def fold(text):
    """Lower case without accents, so 'Muñoz' and 'munoz' are the same."""
    decomposed = unicodedata.normalize('NFKD', str(text))
    return ''.join(char for char in decomposed if not unicodedata.combining(char)).lower()


def words(text):
    """The letters-only words of a text, with 's, 'm, n't and the like removed first."""
    text = re.sub(r"n[’']t\b|[’'](?:s|m|d|ll|re|ve)\b", '', fold(text))
    return re.findall(r'[a-z]+', text)


def word_matches(query_word, name_word):
    return name_word == query_word or (len(query_word) >= 3 and name_word.startswith(query_word))


def name_query(text):
    """The words of a message that could be a student's name, or [] when it is not shaped like one."""
    kept = [word for word in words(text) if word not in NAME_STOPWORDS and len(word) > 1]
    if not kept or len(kept) > MAX_NAME_WORDS:
        return []
    if len(kept) == 1 and len(kept[0]) < 4:
        return []                                  # one short word ("ana", "joy") is not enough to go on
    return kept if max(map(len, kept)) >= 3 else []


def looks_like_a_name(text):
    """True for two to five plain words with no filler or question words, digits or question marks."""
    if '?' in text or any(char.isdigit() for char in text):
        return False
    tokens, plain = text.split(), words(text)
    return 2 <= len(tokens) <= 5 and len(plain) == len(tokens) and not any(word in NAME_STOPWORDS for word in plain)


def find_by_name(text, people):
    """The people whose names match a message, best match first. [] when nothing matches.

    Every word that is left after the filler words are removed must start a word of the name, in any
    order. A name typed in full wins over longer names that merely contain it.
    people is a list of dicts with at least 'name'.
    """
    wanted = name_query(text)
    attempts = [wanted]
    if any(word in OPTIONAL_WORDS for word in wanted):
        attempts = [wanted, [word for word in wanted if word not in OPTIONAL_WORDS]]
    for attempt in attempts:
        attempt = [word for word in attempt if word]
        if not attempt or (len(attempt) == 1 and len(attempt[0]) < 4):
            continue
        hits = []
        for person in people:
            parts = words(person['name'])
            if all(any(word_matches(word, part) for part in parts) for word in attempt):
                exact = [word for word in parts if len(word) > 1] == attempt
                hits.append((not exact, person['name'].lower(), person))
        if hits:
            hits.sort(key=lambda hit: hit[:2])
            if not hits[0][0] and (len(hits) == 1 or hits[1][0]):
                return [hits[0][2]]                # one name typed in full
            return [hit[2] for hit in hits]
    return []


# ─── What the records mean at the gate, and the penalty for false statements ──

NOT_YET_MADE = ('Pending', 'Processing')       # "hindi pa nagagawang ID": the card does not exist yet
STATUS_NOTES = {
    'Pending': 'ID not yet made',
    'Processing': 'ID is being made',
    'For release': 'ID is made, ready to claim',
    'Released': 'ID already claimed',
}
PENALTY_AMOUNT = 1500                          # pesos
PENALTY_AT = 2                                 # charged at the second false statement a student makes
PENALTY_TEXT = f'₱{PENALTY_AMOUNT:,}'
PENALTY_NOTE = f'Giving false information about a missing or lost ID twice carries a {PENALTY_TEXT} penalty.'
CASHIER_STEP = 'Send the student to the Cashier to pay the replacement fee.'
OFFICE_STEP = ('Then the student goes to the Property Management Office with the Cashier’s receipt, '
               'an affidavit of loss and a 1×1 photo.')

STUDENT_LOST_STEPS = ('Go to the Cashier first and pay the replacement fee.',
                      'Then bring the Cashier’s receipt, an affidavit of loss and a 1×1 photo to the Property '
                      'Management Office.')
# What a student is told about where a request stands. The coordinator's own handling is never shown.
REQUEST_STATUS_NOTES = {
    'Submitted': 'waiting for the office to look at it',
    'In review': 'staff are looking at it',
    'Accepted': 'accepted, follow the office’s instructions',
    'Closed': 'finished',
}


def penalty_for(number):
    """The penalty for the number-th false statement a student makes: charged once, at the second.

    The written rule is "lied twice: ₱1,500". It says nothing about a third time, so a third and later
    false statement are recorded without an automatic amount; the coordinator decides.
    """
    return PENALTY_AMOUNT if number == PENALTY_AT else 0


def gate_note(status):
    """What the records mean for a guard who is standing at the gate with this student."""
    if status == 'Released':
        return 'Gate: the ID was released, so the student should have the card.'
    if status == 'For release':
        return ('Gate: the ID is made but not claimed yet. Send the student to the Property Management '
                'Office to claim it.')
    return f'Gate: the ID is not yet made (records show {status}), so the student cannot have the card yet.'


def claim_check(category, status):
    """Does what a student says at the gate agree with the records? Returns (agrees, message).

    agrees is True or False when the records settle it and None when they cannot (a student who says
    "no ID" but whose card was released may simply have left it at home).
    """
    note = STATUS_NOTES.get(status, 'see the office')
    if category == 'Lost ID':
        if status == 'Released':
            return True, 'Records agree: this ID was released, so it can have been lost.'
        return False, (f'Records do not agree: this ID was never released (status {status}: {note}). '
                       'A card that was never handed over cannot be lost.')
    if category == 'No ID':
        if status in NOT_YET_MADE:
            return True, f'Records agree: the ID is not made yet (status {status}: {note}).'
        if status == 'For release':
            return True, 'Records agree: the card is made but the student has not claimed it yet.'
        return None, ('Records show this ID was released. Ask the student where the card is; '
                      'if it was lost, file a Lost ID incident.')
    return None, ''


def incident_steps(category, penalty=0):
    """What the guard should do next after filing an incident."""
    steps = [CASHIER_STEP, OFFICE_STEP] if category == 'Lost ID' else []
    if penalty:
        steps.append(f'This is the second false statement, so the {PENALTY_TEXT} penalty applies. '
                     'Send the student to the Cashier to pay it.')
    return steps
