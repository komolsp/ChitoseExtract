import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from timeline import Archive, Record, Timeline
from unzip_task_processor import UnzipTaskDependencies, UnzipTaskProcessor
from zip import Zip


class UnzipTaskProcessorTests(unittest.TestCase):
    def setUp(self):
        self.processor = UnzipTaskProcessor()

    @staticmethod
    def _timeline(path: str) -> Timeline:
        archive = Archive(path)
        return Timeline(archive, 'find_zip', Zip(path, [], False))

    @staticmethod
    def _dependencies(**overrides):
        registry = SimpleNamespace(
            is_unzipped=mock.Mock(return_value=False),
            mark_unzipped=mock.Mock(),
        )
        values = {
            'archive_registry': registry,
            'is_nested_archive': mock.Mock(return_value=False),
            'requests_reextract': mock.Mock(return_value=False),
            'resolve_extracted_work_root': mock.Mock(return_value=None),
            'has_extracted_content': mock.Mock(return_value=False),
            'should_resume_nested_only': mock.Mock(return_value=False),
            'register_work_root': mock.Mock(),
            'flatten_work_root': mock.Mock(side_effect=lambda path: path),
            'enqueue_nested_archives': mock.Mock(),
            'timeline_targets_outer_zip': mock.Mock(return_value=False),
            'promote_outer_timeline_to_inner': mock.Mock(return_value=False),
            'advance_past_outer_layer': mock.Mock(return_value=True),
            'timeline_has_unzipped_ancestor': mock.Mock(return_value=False),
            'unnest': mock.Mock(return_value=None),
            'pre_filter': mock.Mock(),
            'pending_zip': lambda timeline: timeline.get_current_record().output_file,
            'refresh_zip_volumes': mock.Mock(),
            'prepare_zip_for_unzip': mock.Mock(),
            'resolve_work_root_containing': mock.Mock(return_value=None),
            'snapshot_scan_tree': mock.Mock(return_value={}),
            'skip_duplicate_volume_unzip': mock.Mock(return_value=False),
            'unzip': mock.Mock(return_value=None),
            'recover_outer_with_pending_inner': mock.Mock(return_value=False),
            'remember_unzipped_archive': mock.Mock(),
            'dismiss_volume_sibling_failures': mock.Mock(return_value=0),
            'incremental_scan_roots': mock.Mock(return_value=None),
            'logger': mock.Mock(),
        }
        values.update(overrides)
        return UnzipTaskDependencies(**values)

    def test_existing_outer_archive_skips_physical_unzip(self):
        timeline = self._timeline(os.path.join('work', 'outer.zip'))
        dependencies = self._dependencies(
            resolve_extracted_work_root=mock.Mock(return_value='work-root'),
            has_extracted_content=mock.Mock(return_value=True),
            should_resume_nested_only=mock.Mock(return_value=True),
            flatten_work_root=mock.Mock(return_value='flat-root'),
        )

        with mock.patch(
            'unzip_task_processor.file_ops.is_dir_path',
            return_value=True,
        ):
            self.processor.process(timeline, dependencies)

        zip_obj = timeline.records[0].output_file
        dependencies.archive_registry.mark_unzipped.assert_called_once_with(
            zip_obj.path,
            zip_obj.volumes,
        )
        dependencies.register_work_root.assert_called_once_with('work-root')
        dependencies.enqueue_nested_archives.assert_called_once_with(
            timeline,
            'flat-root',
            zip_obj,
        )
        dependencies.pre_filter.assert_not_called()
        dependencies.unzip.assert_not_called()

    def test_registered_nested_archive_skips_duplicate_unzip(self):
        timeline = self._timeline(os.path.join('work', 'inner.zip'))
        registry = SimpleNamespace(
            is_unzipped=mock.Mock(return_value=True),
            mark_unzipped=mock.Mock(),
        )
        dependencies = self._dependencies(
            archive_registry=registry,
            is_nested_archive=mock.Mock(return_value=True),
            timeline_has_unzipped_ancestor=mock.Mock(return_value=True),
            unnest=mock.Mock(return_value='work-root'),
        )

        self.processor.process(timeline, dependencies)

        dependencies.enqueue_nested_archives.assert_called_once_with(
            timeline,
            'work-root',
            timeline.records[0].output_file,
        )
        dependencies.pre_filter.assert_not_called()
        dependencies.unzip.assert_not_called()
        dependencies.logger.info.assert_called()

    def test_success_records_archive_and_runs_incremental_nested_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            timeline = self._timeline(os.path.join(tmp, 'inner.zip'))
            zip_obj = timeline.get_current_record().output_file

            def unzip(current_timeline):
                current_timeline.add_record(
                    Record(zip_obj, 'unzip', Archive(tmp)),
                )
                return tmp

            dependencies = self._dependencies(
                is_nested_archive=mock.Mock(return_value=True),
                resolve_work_root_containing=mock.Mock(return_value=tmp),
                snapshot_scan_tree=mock.Mock(return_value={'before': (1, 1)}),
                unzip=mock.Mock(side_effect=unzip),
                unnest=mock.Mock(return_value=tmp),
                incremental_scan_roots=mock.Mock(return_value=[tmp]),
            )

            with mock.patch(
                'unzip_task_processor.file_ops.is_dir_path',
                return_value=True,
            ):
                self.processor.process(timeline, dependencies)

        dependencies.refresh_zip_volumes.assert_called_once_with(
            zip_obj,
            timeline.records[0].input_file.path,
        )
        dependencies.prepare_zip_for_unzip.assert_called_once_with(zip_obj)
        dependencies.remember_unzipped_archive.assert_called_once_with(zip_obj)
        dependencies.dismiss_volume_sibling_failures.assert_called_once()
        dependencies.incremental_scan_roots.assert_called_once_with(
            tmp,
            {'before': (1, 1)},
        )
        dependencies.enqueue_nested_archives.assert_called_once_with(
            timeline,
            tmp,
            zip_obj,
            incremental_roots=[tmp],
        )

    def test_failed_unzip_adds_retryable_failure_record(self):
        timeline = self._timeline(os.path.join('work', 'broken.zip'))
        dependencies = self._dependencies()

        self.processor.process(timeline, dependencies)

        self.assertEqual(timeline.get_current_record().ops, 'unzip_failed')
        dependencies.recover_outer_with_pending_inner.assert_called_once()
        dependencies.remember_unzipped_archive.assert_not_called()
        dependencies.enqueue_nested_archives.assert_not_called()
        dependencies.logger.error.assert_called_once()

    def test_recovered_outer_failure_does_not_add_failure_record(self):
        timeline = self._timeline(os.path.join('work', 'outer.zip'))
        dependencies = self._dependencies(
            recover_outer_with_pending_inner=mock.Mock(return_value=True),
        )

        self.processor.process(timeline, dependencies)

        self.assertEqual(timeline.get_current_record().ops, 'find_zip')
        dependencies.recover_outer_with_pending_inner.assert_called_once()
        dependencies.logger.error.assert_not_called()


if __name__ == '__main__':
    unittest.main()
