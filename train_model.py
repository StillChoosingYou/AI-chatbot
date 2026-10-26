"""Train a school ID ANN from your CSVs, evaluate it, and save it locally.

    python train_model.py
    python train_model.py --report-dir /path/to/reports
    python train_model.py --test-dataset independent_student_questions.csv

No online AI service or pretrained language model is used.
"""
import argparse
import csv
import json
import math
import platform
import re
import tempfile
import warnings
from collections import Counter
from pathlib import Path

import joblib
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from threadpoolctl import threadpool_limits

from intent_model import DEFAULT_MODEL, DEFAULT_THRESHOLD, FALLBACK, ROOT, classify, file_hash


def normalized(text):
    return ' '.join(re.findall(r'\w+', text.casefold()))


def load_dataset(path, catalog):
    with Path(catalog).open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or 'intent' not in reader.fieldnames:
            raise ValueError('Intent catalog needs an intent column.')
        labels = [(row.get('intent') or '').strip() for row in reader]
    if not labels or '' in labels or len(labels) != len(set(labels)) or FALLBACK not in labels:
        raise ValueError('Catalog requires unique nonempty intents, including out_of_scope.')
    with Path(path).open(encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not {'id','text','intent'}.issubset(reader.fieldnames):
            raise ValueError('Dataset needs id, text, and intent columns.')
        rows = list(reader)
    texts, identifiers = set(), set()
    for row in rows:
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ('id','text','intent')):
            raise ValueError('Each row needs a nonempty id, text, and intent.')
        if row['id'] in identifiers or normalized(row['text']) in texts:
            raise ValueError('Dataset has duplicate IDs or normalized questions; remove them before splitting.')
        if not normalized(row['text']) or row['intent'] not in labels:
            raise ValueError(f'Invalid question or unknown intent in row {row["id"]}.')
        identifiers.add(row['id'])
        texts.add(normalized(row['text']))
    if not rows:
        raise ValueError('Dataset is empty.')
    return rows, sorted(labels)


def build_pipeline(seed=42, hidden_units=32, max_iter=600):
    return Pipeline([
        ('tfidf', TfidfVectorizer(lowercase=True, ngram_range=(1,2), sublinear_tf=True)),
        ('ann', MLPClassifier(hidden_layer_sizes=(hidden_units,), activation='relu',
                              solver='lbfgs', alpha=0.1, max_iter=max_iter,
                              random_state=seed)),
    ])


def fit(pipeline, rows):
    # Keep small-matrix training fast on machines with many BLAS threads.
    with threadpool_limits(limits=1), warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always', ConvergenceWarning)
        pipeline.fit([row['text'] for row in rows], [row['intent'] for row in rows])
    return [str(item.message) for item in caught if issubclass(item.category, ConvergenceWarning)]


def train(args):
    if not 0 < args.test_size < 1 or not 0 <= args.threshold <= 1:
        raise ValueError('Test size must be between 0 and 1; threshold must be between 0 and 1.')
    if args.hidden_units < 1 or args.max_iter < 1:
        raise ValueError('Hidden units and max iterations must be positive.')
    # Deployment always uses these two source CSVs, ensuring saved fingerprints match inference.
    dataset = ROOT / 'property_management_dataset.csv'
    catalog = ROOT / 'property_management_intent.csv'
    rows, labels = load_dataset(dataset, catalog)
    counts = Counter(row['intent'] for row in rows)
    # out_of_scope is the fallback: the app gives it to a question with no known topic word or a weak match, so
    # the dataset holds school ID questions only and the fallback needs no examples. Every other topic does.
    missing = sorted(set(labels) - set(counts) - {FALLBACK})
    if missing:
        raise ValueError('Training dataset must contain examples for every catalog intent except out_of_scope. '
                         f'Missing: {", ".join(missing)}.')
    if args.test_dataset:
        test_rows, _ = load_dataset(args.test_dataset, catalog)
        if {normalized(row['text']) for row in rows} & {normalized(row['text']) for row in test_rows}:
            raise ValueError('Independent test questions overlap training questions.')
        training_rows = rows
        split = 'independent test CSV; all source examples used for training'
    else:
        n_test = math.ceil(len(rows)*args.test_size)
        if min(counts.values()) < 2 or n_test < len(counts) or len(rows)-n_test < len(counts):
            raise ValueError('Stratified split requires two examples per intent and enough train/test rows for all intents.')
        training_rows, test_rows = train_test_split(rows, test_size=args.test_size,
                                                  stratify=[row['intent'] for row in rows],
                                                  random_state=args.seed)
        split = 'stratified random holdout; synthetic paraphrases may remain across splits'

    evaluation_model = build_pipeline(args.seed, args.hidden_units, args.max_iter)
    convergence = fit(evaluation_model, training_rows)
    questions = [row['text'] for row in test_rows]
    actual = [row['intent'] for row in test_rows]
    raw = evaluation_model.predict(questions)
    served, scores = classify(evaluation_model, questions, args.threshold)
    # Scores are averaged over the topics that have questions. The fallback has no examples of its own, and
    # counting it would hold macro F1 below 1 for a reason that has nothing to do with the network.
    present = set(counts) | set(actual)
    scored = [label for label in labels if label in present]
    turned_away = sum(1 for want, got in zip(actual, served) if got == FALLBACK and want != FALLBACK)
    report = {
        'technique': 'ANN: TF-IDF + MLPClassifier, one ReLU hidden layer, softmax output',
        'dataset_origin': 'Synthetic seed questions; not independently validated student conversations',
        'split_method': split, 'seed': args.seed,
        'train_count': len(training_rows), 'test_count': len(test_rows),
        'deployment_train_count': len(rows),
        'train_ids': [row['id'] for row in training_rows],
        'test_ids': [row['id'] for row in test_rows],
        'parameters': {'hidden_units': args.hidden_units, 'solver':'lbfgs', 'alpha':0.1,
                       'max_iter':args.max_iter, 'ngram_range':[1,2], 'confidence_threshold':args.threshold},
        'accuracy': float(accuracy_score(actual, raw)),
        'macro_f1': float(f1_score(actual, raw, labels=scored, average='macro', zero_division=0)),
        'accuracy_with_fallback': float(accuracy_score(actual, served)),
        'macro_f1_with_fallback': float(f1_score(actual, served, labels=scored, average='macro', zero_division=0)),
        'turned_away_by_fallback': turned_away,
        'classification_report': classification_report(actual, served, labels=scored, output_dict=True, zero_division=0),
        'confusion_matrix_labels': labels,
        'confusion_matrix': confusion_matrix(actual, served, labels=labels).tolist(),
        'evaluation_iterations': int(evaluation_model.named_steps['ann'].n_iter_),
        'convergence_warnings': convergence,
        'versions': {'python':platform.python_version(), 'scikit_learn':sklearn.__version__, 'joblib':joblib.__version__},
        'input_hashes': {dataset.name:file_hash(dataset), catalog.name:file_hash(catalog)},
        'test_dataset_hash': file_hash(args.test_dataset) if args.test_dataset else None,
        'limitations': ['Small synthetic dataset; holdout scores do not establish real-world accuracy.',
                        'The dataset holds school ID questions only. out_of_scope has no examples: a held-out question '
                        'with no known topic word, or a score under the threshold, is turned away and counts as '
                        'wrong for its own topic.',
                        'Review/group near-duplicate paraphrases before formal evaluation.',
                        'Softmax confidence is uncalibrated; threshold was fixed before evaluation.',
                        'Held-out results describe the evaluation model, not the refit deployment model.'],
    }
    # Only after evaluation, refit on all source examples for the application.
    if args.test_dataset:
        deployed = evaluation_model
    else:
        deployed = build_pipeline(args.seed, args.hidden_units, args.max_iter)
        report['convergence_warnings'].extend(fit(deployed, rows))
    report['deployment_iterations'] = int(deployed.named_steps['ann'].n_iter_)
    bundle = {'format_version':1, 'sklearn_version':sklearn.__version__,
              'input_hashes':report['input_hashes'], 'threshold':args.threshold,
              'pipeline':deployed}
    model_path = Path(args.model_out)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    # Avoid partially written artifacts if saving is interrupted.
    with tempfile.NamedTemporaryFile(dir=model_path.parent, suffix='.joblib', delete=False) as file:
        temporary = Path(file.name)
    try:
        joblib.dump(bundle, temporary)
        temporary.replace(model_path)
    finally:
        temporary.unlink(missing_ok=True)
    output = Path(args.report_dir)
    output.mkdir(parents=True, exist_ok=True)
    (output/'metrics.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    with (output/'predictions.csv').open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow(['id','text','actual_intent','raw_prediction','served_prediction','uncalibrated_score'])
        writer.writerows((row['id'], row['text'], row['intent'], str(predicted), str(fallback), float(score))
                         for row,predicted,fallback,score in zip(test_rows,raw,served,scores))
    with (output/'confusion_matrix.csv').open('w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, lineterminator='\n')
        writer.writerow(['actual / predicted', *labels])
        writer.writerows([label, *row] for label, row in zip(labels, report['confusion_matrix']))
    summary = (f'# School ID ANN evaluation\n\n'
               f'Training: {len(training_rows)} examples. Held-out test: {len(test_rows)} examples.\n\n'
               f'- Raw intent accuracy: {report["accuracy"]:.1%}\n'
               f'- Raw macro F1: {report["macro_f1"]:.3f}\n'
               f'- Accuracy including fallback: {report["accuracy_with_fallback"]:.1%}\n'
               f'- Macro F1 including fallback: {report["macro_f1_with_fallback"]:.3f}\n'
               f'- Held-out questions turned away by the fallback: {turned_away} of {len(test_rows)}\n\n'
               f'Deployment model fitted on {len(rows)} source examples after evaluation. '
               'Scores above describe the evaluation model.\n\n'
               + '\n'.join('- '+item for item in report['limitations'])+'\n')
    if report['convergence_warnings']:
        summary += '\nConvergence warnings: '+ '; '.join(report['convergence_warnings'])+'\n'
    (output/'evaluation.md').write_text(summary, encoding='utf-8')
    print(summary)
    print(f'Saved model: {model_path}\nReports: {output}')
    return report


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model-out', type=Path, default=DEFAULT_MODEL)
    parser.add_argument('--report-dir', type=Path, default=ROOT/'data'/'training')
    parser.add_argument('--test-dataset', type=Path, help='Optional independent CSV with id,text,intent columns')
    parser.add_argument('--test-size', type=float, default=0.2)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--hidden-units', type=int, default=32)
    parser.add_argument('--max-iter', type=int, default=600)
    parser.add_argument('--threshold', type=float, default=DEFAULT_THRESHOLD)
    return parser, parser.parse_args()


if __name__ == '__main__':
    parser, options = parse_args()
    try:
        train(options)
    except (ValueError, OSError) as error:
        parser.error(str(error))
