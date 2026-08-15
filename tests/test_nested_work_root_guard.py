"""套娃解压不得把内容子目录提升为独立作品根。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import task_runner
from timeline import Archive, Record, Timeline
from zip import Zip


class NestedWorkRootGuardTests(unittest.TestCase):
    def setUp(self):
        self._previous_conf = task_runner.conf
        self._previous_unzipper = task_runner.unzipper
        self._previous_logger = task_runner.logger
        self._previous_passwords = task_runner.passwords
        task_runner._work_roots.clear()
        task_runner._work_root_preferred_names.clear()
        task_runner.timelines.clear()
        task_runner.conf = SimpleNamespace(
            output_path=r'D:\音声库',
            resource_path=r'D:\资源库',
            thread_threshold_mb=200,
            thread_compression_ratio=0.5,
        )
        task_runner.unzipper = mock.MagicMock()
        task_runner.logger = mock.MagicMock()
        task_runner.passwords = []

    def tearDown(self):
        task_runner.conf = self._previous_conf
        task_runner.unzipper = self._previous_unzipper
        task_runner.logger = self._previous_logger
        task_runner.passwords = self._previous_passwords
        task_runner._work_roots.clear()
        task_runner._work_root_preferred_names.clear()
        task_runner.timelines.clear()

    def test_nested_merge_keeps_existing_outer_work_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'RJ01624987_pk')
            bonus_dir = os.path.join(work_root, '作品', '⑤购买特典')
            inner_path = os.path.join(bonus_dir, '台本.docx')
            os.makedirs(bonus_dir)
            with open(inner_path, 'wb') as fh:
                fh.write(b'PK')
            task_runner._register_work_root(work_root)

            inner = Zip(inner_path, [], False)
            timeline = Timeline(Archive(inner_path), 'find_zip', inner)

            def fake_unzip(_zip, output_path, *_args):
                os.makedirs(output_path)
                with open(os.path.join(output_path, 'document.xml'), 'wb') as fh:
                    fh.write(b'xml')
                return output_path

            task_runner.unzipper.unzip.side_effect = fake_unzip
            result = task_runner.unzip(timeline)

            self.assertEqual(os.path.normcase(result), os.path.normcase(bonus_dir))
            self.assertIn(os.path.normpath(work_root), task_runner._work_roots)
            self.assertNotIn(os.path.normpath(bonus_dir), task_runner._work_roots)
            self.assertEqual(
                os.path.normcase(task_runner._work_root_path(bonus_dir) or ''),
                os.path.normcase(work_root),
            )

    def test_archive_recovers_outer_root_from_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'RJ01624987.rar')
            work_root = os.path.join(tmp, 'RJ01624987_pk')
            bonus_dir = os.path.join(work_root, '作品', '⑤购买特典')
            os.makedirs(bonus_dir)
            with open(outer_path, 'wb') as fh:
                fh.write(b'Rar!')

            outer = Zip(outer_path, [], False)
            timeline = Timeline(Archive(outer_path), 'find_zip', outer)
            timeline.add_record(Record(outer, 'unzip', Archive(work_root)))
            timeline.add_record(Record(Archive(work_root), 'unnest', Archive(bonus_dir)))

            task_runner._register_work_root(work_root)
            task_runner._register_work_root(bonus_dir)
            destination = os.path.join(task_runner.conf.output_path, 'RJ01624987_pk')

            with mock.patch.object(
                task_runner, '_move_to_audio_library', return_value=destination,
            ) as move_to_library:
                result = task_runner._relocate_work_to_library(timeline)

            self.assertEqual(result, destination)
            move_to_library.assert_called_once_with(work_root)
            task_runner.logger.warning.assert_called()


if __name__ == '__main__':
    unittest.main()
