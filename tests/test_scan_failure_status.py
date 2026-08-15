"""扫描不到可处理压缩包时不得误报任务完成。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import task_runner
from gui import _format_run_status_summary
from timeline import Archive, Timeline
from zip import Zip


class ScanFailureStatusTests(unittest.TestCase):
    def setUp(self):
        task_runner.clear()
        self.previous = {
            'conf': task_runner.conf,
            'passwords': task_runner.passwords,
            'unzipper': task_runner.unzipper,
            'progress_ui': task_runner.progress_ui,
            'logger': task_runner.logger,
        }
        task_runner.conf = SimpleNamespace(del_after_unzip=False)
        task_runner.passwords = []
        task_runner.unzipper = mock.MagicMock()
        task_runner.progress_ui = mock.MagicMock()
        task_runner.logger = mock.MagicMock()

    def tearDown(self):
        task_runner.clear()
        for name, value in self.previous.items():
            setattr(task_runner, name, value)

    def test_empty_scan_stays_visible_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'RJ01593274')
            os.makedirs(source)
            archive = Archive(source)
            timeline = Timeline(archive, 'create_timeline', archive)
            task_runner.timelines.append(timeline)

            added = task_runner.scan_work_queue()

        self.assertEqual(added, 0)
        self.assertEqual(len(task_runner.timelines), 1)
        self.assertEqual(timeline.get_current_record().ops, 'scan_failed')
        self.assertEqual(
            _format_run_status_summary(task_runner.timelines),
            ('未发现可解压文件', '请检查分卷是否完整或文件名是否被改动', ''),
        )

    def test_scan_failure_can_be_retried_after_files_are_fixed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'RJ01593274')
            os.makedirs(source)
            archive = Archive(source)
            timeline = Timeline(archive, 'create_timeline', archive)
            task_runner.timelines.append(timeline)

            self.assertEqual(task_runner.scan_work_queue(), 0)
            self.assertEqual(timeline.get_current_record().ops, 'scan_failed')

            fixed = os.path.join(source, 'fixed.zip')
            with open(fixed, 'wb') as handle:
                handle.write(b'PK\x03\x04')

            def find_fixed(_source, _passwords, delete_after, _already, zip_list, **_kwargs):
                zip_list.append(Zip(fixed, [], delete_after))

            task_runner.unzipper.find_zip.side_effect = find_fixed
            self.assertEqual(task_runner.scan_work_queue(), 1)

        self.assertEqual(len(task_runner.timelines), 1)
        self.assertEqual(task_runner.timelines[0].get_current_record().ops, 'find_zip')
        self.assertEqual(task_runner.unzipper.find_zip.call_count, 2)

    def test_scan_reuses_source_archive_for_multiple_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'batch')
            os.makedirs(source)
            paths = [os.path.join(source, name) for name in ('a.zip', 'b.zip')]
            for path in paths:
                with open(path, 'wb') as handle:
                    handle.write(b'PK\x03\x04')

            source_archive = Archive(source)
            task_runner.timelines.append(
                Timeline(source_archive, 'create_timeline', source_archive)
            )

            def find_archives(_source, _passwords, delete_after, _already, zip_list, **_kwargs):
                zip_list.extend(Zip(path, [], delete_after) for path in paths)

            task_runner.unzipper.find_zip.side_effect = find_archives
            with mock.patch.object(
                task_runner,
                '_filter_already_extracted_archives',
                side_effect=lambda items, *_args, **_kwargs: items,
            ):
                self.assertEqual(task_runner.scan_work_queue(), 2)

        self.assertEqual(len(task_runner.timelines), 2)
        self.assertTrue(all(
            timeline.records[0].input_file is source_archive
            for timeline in task_runner.timelines
        ))


if __name__ == '__main__':
    unittest.main()
