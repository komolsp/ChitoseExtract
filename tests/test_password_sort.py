import unittest
from datetime import datetime
from unittest import mock

import task_runner
from password import Password, sort_passwords
from zip import Zip


class PasswordSortTests(unittest.TestCase):
    def test_default_password_date_is_evaluated_for_each_instance(self):
        with mock.patch('password.datetime.datetime') as mocked_datetime:
            mocked_datetime.now.side_effect = [
                datetime(2026, 8, 15),
                datetime(2026, 8, 16),
            ]
            first = Password('first')
            second = Password('second')

        self.assertEqual(first.add_date, '2026-08-15')
        self.assertEqual(second.add_date, '2026-08-16')

    def test_newer_password_is_preferred_over_more_hits(self):
        older_popular = Password('older', '2026-08-03', 100, '2026-08-04')
        newer_unused = Password('newer', '2026-08-04', 0, '')

        result = sort_passwords([older_popular, newer_unused])

        self.assertEqual([item.password for item in result], ['newer', 'older'])

    def test_hit_count_breaks_tie_for_same_add_date(self):
        lower_hits = Password('lower', '2026-08-04', 2, '2026-08-04')
        higher_hits = Password('higher', '2026-08-04', 3, '2020-01-01')

        result = sort_passwords([lower_hits, higher_hits])

        self.assertEqual([item.password for item in result], ['higher', 'lower'])

    def test_equal_keys_keep_existing_order(self):
        first = Password('first', '2026-08-04', 2, '2020-01-01')
        second = Password('second', '2026-08-04', 2, '2026-08-04')

        result = sort_passwords([first, second])

        self.assertEqual([item.password for item in result], ['first', 'second'])

    def test_invalid_add_date_sorts_after_valid_dates(self):
        invalid = Password('invalid', 'not-a-date', 100, '2026-08-04')
        valid = Password('valid', '2020-01-01', 0, '')

        result = sort_passwords([invalid, valid])

        self.assertEqual([item.password for item in result], ['valid', 'invalid'])

    def test_prepare_zip_reorders_existing_library_candidates(self):
        older = Password('older', '2026-08-03', 100, '2026-08-04')
        newer = Password('newer', '2026-08-04', 0, '')
        archive = Zip('archive.zip', ['older', 'newer'])

        with mock.patch.object(task_runner, 'passwords', [older, newer]):
            task_runner._prepare_zip_for_unzip(archive)

        self.assertEqual(archive.pw_list[:2], ['newer', 'older'])

    def test_prepare_zip_keeps_task_note_ahead_of_library(self):
        older = Password('older', '2026-08-03', 100, '2026-08-04')
        newer = Password('newer', '2026-08-04', 0, '')
        archive = Zip('archive.zip', ['older', 'newer'])
        archive.set_note('one-off')

        with mock.patch.object(task_runner, 'passwords', [older, newer]):
            task_runner._prepare_zip_for_unzip(archive)

        self.assertEqual(archive.pw_list[:3], ['one-off', 'newer', 'older'])


if __name__ == '__main__':
    unittest.main()
