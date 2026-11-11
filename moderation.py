"""Chat moderation: finds offensive, abusive and threatening messages, offline and without any outside AI service.

Two parts work together and the result is the stronger of the two:

  rules   a word list (English, Filipino, Taglish) that is read after undoing the usual tricks
          (`g@go`, `p u t a`, `f*ck`, `tangiinaaa`), plus patterns for threats and sexual harassment
  model   a small classifier (character n-grams + logistic regression) trained here on moderation_dataset.csv.
          It catches spellings the word list has never seen. A guess of the model alone is only ever a notice
          (no strike) that goes to the administrator's list to be reviewed; strikes need the word list to agree.

Severity
  0  nothing found
  1  notice    a rude word that is not aimed at anyone ("shit, I lost my ID"). Politely answered, no strike.
  2  violation an insult or vulgar word aimed at a person, a slur. One strike.
  3  severe    a threat, sexual harassment, a slur aimed at a person. Chat is paused at once.

A message about hurting oneself is not a violation. `check` reports it as `crisis` so the chat can answer with care.

The word lists contain offensive words on purpose: a filter has to know them. Nothing here is shown to students.
"""
import csv
import hashlib
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / 'moderation_dataset.csv'
MODEL_PATH = ROOT / 'data' / 'moderation_model.joblib'
MODEL_LABELS = ('ok', 'profanity', 'insult', 'harassment', 'threat')
MODEL_MIN_WORDS = 3
MODEL_THRESHOLD = 0.70          # the model alone must be this sure before it flags anything
MODEL_SEVERITY_CAP = 1          # ...and then it is a notice, never a strike

# ─────────────────────────── word lists ───────────────────────────
# kind: mild = notice only · strong = vulgar (a notice, a violation when aimed at someone)
#       person = names a person insultingly, a violation by itself · adj = an insult only when aimed at someone
#       slur = discriminatory
MILD = ('damn', 'dammit', 'crap', 'darn it', 'tae', 'bwisit', 'buwisit', 'pucha', 'putek', 'potek', 'letse', 'leche',
        'peste', 'pakshet', 'shet')
STRONG = ('fuck', 'shit', 'bitch', 'asshole', 'bastard', 'dick', 'pussy', 'cunt', 'motherfucker', 'whore', 'slut', 'bullshit',
          'putangina', 'putang ina', 'tangina', 'tang ina', 'puta', 'pota', 'pakyu', 'pak yu', 'punyeta', 'hinayupak', 'hudas',
          'kantot', 'kantutan', 'jakol', 'salsal', 'tite', 'titi', 'burat', 'bayag', 'iyot', 'hindot', 'chupa', 'putragis',
          'gaga ka', 'hayop ka', 'animal ka', 'demonyo ka', 'lintik', 'lintek')
PERSON = ('gago', 'gaga', 'tanga', 'bobo', 'boba', 'tarantado', 'tarantada', 'siraulo', 'sira ulo', 'engot', 'shunga', 'kupal',
          'abnoy', 'mangmang', 'hangal', 'walang kwenta', 'walang silbi', 'idiot', 'moron', 'imbecile', 'dumbass',
          'jackass', 'dipshit', 'scumbag', 'loser', 'jerk', 'twat', 'wanker', 'asshat', 'ulol')
ADJ = ('stupid', 'dumb', 'ugly', 'useless', 'pathetic', 'trash', 'worthless', 'garbage', 'incompetent', 'clueless', 'pangit',
       'walang utak', 'bulok', 'nakakainis ka', 'annoying', 'disgusting', 'lazy', 'tamad', 'palpak')
SLUR = ('nigger', 'nigga', 'faggot', 'fag', 'chink', 'retard', 'retarded', 'tranny', 'kike', 'spic', 'gook', 'mongoloid')
# Words that are only rude as part of a phrase are in PERSON/STRONG above as phrases; a few real words that look
# like a listed word must never be flagged, whatever the letters around them are.
SAFE_WORDS = ('class', 'classic', 'assignment', 'assist', 'assistant', 'assess', 'assume', 'assure', 'pass', 'passed', 'bass',
              'mass', 'grass', 'glass', 'brass', 'compass', 'embassy', 'cassette', 'analysis', 'dickson', 'hancock', 'cocktail',
              'scunthorpe', 'shiitake', 'title', 'titles', 'titled', 'tae kwon do', 'taekwondo')

# aimed at someone: a word like "you", "ka", "mo" close to the rude word
DIRECT_STRICT = frozenset(('you', 'u', 'ur', 'your', 'youre', 'yours', 'yourself', 'ikaw', 'ka', 'mo', 'kayo', 'niyo', 'ninyo',
                           'kang', 'bot', 'chatbot', 'assistant', 'staff', 'sir', 'maam', 'madam', 'guard', 'teacher',
                           'cashier', 'registrar', 'kayong', 'nyo'))

# threats and harassment: (pattern, category, severity)
_PERSON = r'(?:you|u|ya|him|her|them|everyone|everybody|all of you|staff|sir|maam|guard|teacher|prof|cashier|registrar|ka|kayo|siya|sila)'
_PATTERNS = (
    (r"\b(?:i|ill|i will|i'?m going to|im going to|im gonna|gonna|we will|we'?ll)\s+(?:\w+\s+){0,2}"
     r"(?:kill|murder|stab|shoot|strangle|hurt|beat up|beat|punch|slap|rape|choke|hang|lynch|cut)\s+" + _PERSON + r"\b",
     'threat', 3),
    (r"\b(?:kill|murder|stab|shoot|strangle|rape)\s+(?:yourself|urself)\b", 'threat', 3),
    (r"\b(?:i|ill|i will|gonna|going to)\s+(?:\w+\s+)?(?:bomb|burn|blow up|shoot up|burn down|set fire to)\s+(?:the\s+)?"
     r"(?:school|campus|office|building|college|classroom)\b", 'threat', 3),
    (r"\b(?:bomb threat|there is a bomb|there'?s a bomb|may bomba sa|may bomba dito)\b", 'threat', 3),
    (r"\b(?:i know where you (?:live|stay|study|work)|you will regret (?:this|it)|youll regret (?:this|it)|watch your back|"
     r"you(?:'?re| are) dead|youre dead|you are going to die|you will die)\b", 'threat', 3),
    (r"\b(?:papatayin|patayin|ipapapatay|sasaktan|bubugbugin|susuntukin|sasampalin|sasaksakin|babarilin|barilin|gagantihan|"
     r"pupugutan|ililibing)\s+(?:kita|ko ka|ko kayo|ka|kayo|ko sila|ko siya)\b", 'threat', 3),
    (r"\b(?:mamamatay ka|mamatay ka|pagsisisihan mo|alam ko kung saan ka|sunugin ko|pasasabugin ko|hintayin mo ako sa labas|"
     r"abangan kita|aabangan kita|tatadyakan kita|bubugbugin ko)\b", 'threat', 3),
    (r"\b(?:send|show)\s+(?:me\s+)?(?:your\s+)?(?:nudes?|nude pics?|naked|boobs|tits|ass|pussy|dick|body)\b", 'harassment', 3),
    (r"\b(?:sexy mo|sexy ka|ang sexy mo|hubad ka|magpakita ka ng|pa-sex|sex tayo|kantutin kita|chupain kita|jakulin kita|"
     r"ikama kita|kama tayo|tikman kita|gahasain kita|hipuan kita)\b", 'harassment', 3),
    (r"\b(?:have sex with you|sleep with you|sleep with me|fuck you tonight|bang you|want to fuck|wanna fuck|nude of you)\b",
     'harassment', 3),
    (r"\b(?:ang\s+)?(?:ganda|gwapo|hot)\s+mo\s+(?:naman|talaga)?\s*(?:sa|pag)\s+(?:kama|gabi)\b", 'harassment', 2),
    (r"\b(?:stalk(?:ing)?\s+(?:you|u)|susundan kita|sinusundan kita|i am watching you|im watching you)\b", 'harassment', 2),
)
_CRISIS = (
    r"\b(?:i want to die|i wanna die|i want to kill myself|kill myself|end my life|ending my life|i will kill myself|"
    r"want to end it all|suicide|suicidal|better off dead)\b",
    r"\b(?:gusto ko nang mamatay|gusto ko ng mamatay|magpapakamatay|papatayin ko ang sarili ko|ayoko nang mabuhay|"
    r"ayaw ko nang mabuhay|wala nang saysay ang buhay ko)\b",
)

LEET = str.maketrans({'0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's', '7': 't', '8': 'b', '@': 'a', '$': 's'})


# ─────────────────────────── reading the text ───────────────────────────
def fold(text):
    """Lower case, accents removed."""
    text = unicodedata.normalize('NFKD', str(text).lower())
    return ''.join(ch for ch in text if not unicodedata.combining(ch))


def _token(token):
    """One word with number-and-symbol tricks undone ("g@g0" -> "gago"), only when it already holds letters."""
    token = re.sub(r'(?<=[a-z])[!+]+$', '', token)           # "ka!" is "ka" with an exclamation mark, not "kai"
    if re.search('[a-z]', token) and re.search(r'[0-9@$!+]', token):
        token = re.sub(r'(?<=[a-z])[!+](?=[a-z])', lambda m: 'i' if m.group() == '!' else 't', token)
        token = token.translate(LEET)
    return re.sub(r'(.)\1{2,}', r'\1\1', token)           # "puuuta" -> "puuta"


def normalise(text):
    """The text as a list of lower-case words, tricks undone. Letters spaced out ("p u t a", "f.u.c.k") are joined."""
    text = fold(text)
    text = re.sub(r'(?<![a-z0-9])(?:[a-z0-9][.\-_*]){3,}[a-z0-9](?![a-z0-9])',
                  lambda m: re.sub(r'[^a-z0-9]', '', m.group()), text)
    raw = re.findall(r"[a-z0-9@$!+*]+(?:'[a-z]+)?", text)
    words, run = [], []
    for item in raw + ['']:
        if len(item) == 1 and item.isalpha():
            run.append(item)
            continue
        if len(run) >= 4:                                       # a b c d  ->  abcd
            words.append(''.join(run))
        else:
            words.extend(run)
        run = []
        if item:
            words.append(_token(item.replace("'", '')))
    return [w for w in words if w]


def _letters(word):
    parts, i = [], 0
    while i < len(word):
        j = i
        while j < len(word) and word[j] == word[i]:
            j += 1
        ch, size = re.escape(word[i]), j - i
        cls = f'[{ch}*]' if i else ch                           # "f*ck": a star may stand for a letter
        parts.append(f'{cls}{{{size},}}' if size > 1 else f'{cls}+')
        i = j
    return ''.join(parts)


def _term(term):
    return r'\s+'.join(_letters(word) for word in term.split())


def _compile(terms, suffixes=True):
    body = '|'.join(_term(t) for t in sorted(set(terms), key=len, reverse=True))
    tail = r'(?:s|es|ed|er|ers|ing|ng|g)?' if suffixes else ''
    return re.compile(r'(?<![a-z*])(?:' + body + r')' + tail + r'(?![a-z*])')


_KINDS = (('mild', _compile(MILD), 'profanity'), ('strong', _compile(STRONG), 'profanity'),
          ('person', _compile(PERSON), 'insult'), ('adj', _compile(ADJ), 'insult'), ('slur', _compile(SLUR), 'hate'))
_SAFE = re.compile(r'(?<![a-z*])(?:' + '|'.join(re.escape(w) for w in sorted(SAFE_WORDS, key=len, reverse=True)) + r')(?![a-z*])')
_PATTERNS = tuple((re.compile(p), cat, sev) for p, cat, sev in _PATTERNS)
_CRISIS = tuple(re.compile(p) for p in _CRISIS)
# "hayopka", "gagoka", "bobomo": the rude word and the "ka"/"mo"/"you" aimed at someone typed as one word.
_GLUED = {t.replace(' ', ''): 'insult' for t in STRONG + PERSON if ' ' in t and t.split()[-1] in ('ka',)}
for _term_ in STRONG + PERSON:
    _stem = _term_.replace(' ', '')
    if ' ' not in _term_ and len(_stem) >= 4:
        for _tail in ('ka', 'mo', 'kayo', 'niyo', 'you', 'u'):
            _GLUED.setdefault(_stem + _tail, 'insult')
_SQUASH = re.compile(r'(?:putangina|tangina|motherfucker|fuckyou|fuckoff|pakyou|gagoka|tangaka|bobomo|ulolka)')


def _aimed(words, start, end):
    near = words[max(0, start - 4):end + 4]
    return any(w in DIRECT_STRICT for w in near)


def mask(word):
    return word[:1] + '*' * (len(word) - 1) if len(word) > 1 else word


def rules(text):
    """What the word list and the patterns find: {'severity', 'categories', 'terms', 'masked', 'crisis'}."""
    words = normalise(text)
    joined = ' '.join(words)
    index, offset = [], 0
    for position, word in enumerate(words):
        index.append((offset, offset + len(word), position))
        offset += len(word) + 1

    def word_range(a, b):
        hit = [p for s, e, p in index if s < b and e > a]
        return (hit[0], hit[-1] + 1) if hit else (0, 0)

    safe = [(m.start(), m.end()) for m in _SAFE.finditer(joined)]
    severity, categories, terms, labels, to_mask = 0, set(), set(), set(), []
    for kind, pattern, category in _KINDS:
        for match in pattern.finditer(joined):
            if any(s <= match.start() and match.end() <= e for s, e in safe):
                continue
            first, last = word_range(match.start(), match.end())
            aimed = _aimed(words, first, last)
            blunt = len(words) <= 3                                      # "gago!" on its own is meant for someone
            level = {'mild': 1, 'strong': 2 if aimed else 1, 'person': 2 if aimed or blunt else 1,
                     'adj': 2 if aimed else 1, 'slur': 3 if aimed else 2}[kind]
            severity = max(severity, level)
            categories.add(category)
            terms.add(match.group().strip())
            to_mask.append((match.start(), match.end()))
    for start, end, position in index:                                      # "hayopka": the word and "ka" typed together
        word = words[position]
        if word in _GLUED and not any(s <= start and end <= e for s, e in safe):
            severity = max(severity, 2)
            categories.add('insult')
            terms.add(word)
            to_mask.append((start, end))
    squashed = False
    if not terms:                                                           # "t a n g i n a" with the gaps closed up
        for match in _SQUASH.finditer(re.sub(r'[^a-z]', '', joined)):
            severity = max(severity, 2)
            categories.add('profanity')
            terms.add(match.group())
            squashed = True
    for pattern, category, level in _PATTERNS:
        for match in pattern.finditer(joined):
            severity = max(severity, level)
            categories.add(category)
            labels.add(f'{category} phrase')
    masked = joined
    for start, end in sorted(set(to_mask), reverse=True):                   # right to left keeps the offsets valid
        masked = masked[:start] + ' '.join(mask(w) for w in masked[start:end].split()) + masked[end:]
    if squashed:                                                            # the word is spread over the text: hide all but first letters
        masked = ' '.join(mask(w) for w in masked.split())
    crisis = any(p.search(joined) for p in _CRISIS)
    shown = sorted({' '.join(mask(w) for w in t.split()[:2]) + (' …' if len(t.split()) > 2 else '') for t in terms} | labels)
    return {'severity': severity, 'categories': sorted(categories), 'terms': shown, 'masked': masked[:1000], 'crisis': crisis}


# ─────────────────────────── the trained model ───────────────────────────
def dataset_hash():
    try:
        return hashlib.sha256(DATASET.read_bytes()).hexdigest()
    except OSError:
        return ''


def build_pipeline():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    return Pipeline([
        ('tfidf', TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 5), min_df=2, sublinear_tf=True)),
        ('clf', LogisticRegression(max_iter=2000, C=8.0, class_weight='balanced', random_state=7)),
    ])


def prepared(text):
    return ' '.join(normalise(text))


def read_dataset(path=DATASET):
    with Path(path).open(encoding='utf-8', newline='') as file:
        return list(csv.DictReader(file))


_MODEL = {'loaded': False, 'pipeline': None, 'reason': ''}


def load_model(force=False):
    """The trained model, or None when it is missing or was trained on another dataset. The rules still work then."""
    if _MODEL['loaded'] and not force:
        return _MODEL['pipeline']
    _MODEL.update(loaded=True, pipeline=None, reason='')
    try:
        import joblib
        bundle = joblib.load(MODEL_PATH)
        if bundle.get('dataset_hash') != dataset_hash():
            _MODEL['reason'] = 'the model was trained on another version of moderation_dataset.csv: run python train_moderation.py'
        else:
            _MODEL['pipeline'] = bundle['pipeline']
    except Exception as error:                                  # a missing or unreadable model is not a crash
        _MODEL['reason'] = f'no moderation model ({type(error).__name__})'
    return _MODEL['pipeline']


def model_status():
    load_model()
    return {'loaded': _MODEL['pipeline'] is not None, 'reason': _MODEL['reason']}


def model_scores(text):
    """{label: probability} from the trained model, or None."""
    pipeline = load_model()
    if pipeline is None:
        return None
    probs = pipeline.predict_proba([prepared(text)])[0]
    return {label: float(p) for label, p in zip(pipeline.classes_, probs)}


def check(text):
    """The verdict on one message.

    {'flagged', 'severity', 'categories', 'terms', 'masked', 'crisis', 'source', 'score'}
    `source` is 'rules', 'model' or 'both'; `score` is the model's chance that the message is not clean.
    """
    found = rules(text)
    severity, categories, source = found['severity'], list(found['categories']), 'rules' if found['severity'] else ''
    scores = model_scores(text) if len(normalise(text)) >= MODEL_MIN_WORDS else None     # one or two words tell it too little
    score = None
    if scores is not None:
        score = round(1 - scores.get('ok', 1.0), 4)
        label = max((k for k in scores if k != 'ok'), key=lambda k: scores[k], default=None)
        if label and score >= MODEL_THRESHOLD and not found['crisis']:
            model_level = MODEL_SEVERITY_CAP
            if label not in categories:
                categories.append(label)
            if model_level > severity:
                severity, source = model_level, ('both' if source else 'model')
            elif source:
                source = 'both'
    return {'flagged': severity > 0 and not found['crisis'], 'severity': 0 if found['crisis'] else severity,
            'categories': sorted(categories), 'terms': found['terms'], 'masked': found['masked'],
            'crisis': found['crisis'], 'source': source, 'score': score}
