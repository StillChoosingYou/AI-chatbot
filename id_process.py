"""The steps an ID goes through at the Property Management Office, and the answers about them.

The flow below is the one assumed for this project (production, signature, Canva edit, printing, case and lace,
releasing). If the office does it differently, edit STEPS: the chatbot, its follow-up chips and the tests read from here.

The ANN decides that a question is about the ID process (intent id_process). This module then decides which step it is
about, by plain keywords, and builds the answer. The same question asked twice in one conversation gets a different
answer each time, so the chat never repeats itself word for word.
"""
import re

STEPS = (
    {
        'key': 'production', 'title': 'Production', 'chip': 'Production',
        'words': ('production', 'produce', 'produced', 'producing', 'gawa', 'gawin', 'ginagawa', 'paggawa', 'encode', 'encoding'),
        'who': 'Property Management Office',
        'what': 'The office checks that your requirements are complete, then encodes your name, student number, year and '
                'section and your 1×1 photo for the card. A record with missing requirements stays Pending until they are submitted.',
        'you': 'Submit the requirements (see “Requirements”) and check that your name and year and section are spelled correctly.',
        'tip': 'Most delays happen here: a missing photo or enrollment proof keeps the ID Pending.',
        'brief': 'requirements checked and your data encoded',
    },
    {
        'key': 'signature', 'title': 'Signature', 'chip': 'Signature',
        'words': ('signature', 'signatures', 'sign', 'signed', 'signing', 'pirma', 'pirmahan', 'lagda'),
        'who': 'You and the authorized signatory',
        'what': 'Your signature is scanned and placed on the card, and the signature of the school’s authorized signatory is added to the back.',
        'you': 'Sign in black ink on clean white paper when the office asks, keeping it inside the box.',
        'tip': 'An unclear or cut-off signature has to be done again, which delays the next step.',
        'brief': 'your signature and the signatory’s are added',
    },
    {
        'key': 'canva', 'title': 'Canva edit', 'chip': 'Canva edit',
        'words': ('canva', 'edit', 'edited', 'editing', 'layout', 'design', 'disenyo'),
        'who': 'Property Management Office',
        'what': 'The front and back are laid out in Canva on the PGPC template: photo, name, student number, year and section, '
                'signature and QR code. The layout is checked for spelling and alignment before it goes to print.',
        'you': 'Nothing, unless the office asks you to confirm how your name should look on the card.',
        'tip': 'A spelling mistake is cheapest to fix here, before anything is printed.',
        'brief': 'the card layout is designed and checked in Canva',
    },
    {
        'key': 'printing', 'title': 'Printing', 'chip': 'Printing',
        'words': ('print', 'printing', 'printed', 'limbag', 'pagpapalimbag', 'pag-print', 'iprint'),
        'who': 'Property Management Office',
        'what': 'The front and back are printed on the card, and the QR sticker is printed at actual size (30 mm). Colors and '
                'alignment are checked on the printed card.',
        'you': 'Nothing. Do not print your own ID; only the office card is valid.',
        'tip': 'The QR is tested with a scan before the card moves on.',
        'brief': 'the card and QR sticker are printed and checked',
    },
    {
        'key': 'case_lace', 'title': 'Case and lace', 'chip': 'Case and lace',
        'words': ('case', 'lace', 'lanyard', 'holder', 'laminate', 'laminated', 'lamination', 'cut', 'cutting'),
        'who': 'Property Management Office',
        'what': 'The printed card is cut, laminated, and put in an ID case, and a lace (lanyard) is attached so it can be worn at all times on campus.',
        'you': 'Nothing. Report a damaged case or lace when you claim the ID.',
        'tip': 'This is the last step before the ID is marked For release.',
        'brief': 'the card is cut, put in a case and given a lace',
    },
    {
        'key': 'releasing', 'title': 'Releasing', 'chip': 'Releasing',
        'words': ('release', 'released', 'releasing', 'claim', 'claiming', 'kuha', 'kunin', 'pagkuha'),
        'who': 'Property Management Office',
        'what': 'The status changes to For release and a release schedule is set. You claim the ID at the office during release hours '
                'and sign the release record, then the status becomes Released.',
        'you': 'Come during release hours with proof of identity. Ask someone else to claim it only if the office allows it.',
        'tip': 'Check your status and release date with “When can I collect my school ID?”.',
        'brief': 'you claim the ID and the status becomes Released',
    },
)
STEP_NUMBERS = {step['key']: number for number, step in enumerate(STEPS, 1)}
OVERVIEW_WORDS = ('process', 'steps', 'step', 'flow', 'proseso', 'hakbang', 'stages', 'stage', 'overview', 'lahat')

# Where an ID is, by its status. The records only hold a status, not the exact step.
WHERE_IS_IT = {
    'Pending': 'it is waiting for requirements, before step 1 (Production) can finish',
    'Processing': 'it is being made, somewhere in steps 1 to 5 (Production to Case and lace)',
    'For release': 'it is at step 6 (Releasing): ready to claim',
    'Released': 'it has been through all six steps and was claimed',
}


def words_of(text):
    return re.findall(r"[a-z0-9'-]+", str(text).lower())


def detect_step(text):
    """The step a question is about (the one named first), or None for a question about the whole process."""
    tokens = words_of(text)
    found = [(tokens.index(word), step) for step in STEPS for word in step['words'] if word in tokens]
    return min(found, key=lambda item: item[0])[1] if found else None


def _where(student):
    if not student:
        return ''
    note = WHERE_IS_IT.get(student.get('status'))
    return f'\n📍 {student["name"]}: status is {student["status"]}, so {note}.' if note else ''


def _overview_variants(student, release_hours):
    steps = '\n'.join(f'{number}. {step["title"]}: {step["brief"]}' for number, step in enumerate(STEPS, 1))
    line = ' → '.join(step['title'] for step in STEPS)
    where = _where(student)
    return [
        f'Your school ID goes through 6 steps at the Property Management Office:\n{steps}{where}\nAsk about any step to see what happens in it.',
        f'The short version: {line}.{where}\nReleasing happens during release hours ({release_hours}). Tap a step to learn more.',
    ]


def _step_variants(step, student, release_hours):
    number, total = STEP_NUMBERS[step['key']], len(STEPS)
    nxt = STEPS[number] if number < total else None
    after = f'Next: {nxt["title"]}.' if nxt else 'This is the last step.'
    extra = f'\nRelease hours: {release_hours}.' if step['key'] == 'releasing' else ''
    return [
        f'Step {number} of {total} · {step["title"]}\n{step["what"]}\nDone by: {step["who"]}.\n{after}{extra}{_where(student)}',
        f'More on {step["title"]} (step {number}): what you can do — {step["you"]}\nGood to know: {step["tip"]}\n{after}{extra}',
        f'{step["title"]} in one line: {step["brief"]}. It is step {number} of {total}. {after}{extra}',
    ]


def process_answer(text, student, recent_answers, release_hours):
    """(answer, chips) for a question about the ID process.

    recent_answers are the bot's latest answers in this conversation; an answer already given is not given again.
    """
    step = detect_step(text)
    variants = _step_variants(step, student, release_hours) if step else _overview_variants(student, release_hours)
    given = {answer.strip() for answer in recent_answers}
    answer = next((variant for variant in variants if variant.strip() not in given), None)
    if answer is None:                                    # everything was said already: point to what else there is
        asked = step['title'] if step else 'the ID process'
        pointer = STEPS[min(STEP_NUMBERS[step['key']], len(STEPS) - 1)] if step else STEPS[0]
        nudges = (f'We already went over {asked}. Pick another step to continue; {pointer["title"]} is a good one to ask about next.',
                  f'That is all I have on {asked}. To see your own release date, ask “When can I collect my school ID?”.',
                  f'{asked.capitalize()} was covered above. Tap a step below, or contact the office if something is still unclear.')
        count = 0
        while True:                                       # a longer conversation still gets a new sentence each time
            answer = nudges[count % len(nudges)] + ('' if count < len(nudges) else f' (asked {count + 1} times)')
            if answer.strip() not in given:
                break
            count += 1
    return answer, chips_for(step)


def chips_for(step):
    """Chips for what to ask next: the next step, the one before it, and the whole process."""
    chips = []
    if step:
        number = STEP_NUMBERS[step['key']]
        if number < len(STEPS):
            chips.append(_chip(STEPS[number]))
        if number > 1:
            chips.append(_chip(STEPS[number - 2]))
        chips.append({'label': 'All steps', 'question': 'What is the process of making a school ID?'})
    else:
        chips = [_chip(STEPS[0]), _chip(STEPS[3]), _chip(STEPS[5])]
    return chips[:3]


def _chip(step):
    return {'label': step['chip'], 'question': f'What happens during {step["title"].lower()} of my school ID?'}
