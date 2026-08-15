import os
import tempfile
import unittest
from unittest import mock

import file_ops
from queue_scanner import QueueScanDependencies, QueueScanner
from timeline import Archive, Timeline
from workflow_context import WorkflowState
from zip import Zip


class QueueScannerTests(unittest.TestCase):
    def setUp(self):
        self.state = WorkflowState()
        self.scanner = QueueScanner(self.state)

    @staticmethod
    def _dependencies(find_zip, **overrides):
        values = {
            'find_zip': find_zip,
            'prepare_rescan': mock.Mock(),
            'filter_discovered': lambda items, *_args, **_kwargs: items,
            'forget_archive': mock.Mock(),
            'logger': mock.Mock(),
            'on_queue_changed': mock.Mock(),
        }
        values.update(overrides)
        return QueueScanDependencies(**values)

    def test_scan_replaces_source_with_discovered_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, 'batch')
            os.makedirs(source)
            archive_path = os.path.join(source, 'album.zip')
            source_archive = Archive(source)
            source_archive.set_note('secret')
            self.state.timelines.append(
                Timeline(source_archive, 'create_timeline', source_archive),
            )

            def find_zip(_source, passwords, delete_after, already, items, **_kwargs):
                self.assertEqual(passwords, ['pw'])
                self.assertFalse(delete_after)
                self.assertEqual(already, [])
                items.append(Zip(archive_path, passwords, delete_after))

            dependencies = self._dependencies(find_zip)
            added = self.scanner.scan(
                passwords=['pw'],
                delete_after_unzip=False,
                dependencies=dependencies,
            )

        self.assertEqual(added, 1)
        self.assertEqual(len(self.state.timelines), 1)
        timeline = self.state.timelines[0]
        self.assertIs(timeline.records[0].input_file, source_archive)
        self.assertEqual(timeline.get_current_record().ops, 'find_zip')
        self.assertEqual(timeline.get_current_record().output_file.note, 'secret')
        dependencies.prepare_rescan.assert_called_once_with(source)
        dependencies.forget_archive.assert_called_once()
        dependencies.on_queue_changed.assert_called_once_with(self.state.timelines)

    def test_empty_scan_keeps_retryable_failure_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_archive = Archive(tmp)
            timeline = Timeline(source_archive, 'create_timeline', source_archive)
            self.state.timelines.append(timeline)
            dependencies = self._dependencies(lambda *_args, **_kwargs: None)

            added = self.scanner.scan(
                passwords=[],
                delete_after_unzip=False,
                dependencies=dependencies,
            )

        self.assertEqual(added, 0)
        self.assertEqual(self.state.timelines, [timeline])
        self.assertEqual(timeline.get_current_record().ops, 'scan_failed')
        dependencies.logger.warning.assert_called_once()
        dependencies.on_queue_changed.assert_called_once_with(self.state.timelines)

    def test_one_scan_exception_does_not_abort_later_queue_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            sources = [os.path.join(tmp, name) for name in ('broken', 'valid')]
            for source in sources:
                os.makedirs(source)
                archive = Archive(source)
                self.state.timelines.append(
                    Timeline(archive, 'create_timeline', archive),
                )
            archive_path = os.path.join(sources[1], 'album.zip')

            def find_zip(source, passwords, delete_after, _already, items, **_kwargs):
                if source == sources[0]:
                    raise FileNotFoundError(source)
                items.append(Zip(archive_path, passwords, delete_after))

            dependencies = self._dependencies(find_zip)
            added = self.scanner.scan(
                passwords=[],
                delete_after_unzip=False,
                dependencies=dependencies,
            )

        self.assertEqual(added, 1)
        self.assertEqual(
            [item.get_current_record().ops for item in self.state.timelines],
            ['scan_failed', 'find_zip'],
        )
        dependencies.logger.error.assert_called_once()
        dependencies.on_queue_changed.assert_called_once_with(self.state.timelines)

    def test_missing_parent_has_no_similar_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, 'missing-parent', 'archive.zip')
            self.assertIsNone(file_ops.get_similar(missing))

    def test_duplicate_volume_groups_create_one_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            sources = [os.path.join(tmp, name) for name in ('first', 'second')]
            for source in sources:
                os.makedirs(source)
                archive = Archive(source)
                self.state.timelines.append(
                    Timeline(archive, 'create_timeline', archive),
                )
            volumes = [
                os.path.join(tmp, 'album.7z.001'),
                os.path.join(tmp, 'album.7z.002'),
            ]
            for path in volumes:
                with open(path, 'wb') as handle:
                    handle.write(b'volume')

            def find_zip(source, passwords, delete_after, _already, items, **_kwargs):
                path = volumes[0] if source == sources[0] else volumes[1]
                items.append(
                    Zip(path, passwords, delete_after, volumes=list(volumes)),
                )

            dependencies = self._dependencies(find_zip)
            added = self.scanner.scan(
                passwords=[],
                delete_after_unzip=False,
                dependencies=dependencies,
            )

        self.assertEqual(added, 1)
        self.assertEqual(len(self.state.timelines), 1)
        self.assertEqual(dependencies.forget_archive.call_count, 1)

    def test_volume_sibling_skipped_by_already_add_is_not_left_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            volumes = [
                os.path.join(tmp, 'album.7z.001'),
                os.path.join(tmp, 'album.7z.002'),
            ]
            for path in volumes:
                with open(path, 'wb') as handle:
                    handle.write(b'volume')
                archive = Archive(path)
                self.state.timelines.append(
                    Timeline(archive, 'create_timeline', archive),
                )

            def find_zip(source, passwords, delete_after, already, items, **_kwargs):
                if os.path.normcase(source) in {
                    os.path.normcase(path) for path in already
                }:
                    return False
                items.append(
                    Zip(volumes[0], passwords, delete_after, volumes=list(volumes)),
                )
                already.extend(volumes)
                return True

            dependencies = self._dependencies(find_zip)
            added = self.scanner.scan(
                passwords=[],
                delete_after_unzip=False,
                dependencies=dependencies,
            )

        self.assertEqual(added, 1)
        self.assertEqual(len(self.state.timelines), 1)
        self.assertEqual(
            self.state.timelines[0].get_current_record().ops,
            'find_zip',
        )

    def test_failed_volume_group_creates_one_retry_task_without_sibling_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            volumes = [
                os.path.join(tmp, 'locked.7z.001'),
                os.path.join(tmp, 'locked.7z.002'),
            ]
            for path in volumes:
                with open(path, 'wb') as handle:
                    handle.write(b'volume')
                archive = Archive(path)
                self.state.timelines.append(
                    Timeline(archive, 'create_timeline', archive),
                )

            def find_zip(source, passwords, delete_after, already, _items, **kwargs):
                if os.path.normcase(source) in {
                    os.path.normcase(path) for path in already
                }:
                    return False
                kwargs['unresolved_list'].append(
                    Zip(volumes[0], passwords, delete_after, volumes=list(volumes)),
                )
                already.extend(volumes)
                return False

            added = self.scanner.scan(
                passwords=[],
                delete_after_unzip=False,
                dependencies=self._dependencies(find_zip),
            )

        self.assertEqual(added, 1)
        self.assertEqual(len(self.state.timelines), 1)
        self.assertEqual(
            self.state.timelines[0].get_current_record().ops,
            'unzip_failed',
        )

    def test_new_volume_sibling_of_existing_task_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            volumes = [
                os.path.join(tmp, 'album.7z.001'),
                os.path.join(tmp, 'album.7z.002'),
            ]
            for path in volumes:
                with open(path, 'wb') as handle:
                    handle.write(b'volume')

            first_source = Archive(volumes[0])
            grouped = Zip(volumes[0], [], volumes=list(volumes))
            existing = Timeline(first_source, 'find_zip', grouped)
            sibling_source = Archive(volumes[1])
            sibling = Timeline(
                sibling_source,
                'create_timeline',
                sibling_source,
            )
            self.state.timelines[:] = [existing, sibling]

            dependencies = self._dependencies(
                lambda *_args, **_kwargs: False,
            )
            added = self.scanner.scan(
                passwords=[],
                delete_after_unzip=False,
                dependencies=dependencies,
            )

        self.assertEqual(added, 0)
        self.assertEqual(self.state.timelines, [existing])
        dependencies.logger.warning.assert_not_called()

    def test_queue_lookup_and_scan_history_use_context_state(self):
        first = Archive(os.path.join('root', 'input'))
        first_zip = Zip(
            os.path.join('root', 'album.7z.001'),
            [],
            volumes=[
                os.path.join('root', 'album.7z.001'),
                os.path.join('root', 'album.7z.002'),
            ],
        )
        self.state.timelines.append(Timeline(first, 'find_zip', first_zip))

        self.assertTrue(self.scanner.is_source_path_queued(first.path))
        self.assertTrue(self.scanner.is_archive_path_queued(first_zip.path))
        self.assertEqual(self.scanner.collect_already_add(), first_zip.volumes)

        failed = Timeline(first, 'unzip_failed', first_zip)
        self.state.timelines[:] = [failed]
        self.assertFalse(self.scanner.is_archive_path_queued(first_zip.path))


if __name__ == '__main__':
    unittest.main()
