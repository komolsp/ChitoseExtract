import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import filter as filter_module
import task_runner
from filter_rules import DEFAULT_FILTER_RULES, build_filter_keywords


class FilterRecycleCollisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = os.path.join(self.tmp.name, 'output')
        self.recycle = os.path.join(self.tmp.name, 'recycle')
        self.work_name = 'RJ01726751zip'
        self.work = os.path.join(self.output, self.work_name)
        os.makedirs(self.work)
        os.makedirs(self.recycle)
        self.logger = mock.MagicMock()
        self.conf = SimpleNamespace(
            logical_deletion=True, output_path=self.output,
            recycle_path=self.recycle,
        )
        self.enterContext(mock.patch.object(task_runner, 'conf', self.conf))
        self.enterContext(mock.patch.object(task_runner, 'logger', self.logger))

    def write(self, path, content=b'audio payload'):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as handle:
            handle.write(content)
        return path

    def assert_content(self, path, content=b'audio payload'):
        with open(path, 'rb') as handle:
            self.assertEqual(handle.read(), content)

    def make_filter(self):
        keywords = build_filter_keywords({
            **dict(DEFAULT_FILTER_RULES),
            'mp3': True,
            'no_se_folder': False,
            'no_se_all': False,
            'no_se_wav': False,
        })
        return filter_module.Filter(keywords, True, self.logger)

    def test_recycles_folder_and_file_beside_same_named_archive(self):
        archive = self.write(os.path.join(self.recycle, self.work_name), b'original archive')
        source = os.path.join(self.work, 'audio', 'NoSE')
        self.write(os.path.join(source, 'track.wav'))
        self.assertTrue(task_runner.delete_file(source))
        self.assertFalse(os.path.exists(source))
        self.assert_content(os.path.join(self.recycle, self.work_name + '(1)', 'audio', 'NoSE', 'track.wav'))
        track = self.write(os.path.join(self.work, 'audio', 'main', 'track.mp3'))
        self.assertTrue(task_runner.delete_file(track))
        self.assertFalse(os.path.exists(track))
        self.assert_content(os.path.join(self.recycle, self.work_name + '(1)', 'audio', 'main', 'track.mp3'))
        self.assert_content(archive, b'original archive')

    def test_recycles_beside_file_blocking_nested_directory(self):
        blocker = self.write(os.path.join(self.recycle, self.work_name, 'audio'), b'old file')
        source = self.write(os.path.join(self.work, 'audio', 'track.mp3'))
        self.assertTrue(task_runner.delete_file(source))
        self.assertFalse(os.path.exists(source))
        self.assert_content(os.path.join(self.recycle, self.work_name, 'audio(1)', 'track.mp3'))
        self.assert_content(blocker, b'old file')

    def test_existing_folder_is_preserved_when_recycling_another_folder(self):
        old = self.write(os.path.join(self.recycle, self.work_name, 'NoSE', 'old.wav'), b'old audio')
        source = os.path.join(self.work, 'NoSE')
        self.write(os.path.join(source, 'track.wav'))
        self.assertTrue(task_runner.delete_file(source))
        self.assert_content(old, b'old audio')
        self.assert_content(os.path.join(self.recycle, self.work_name, 'NoSE(1)', 'track.wav'))

    def test_existing_file_is_preserved_when_recycling_another_file(self):
        old = self.write(os.path.join(self.recycle, self.work_name, 'track.mp3'), b'old audio')
        source = self.write(os.path.join(self.work, 'track.mp3'))
        self.assertTrue(task_runner.delete_file(source))
        self.assert_content(old, b'old audio')
        self.assert_content(os.path.join(self.recycle, self.work_name, 'track(1).mp3'))

    def test_destination_not_found_is_logged_and_source_preserved(self):
        source = self.write(os.path.join(self.work, 'track.mp3'))
        with mock.patch.object(task_runner.os, 'makedirs', side_effect=FileNotFoundError('missing destination parent')):
            self.assertFalse(task_runner.delete_file(source))
        self.assert_content(source)
        self.logger.error.assert_called_once()
        self.assertIn('missing destination parent', self.logger.error.call_args.args[0])

    def test_missing_source_is_already_deleted(self):
        self.assertTrue(task_runner.delete_file(os.path.join(self.work, 'missing.mp3')))
        self.logger.error.assert_not_called()

    def test_mp3_folder_failure_propagates_to_filter_loop(self):
        from timeline import Archive, Timeline
        import config

        source = self.write(os.path.join(self.work, 'MP3', 'track.mp3'))
        self.write(os.path.join(self.work, 'track.flac'), b'lossless audio')
        current_conf = config.Config.__new__(config.Config)
        current_conf._apply_config(config._prepare_config({
            'path': {'output': self.output, 'recycle': self.recycle},
        }))
        timeline = Timeline(Archive(self.work), 'unnest', Archive(self.work))
        with mock.patch.object(task_runner, 'conf', current_conf), mock.patch.object(
            task_runner, 'timelines', [timeline],
        ), mock.patch.object(task_runner, 'filter', self.make_filter()), mock.patch.object(
            task_runner, 'progress_ui', mock.MagicMock(),
        ), mock.patch.object(task_runner, 'insert_rj', return_value=self.work), mock.patch.object(
            task_runner, '_pending_unzip_under_work_root', return_value=False,
        ), mock.patch.object(task_runner, 'delete_file', return_value=False):
            task_runner.filter_loop()
        self.assertEqual(timeline.get_current_record().ops, 'filter_failed')
        self.assert_content(source)

    def test_folder_recycle_can_recover_individual_file_failure(self):
        source = self.write(os.path.join(self.work, 'MP3', 'track.mp3'))
        lossless = self.write(os.path.join(self.work, 'track.flac'), b'lossless audio')
        original_delete = task_runner.delete_file

        def delete(path):
            return False if path == source else original_delete(path)

        with mock.patch.object(task_runner, 'delete_file', side_effect=delete):
            self.assertTrue(self.make_filter().post_filter(self.work))
        self.assertFalse(os.path.exists(source))
        self.assert_content(os.path.join(self.recycle, self.work_name, 'MP3', 'track.mp3'))
        self.assert_content(lossless, b'lossless audio')

    def test_post_filter_recycles_with_archive_collision_and_keeps_lossless(self):
        archive = self.write(os.path.join(self.recycle, self.work_name), b'original archive')
        source = self.write(os.path.join(self.work, 'MP3', 'track.mp3'))
        lossless = self.write(os.path.join(self.work, 'track.flac'), b'lossless audio')
        self.assertTrue(self.make_filter().post_filter(self.work))
        self.assertFalse(os.path.exists(source))
        self.assertFalse(os.path.exists(os.path.join(self.work, 'MP3')))
        self.assert_content(os.path.join(self.recycle, self.work_name + '(1)', 'MP3', 'track.mp3'))
        self.assert_content(lossless, b'lossless audio')
        self.assert_content(archive, b'original archive')


if __name__ == '__main__':
    unittest.main()
