import os
import tempfile
import unittest
from unittest import mock

import task_runner
from timeline import Archive, Timeline


class WorkflowFailureStatusTests(unittest.TestCase):
    def setUp(self):
        self.previous = {
            'logger': task_runner.logger,
            'progress_ui': task_runner.progress_ui,
        }
        task_runner.logger = mock.MagicMock()
        task_runner.progress_ui = mock.MagicMock()
        task_runner.timelines.clear()

    def tearDown(self):
        task_runner.timelines.clear()
        for name, value in self.previous.items():
            setattr(task_runner, name, value)

    def test_archive_exception_is_recorded_and_retryable(self):
        with tempfile.TemporaryDirectory() as root:
            timeline = Timeline(Archive(root), 'post_filter', Archive(root))
            task_runner.timelines.append(timeline)
            common = (
                mock.patch.object(task_runner, 'prepare_archive_queue'),
                mock.patch.object(task_runner, '_resolve_task_work_root', return_value=root),
                mock.patch.object(task_runner, '_pending_unzip_under_work_root', return_value=False),
                mock.patch.object(task_runner, '_flatten_work_root', side_effect=lambda path: path),
            )
            with common[0], common[1], common[2], common[3], mock.patch.object(
                task_runner, 'archive', side_effect=OSError('move failed'),
            ):
                task_runner.archive_loop()

            self.assertEqual(timeline.get_current_record().ops, 'archive_failed')

            def succeed(current):
                task_runner._append_step_record(current, 'archive')
                return root

            with mock.patch.object(
                task_runner, 'prepare_archive_queue',
            ), mock.patch.object(
                task_runner, '_resolve_task_work_root', return_value=root,
            ), mock.patch.object(
                task_runner, '_pending_unzip_under_work_root', return_value=False,
            ), mock.patch.object(
                task_runner, '_flatten_work_root', side_effect=lambda path: path,
            ), mock.patch.object(
                task_runner, 'archive', side_effect=succeed,
            ) as archive:
                task_runner.archive_loop()

        archive.assert_called_once_with(timeline)
        self.assertEqual(timeline.get_current_record().ops, 'archive')

    def test_rename_exception_is_recorded_and_retryable(self):
        with tempfile.TemporaryDirectory() as root:
            timeline = Timeline(Archive(root), 'post_filter', Archive(root))
            task_runner.timelines.append(timeline)

            def run(action):
                with mock.patch.object(
                    task_runner, '_is_in_resource_library', return_value=False,
                ), mock.patch.object(
                    task_runner, '_rename_root_path', return_value=root,
                ), mock.patch.object(
                    task_runner, '_narrow_rename_root', side_effect=lambda value, _path: value,
                ), mock.patch.object(
                    task_runner, '_pending_unzip_under_work_root', return_value=False,
                ), mock.patch.object(
                    task_runner, '_is_under_output', return_value=False,
                ), mock.patch.object(
                    task_runner, '_under_work_root', return_value=None,
                ), mock.patch.object(
                    task_runner, '_ensure_rj_prefix_in_place', side_effect=lambda value, _timeline: value,
                ), mock.patch.object(
                    task_runner, '_is_container_or_library_root', return_value=False,
                ), mock.patch.object(task_runner, 'rename', side_effect=action) as rename:
                    task_runner.rename_loop()
                return rename

            run(OSError('rename failed'))
            self.assertEqual(timeline.get_current_record().ops, 'rename_failed')

            def succeed(current):
                task_runner._append_step_record(current, 'rename')
                return root

            rename = run(succeed)

        rename.assert_called_once_with(timeline)
        self.assertEqual(timeline.get_current_record().ops, 'rename')


if __name__ == '__main__':
    unittest.main()
