import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import joblib

from intent_model import ANNIntentModel, ROOT, classify
from train_model import build_pipeline, fit, load_dataset, train


class ANNTrainingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.options = SimpleNamespace(test_size=.2, threshold=.35, hidden_units=32,
                                       max_iter=600, seed=42, test_dataset=None,
                                       model_out=self.directory/'model.joblib',
                                       report_dir=self.directory/'reports')

    def tearDown(self):
        self.temp.cleanup()

    def test_training_saved_model_and_reports(self):
        with contextlib.redirect_stdout(io.StringIO()):
            report = train(self.options)
        self.assertEqual(report['train_count'],216)
        self.assertEqual(report['test_count'],54)
        self.assertFalse(set(report['train_ids']) & set(report['test_ids']))
        self.assertEqual(report['deployment_train_count'],270)
        saved = ANNIntentModel(self.options.model_out)
        self.assertEqual(saved.match('When can I collect my school ID?'), 'id_release_schedule')
        self.assertEqual(saved.match('purple asteroid quantum banana'), 'out_of_scope')
        self.assertEqual(saved.match(''), 'out_of_scope')
        metrics=json.loads((self.options.report_dir/'metrics.json').read_text())
        self.assertEqual(sum(map(sum,metrics['confusion_matrix'])),54)
        self.assertEqual(len((self.options.report_dir/'predictions.csv').read_text().splitlines()),55)
        self.assertEqual(len((self.options.report_dir/'confusion_matrix.csv').read_text().splitlines()),19)
        bundle=joblib.load(self.options.model_out)
        bundle['input_hashes']['property_management_dataset.csv']='changed'
        joblib.dump(bundle,self.options.model_out)
        with self.assertRaisesRegex(RuntimeError, 'changed since training'):
            ANNIntentModel(self.options.model_out)

    def test_training_vocabulary_and_low_confidence_fallback(self):
        pipeline=build_pipeline()
        fit(pipeline,[{'text':'release pickup schedule','intent':'release'},
                      {'text':'missing lost card','intent':'lost'}])
        self.assertNotIn('heldoutword',pipeline.named_steps['tfidf'].vocabulary_)
        labels,_=classify(pipeline,['heldoutword'],.35)
        self.assertEqual(labels[0],'out_of_scope')
        labels,_=classify(pipeline,['release pickup'],1.0)
        self.assertEqual(labels[0],'out_of_scope')

    def test_invalid_split_and_independent_overlap(self):
        self.options.test_size=.01
        with self.assertRaisesRegex(ValueError,'Stratified split'):
            train(self.options)
        self.options.test_size=.2
        self.options.test_dataset=ROOT/'property_management_dataset.csv'
        with self.assertRaisesRegex(ValueError,'overlap'):
            train(self.options)
        self.assertFalse(self.options.model_out.exists())
        with self.assertRaisesRegex(RuntimeError,'missing'):
            ANNIntentModel(self.directory/'missing.joblib')

    def test_independent_test_dataset(self):
        independent=self.directory/'independent.csv'
        independent.write_text('id,text,intent\nE1,When is the pickup appointment for my card?,id_release_schedule\nE2,My student card went missing yesterday,lost_id_report\n')
        self.options.test_dataset=independent
        with contextlib.redirect_stdout(io.StringIO()):
            report=train(self.options)
        self.assertEqual(report['train_count'],270)
        self.assertEqual(report['test_count'],2)
        self.assertEqual(report['deployment_train_count'],270)
        self.assertEqual(report['test_ids'],['E1','E2'])
        self.assertIsNotNone(report['test_dataset_hash'])
        self.assertEqual(sum(map(sum,report['confusion_matrix'])),2)

    def test_dataset_rejects_duplicates_and_unknown_intents(self):
        catalog=ROOT/'property_management_intent.csv'
        csv_path=self.directory/'test.csv'
        csv_path.write_text('id,text,intent\nA,Hello!,greeting\nB,hello,greeting\n')
        with self.assertRaisesRegex(ValueError,'duplicate'):
            load_dataset(csv_path,catalog)
        csv_path.write_text('id,text,intent\nA,hello,unknown\n')
        with self.assertRaisesRegex(ValueError,'unknown intent'):
            load_dataset(csv_path,catalog)


if __name__=='__main__':
    unittest.main()
