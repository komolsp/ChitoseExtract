import os
import tempfile
import unittest
from unittest import mock

import config
import filter as filter_module
import task_runner
from timeline import Archive, Record, Timeline
from zip import Zip


class FilterLoopTests(unittest.TestCase):
    def setUp(self):
        task_runner.timelines.clear()
        task_runner.already_add.clear()
        task_runner._work_roots.clear()
        task_runner.conf = config.Config()
        task_runner.logger = mock.MagicMock()
        task_runner.progress_ui = mock.MagicMock()
        task_runner.filter = filter_module.Filter(
            task_runner.conf.filter_kw,
            task_runner.conf.filter_dir,
            task_runner.logger,
        )

    def test_filter_deferred_when_pending_nested_unzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, 'output')
            os.makedirs(output)
            work = os.path.join(output, 'RJ01330941')
            os.makedirs(work)
            with open(os.path.join(work, 'track.wav'), 'wb') as handle:
                handle.write(b'RIFF')
            nested = os.path.join(work, 'inner.zip')
            with open(nested, 'wb') as handle:
                handle.write(b'PK')

            task_runner.conf.output_path = output
            task_runner._work_roots.add(work)

            main = Timeline(Archive(work), 'unnest', Archive(work))
            pending = Timeline(Archive(nested), 'find_zip', Zip(nested, [], False))
            task_runner.timelines[:] = [main, pending]

            task_runner.filter_loop()

            self.assertEqual(main.get_current_record().ops, 'unnest')
            task_runner.logger.info.assert_any_call(mock.ANY)
            deferred = any(
                '推迟过滤' in str(call)
                for call in task_runner.logger.info.call_args_list
            )
            self.assertTrue(deferred)

    def test_filter_runs_after_insert_rj_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, 'output')
            os.makedirs(output)
            work = os.path.join(output, '作品')
            os.makedirs(work)
            # 无 SE 文件夹，应被过滤
            nose = os.path.join(work, 'SEなし')
            os.makedirs(nose)
            with open(os.path.join(nose, 'a.wav'), 'wb') as handle:
                handle.write(b'RIFF')
            with open(os.path.join(work, 'ok.wav'), 'wb') as handle:
                handle.write(b'RIFF')

            task_runner.conf.output_path = output
            task_runner._work_roots.add(work)

            archive = Archive(work)
            archive.RJ_code = 'RJ01330941'
            archive.name = 'RJ01330941.zip'
            timeline = Timeline(archive, 'unnest', Archive(work))
            # 让 _find_rj_for_timeline 能拿到带前缀的 RJ
            timeline.records[0].input_file.name = '[RJ01330941]作品.zip'
            timeline.records[0].input_file.RJ_code = 'RJ01330941'
            task_runner.timelines[:] = [timeline]

            with mock.patch.object(
                task_runner, '_find_rj_for_timeline', return_value='RJ01330941',
            ):
                task_runner.filter_loop()

            self.assertEqual(timeline.get_current_record().ops, 'post_filter')
            # SEなし 目录应被删掉（逻辑删除到 recycle 或永久删除）
            self.assertFalse(os.path.isdir(nose))
            self.assertTrue(os.path.isfile(os.path.join(
                os.path.dirname(timeline.get_current_path()),
                os.path.basename(timeline.get_current_path()),
                'ok.wav',
            )) or os.path.isfile(os.path.join(timeline.get_current_path(), 'ok.wav')))


if __name__ == '__main__':
    unittest.main()
