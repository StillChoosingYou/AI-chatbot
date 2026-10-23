"""Turn reviewed chatbot feedback into an evaluation CSV for train_model.py.

This closes the loop on the 👎 list. A student shares a question, the coordinator picks the
topic it should have matched, and the Feedback page exports a CSV. This script keeps the rows
that have both and writes the `id,text,intent` file that `train_model.py --test-dataset` reads.
The questions were already redacted (IDs, names, e-mails and long numbers) when they were shared.

    python feedback_to_dataset.py feedback.csv data/feedback_questions.csv
    python train_model.py --test-dataset data/feedback_questions.csv

Use the result as an independent test set. If you also train on it you no longer have one.
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

from intent_model import ROOT
from train_model import normalized

CATALOG = ROOT / 'property_management_intent.csv'
OUTPUT_COLUMNS = ('id', 'text', 'intent')
NEEDED_COLUMNS = {'id', 'shared_question', 'correct_intent'}
TOO_FEW = 30


def read_labels(catalog):
    with Path(catalog).open(encoding='utf-8-sig', newline='') as file:
        return {(row.get('intent') or '').strip() for row in csv.DictReader(file)} - {''}


def convert(rows, labels):
    """Return (kept, skipped): kept is [{'id','text','intent'}], skipped counts rows by reason.

    A question is kept once. The same question (ignoring case and punctuation) with two
    different topics is ambiguous, so every copy of it is dropped.
    """
    skipped = Counter()
    groups = {}                                   # normalised text -> [(id, text, intent), ...]
    for row in rows:
        text = (row.get('shared_question') or '').strip()
        intent = (row.get('correct_intent') or '').strip()
        identifier = (row.get('id') or '').strip()
        if not identifier:
            skipped['the row has no id'] += 1
        elif not text:
            skipped['the question was not shared'] += 1
        elif not intent:
            skipped['no correct topic chosen yet'] += 1
        elif intent not in labels:
            skipped['topic is not in the intent catalog'] += 1
        elif not normalized(text):
            skipped['the question has no words'] += 1
        else:
            groups.setdefault(normalized(text), []).append((f'feedback-{identifier}', text, intent))
    kept = []
    for group in groups.values():
        if len({intent for _, _, intent in group}) > 1:
            skipped['same question given different topics'] += len(group)
            continue
        if len(group) > 1:
            skipped['repeated question'] += len(group) - 1
        first_id, text, intent = group[0]
        kept.append({'id': first_id, 'text': text, 'intent': intent})
    return kept, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('feedback_csv', type=Path, help='CSV from the Feedback page (Export CSV)')
    parser.add_argument('output', type=Path, help='evaluation CSV to write (id,text,intent)')
    parser.add_argument('--catalog', type=Path, default=CATALOG, help='intent catalog CSV (default: %(default)s)')
    parser.add_argument('--force', action='store_true', help='overwrite the output file if it exists')
    args = parser.parse_args(argv)

    if args.output.exists() and not args.force:
        print(f'{args.output} already exists. Choose another name or add --force.', file=sys.stderr)
        return 2
    with args.feedback_csv.open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        if not NEEDED_COLUMNS.issubset(reader.fieldnames or ()):
            print('That does not look like the Feedback export: it needs id, shared_question and '
                  'correct_intent columns.', file=sys.stderr)
            return 2
        rows = list(reader)
    kept, skipped = convert(rows, read_labels(args.catalog))

    print(f'Read {len(rows)} rows: kept {len(kept)}, skipped {sum(skipped.values())}.')
    for reason, count in skipped.most_common():
        print(f'  skipped {count}: {reason}')
    if not kept:
        print('Nothing to write. A row needs a shared question and a correct topic '
              '(set both on the Feedback page).', file=sys.stderr)
        return 1
    with args.output.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS, lineterminator='\n')
        writer.writeheader()
        writer.writerows(kept)
    print(f'Wrote {args.output}. Questions per topic:')
    for intent, count in sorted(Counter(row['intent'] for row in kept).items()):
        print(f'  {intent}: {count}')
    if len(kept) < TOO_FEW:
        print(f'Only {len(kept)} questions: report them as examples and counts, not as an accuracy figure.')
    print(f'Next: python train_model.py --test-dataset {args.output}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
