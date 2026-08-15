import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import task_runner
from timeline import Archive, Timeline


class AudioStepStatusTests(unittest.TestCase):
    def setUp(self):
        self.previous = {
            'conf': task_runner.conf,
            'logger': task_runner.logger,
            'progress_ui': task_runner.progress_ui,
        }
        task_runner.logger = mock.MagicMock()
        task_runner.progress_ui = mock.MagicMock()
        task_runner.timelines.clear()

    def tearDown(self):
        task_runner.timelines.clear()
        for name, value in self.previous.items():
            setattr(task_runner, name, value)

    def test_partial_conversion_is_recorded_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'RJ123456')
            os.makedirs(root)
            timeline = Timeline(Archive(root), 'rename', Archive(root))
            task_runner.conf = SimpleNamespace(audio_convert_config={
                'source_extensions': ['.wav'],
                'flac_path': '',
                'ffmpeg_fallback_path': '',
            })

            with mock.patch.object(
                task_runner, '_rename_root_path', return_value=root,
            ), mock.patch.object(
                task_runner.audio_convert,
                'find_convertible_files',
                return_value=['a.wav', 'b.wav'],
            ), mock.patch.object(
                task_runner.audio_convert, 'resolve_flac', return_value='flac.exe',
            ), mock.patch.object(
                task_runner.audio_convert,
                'resolve_ffmpeg_fallback',
                return_value='ffmpeg.exe',
            ), mock.patch.object(
                task_runner.audio_convert, 'convert_work_folder', return_value=(0, 2),
            ):
                self.assertIsNone(task_runner.convert_audio(timeline))

        self.assertEqual(timeline.get_current_record().ops, 'convert_audio_failed')
        self.assertTrue(task_runner._timeline_step_failed(timeline))

    def test_partial_tagging_is_recorded_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'RJ123456')
            os.makedirs(root)
            timeline = Timeline(Archive(root), 'rename', Archive(root))
            task_runner.conf = SimpleNamespace(
                audio_tag_config={'embed_cover': False},
                renamer_config={},
            )
            scraper = mock.MagicMock()
            scraper.scrape_metadata.return_value = {
                'work_name': '作品',
                'cover_url': '',
            }

            with mock.patch.object(
                task_runner, '_rename_root_path', return_value=root,
            ), mock.patch.object(
                task_runner, '_resolve_rj_for_timeline_root', return_value='RJ123456',
            ), mock.patch.object(
                task_runner, 'get_shared_scraper', return_value=scraper,
            ), mock.patch.object(
                task_runner.audio_tagger, 'tag_work_folder', return_value=(0, 2),
            ):
                self.assertIsNone(task_runner.tag_audio(timeline))

        self.assertEqual(timeline.get_current_record().ops, 'tag_audio_failed')
        self.assertTrue(task_runner._timeline_step_failed(timeline))

    def test_missing_rj_is_recorded_as_tagging_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            timeline = Timeline(Archive(tmp), 'rename', Archive(tmp))
            task_runner.conf = SimpleNamespace(
                audio_tag_config={'embed_cover': False},
                renamer_config={},
            )
            with mock.patch.object(
                task_runner, '_rename_root_path', return_value=tmp,
            ), mock.patch.object(
                task_runner, '_resolve_rj_for_timeline_root', return_value=None,
            ):
                self.assertIsNone(task_runner.tag_audio(timeline))

        self.assertEqual(timeline.get_current_record().ops, 'tag_audio_failed')

    def test_missing_metadata_is_recorded_as_tagging_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            timeline = Timeline(Archive(tmp), 'rename', Archive(tmp))
            task_runner.conf = SimpleNamespace(
                audio_tag_config={'embed_cover': False},
                renamer_config={},
            )
            scraper = mock.MagicMock()
            scraper.scrape_metadata.return_value = None
            with mock.patch.object(
                task_runner, '_rename_root_path', return_value=tmp,
            ), mock.patch.object(
                task_runner, '_resolve_rj_for_timeline_root', return_value='RJ123456',
            ), mock.patch.object(
                task_runner, 'get_shared_scraper', return_value=scraper,
            ):
                self.assertIsNone(task_runner.tag_audio(timeline))

        self.assertEqual(timeline.get_current_record().ops, 'tag_audio_failed')

    def test_audio_loops_retry_their_own_failure_state(self):
        cases = (
            ('convert_audio_failed', 'convert_audio', 'convert_audio_loop', 'convert_audio'),
            ('tag_audio_failed', 'tag_audio', 'tag_audio_loop', 'tag_audio'),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, 'RJ123456')
            os.makedirs(root)

            for failed_op, success_op, loop_name, action_name in cases:
                with self.subTest(failed_op=failed_op):
                    timeline = Timeline(Archive(root), failed_op, Archive(root))
                    task_runner.timelines[:] = [timeline]

                    def mark_success(current, *, ops=success_op):
                        task_runner._append_step_record(current, ops)
                        return root

                    with mock.patch.object(
                        task_runner, '_start_audio_disk_monitor', return_value=None,
                    ), mock.patch.object(
                        task_runner, '_stop_disk_monitor',
                    ), mock.patch.object(
                        task_runner, '_rename_root_path', return_value=root,
                    ), mock.patch.object(
                        task_runner, action_name, side_effect=mark_success,
                    ) as action:
                        getattr(task_runner, loop_name)()

                    action.assert_called_once_with(timeline)
                    self.assertEqual(timeline.get_current_record().ops, success_op)


if __name__ == '__main__':
    unittest.main()
