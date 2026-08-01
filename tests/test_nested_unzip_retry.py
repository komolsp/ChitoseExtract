import os
import tempfile
import unittest
from unittest import mock

import archive_registry
import config
import task_runner
from timeline import Archive, Record, Timeline
from zip import Zip


class NestedUnzipRetryTests(unittest.TestCase):
    def setUp(self):
        archive_registry.clear()
        task_runner.timelines.clear()
        task_runner.already_add.clear()
        task_runner._work_roots.clear()
        task_runner.conf = config.Config()
        task_runner.unzipper = mock.MagicMock()
        task_runner.unzipper.work_root_has_valid_inner_archive.return_value = False
        task_runner.logger = mock.MagicMock()
        task_runner.passwords = []

    def test_incremental_scan_roots_only_include_changed_top_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            stable_dir = os.path.join(tmp, 'stable')
            new_dir = os.path.join(tmp, 'new_layer')
            os.makedirs(stable_dir)
            with open(os.path.join(stable_dir, 'audio.wav'), 'wb') as fh:
                fh.write(b'audio')

            before = task_runner._snapshot_scan_tree(tmp)
            os.makedirs(new_dir)
            with open(os.path.join(new_dir, 'inner.zip'), 'wb') as fh:
                fh.write(b'PK\x03\x04')
            direct = os.path.join(tmp, 'next.7z')
            with open(direct, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c')

            roots = task_runner._incremental_scan_roots(tmp, before)

            self.assertEqual(
                {os.path.normcase(path) for path in roots},
                {os.path.normcase(new_dir), os.path.normcase(direct)},
            )

    def test_incremental_scan_detects_replaced_existing_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = os.path.join(tmp, 'inner.zip')
            with open(archive, 'wb') as fh:
                fh.write(b'old')
            before = task_runner._snapshot_scan_tree(tmp)
            with open(archive, 'wb') as fh:
                fh.write(b'PK\x03\x04-new-content')

            roots = task_runner._incremental_scan_roots(tmp, before)

            self.assertEqual(
                [os.path.normcase(path) for path in roots],
                [os.path.normcase(archive)],
            )

    def test_should_resume_nested_only_when_work_dir_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'outer.7z')
            work_root = os.path.join(tmp, 'outer')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'pk')
            with open(os.path.join(work_root, 'inner.zip'), 'wb') as fh:
                fh.write(b'pk')

            zip_obj = Zip(outer_path, [], False)
            self.assertTrue(task_runner._should_resume_nested_only(zip_obj))

    def test_nested_inner_not_treated_as_outer_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            inner_path = os.path.join(tmp, 'work', 'inner.zip')
            os.makedirs(os.path.dirname(inner_path))
            with open(inner_path, 'wb') as fh:
                fh.write(b'pk')
            task_runner._register_work_root(os.path.join(tmp, 'work'))

            zip_obj = Zip(inner_path, [], False)
            self.assertFalse(task_runner._should_resume_nested_only(zip_obj))

    def test_mark_timeline_unzipped_layers_from_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'outer.7z')
            with open(outer_path, 'wb') as fh:
                fh.write(b'pk')
            outer = Zip(outer_path, [], False)
            work_root = os.path.join(tmp, 'outer')
            os.makedirs(work_root)

            timeline = Timeline(Archive(outer_path), 'find_zip', outer)
            timeline.add_record(Record(outer, 'unzip', Archive(work_root)))

            task_runner._mark_timeline_unzipped_layers(timeline)
            self.assertTrue(archive_registry.is_unzipped(outer_path))

    def test_cleanup_failed_inner_preserves_work_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'album')
            os.makedirs(work_root)
            marker = os.path.join(work_root, 'keep.txt')
            with open(marker, 'w', encoding='utf-8') as fh:
                fh.write('stay')
            inner_path = os.path.join(work_root, 'inner.zip')
            with open(inner_path, 'wb') as fh:
                fh.write(b'pk')
            task_runner._register_work_root(work_root)

            zip_obj = Zip(inner_path, [], False)
            task_runner._cleanup_failed_unzip_output(work_root, zip_obj)
            self.assertTrue(os.path.isfile(marker))

    def test_cleanup_preserves_preexisting_subfolder_without_work_root(self):
        """外层已解压出 inner/ 目录时，内层密码失败不得删除该目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'album')
            inner_dir = os.path.join(work_root, 'inner')
            os.makedirs(inner_dir)
            marker = os.path.join(inner_dir, 'track.wav')
            with open(marker, 'wb') as fh:
                fh.write(b'RIFF')
            inner_zip = os.path.join(work_root, 'inner.zip')
            with open(inner_zip, 'wb') as fh:
                fh.write(b'pk')

            zip_obj = Zip(inner_zip, [], False)
            task_runner._cleanup_failed_unzip_output(
                inner_dir,
                zip_obj,
                existed_before=True,
            )
            self.assertTrue(os.path.isfile(marker))

    def test_cleanup_removes_new_staging_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'album')
            os.makedirs(work_root)
            inner_zip = os.path.join(work_root, 'inner.zip')
            with open(inner_zip, 'wb') as fh:
                fh.write(b'pk')
            staging = os.path.join(work_root, '.pk_inner.zip')
            os.makedirs(staging)
            partial = os.path.join(staging, 'partial.txt')
            with open(partial, 'w', encoding='utf-8') as fh:
                fh.write('x')
            task_runner._register_work_root(work_root)

            zip_obj = Zip(inner_zip, [], False)
            task_runner._cleanup_failed_unzip_output(
                staging,
                zip_obj,
                existed_before=False,
            )
            self.assertFalse(os.path.exists(staging))

    def test_cleanup_removes_new_dedicated_subfolder_for_top_level_only(self):
        """顶层压缩包失败时，仍可清理本次新建的专属输出目录。"""
        with tempfile.TemporaryDirectory() as tmp:
            top_zip = os.path.join(tmp, 'pack.zip')
            with open(top_zip, 'wb') as fh:
                fh.write(b'pk')
            dedicated = os.path.join(tmp, 'pack')
            os.makedirs(dedicated)
            partial = os.path.join(dedicated, 'partial.txt')
            with open(partial, 'w', encoding='utf-8') as fh:
                fh.write('x')

            zip_obj = Zip(top_zip, [], False)
            task_runner._cleanup_failed_unzip_output(
                dedicated,
                zip_obj,
                existed_before=False,
                timeline=None,
            )
            self.assertFalse(os.path.exists(dedicated))

    def test_nested_inner_failure_preserves_dedicated_subfolder(self):
        """套娃内层失败时，不得删除非暂存的专属子目录（可能含外层内容）。"""
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'album')
            os.makedirs(work_root)
            inner_zip = os.path.join(work_root, 'pack.zip')
            with open(inner_zip, 'wb') as fh:
                fh.write(b'pk')
            dedicated = os.path.join(work_root, 'pack')
            os.makedirs(dedicated)
            marker = os.path.join(dedicated, 'outer_track.wav')
            with open(marker, 'wb') as fh:
                fh.write(b'RIFF')
            task_runner._register_work_root(work_root)

            zip_obj = Zip(inner_zip, [], False)
            task_runner._cleanup_failed_unzip_output(
                dedicated,
                zip_obj,
                existed_before=False,
            )
            self.assertTrue(os.path.isfile(marker))

    def test_unzip_inner_failure_preserves_outer_extracted_files(self):
        """模拟外层已解压、内层密码失败：作品目录内容必须保留。"""
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'album')
            os.makedirs(work_root)
            outer_marker = os.path.join(work_root, 'outer_track.wav')
            with open(outer_marker, 'wb') as fh:
                fh.write(b'RIFF')
            inner_path = os.path.join(work_root, 'inner.zip')
            with open(inner_path, 'wb') as fh:
                fh.write(b'pk')

            outer = Zip(os.path.join(tmp, 'album.7z'), [], False)
            inner = Zip(inner_path, ['wrong'], False)
            inner.compression_ratio_info = {'encrypted': True}
            timeline = Timeline(Archive(os.path.join(tmp, 'album.7z')), 'find_zip', outer)
            timeline.add_record(Record(outer, 'unzip', Archive(work_root)))
            timeline.add_record(Record(Archive(work_root), 'unnest', Archive(work_root)))
            timeline.add_record(Record(Archive(work_root), 'find_zip', inner))

            task_runner._register_work_root(work_root)
            task_runner.unzipper.unzip.return_value = None

            result = task_runner.unzip(timeline)
            self.assertIsNone(result)
            self.assertTrue(os.path.isfile(outer_marker))
            self.assertTrue(os.path.isfile(inner_path))

    def test_nested_resolve_always_uses_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'album')
            os.makedirs(work_root)
            inner_path = os.path.join(work_root, 'inner.zip')
            with open(inner_path, 'wb') as fh:
                fh.write(b'pk')
            task_runner._register_work_root(work_root)

            inner = Zip(inner_path, [], False)
            output_path, merge_mode = task_runner._resolve_unzip_output_path(inner)
            self.assertTrue(task_runner._is_staging_unzip_path(output_path))
            self.assertTrue(merge_mode)

    def test_cleanup_preserves_father_when_other_files_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'album')
            os.makedirs(work_root)
            marker = os.path.join(work_root, 'readme.txt')
            with open(marker, 'w', encoding='utf-8') as fh:
                fh.write('outer')
            inner_zip = os.path.join(work_root, 'inner.zip')
            with open(inner_zip, 'wb') as fh:
                fh.write(b'pk')

            zip_obj = Zip(inner_zip, [], False)
            task_runner._cleanup_failed_unzip_output(
                work_root,
                zip_obj,
                existed_before=True,
            )
            self.assertTrue(os.path.isfile(marker))
            self.assertTrue(os.path.isfile(inner_zip))

    def test_should_resume_when_outer_zip_removed_but_work_root_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'outer.7z')
            work_root = os.path.join(tmp, 'outer')
            os.makedirs(work_root)
            with open(os.path.join(work_root, 'track.wav'), 'wb') as fh:
                fh.write(b'RIFF')
            outer = Zip(outer_path, [], False)
            self.assertTrue(task_runner._should_resume_nested_only(outer))

    def test_requeue_skips_false_positive_nested_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_path = os.path.join(tmp, 'work', 'track.bin')
            os.makedirs(os.path.dirname(fake_path))
            with open(fake_path, 'wb') as fh:
                fh.write(b'not-an-archive')
            task_runner._register_work_root(os.path.join(tmp, 'work'))
            fake = Zip(fake_path, [], False)
            timeline = Timeline(Archive(fake_path), 'find_zip', fake)
            timeline.add_record(Record(fake, 'unzip_failed', fake))
            self.assertFalse(task_runner.requeue_unzip_failure(timeline))
            self.assertEqual(timeline.get_current_record().ops, 'unzip_failed')

    def test_process_unzip_skips_outer_when_work_root_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album')
            os.makedirs(work_root)
            with open(os.path.join(work_root, 'keep.wav'), 'wb') as fh:
                fh.write(b'RIFF')
            with open(os.path.join(work_root, 'inner.zip'), 'wb') as fh:
                fh.write(b'pk')
            outer = Zip(outer_path, [], False)
            inner = Zip(os.path.join(work_root, 'inner.zip'), [], False)
            timeline = Timeline(Archive(outer_path), 'find_zip', outer)

            def fake_find_zip(path, passwords, delete_after, already_add, zip_list, **kwargs):
                if path == work_root:
                    zip_list.append(inner)

            task_runner.unzipper.find_zip.side_effect = fake_find_zip
            task_runner._process_unzip_timeline(timeline)
            self.assertTrue(os.path.isfile(os.path.join(work_root, 'keep.wav')))
            task_runner.unzipper.unzip.assert_not_called()
            task_runner.unzipper.find_zip.assert_called()
            last_ops = timeline.get_current_record().ops
            self.assertIn(last_ops, ('find_zip', 'unnest'))

    def test_discovered_inner_rescanned_at_nested_depth(self):
        """已发现但未解压的内层，套娃扫描时应重新入队。"""
        import file_ops
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'outer')
            os.makedirs(work_root)
            inner_path = os.path.join(work_root, 'inner.zip')
            with open(inner_path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 64)
            archive_registry.note_discovered(inner_path)
            self.assertFalse(archive_registry.is_unzipped(inner_path))

            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper.load_namelist = mock.MagicMock(return_value=True)
            zip_list: list = []
            unzipper.find_zip(
                work_root, [''], False, [], zip_list,
                depth=1, collect_unresolved=False,
                unresolved_limit=task_runner.NESTED_UNRESOLVED_LIMIT,
            )
            self.assertEqual(len(zip_list), 1)
            self.assertEqual(
                os.path.normcase(zip_list[0].path),
                os.path.normcase(inner_path),
            )

    def test_enqueue_nested_resumes_inner_from_timeline_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            work_root = os.path.join(tmp, 'album')
            os.makedirs(work_root)
            inner_path = os.path.join(work_root, 'inner.zip')
            with open(inner_path, 'wb') as fh:
                fh.write(b'pk')
            inner = Zip(inner_path, ['bad'], False)
            inner.compression_ratio_info = {'encrypted': True}
            outer = Zip(os.path.join(tmp, 'album.7z'), [], False)
            timeline = Timeline(Archive(os.path.join(tmp, 'album.7z')), 'find_zip', outer)
            timeline.add_record(Record(inner, 'unzip_failed', inner))
            task_runner._register_work_root(work_root)

            def empty_find_zip(*args, **kwargs):
                return False

            task_runner.unzipper.find_zip.side_effect = empty_find_zip
            task_runner._enqueue_nested_archives(timeline, work_root, None)
            self.assertEqual(timeline.get_current_record().ops, 'find_zip')
            self.assertEqual(timeline.get_current_record().output_file.path, inner_path)

    def test_inner_recognized_without_work_root_registry(self):
        """未登记 work_root 时，也应把作品目录内的压缩包识别为内层。"""
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album')
            inner_path = os.path.join(work_root, 'inner.zip')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            with open(inner_path, 'wb') as fh:
                fh.write(b'pk')
            archive_registry.mark_unzipped(outer_path)
            inner = Zip(inner_path, [], False)
            self.assertTrue(task_runner._is_nested_archive(inner))

    def test_promote_outer_timeline_to_inner(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album')
            inner_path = os.path.join(work_root, 'inner.zip')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            with open(inner_path, 'wb') as fh:
                fh.write(b'pk')
            archive_registry.mark_unzipped(outer_path)
            archive_registry.note_discovered(inner_path)
            outer = Zip(outer_path, [], False)
            inner = Zip(inner_path, ['bad'], False)
            inner.compression_ratio_info = {'encrypted': True}
            timeline = Timeline(Archive(outer_path), 'find_zip', outer)

            def empty_find_zip(*args, **kwargs):
                return False

            task_runner.unzipper.find_zip.side_effect = empty_find_zip
            self.assertTrue(task_runner._promote_outer_timeline_to_inner(timeline))
            self.assertEqual(timeline.get_current_record().ops, 'find_zip')
            self.assertEqual(
                os.path.normcase(timeline.get_current_record().output_file.path),
                os.path.normcase(inner_path),
            )

    def test_timeline_input_label_shows_nested_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album')
            inner_path = os.path.join(work_root, 'inner.zip')
            os.makedirs(work_root)
            outer = Zip(outer_path, [], False)
            inner = Zip(inner_path, ['bad'], False)
            timeline = Timeline(Archive(outer_path), 'find_zip', outer)
            timeline.add_record(Record(inner, 'unzip_failed', inner))
            task_runner._register_work_root(work_root)
            label = task_runner._timeline_input_label(timeline)
            self.assertIn('album.7z', label)
            self.assertIn('inner.zip', label)

    def test_outer_archive_not_classified_as_nested(self):
        """album.7z 与 album_pk/ 并列时，外层不得被误判为内层。"""
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album_pk')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            with open(os.path.join(work_root, 'track.wav'), 'wb') as fh:
                fh.write(b'RIFF')
            outer = Zip(outer_path, [], False)
            self.assertFalse(task_runner._is_nested_archive(outer))
            self.assertTrue(task_runner._should_resume_nested_only(outer))

    def test_inner_zip_inside_work_root_is_nested(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album')
            inner_path = os.path.join(work_root, 'inner.zip')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            with open(inner_path, 'wb') as fh:
                fh.write(b'pk')
            archive_registry.mark_unzipped(outer_path)
            inner = Zip(inner_path, [], False)
            self.assertTrue(task_runner._is_nested_archive(inner))

    def test_advance_past_outer_stops_find_zip_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            with open(os.path.join(work_root, 'keep.txt'), 'w', encoding='utf-8') as fh:
                fh.write('x')
            outer = Zip(outer_path, [], False)
            timeline = Timeline(Archive(outer_path), 'find_zip', outer)
            self.assertTrue(task_runner._advance_past_outer_layer(timeline, outer, work_root))
            self.assertEqual(timeline.get_current_record().ops, 'unnest')
            self.assertTrue(archive_registry.is_unzipped(outer_path))

    def test_normalize_nested_scan_root_from_outer_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album_pk')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            outer = Zip(outer_path, [], False)
            archive_registry.mark_unzipped(outer_path)
            resolved = task_runner._normalize_nested_scan_root(outer_path, outer)
            self.assertEqual(
                os.path.normcase(resolved or ''),
                os.path.normcase(work_root),
            )

    def test_recover_outer_with_pending_inner(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album')
            inner_path = os.path.join(work_root, 'inner.zip')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            with open(inner_path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 32)
            outer = Zip(outer_path, [], False)
            timeline = Timeline(Archive(outer_path), 'find_zip', outer)

            def empty_find_zip(*args, **kwargs):
                return False

            task_runner.unzipper.find_zip.side_effect = empty_find_zip
            task_runner.unzipper.work_root_has_valid_inner_archive.return_value = True
            self.assertTrue(task_runner._recover_outer_with_pending_inner(timeline, outer))
            self.assertEqual(timeline.get_current_record().ops, 'find_zip')
            self.assertEqual(
                os.path.normcase(timeline.get_current_record().output_file.path),
                os.path.normcase(inner_path),
            )

    def test_top_level_outer_uses_staging_and_pk_work_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            outer = Zip(outer_path, [], False)
            output_path, merge_mode = task_runner._resolve_unzip_output_path(outer)
            self.assertEqual(merge_mode, 'top_staging')
            self.assertTrue(task_runner._is_staging_unzip_path(output_path))
            self.assertEqual(
                os.path.basename(task_runner._intended_top_work_root(outer)),
                'album_pk',
            )

    def test_work_root_path_resolves_album_folder_for_archive_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album_pk')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            resolved = task_runner._work_root_path(outer_path, allow_external=True)
            self.assertEqual(
                os.path.normcase(resolved or ''),
                os.path.normcase(work_root),
            )

    def test_work_root_path_falls_back_to_legacy_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            resolved = task_runner._work_root_path(outer_path, allow_external=True)
            self.assertEqual(
                os.path.normcase(resolved or ''),
                os.path.normcase(work_root),
            )

    def test_cleanup_preserves_staging_with_valid_inner_zip(self):
        """外层部分解压后暂存里内层可正常打开时，失败清理应保留。"""
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            staging = os.path.join(tmp, '.pk_album.7z')
            os.makedirs(staging)
            inner_path = os.path.join(staging, 'inner.zip')
            with open(inner_path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 32)

            zip_obj = Zip(outer_path, [], False)
            task_runner.unzipper.work_root_has_valid_inner_archive.return_value = True
            task_runner._cleanup_failed_unzip_output(
                staging,
                zip_obj,
                existed_before=False,
            )
            self.assertTrue(os.path.isfile(inner_path))

    def test_cleanup_removes_staging_with_fake_inner_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            staging = os.path.join(tmp, '.pk_album.7z')
            os.makedirs(staging)
            inner_path = os.path.join(staging, 'inner.zip')
            with open(inner_path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 32)

            zip_obj = Zip(outer_path, [], False)
            task_runner.unzipper.work_root_has_valid_inner_archive.return_value = False
            task_runner._cleanup_failed_unzip_output(
                staging,
                zip_obj,
                existed_before=False,
            )
            self.assertFalse(os.path.exists(staging))

    def test_should_resume_when_staging_has_inner_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            staging = os.path.join(tmp, '.pk_album.7z')
            os.makedirs(staging)
            with open(os.path.join(staging, 'inner.zip'), 'wb') as fh:
                fh.write(b'pk')
            outer = Zip(outer_path, [], False)
            task_runner.unzipper.work_root_has_valid_inner_archive.return_value = True
            self.assertTrue(task_runner._should_resume_nested_only(outer))

    def test_encrypted_unzip_uses_password_path_not_collision(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            output_path = os.path.join(tmp, 'out')
            os.makedirs(output_path)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 16)

            zip_obj = Zip(outer_path, ['secret'], False)
            zip_obj.file_list = ['inner.zip']
            zip_obj.compression_ratio_info = {'encrypted': True}
            zip_obj.mark_namelist_scanned('secret')

            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper.password_collision = mock.MagicMock()
            unzipper._resolve_encrypted_password = mock.MagicMock(return_value=True)
            unzipper.single_threaded_unzip = mock.MagicMock(return_value=True)

            result = unzipper.unzip(zip_obj, output_path, 200, 0.5)
            unzipper.password_collision.assert_not_called()
            self.assertEqual(result, output_path)

    def test_requeue_outer_failure_with_partial_inner_promotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'album.7z')
            work_root = os.path.join(tmp, 'album')
            inner_path = os.path.join(work_root, 'inner.zip')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z')
            with open(inner_path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 32)
            outer = Zip(outer_path, [], False)
            timeline = Timeline(Archive(outer_path), 'find_zip', outer)
            timeline.add_record(Record(outer, 'unzip_failed', outer))

            def empty_find_zip(*args, **kwargs):
                return False

            task_runner.unzipper.find_zip.side_effect = empty_find_zip
            self.assertTrue(task_runner.requeue_unzip_failure(timeline))
            self.assertEqual(timeline.get_current_record().ops, 'find_zip')
            self.assertEqual(
                os.path.normcase(timeline.get_current_record().output_file.path),
                os.path.normcase(inner_path),
            )

    def test_filter_already_extracted_scans_inner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'outer.7z')
            work_root = os.path.join(tmp, 'outer')
            os.makedirs(work_root)
            with open(outer_path, 'wb') as fh:
                fh.write(b'pk')
            with open(os.path.join(work_root, 'inner.zip'), 'wb') as fh:
                fh.write(b'pk')
            outer = Zip(outer_path, [], False)
            inner = Zip(os.path.join(work_root, 'inner.zip'), [], False)

            def fake_find_zip(path, passwords, delete_after, already_add, zip_list, **kwargs):
                if path == work_root:
                    zip_list.append(inner)

            task_runner.unzipper.find_zip.side_effect = fake_find_zip

            result = task_runner._filter_already_extracted_archives(
                [outer], [], [],
            )
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].path, inner.path)
            self.assertTrue(archive_registry.is_unzipped(outer_path))

    def test_filter_volume_sibling_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            vols = [
                os.path.join(tmp, 'foo.part1'),
                os.path.join(tmp, 'foo.part2'),
                os.path.join(tmp, 'foo.part3'),
            ]
            head = Zip(vols[0], [], False)
            head.volumes = vols
            tail = Zip(vols[2], [], False)
            tail.volumes = [vols[2]]
            filtered = task_runner._filter_volume_sibling_unresolved([head], [tail])
            self.assertEqual(filtered, [])

    def test_dismiss_volume_sibling_failures_after_head_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            vols = [
                os.path.join(tmp, 'foo.part1'),
                os.path.join(tmp, 'foo.part2'),
                os.path.join(tmp, 'foo.part3'),
            ]
            source = tmp
            head = Zip(vols[0], [], False)
            head.volumes = vols
            tail = Zip(vols[2], [], False)
            tail.volumes = [vols[2]]
            task_runner.timelines.append(Timeline(Archive(source), 'unzip_failed', tail))
            archive_registry.mark_unzipped(head.path, head.volumes)
            removed = task_runner._dismiss_volume_sibling_failures(head, source)
            self.assertEqual(removed, 1)
            self.assertEqual(task_runner.timelines, [])

    def test_prune_unzipped_volume_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            vols = [
                os.path.join(tmp, 'bar.7z.001'),
                os.path.join(tmp, 'bar.7z.002'),
            ]
            for path in vols:
                with open(path, 'wb') as fh:
                    fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            tail = Zip(vols[1], [], False)
            archive_registry.mark_unzipped(vols[0], vols)
            task_runner.timelines.append(Timeline(Archive(tmp), 'unzip_failed', tail))
            pruned = task_runner._prune_unzipped_volume_failures()
            self.assertEqual(pruned, 1)
            self.assertEqual(task_runner.timelines, [])


if __name__ == '__main__':
    unittest.main()
