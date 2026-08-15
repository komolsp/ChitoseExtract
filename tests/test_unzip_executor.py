import os
import unittest
from unittest import mock

from timeline import Archive, Record, Timeline
from unzip_executor import (
    UnzipExecutionDependencies,
    UnzipExecutor,
    unzip_task_priority,
)
from workflow_context import WorkflowState
from zip import Zip


class UnzipExecutorTests(unittest.TestCase):
    def setUp(self):
        self.state = WorkflowState()
        self.executor = UnzipExecutor(self.state)

    @staticmethod
    def _timeline(path: str, *, volumes=None) -> Timeline:
        source = Archive(path)
        zip_obj = Zip(path, [], False, volumes=volumes)
        return Timeline(source, 'find_zip', zip_obj)

    @staticmethod
    def _complete(timeline: Timeline) -> None:
        zip_obj = timeline.get_current_record().output_file
        timeline.add_record(
            Record(zip_obj, 'unzip', Archive(zip_obj.path + '.out')),
        )

    @staticmethod
    def _dependencies(process, **overrides):
        values = {
            'process_timeline': process,
            'pending_zip': lambda timeline: timeline.get_current_record().output_file,
            'recover_timeline': mock.Mock(return_value=False),
            'task_priority': unzip_task_priority,
            'logger': mock.Mock(),
            'on_round_complete': mock.Mock(),
        }
        values.update(overrides)
        return UnzipExecutionDependencies(**values)

    def test_run_consumes_new_nested_tasks_in_following_round(self):
        first = self._timeline(os.path.join('work', 'b.zip'))
        second = self._timeline(os.path.join('work', 'a.zip'))
        self.state.timelines.extend([first, second])
        calls = []

        def process(timeline):
            calls.append(timeline.get_current_path())
            self._complete(timeline)
            if len(calls) == 1:
                self.state.timelines.append(
                    self._timeline(os.path.join('work', 'inner.zip')),
                )

        dependencies = self._dependencies(process)
        result = self.executor.run(max_rounds=4, dependencies=dependencies)

        self.assertEqual(
            calls,
            [
                os.path.join('work', 'a.zip'),
                os.path.join('work', 'b.zip'),
                os.path.join('work', 'inner.zip'),
            ],
        )
        self.assertEqual(result.rounds, 2)
        self.assertEqual(result.processed, 3)
        self.assertFalse(result.stopped_at_limit)
        self.assertEqual(dependencies.on_round_complete.call_count, 2)

    def test_task_exception_is_marked_failed_and_next_task_continues(self):
        failed = self._timeline(os.path.join('work', 'a-bad.zip'))
        succeeded = self._timeline(os.path.join('work', 'b-good.zip'))
        self.state.timelines.extend([failed, succeeded])

        def process(timeline):
            if timeline is failed:
                raise RuntimeError('broken')
            self._complete(timeline)

        dependencies = self._dependencies(process)
        result = self.executor.run(max_rounds=2, dependencies=dependencies)

        self.assertEqual(failed.get_current_record().ops, 'unzip_failed')
        self.assertEqual(succeeded.get_current_record().ops, 'unzip')
        self.assertEqual(result.processed, 2)
        dependencies.logger.error.assert_called_once()
        dependencies.logger.debug.assert_called_once()

    def test_recovered_exception_does_not_create_failure_record(self):
        timeline = self._timeline(os.path.join('work', 'outer.zip'))
        self.state.timelines.append(timeline)

        def recover(recovered_timeline, _zip_obj):
            self._complete(recovered_timeline)
            return True

        dependencies = self._dependencies(
            mock.Mock(side_effect=RuntimeError('inner password')),
            recover_timeline=mock.Mock(side_effect=recover),
        )
        result = self.executor.run(max_rounds=2, dependencies=dependencies)

        self.assertEqual(timeline.get_current_record().ops, 'unzip')
        self.assertEqual(result.processed, 1)
        dependencies.recover_timeline.assert_called_once()
        dependencies.logger.info.assert_called_once()
        dependencies.logger.error.assert_not_called()

    def test_round_limit_stops_repeating_task(self):
        timeline = self._timeline(os.path.join('work', 'loop.zip'))
        self.state.timelines.append(timeline)
        dependencies = self._dependencies(mock.Mock())

        result = self.executor.run(max_rounds=2, dependencies=dependencies)

        self.assertEqual(result.rounds, 2)
        self.assertEqual(result.processed, 2)
        self.assertTrue(result.stopped_at_limit)
        dependencies.logger.error.assert_called_once()
        self.assertEqual(dependencies.on_round_complete.call_count, 2)

    def test_requeue_failures_uses_shared_context_state(self):
        failed = self._timeline(os.path.join('work', 'failed.zip'))
        failed.add_record(
            Record(
                failed.get_current_record().output_file,
                'unzip_failed',
                failed.get_current_record().output_file,
            ),
        )
        pending = self._timeline(os.path.join('work', 'pending.zip'))
        self.state.timelines.extend([failed, pending])

        def requeue(timeline):
            if timeline.get_current_record().ops != 'unzip_failed':
                return False
            record = timeline.get_current_record()
            timeline.add_record(Record(record.input_file, 'find_zip', record.output_file))
            return True

        self.assertEqual(self.executor.requeue_failures(requeue), 1)
        self.assertEqual(failed.get_current_record().ops, 'find_zip')
        self.assertEqual(pending.get_current_record().ops, 'find_zip')

    def test_priority_orders_complete_single_and_incomplete_archives(self):
        volumes = [
            os.path.join('work', 'album.7z.001'),
            os.path.join('work', 'album.7z.002'),
        ]
        grouped = self._timeline(volumes[0], volumes=volumes)
        single = self._timeline(os.path.join('work', 'single.zip'))

        with mock.patch(
            'volume.resolver.is_complete_volume_group',
            side_effect=[True, False],
        ):
            self.assertEqual(unzip_task_priority(grouped)[0], 0)
            self.assertEqual(unzip_task_priority(grouped)[0], 2)
        self.assertEqual(unzip_task_priority(single)[0], 1)


if __name__ == '__main__':
    unittest.main()
