import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import task_runner
from password import (
    Password,
    prioritize_latest_hits,
    read_password,
    sort_passwords,
    write_password,
)
from zip import Zip


class PasswordSortTests(unittest.TestCase):
    def test_password_file_round_trip_preserves_metadata(self):
        items = [
            Password('密碼', '2026-08-01', 3, '2026-08-20'),
            Password('plain', '2026-08-02', 0, ''),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'password.txt')

            write_password(items, path)
            loaded = read_password(path)

            self.assertEqual(
                [
                    (item.password, item.add_date, item.hit_count, item.last_hit_date)
                    for item in loaded
                ],
                [
                    ('密碼', '2026-08-01', 3, '2026-08-20'),
                    ('plain', '2026-08-02', 0, ''),
                ],
            )
            self.assertFalse(os.path.exists(path + '.tmp'))

    def test_read_password_tolerates_missing_and_dirty_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'password.txt')
            self.assertEqual(read_password(path), [])

            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('\n')
                fh.write('\t2026-08-01\t3\t2026-08-20\n')
                fh.write('valid\t2026-08-01\tnot-a-number\t2026-08-20\n')
                fh.write('minimal\n')

            loaded = read_password(path)

            self.assertEqual([item.password for item in loaded], ['valid', 'minimal'])
            self.assertEqual(loaded[0].hit_count, 0)
            self.assertEqual(loaded[0].last_hit_date, '2026-08-20')
            self.assertEqual(loaded[1].add_date, str(datetime.now().date()))
            self.assertEqual(loaded[1].last_hit_date, '')

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

    def test_latest_hits_use_fast_lane_without_dropping_candidates(self):
        newer = Password('newer', '2026-08-20', 0, '')
        latest_low = Password('latest-low', '2026-08-01', 2, '2026-08-22')
        latest_high = Password('latest-high', '2026-07-01', 5, '2026-08-22')
        previous = Password('previous', '2026-08-21', 100, '2026-08-21')

        result = prioritize_latest_hits([
            newer, latest_low, latest_high, previous,
        ])

        self.assertEqual(
            [item.password for item in result],
            ['latest-high', 'latest-low', 'previous', 'newer'],
        )
        self.assertCountEqual(
            [item.password for item in result],
            ['newer', 'latest-low', 'latest-high', 'previous'],
        )

    def test_prepare_zip_reorders_existing_library_candidates(self):
        older = Password('older', '2026-08-03', 100, '2026-08-04')
        newer = Password('newer', '2026-08-04', 0, '')
        archive = Zip('archive.zip', ['older', 'newer'])

        with mock.patch.object(task_runner, 'passwords', [older, newer]):
            task_runner._prepare_zip_for_unzip(archive)

        self.assertEqual(archive.pw_list[:3], ['archive', 'older', 'newer'])

    def test_prepare_zip_keeps_task_note_ahead_of_library(self):
        older = Password('older', '2026-08-03', 100, '2026-08-04')
        newer = Password('newer', '2026-08-04', 0, '')
        archive = Zip('archive.zip', ['older', 'newer'])
        archive.set_note('one-off')

        with mock.patch.object(task_runner, 'passwords', [older, newer]):
            task_runner._prepare_zip_for_unzip(archive)

        self.assertEqual(
            archive.pw_list[:4], ['one-off', 'archive', 'older', 'newer'],
        )

    def test_prepare_zip_reuses_current_scan_after_library_reorder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'archive.7z')
            with open(path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 16)
            archive = Zip(path, ['secret'])
            archive.file_list = ['inner.zip']
            archive.compression_ratio_info = {'encrypted': True}
            archive.mark_namelist_scanned('secret')
            latest = Password('latest', '2026-08-01', 2, '2026-08-22')

            with mock.patch.object(task_runner, 'passwords', [latest]), mock.patch.object(
                task_runner, 'logger', mock.MagicMock(),
            ):
                task_runner._prepare_zip_for_unzip(archive)

            self.assertTrue(archive.is_namelist_current())
            self.assertEqual(archive.namelist_password(), 'secret')
            self.assertFalse(archive.is_extract_password_verified())

    def test_restore_without_volume_rename_keeps_current_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'archive.7z')
            with open(path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 16)
            archive = Zip(path, ['secret'])
            archive.file_list = ['inner.zip']
            archive.compression_ratio_info = {'encrypted': True}
            archive.mark_namelist_scanned('secret')

            task_runner._restore_volume_original_names(archive)

            self.assertTrue(archive.is_namelist_current())


if __name__ == '__main__':
    unittest.main()
