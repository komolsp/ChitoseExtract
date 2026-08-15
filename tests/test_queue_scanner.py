import os
import tempfile
import unittest
from unittest import mock

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
