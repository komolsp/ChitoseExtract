"""套娃影子任务在流水线结束后应被清理。"""

import os
import tempfile
import unittest

import task_runner
from timeline import Archive, Record, Timeline


class TestPruneCompanionTimelines(unittest.TestCase):
    def setUp(self):
        task_runner.clear()
        task_runner.progress_ui = 'not initialized'

    def tearDown(self):
        task_runner.clear()
        task_runner.progress_ui = 'not initialized'

    def _make_timeline(self, work_root: str, ops: str) -> Timeline:
        archive = Archive(work_root)
        timeline = Timeline(archive, 'create_timeline', archive)
        timeline.add_record(Record(archive, ops, Archive(work_root)))
        return timeline

    def test_prune_shadow_post_filter_after_tag_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'RJ01629264')
            os.makedirs(work_root)

            main = self._make_timeline(work_root, 'tag_audio')
            shadow = self._make_timeline(work_root, 'post_filter')
            task_runner.timelines.extend([main, shadow])

            completed = task_runner._collect_completed_work_root_keys('tag_audio')
            task_runner._prune_successful_timelines('tag_audio')
            task_runner._prune_companion_timelines(completed)

            self.assertEqual(task_runner.timelines, [])

    def test_keep_failed_timeline_for_same_work_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'RJ01629264')
            os.makedirs(work_root)

            main = self._make_timeline(work_root, 'tag_audio')
            failed = self._make_timeline(work_root, 'unzip_failed')
            task_runner.timelines.extend([main, failed])

            completed = task_runner._collect_completed_work_root_keys('tag_audio')
            task_runner._prune_successful_timelines('tag_audio')
            task_runner._prune_companion_timelines(completed)

            self.assertEqual(len(task_runner.timelines), 1)
            self.assertEqual(
                task_runner.timelines[0].get_current_record().ops,
                'unzip_failed',
            )

    def test_prune_after_step_clears_shadows(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'RJ01653819')
            os.makedirs(work_root)

            main = self._make_timeline(work_root, 'rename')
            shadow = self._make_timeline(work_root, 'post_filter')
            task_runner.timelines.extend([main, shadow])
            task_runner._last_rename_succeeded_roots = {
                os.path.normcase(work_root),
            }

            task_runner.prune_after_step('rename')

            self.assertEqual(task_runner.timelines, [])


if __name__ == '__main__':
    unittest.main()
