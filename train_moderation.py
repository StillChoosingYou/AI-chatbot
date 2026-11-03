"""Trains the chat-moderation model. Run: python train_moderation.py   (after build_moderation_dataset.py changes the data)

1. fit on the `train` rows, measure on the `test` rows (held-out rude words and held-out sentence frames),
2. refit on every row and save data/moderation_model.joblib together with the dataset's hash,
3. write evaluation/moderation_metrics.json and evaluation/moderation.md.
The app refuses a model whose hash does not match moderation_dataset.csv and falls back to the word list alone.
"""
import json
from datetime import datetime
from pathlib import Path

import joblib
from sklearn.metrics import precision_recall_fscore_support

import moderation as m

ROOT = Path(__file__).resolve().parent


def rate(truth, guess):
    tp = sum(1 for t, g in zip(truth, guess) if t and g)
    fp = sum(1 for t, g in zip(truth, guess) if not t and g)
    fn = sum(1 for t, g in zip(truth, guess) if t and not g)
    tn = sum(1 for t, g in zip(truth, guess) if not t and not g)
    return {'precision': round(tp / (tp + fp), 4) if tp + fp else None, 'recall': round(tp / (tp + fn), 4) if tp + fn else None,
            'false_positive_rate': round(fp / (fp + tn), 4) if fp + tn else None, 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn}


def main():
    rows = m.read_dataset()
    train = [r for r in rows if r['split'] == 'train']
    test = [r for r in rows if r['split'] == 'test']
    pipeline = m.build_pipeline().fit([m.prepared(r['text']) for r in train], [r['label'] for r in train])
    texts, labels = [r['text'] for r in test], [r['label'] for r in test]
    guess = list(pipeline.predict([m.prepared(t) for t in texts]))
    classes = list(pipeline.classes_)
    p, r, f, support = precision_recall_fscore_support(labels, guess, labels=classes, zero_division=0)
    per_class = {c: {'precision': round(float(p[i]), 3), 'recall': round(float(r[i]), 3), 'f1': round(float(f[i]), 3), 'n': int(support[i])}
                 for i, c in enumerate(classes)}
    truth = [label != 'ok' for label in labels]
    model_only = rate(truth, [g != 'ok' for g in guess])
    # the rules alone, and rules + model together, as the app uses them
    m._MODEL.update(loaded=True, pipeline=None, reason='')
    rules_only = rate(truth, [m.check(t)['flagged'] for t in texts])
    m._MODEL.update(loaded=True, pipeline=pipeline, reason='')
    together = rate(truth, [m.check(t)['flagged'] for t in texts])
    severe = [r for r in test if r['label'] in ('threat', 'harassment')]
    severe_caught = sum(1 for r in severe if m.check(r['text'])['severity'] >= 3)
    questions = [r['text'] for r in m.read_dataset(ROOT / 'property_management_dataset.csv')]
    school_flagged = [q for q in questions if m.check(q)['flagged']]
    probes = list(__import__('csv').DictReader((ROOT / 'evaluation' / 'moderation_probes.csv').open(encoding='utf-8')))
    probe_truth = [r['expect'] == 'flag' for r in probes]
    probe_flag = rate(probe_truth, [m.check(r['text'])['flagged'] for r in probes])
    clean_texts = [r['text'] for r in test if r['label'] == 'ok'] + [r['text'] for r in probes if r['expect'] == 'clean'] + questions
    strike_false = [t for t in clean_texts if m.check(t)['severity'] >= 2]
    # the model that ships is fitted on everything
    final = m.build_pipeline().fit([m.prepared(r['text']) for r in rows], [r['label'] for r in rows])
    (ROOT / 'data').mkdir(exist_ok=True)
    joblib.dump({'pipeline': final, 'dataset_hash': m.dataset_hash(), 'labels': classes}, m.MODEL_PATH, compress=3)
    metrics = {'trained': datetime.now().strftime('%Y-%m-%d'), 'rows': len(rows), 'train': len(train), 'test': len(test),
               'per_class_model': per_class, 'flagged_model_only': model_only, 'flagged_rules_only': rules_only,
               'flagged_rules_and_model': together, 'severe_test_rows': len(severe), 'severe_caught_as_severe': severe_caught,
               'school_questions_checked': len(questions), 'school_questions_flagged': len(school_flagged),
               'hand_written_probes': probe_flag, 'clean_messages_given_a_strike': len(strike_false), 'clean_messages_checked': len(clean_texts)}
    (ROOT / 'evaluation').mkdir(exist_ok=True)
    (ROOT / 'evaluation' / 'moderation_metrics.json').write_text(json.dumps(metrics, indent=2), encoding='utf-8')
    lines = ['# Chat moderation evaluation', '',
             f'Rows: {len(rows)} (train {len(train)}, test {len(test)}). The test rows hold out 30% of the rude words and 20% of the sentence frames.',
             '', '| What is measured | Precision | Recall | False alarms on clean test rows |', '| --- | --- | --- | --- |']
    for name, result in (('Rules only', rules_only), ('Model only', model_only), ('Rules and model (what the app uses)', together)):
        lines.append(f'| {name} | {result["precision"]} | {result["recall"]} | {result["false_positive_rate"]} |')
    lines += ['', f'Threats and sexual harassment in the test rows judged severe: {severe_caught} of {len(severe)}.',
              f'The {len(questions)} real school ID questions flagged as offensive: {len(school_flagged)}.',
              f'Clean messages that would earn a strike (severity 2 or more), of {len(clean_texts)} checked: {len(strike_false)}.', '',
              f'## Hand-written probes ({len(probes)} messages, evaluation/moderation_probes.csv)', '',
              f'Recall {probe_flag["recall"]}, precision {probe_flag["precision"]}, false alarms {probe_flag["false_positive_rate"]}. '
              'The probes were used while the lists were tuned, so this number is optimistic.', '',
              '## Limits', '- The data are written by the team from templates: a synthetic test. Real students write in ways it does not cover.',
              '- Scores are for held-out words and frames of the same templates, so they are an upper bound on real-world accuracy.',
              '- The model alone never decides a severe case; the rules and the administrator do.',
              '- Sarcasm, quoting someone, and words with a harmless meaning in context can still be flagged. That is why every flag is reviewable.']
    (ROOT / 'evaluation' / 'moderation.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({k: metrics[k] for k in ('rows', 'flagged_rules_only', 'flagged_model_only', 'flagged_rules_and_model',
                                              'severe_test_rows', 'severe_caught_as_severe', 'school_questions_flagged', 'hand_written_probes', 'clean_messages_given_a_strike')}, indent=1))
    print(json.dumps(per_class))
    for q in school_flagged[:5]:
        print('FLAGGED SCHOOL QUESTION:', q)


if __name__ == '__main__':
    main()
