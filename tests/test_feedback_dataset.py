"""Tests for feedback_to_dataset.py: reviewed chat feedback becomes an evaluation CSV."""
import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from feedback_to_dataset import CATALOG, convert, main, read_labels
from pg_support import DatabaseTestCase
from train_model import load_dataset

LABELS = read_labels(CATALOG)
COLUMNS = ['id', 'timestamp', 'rating', 'reason', 'comment', 'predicted_intent', 'match_score',
           'role', 'shared_question', 'correct_intent', 'status']


def export_row(identifier, question='', topic='', **extra):
    row = dict.fromkeys(COLUMNS, '')
    row.update(id=str(identifier), rating='down', shared_question=question, correct_intent=topic, **extra)
    return row


class ConvertTests(unittest.TestCase):
    def test_keeps_only_rows_with_a_shared_question_and_a_known_topic(self):
        rows = [
            export_row(1, 'When can I get my ID?', 'id_release_schedule'),
            export_row(2, '', 'id_release_schedule'),                       # question not shared
            export_row(3, 'Why is my ID pending?', ''),                     # no topic chosen yet
            export_row(4, 'Where do I park?', 'parking_help'),              # topic not in the catalog
            export_row(5, '   ', 'pending_id'),                             # blank question
            export_row(6, '???', 'pending_id'),                             # no words
            export_row('', 'Lost my ID', 'lost_id_report'),                 # no id
            export_row(8, 'Lost my ID', 'lost_id_report'),
        ]
        kept, skipped = convert(rows, LABELS)
        self.assertEqual(kept, [
            {'id': 'feedback-1', 'text': 'When can I get my ID?', 'intent': 'id_release_schedule'},
            {'id': 'feedback-8', 'text': 'Lost my ID', 'intent': 'lost_id_report'}])
        self.assertEqual(sum(skipped.values()), 6)
        self.assertEqual(skipped['topic is not in the intent catalog'], 1)

    def test_out_of_scope_is_a_valid_correct_topic(self):
        kept, _ = convert([export_row(1, 'What is the weather?', 'out_of_scope')], LABELS)
        self.assertEqual([row['intent'] for row in kept], ['out_of_scope'])

    def test_a_repeated_question_is_kept_once_and_a_contradiction_is_dropped(self):
        rows = [
            export_row(1, 'When is my ID ready?', 'id_release_schedule'),
            export_row(2, 'when is my id ready', 'id_release_schedule'),    # same words, same topic
            export_row(3, 'Is my ID pending?', 'pending_id'),
            export_row(4, 'is my ID pending!!', 'id_processing_status'),    # same words, other topic
        ]
        kept, skipped = convert(rows, LABELS)
        self.assertEqual([row['id'] for row in kept], ['feedback-1'])
        self.assertEqual(skipped['repeated question'], 1)
        self.assertEqual(skipped['same question given different topics'], 2)

    def test_the_spreadsheet_guard_from_the_export_is_left_in_place(self):
        kept, _ = convert([export_row(1, "'=cmd please", 'out_of_scope')], LABELS)
        self.assertEqual(kept[0]['text'], "'=cmd please")


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_export(self, rows, name='feedback.csv'):
        path = self.dir / name
        with path.open('w', encoding='utf-8-sig', newline='') as file:      # utf-8-sig: as Excel saves it
            writer = csv.DictWriter(file, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def run_main(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([str(arg) for arg in args])
        return code, out.getvalue(), err.getvalue()

    def test_the_file_it_writes_passes_the_trainers_own_checks(self):
        source = self.write_export([
            export_row(1, 'When can I get my ID?', 'id_release_schedule'),
            export_row(2, 'Why is mine pending, ate?', 'pending_id'),
            export_row(3, 'Nothing shared here', ''),
        ])
        target = self.dir / 'questions.csv'
        code, out, _ = self.run_main(source, target)
        self.assertEqual(code, 0)
        rows, _ = load_dataset(target, CATALOG)
        self.assertEqual([(row['id'], row['intent']) for row in rows],
                         [('feedback-1', 'id_release_schedule'), ('feedback-2', 'pending_id')])
        self.assertIn('Read 3 rows: kept 2, skipped 1.', out)
        self.assertIn('Only 2 questions', out)                              # too few to quote as accuracy
        self.assertIn(f'train_model.py --test-dataset {target}', out)

    def test_it_never_overwrites_a_file_unless_told_to(self):
        source = self.write_export([export_row(1, 'When can I get my ID?', 'id_release_schedule')])
        target = self.dir / 'questions.csv'
        target.write_text('keep me', encoding='utf-8')
        code, _, err = self.run_main(source, target)
        self.assertEqual(code, 2)
        self.assertIn('already exists', err)
        self.assertEqual(target.read_text(encoding='utf-8'), 'keep me')
        self.assertEqual(self.run_main(source, target, '--force')[0], 0)
        self.assertIn('feedback-1', target.read_text(encoding='utf-8'))

    def test_it_writes_nothing_when_no_row_is_usable(self):
        source = self.write_export([export_row(1, '', ''), export_row(2, 'Pending?', '')])
        target = self.dir / 'questions.csv'
        code, out, err = self.run_main(source, target)
        self.assertEqual(code, 1)
        self.assertFalse(target.exists())
        self.assertIn('skipped 1: the question was not shared', out)
        self.assertIn('Nothing to write', err)

    def test_a_file_that_is_not_the_export_is_refused_with_a_clear_message(self):
        other = self.dir / 'other.csv'
        other.write_text('id,text\n1,hello\n', encoding='utf-8')
        code, _, err = self.run_main(other, self.dir / 'questions.csv')
        self.assertEqual(code, 2)
        self.assertIn('shared_question', err)
        empty = self.dir / 'empty.csv'
        empty.write_text('', encoding='utf-8')
        self.assertEqual(self.run_main(empty, self.dir / 'questions.csv')[0], 2)


class WholeLoopTests(DatabaseTestCase):
    """Student rates 👎 and shares, coordinator corrects the topic, the export becomes a test set."""

    def test_from_a_thumbs_down_in_the_app_to_a_file_train_model_accepts(self):
        with tempfile.TemporaryDirectory() as folder:
            client = self.make_app().test_client()
            session = 'loop-test:student'
            reply = client.post('/api/chat', json={
                'message': 'ate pwede ba makuha ID ko ngayon', 'session_id': session, 'workspace_role': 'student'}).json
            self.assertEqual(client.post('/api/chat/feedback', json={
                'message_id': reply['message_id'], 'session_id': session, 'rating': 'down',
                'reason': 'Wrong answer', 'share_text': True}).status_code, 200)
            item = client.get('/api/feedback').json['items'][0]
            self.assertEqual(client.patch(f'/api/feedback/{item["id"]}', json={
                'correct_intent': 'id_release_schedule', 'status': 'Reviewed'}).status_code, 200)

            export = Path(folder) / 'feedback.csv'
            export.write_bytes(client.get('/api/feedback/export').data)
            target = Path(folder) / 'questions.csv'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(export), str(target)]), 0)
            rows, _ = load_dataset(target, CATALOG)
            self.assertEqual([(row['text'], row['intent']) for row in rows],
                             [('ate pwede ba makuha ID ko ngayon', 'id_release_schedule')])


if __name__ == '__main__':
    unittest.main()
