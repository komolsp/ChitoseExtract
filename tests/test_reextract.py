"""再次拖入已解压压缩包时应允许重新解压。"""

import os
import tempfile
import unittest
from unittest import mock

import archive_registry
import config
import task_runner
from timeline import Archive, Timeline
from zip import Zip


class ReextractTests(unittest.TestCase):
    def setUp(self):
        archive_registry.clear()
        task_runner.timelines.clear()
        task_runner.already_add.clear()
        task_runner._work_roots.clear()
        task_runner.conf = config.Config()
        task_runner.unzipper = mock.MagicMock()
        task_runner.logger = mock.MagicMock()
        task_runner.passwords = []

    def test_forget_clears_volume_identity(self):
        from volume.collect import volume_group_identity

        vols = [r'D:\work\a.7z.001', r'D:\work\a.7z.002']
        identity = volume_group_identity(vols)
        archive_registry.mark_unzipped(vols[0], vols)
        self.assertTrue(archive_registry.is_unzipped(vols[0], vols))
        archive_registry.forget_under(r'D:\work')
        self.assertFalse(archive_registry.is_unzipped(vols[0], vols))
        self.assertFalse(archive_registry.is_volume_part_unzipped(vols[1]))

    def test_folder_top_level_archive_requests_reextract(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, '乌拉拉.7z.001')
            with open(archive_path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            outer = Zip(archive_path, [], False)
            outer.volumes = [archive_path, os.path.join(tmp, '乌拉拉.7z.002')]
            timeline = Timeline(Archive(tmp), 'find_zip', outer)
            archive_registry.mark_unzipped(archive_path, outer.volumes)
            self.assertTrue(
                task_runner._timeline_requests_reextract(timeline, outer),
            )

    def test_forget_allows_rediscovery(self):
        path = r'D:\work\album.7z'
        archive_registry.mark_unzipped(path)
        self.assertTrue(archive_registry.is_unzipped(path))
        archive_registry.forget(path)
        self.assertFalse(archive_registry.is_unzipped(path))
        self.assertFalse(archive_registry.is_discovered(path))

    def test_filter_keeps_outer_when_user_rescans(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album_pk')
            os.makedirs(work_root)
            with open(archive_path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            with open(os.path.join(work_root, 'track.wav'), 'wb') as fh:
                fh.write(b'RIFF')

            outer = Zip(archive_path, [], False)
            archive_registry.mark_unzipped(archive_path)

            filtered = task_runner._filter_already_extracted_archives(
                [outer], [''], [],
                allow_reextract=True,
            )
            self.assertEqual(len(filtered), 1)
            self.assertEqual(filtered[0].path, archive_path)

            skipped = task_runner._filter_already_extracted_archives(
                [outer], [''], [],
                allow_reextract=False,
            )
            self.assertEqual(skipped, [])

    def test_scan_work_queue_rediscovers_after_mark_unzipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album_pk')
            os.makedirs(work_root)
            with open(archive_path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            with open(os.path.join(work_root, 'done.wav'), 'wb') as fh:
                fh.write(b'RIFF')

            archive_registry.mark_unzipped(archive_path)
            task_runner.timelines.append(
                Timeline(Archive(archive_path), 'create_timeline', Archive(archive_path)),
            )

            found: list = []

            def fake_find_zip(path, passwords, delete_after, already_add, zip_list, **kwargs):
                if os.path.isfile(path) and path.endswith('.7z'):
                    zip_list.append(Zip(path, passwords, delete_after))

            task_runner.unzipper.find_zip.side_effect = fake_find_zip
            task_runner.progress_ui = mock.MagicMock()

            added = task_runner.scan_work_queue()
            self.assertEqual(added, 1)
            self.assertEqual(task_runner.timelines[0].get_current_record().ops, 'find_zip')
            self.assertEqual(
                task_runner.timelines[0].get_current_record().output_file.path,
                archive_path,
            )

    def test_process_unzip_does_not_skip_fresh_user_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album_pk')
            os.makedirs(work_root)
            with open(archive_path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            with open(os.path.join(work_root, 'keep.wav'), 'wb') as fh:
                fh.write(b'RIFF')

            outer = Zip(archive_path, [], False)
            archive_registry.mark_unzipped(archive_path)
            timeline = Timeline(Archive(archive_path), 'find_zip', outer)

            original_unzip = task_runner.unzip
            task_runner.pre_filter = mock.MagicMock()
            try:
                task_runner.unzip = mock.MagicMock(return_value=work_root)
                task_runner._process_unzip_timeline(timeline)
                task_runner.unzip.assert_called_once()
            finally:
                task_runner.unzip = original_unzip


if __name__ == '__main__':
    unittest.main()
