"""The rules for student records that need no database: formats, release hours, CSV import, name matching."""
import unittest
from datetime import datetime

from student_data import (
    IMPORT_COLUMNS, MAX_IMPORT_ROWS, MAX_YEAR, PENALTY_AMOUNT, PENALTY_TEXT, STATUSES, YEAR_SECTION_HINT,
    ImportFileError, claim_check,
    clean_contact, clean_status, clean_student_id, find_by_name, format_year_section, gate_note, incident_steps,
    looks_like_a_name, name_query, office_hours_error, parse_year_section, penalty_for, read_student_csv)

MONDAY = datetime(2030, 1, 7)          # a Monday, so the day of the week is not a surprise
assert MONDAY.weekday() == 0


class FormatTests(unittest.TestCase):
    def test_a_student_id_is_the_letter_p_and_nine_digits(self):
        for good, stored in (('P202400230', 'P202400230'), (' p202400230 ', 'P202400230'), ('p202400230\n', 'P202400230')):
            self.assertEqual(clean_student_id(good), stored, repr(good))
        for bad in ('P20240023', 'P2024002300', 'X202400230', 'P20240023O', 'P 202400230', 'PP202400230', '202400230',
                    'DEMO-001', '', '   ', None, 202400230, ['P202400230'],
                    'P２０２４００２３０', 'P٢٠٢٤٠٠٢٣٠', 'P2024002³0'):             # full-width, Arabic-Indic and superscript digits
            self.assertIsNone(clean_student_id(bad), repr(bad))

    def test_year_and_section_are_one_value_that_can_be_written_several_ways(self):
        for written, expected in (('3E', (3, 'E')), ('3-e', (3, 'E')), (' 3 E ', (3, 'E')), ('3/E', (3, 'E')),
                                  ('1A', (1, 'A')), ('4C', (4, 'C')), ('2-1', (2, '1')), ('21', (2, '1')), ('4B12', (4, 'B12'))):
            self.assertEqual(parse_year_section(written), expected, written)
        for bad in ('5A', '6C', '5-1', '7A', '0A', 'E', '3', '', '   ', None, 3, ['3E'], '3E3E3E3E3E3E', '3 E E', '③E', '१A', '3\u00a0'):
            self.assertIsNone(parse_year_section(bad), repr(bad))

    def test_the_school_has_four_year_levels(self):
        self.assertEqual(MAX_YEAR, 4)
        self.assertIn('from 1 to 4', YEAR_SECTION_HINT)
        for year in range(1, 10):
            self.assertEqual(parse_year_section(f'{year}A'), (year, 'A') if year <= MAX_YEAR else None, year)

    def test_year_and_section_are_shown_together_and_a_missing_section_says_tbd(self):
        self.assertEqual(format_year_section(3, 'E'), '3E')
        self.assertEqual(format_year_section(2, '1'), '2-1')          # a number would run into the year
        for section in ('', 'TBD', 'tbd', None, '  '):
            self.assertEqual(format_year_section(1, section), 'TBD')

    def test_contact_numbers_are_cleaned_and_a_mobile_number_has_to_be_whole(self):
        for written, stored in (('', ''), ('   ', ''), ('09171234567', '09171234567'), ('0917 123 4567', '09171234567'),
                                ('+63 917 123 4567', '09171234567'), ('9171234567', '09171234567'), ('(0917) 123-4567', '09171234567'),
                                ('639171234567', '09171234567'), ('0431234567', '0431234567'), ('+0431234567', '+0431234567')):
            self.assertEqual(clean_contact(written), stored, written)
        for bad in ('63917123456', '0917123456', '091712345678', '091712345', 'call me', '12345', '1' * 14, '+63', None, 9171234567,
                    '09١٧١٢٣٤٥٦٧'):
            self.assertIsNone(clean_contact(bad), repr(bad))

    def test_the_four_statuses_and_the_other_ways_they_have_been_written(self):
        self.assertEqual(STATUSES, ('Pending', 'Processing', 'For release', 'Released'))
        for written, stored in (('Pending', 'Pending'), ('processing', 'Processing'), ('For release', 'For release'),
                                ('For released', 'For release'), ('for-released', 'For release'), ('Ready for release', 'For release'),
                                ('  RELEASED ', 'Released'), ('claimed', 'Released'), ('In progress', 'Processing')):
            self.assertEqual(clean_status(written), stored, written)
        self.assertEqual((clean_status(None), clean_status(''), clean_status('  ', default='Processing')), ('Pending', 'Pending', 'Processing'))
        for bad in ('done', 'Lost', 'released!!!x', 7, ['Pending']):
            self.assertIsNone(clean_status(bad), repr(bad))
        self.assertIsNone(clean_status('', default=None))


class ReleaseHoursTests(unittest.TestCase):
    def at(self, day, hour, minute=0):
        return datetime(2030, 1, 7 + day, hour, minute)         # day 0 is the Monday, 4 the Friday, 5 and 6 the weekend

    def test_the_office_is_open_8_to_12_and_1_to_5_on_weekdays(self):
        for day in range(5):
            for hour, minute in ((8, 0), (9, 30), (11, 59), (13, 0), (14, 45), (16, 59)):
                self.assertIsNone(office_hours_error(self.at(day, hour, minute)), (day, hour, minute))

    def test_nothing_can_start_at_lunch_before_opening_or_from_closing_time(self):
        for hour, minute in ((12, 0), (12, 1), (12, 30), (12, 59)):
            self.assertIn('lunch', office_hours_error(self.at(1, hour, minute)))
        for hour, minute in ((0, 0), (6, 0), (7, 59), (17, 0), (17, 30), (23, 59)):
            self.assertIn('8:00 AM to 5:00 PM', office_hours_error(self.at(1, hour, minute)))

    def test_saturday_and_sunday_are_closed_all_day(self):
        for day in (5, 6):
            for hour in (8, 10, 13, 16):
                self.assertIn('Saturdays and Sundays', office_hours_error(self.at(day, hour)))


class ImportTests(unittest.TestCase):
    HEADER = 'student_id,full_name,year_section,status,address,contact_number\n'

    def read(self, text, encoding='utf-8'):
        return read_student_csv(text.encode(encoding) if isinstance(text, str) else text)

    def test_a_good_file_gives_students_in_the_form_they_are_stored(self):
        students, problems, total = self.read(
            self.HEADER + 'p202400001,  Maria  L. Santos ,3-e,for released,"Purok 2, Brgy. Poblacion",9171234567\n'
                          'P202400002,Juan D. Dela Cruz,1A,,,\n')
        self.assertEqual((problems, total), ([], 2))
        self.assertEqual(students[0], {
            'student_id': 'P202400001', 'name': 'Maria L. Santos', 'year_level': 3, 'section': 'E', 'status': 'For release',
            'address': 'Purok 2, Brgy. Poblacion', 'contact_number': '09171234567', 'line': 2})
        self.assertEqual((students[1]['status'], students[1]['address'], students[1]['contact_number'], students[1]['line']),
                         ('Pending', '', '', 3))

    def test_excel_files_work_with_a_marker_crlf_semicolons_tabs_and_windows_encoding(self):
        row = 'P202400003,Ana Peña,2B,Pending,Calle Niño,09171234567'
        for label, data in (('utf-8 with marker', ('\ufeff' + self.HEADER + row + '\n').encode('utf-8')),
                            ('crlf', (self.HEADER + row + '\n').replace('\n', '\r\n').encode('utf-8')),
                            ('old Mac line ends', (self.HEADER + row + '\n').replace('\n', '\r').encode('utf-8')),
                            ('semicolons', (self.HEADER + row + '\n').replace(',', ';').encode('utf-8')),
                            ('tabs', (self.HEADER + row + '\n').replace(',', '\t').encode('utf-8')),
                            ('windows-1252', (self.HEADER + row + '\n').encode('cp1252')),
                            ('no final newline', (self.HEADER + row).encode('utf-8'))):
            students, problems, _ = self.read(data)
            self.assertEqual((problems, [s['name'] for s in students], [s['address'] for s in students]),
                             ([], ['Ana Peña'], ['Calle Niño']), label)

    def test_other_names_for_the_columns_are_understood_and_extra_columns_are_ignored(self):
        text = 'ID Number,Name,Year & Section,Status,Home Address,Mobile,Course,Remarks\nP202400004,Lea Cruz,4C,Processing,Here,0917 000 0000,BSIT,x\n'
        students, problems, _ = self.read(text)
        self.assertEqual(problems, [])
        self.assertEqual((students[0]['student_id'], students[0]['name'], students[0]['year_level'], students[0]['section'],
                          students[0]['status'], students[0]['contact_number']),
                         ('P202400004', 'Lea Cruz', 4, 'C', 'Processing', '09170000000'))

    def test_a_file_without_the_required_columns_is_refused_and_says_which(self):
        with self.assertRaises(ImportFileError) as caught:
            self.read('student_id,name\nP202400005,Pat\n')
        self.assertIn('year_section', str(caught.exception))
        self.assertNotIn('full_name,', str(caught.exception).split('Expected')[0])
        for data, words in ((b'', 'empty'), (b'\n\n  \n', 'empty'), (b'abc\x00def,x\n1,2\n', 'CSV text file')):
            with self.assertRaises(ImportFileError) as caught:
                self.read(data)
            self.assertIn(words, str(caught.exception))

    def test_each_bad_row_is_reported_with_its_line_and_the_good_rows_still_count(self):
        students, problems, total = self.read(self.HEADER + '\n'.join([
            'P202400010,Good One,1A,,,',                      # line 2
            'P20240001,Short Id,1A,,,',                       # line 3
            'P202400012,,1A,,,',                              # line 4: no name
            'P202400013,Bad Year,9A,,,',                      # line 5
            'P202400014,Bad Status,1A,done,,',                # line 6
            'P202400015,Bad Phone,1A,,,call me',              # line 7
            'P202400010,Same Again,2B,,,',                    # line 8: repeated
            '',                                               # a blank line is not a row
            'P202400016,Short Row',                           # line 10: missing cells are blank
            'P202400017,' + 'x' * 101 + ',1A,,,',             # line 11: name too long
            'P202400018,Long Address,1A,,' + 'y' * 201 + ',',  # line 12
        ]) + '\n')
        self.assertEqual([s['student_id'] for s in students], ['P202400010'])
        self.assertEqual(total, 10)
        self.assertEqual([p['line'] for p in problems], [3, 4, 5, 6, 7, 8, 10, 11, 12])
        by_line = {p['line']: p for p in problems}
        self.assertEqual(by_line[3]['student_id'], 'P20240001')
        self.assertIn('letter P and 9 digits', by_line[3]['error'])
        self.assertIn('Full name', by_line[4]['error'])
        self.assertIn('Year & Section', by_line[5]['error'])
        self.assertIn('Status', by_line[6]['error'])
        self.assertIn('phone number', by_line[7]['error'])
        self.assertIn('line 2', by_line[8]['error'])
        self.assertIn('Year & Section', by_line[10]['error'])        # "P202400016,Short Row" has no year and section
        self.assertIn('Full name', by_line[11]['error'])
        self.assertIn('Address is too long', by_line[12]['error'])

    def test_a_student_in_year_five_or_six_is_not_imported(self):
        students, problems, total = self.read(
            self.HEADER + 'P202400031,Fifth Year,5A,,,\nP202400032,Sixth Year,6B,,,\nP202400033,Fourth Year,4B,,,\n')
        self.assertEqual(([s['student_id'] for s in students], [p['line'] for p in problems], total), (['P202400033'], [2, 3], 3))
        self.assertIn('from 1 to 4', problems[0]['error'])

    def test_a_file_with_too_many_rows_or_a_giant_cell_is_refused_whole(self):
        rows = ''.join(f'P{202400000 + n},Student {n},1A,,,\n' for n in range(MAX_IMPORT_ROWS + 1))
        with self.assertRaises(ImportFileError) as caught:
            self.read(self.HEADER + rows)
        self.assertIn(str(MAX_IMPORT_ROWS), str(caught.exception))
        students, problems, total = self.read(self.HEADER + ''.join(rows.splitlines(True)[:MAX_IMPORT_ROWS]))
        self.assertEqual((len(students), problems, total), (MAX_IMPORT_ROWS, [], MAX_IMPORT_ROWS))      # exactly the limit is fine
        with self.assertRaises(ImportFileError) as caught:
            self.read(self.HEADER + 'P202400020,"' + 'x' * 140000 + '",1A,,,\n')
        self.assertIn('could not be read', str(caught.exception))

    def test_the_columns_of_the_template_are_the_columns_of_the_dashboard(self):
        self.assertEqual(IMPORT_COLUMNS, ('student_id', 'full_name', 'year_section', 'status', 'address', 'contact_number'))


class NameTests(unittest.TestCase):
    PEOPLE = [{'name': name} for name in (
        'Reynald Jake L. Malabanan', 'Ann Cruz', 'Ann Cruzado', 'Zoë Muñoz', 'May Santos', 'Maria Santos', 'Maria Clara Reyes',
        'Joy Lim', 'Jamie L. Santos')]

    def names(self, text):
        return [person['name'] for person in find_by_name(text, self.PEOPLE)]

    def test_filler_words_are_dropped_and_what_is_left_is_the_name(self):
        self.assertEqual(name_query('Reynald Jake L. Malabanan'), ['reynald', 'jake', 'malabanan'])
        self.assertEqual(name_query('what is the status of reynald?'), ['reynald'])
        self.assertEqual(name_query('pakicheck si Jamie Santos'), ['jamie', 'santos'])
        self.assertEqual(name_query("Where's the ID of O'Brien"), ['brien'])
        for text in ('', 'ana', 'ID', 'the status of my ID', 'hi', 'a b'):
            self.assertEqual(name_query(text), [], text)
        self.assertEqual(name_query('one two three four five six seven'), [])      # too many words to be a name

    def test_a_name_search_finds_students_whose_name_has_every_word_in_any_order(self):
        self.assertEqual(self.names('Reynald Malabanan'), ['Reynald Jake L. Malabanan'])
        self.assertEqual(self.names('malabanan reynald'), ['Reynald Jake L. Malabanan'])
        self.assertEqual(self.names('Reyn'), ['Reynald Jake L. Malabanan'])          # a start of a word is enough from 3 letters
        self.assertEqual(self.names('Jake Malabanan Reynald'), ['Reynald Jake L. Malabanan'])
        self.assertEqual(self.names('Jamie Reyes'), [])                              # every word has to belong to one name
        self.assertEqual(self.names('Santos'), ['Jamie L. Santos', 'Maria Santos', 'May Santos'])

    def test_accents_and_letter_case_do_not_matter(self):
        self.assertEqual(self.names('zoe munoz'), ['Zoë Muñoz'])
        self.assertEqual(self.names('ZOË MUÑOZ'), ['Zoë Muñoz'])

    def test_a_full_name_beats_longer_names_that_only_contain_it(self):
        self.assertEqual(self.names('Ann Cruz'), ['Ann Cruz'])
        self.assertEqual(self.names('Ann Cru'), ['Ann Cruz', 'Ann Cruzado'])
        self.assertEqual(self.names('Maria Santos'), ['Maria Santos'])

    def test_may_is_a_name_first_and_a_filler_word_only_when_nobody_has_that_name(self):
        self.assertEqual(self.names('May Santos'), ['May Santos'])
        self.assertEqual(self.names('may Jamie'), ['Jamie L. Santos'])               # "may Jamie": there is a Jamie
        self.assertEqual(self.names('may Reynald'), ['Reynald Jake L. Malabanan'])

    def test_one_short_word_is_not_enough_to_go_on(self):
        self.assertEqual(self.names('Joy'), [])
        self.assertEqual(self.names('Joy Lim'), ['Joy Lim'])

    def test_only_two_to_five_plain_words_look_like_a_name(self):
        for text in ('Juan Dela Cruz', 'purple asteroid quantum banana', 'Maria Cristina de la Cruz'):
            self.assertTrue(looks_like_a_name(text), text)
        for text in ('Juan', 'what is my status', 'Juan Dela Cruz?', 'P202400230 Juan', 'one two three four five six', 'tell me a joke',
                     'hello there', 'check ID'):
            self.assertFalse(looks_like_a_name(text), text)


class RecordsAtTheGateTests(unittest.TestCase):
    def test_the_penalty_is_charged_once_at_the_second_false_statement(self):
        self.assertEqual(PENALTY_AMOUNT, 1500)
        self.assertEqual(PENALTY_TEXT, '₱1,500')
        self.assertEqual([penalty_for(number) for number in (0, 1, 2, 3, 4)], [0, 0, 1500, 0, 0])

    def test_what_a_student_says_is_checked_against_the_records(self):
        self.assertEqual(claim_check('Lost ID', 'Released')[0], True)
        for status in ('Pending', 'Processing', 'For release'):
            agrees, message = claim_check('Lost ID', status)
            self.assertIs(agrees, False)
            self.assertIn('never released', message)
        for status in ('Pending', 'Processing', 'For release'):
            self.assertIs(claim_check('No ID', status)[0], True)
        agrees, message = claim_check('No ID', 'Released')
        self.assertIsNone(agrees)                                   # the card may simply be at home
        self.assertIn('Lost ID incident', message)
        self.assertEqual(claim_check('Visitor', 'Pending'), (None, ''))

    def test_the_gate_note_says_whether_the_card_can_exist(self):
        self.assertIn('should have the card', gate_note('Released'))
        self.assertIn('not claimed yet', gate_note('For release'))
        for status in ('Pending', 'Processing'):
            self.assertIn(f'records show {status}', gate_note(status))
            self.assertIn('not yet made', gate_note(status))

    def test_a_lost_id_goes_to_the_cashier_first_and_a_penalty_goes_to_the_cashier_too(self):
        steps = incident_steps('Lost ID')
        self.assertIn('Cashier', steps[0])
        self.assertIn('Property Management Office', steps[1])
        self.assertEqual(incident_steps('No ID'), [])
        self.assertIn(PENALTY_TEXT, incident_steps('Lost ID', 1500)[-1])
        self.assertIn('Cashier', incident_steps('No ID', 1500)[-1])


if __name__ == '__main__':
    unittest.main()
