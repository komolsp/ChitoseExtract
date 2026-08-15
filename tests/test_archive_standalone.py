"""单独执行归档步骤时应能处理音声库外的就地作品目录。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import task_runner
from timeline import Archive, Timeline


class TestArchiveStandalone(unittest.TestCase):
    def setUp(self):
        task_runner.clear()
        task_runner.progress_ui = MagicMock()
        self._prev_conf = task_runner.conf
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = self._tmpdir.__enter__()
        self.output = os.path.join(self.tmp, 'output')
        self.download = os.path.join(self.tmp, 'download')
        os.makedirs(self.output)
        os.makedirs(self.download)
        task_runner.conf = SimpleNamespace(
            output_path=self.output,
            resource_path=os.path.join(self.tmp, 'resource'),
            recycle_path=os.path.join(self.tmp, 'recycle'),
            logical_deletion=True,
        )
        os.makedirs(task_runner.conf.resource_path)
        os.makedirs(task_runner.conf.recycle_path)
        task_runner.logger = None

    def tearDown(self):
        task_runner.conf = self._prev_conf
        task_runner.clear()
        self._tmpdir.__exit__(None, None, None)

    def test_archive_moves_external_work_root(self):
        work_root = os.path.join(self.download, 'RJ01653819_pk')
        os.makedirs(work_root)
        with open(os.path.join(work_root, 'track.wav'), 'wb') as fh:
            fh.write(b'data')

        archive = Archive(work_root)
        timeline = Timeline(archive, 'create_timeline', archive)
        task_runner.timelines.append(timeline)

        task_runner.archive_loop()

        self.assertFalse(os.path.isdir(work_root))
        moved = os.listdir(self.output)
        self.assertEqual(len(moved), 1)
        self.assertIn('RJ01653819', moved[0])

    def test_blank_resource_path_keeps_unknown_work_in_audio_library(self):
        task_runner.conf.resource_path = ''
        task_runner.logger = MagicMock()
        work_root = os.path.join(self.download, '未识别作品_pk')
        os.makedirs(work_root)
        with open(os.path.join(work_root, 'track.wav'), 'wb') as fh:
            fh.write(b'data')
        archive = Archive(work_root)
        timeline = Timeline(archive, 'create_timeline', archive)
        task_runner.timelines.append(timeline)
        task_runner._register_work_root(work_root)

        task_runner.archive_loop()

        self.assertFalse(os.path.isdir(work_root))
        self.assertEqual(os.listdir(self.output), ['未识别作品_pk'])
        self.assertEqual(timeline.get_current_record().ops, 'archive')

    def test_prepare_archive_queue_registers_dropped_folder(self):
        work_root = os.path.join(self.download, 'RJ01629264_pk')
        os.makedirs(work_root)

        archive = Archive(work_root)
        timeline = Timeline(archive, 'create_timeline', archive)
        task_runner.timelines.append(timeline)

        task_runner.prepare_archive_queue()

        self.assertIn(os.path.normpath(work_root), task_runner._work_roots)

    def test_recover_generated_shallow_root_from_matching_source_archive(self):
        source = os.path.join(self.download, '无RJ作品.zzz')
        work_root = os.path.join(self.download, '无RJ作品_pk')
        with open(source, 'wb') as fh:
            fh.write(b'PK\x03\x04')
        os.makedirs(work_root)
        with open(os.path.join(work_root, 'track.wav'), 'wb') as fh:
            fh.write(b'RIFF')

        with patch.object(
            task_runner, '_is_drive_or_shallow_root', return_value=True,
        ), patch.object(
            task_runner.file_ops, 'probe_archive',
            return_value=SimpleNamespace(is_candidate=True),
        ):
            self.assertTrue(task_runner._is_recoverable_generated_work_root(work_root))

    def test_archive_retry_recovers_shallow_root_after_post_filter(self):
        work_root = os.path.join(self.download, '作品RJ01638016_pk')
        os.makedirs(work_root)
        with open(os.path.join(work_root, 'track.wav'), 'wb') as fh:
            fh.write(b'data')

        archive = Archive(work_root)
        timeline = Timeline(archive, 'post_filter', archive)
        task_runner.timelines.append(timeline)

        original_guard = task_runner._is_drive_or_shallow_root

        def shallow_only_for_work(path):
            if os.path.normcase(os.path.normpath(path)) == os.path.normcase(work_root):
                return True
            return original_guard(path)

        with patch.object(
            task_runner, '_is_drive_or_shallow_root', side_effect=shallow_only_for_work,
        ):
            task_runner.archive_loop()

        self.assertFalse(os.path.isdir(work_root))
        self.assertEqual(len(os.listdir(self.output)), 1)
        self.assertEqual(timeline.get_current_record().ops, 'archive')

    def test_cross_drive_library_move_skips_expected_rename_failure(self):
        src = r'E:\作品RJ01638016_pk'
        dest_dir = r'D:\奥术魔刃\同人音声'
        expected = os.path.join(dest_dir, os.path.basename(src))

        with patch.object(
            task_runner.file_ops, 'path_exists', side_effect=lambda path: path == src,
        ), patch.object(
            task_runner.file_ops, 'mk_if_not_exit',
        ), patch.object(
            task_runner.file_ops, 'is_dir_path', return_value=True,
        ), patch.object(
            task_runner.file_ops, 'safe_rename_path',
        ) as rename_path, patch.object(
            task_runner.file_ops.shutil, 'move',
        ) as fallback_move:
            result = task_runner.file_ops.move_into_directory(src, dest_dir)

        self.assertEqual(os.path.normcase(result), os.path.normcase(expected))
        rename_path.assert_not_called()
        fallback_move.assert_called_once_with(src, expected)


if __name__ == '__main__':
    unittest.main()
