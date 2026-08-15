"""分卷识别单元测试。"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from volume import parse, resolve_volume_archives
from volume.resolver import VolumeResolver, clear_index_cache


class VolumeResolveTest(unittest.TestCase):

    def setUp(self):
        clear_index_cache()

    def _tmpdir(self):
        return tempfile.mkdtemp(prefix='vol_test_')

    def test_disguised_zip_mac(self):
        d = self._tmpdir()
        try:
            for name in ['MAC.0删01', 'MAC.00删2', 'MAC.00删3']:
                data = b'PK\x03\x04' + b'\x00' * 100 if '01' in name else b'\x00' * 100
                open(os.path.join(d, name), 'wb').write(data)
            vols = resolve_volume_archives(os.path.join(d, 'MAC.00删2'))
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names, ['MAC.zip.001', 'MAC.zip.002', 'MAC.zip.003'])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_7z_numeric_volumes_with_appended_pdf_suffix(self):
        d = self._tmpdir()
        try:
            names = [
                'quanquannNZK.7z.001.pdf',
                'quanquannNZK.7z.002.pdf',
            ]
            with open(os.path.join(d, names[0]), 'wb') as stream:
                stream.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 64)
            with open(os.path.join(d, names[1]), 'wb') as stream:
                stream.write(b'\x00' * 64)

            self.assertEqual(
                parse.parse_disguised_split(names[0]),
                ('quanquannNZK', 1),
            )
            self.assertTrue(
                VolumeResolver.is_volume_readonly(os.path.join(d, names[1])),
            )

            vols = resolve_volume_archives(os.path.join(d, names[1]))
            self.assertEqual(
                [os.path.basename(path) for path in vols],
                ['quanquannNZK.7z.001', 'quanquannNZK.7z.002'],
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_plain_pdfs_with_numeric_names_are_not_volumes(self):
        d = self._tmpdir()
        try:
            paths = [
                os.path.join(d, 'report.001.pdf'),
                os.path.join(d, 'report.002.pdf'),
            ]
            for path in paths:
                with open(path, 'wb') as stream:
                    stream.write(b'%PDF-1.7\n' + b'\x00' * 64)
            self.assertIsNone(resolve_volume_archives(paths[0]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_disguised_rar_mixed_naming(self):
        d = self._tmpdir()
        try:
            files = {
                '下载.吗1对': b'Rar!\x1a\x07\x00' + b'\x00' * 100,
                '下载.part2掉': b'\x00' * 100,
                '下载.删3': b'\x00' * 100,
            }
            for name, data in files.items():
                open(os.path.join(d, name), 'wb').write(data)
            vols = resolve_volume_archives(os.path.join(d, '下载.part2掉'))
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names, ['下载.part1', '下载.part2', '下载.part3'])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_rar_part_junk_suffix(self):
        d = self._tmpdir()
        try:
            names = ['cqyz.part1加个点rar', 'cqyz.part2加个点rar', 'cqyz.part3加个点rar']
            for i, name in enumerate(names):
                open(os.path.join(d, name), 'wb').write(
                    b'Rar!\x1a\x07\x00' + b'\x00' * 50 if i == 0 else b'\x00' * 50,
                )
            vols = resolve_volume_archives(os.path.join(d, names[1]))
            names_out = [os.path.basename(v) for v in vols]
            self.assertEqual(names_out, ['cqyz.part1', 'cqyz.part2', 'cqyz.part3'])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_rar_oldstyle_rar_r00(self):
        d = self._tmpdir()
        try:
            open(os.path.join(d, 'archive.rar'), 'wb').write(b'Rar!\x1a\x07\x00' + b'\x00' * 64)
            open(os.path.join(d, 'archive.r00'), 'wb').write(b'\x00' * 64)
            open(os.path.join(d, 'archive.r01'), 'wb').write(b'\x00' * 64)
            vols = resolve_volume_archives(os.path.join(d, 'archive.r00'))
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names, ['archive.rar', 'archive.r00', 'archive.r01'])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_classic_zip_z_parts_wait_for_main_zip(self):
        d = self._tmpdir()
        try:
            z01 = os.path.join(d, 'archive.z01')
            z02 = os.path.join(d, 'archive.z02')
            open(z01, 'wb').write(b'PK\x03\x04' + b'\x00' * 64)
            open(z02, 'wb').write(b'\x00' * 64)

            self.assertIsNone(resolve_volume_archives(z01))
            self.assertTrue(os.path.isfile(z01))
            self.assertTrue(os.path.isfile(z02))

            main_zip = os.path.join(d, 'archive.zip')
            open(main_zip, 'wb').write(b'\x00' * 64)
            clear_index_cache()
            volumes = resolve_volume_archives(z01)
            self.assertIsNotNone(volumes)
            self.assertEqual(
                [os.path.basename(path) for path in volumes],
                ['archive.zip', 'archive.z01', 'archive.z02'],
            )
            self.assertEqual(os.path.getsize(volumes[0]), 64)
            self.assertEqual(os.path.getsize(volumes[1]), 68)
            self.assertEqual(os.path.getsize(volumes[2]), 64)

            clear_index_cache()
            resolved_again = resolve_volume_archives(volumes[0])
            self.assertEqual(resolved_again, volumes)
            self.assertTrue(os.path.isfile(z01))
            self.assertTrue(os.path.isfile(z02))
            self.assertTrue(os.path.isfile(main_zip))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_classic_zip_with_disguised_main_restores_only_main_name(self):
        d = self._tmpdir()
        try:
            z01 = os.path.join(d, 'RJ01593274.z01')
            z02 = os.path.join(d, 'RJ01593274.z02')
            disguised_main = os.path.join(d, 'RJ01593274.zip删')
            with open(z01, 'wb') as stream:
                stream.write(b'PK\x07\x08PK\x03\x04' + b'\x00' * 64)
            with open(z02, 'wb') as stream:
                stream.write(b'\x00' * 64)
            with open(disguised_main, 'wb') as stream:
                stream.write(b'\x00' * 64)

            peeked = VolumeResolver.peek_volumes(z02)
            self.assertEqual(
                [os.path.basename(path) for path in peeked],
                ['RJ01593274.zip删', 'RJ01593274.z01', 'RJ01593274.z02'],
            )

            volumes = resolve_volume_archives(z02)
            self.assertEqual(
                [os.path.basename(path) for path in volumes],
                ['RJ01593274.zip', 'RJ01593274.z01', 'RJ01593274.z02'],
            )
            self.assertFalse(os.path.exists(disguised_main))
            self.assertTrue(all(os.path.isfile(path) for path in volumes))

            from volume.rename import restore_renamed_volumes
            restored = restore_renamed_volumes(volumes)
            self.assertEqual(
                [os.path.basename(path) for path in restored],
                ['RJ01593274.zip删', 'RJ01593274.z01', 'RJ01593274.z02'],
            )
            self.assertTrue(os.path.isfile(disguised_main))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_find_zip_queues_classic_zip_with_disguised_main(self):
        from unzipper import Unzipper

        d = self._tmpdir()
        try:
            for name, data in (
                ('RJ01593274.z01', b'PK\x07\x08PK\x03\x04' + b'\x00' * 64),
                ('RJ01593274.z02', b'\x00' * 64),
                ('RJ01593274.zip删', b'\x00' * 64),
            ):
                with open(os.path.join(d, name), 'wb') as stream:
                    stream.write(data)
            unzipper = Unzipper.__new__(Unzipper)
            unzipper.logger = mock.MagicMock()
            unzipper.load_namelist = mock.MagicMock(return_value=True)
            zip_list = []

            found = unzipper.find_zip(d, [''], False, [], zip_list)

            self.assertTrue(found)
            self.assertEqual(len(zip_list), 1)
            self.assertEqual(os.path.basename(zip_list[0].path), 'RJ01593274.zip')
            self.assertEqual(
                [os.path.basename(path) for path in zip_list[0].volumes],
                ['RJ01593274.zip', 'RJ01593274.z01', 'RJ01593274.z02'],
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_failed_volume_group_is_kept_once_for_password_retry(self):
        import archive_registry
        from unzipper import Unzipper

        d = self._tmpdir()
        archive_registry.clear()
        try:
            volumes = [
                os.path.join(d, 'locked.7z.001'),
                os.path.join(d, 'locked.7z.002'),
            ]
            for path in volumes:
                with open(path, 'wb') as stream:
                    stream.write(b'volume')
            unzipper = Unzipper.__new__(Unzipper)
            unzipper.logger = mock.MagicMock()
            unzipper.load_namelist = mock.MagicMock(return_value=False)
            already = []
            discovered = []
            unresolved = []

            with mock.patch(
                'unzipper.file_ops.resolve_volume_archives',
                return_value=volumes,
            ), mock.patch(
                'volume.resolver.is_complete_volume_group',
                return_value=True,
            ):
                found = unzipper.find_zip(
                    volumes[0], [], False, already, discovered,
                    unresolved_list=unresolved,
                    collect_unresolved=True,
                )
                unzipper.find_zip(
                    volumes[1], [], False, already, discovered,
                    unresolved_list=unresolved,
                    collect_unresolved=True,
                )

            self.assertFalse(found)
            self.assertEqual(discovered, [])
            self.assertEqual(len(unresolved), 1)
            self.assertEqual(unresolved[0].volumes, volumes)
            self.assertEqual(already, volumes)
        finally:
            archive_registry.clear()
            shutil.rmtree(d, ignore_errors=True)

    def test_clearing_queue_restores_pending_volume_names(self):
        import task_runner
        from volume.rename import clear_rename_registry, rename_volume

        d = self._tmpdir()
        clear_rename_registry()
        try:
            original = os.path.join(d, '拖入分卷名.奇1')
            normalized = os.path.join(d, '拖入分卷名.7z.001')
            with open(original, 'wb') as stream:
                stream.write(b'volume')
            self.assertTrue(rename_volume(original, normalized))
            self.assertFalse(os.path.exists(original))

            task_runner.clear()

            self.assertTrue(os.path.isfile(original))
            self.assertFalse(os.path.exists(normalized))
        finally:
            clear_rename_registry()
            shutil.rmtree(d, ignore_errors=True)

    def test_simple_numeric_zip(self):
        d = self._tmpdir()
        try:
            for name in ['FOO.001', 'FOO.002']:
                data = b'PK\x03\x04' + b'\x00' * 50 if name.endswith('001') else b'\x00' * 50
                open(os.path.join(d, name), 'wb').write(data)
            vols = resolve_volume_archives(os.path.join(d, 'FOO.002'))
            self.assertTrue(vols[0].endswith('FOO.zip.001'))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_single_part_not_volume(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'only.part2')
            open(path, 'wb').write(b'\x00' * 32)
            self.assertIsNone(resolve_volume_archives(path))
            self.assertFalse(VolumeResolver.is_volume(path))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_7z_split(self):
        d = self._tmpdir()
        try:
            open(os.path.join(d, '166.7z.001'), 'wb').write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            open(os.path.join(d, '166.7z.002'), 'wb').write(b'\x00' * 32)
            vols = resolve_volume_archives(os.path.join(d, '166.7z.002'))
            self.assertEqual(len(vols), 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_score_picks_larger_group(self):
        from volume import score

        group_a = ['/tmp/a.zip.001', '/tmp/a.zip.002']
        group_b = ['/tmp/a.zip.001', '/tmp/a.zip.002', '/tmp/a.zip.003']
        picked = score.pick_best_candidate([
            (score.score_volume_group(group_a), group_a),
            (score.score_volume_group(group_b), group_b),
        ])
        self.assertEqual(picked, group_b)

    def test_parse_missing_volume(self):
        from volume.probe import parse_missing_volume, find_file_for_expected

        msg = 'ERROR: Missing volume : 下载.吗2对\n'
        self.assertEqual(parse_missing_volume(msg), '下载.吗2对')

        d = self._tmpdir()
        try:
            path = os.path.join(d, '下载.吗2对')
            open(path, 'wb').write(b'\x00' * 32)
            found = find_file_for_expected(d, '下载.吗2对')
            self.assertEqual(found, path)
            found2 = find_file_for_expected(d, '下载.part2掉')
            self.assertEqual(found2, path)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_try_expand_volumes(self):
        from volume.probe import try_expand_volumes

        d = self._tmpdir()
        try:
            files = {
                '下载.吗1对': b'Rar!\x1a\x07\x00' + b'\x00' * 100,
                '下载.part2掉': b'\x00' * 100,
                '下载.删3': b'\x00' * 100,
            }
            for name, data in files.items():
                open(os.path.join(d, name), 'wb').write(data)
            partial = [
                os.path.join(d, '下载.吗1对'),
                os.path.join(d, '下载.part2掉'),
            ]
            expanded = try_expand_volumes(
                partial,
                'ERROR: Missing volume : 下载.吗2对\n',
            )
            self.assertIsNotNone(expanded)
            self.assertEqual(len(expanded), 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_legacy_no_orphan_rename(self):
        from volume.collect import collect_legacy_pattern

        d = self._tmpdir()
        try:
            open(os.path.join(d, '下载.part2掉'), 'wb').write(b'\x00' * 32)
            open(os.path.join(d, '下载.吗1对'), 'wb').write(b'Rar!\x1a\x07\x00' + b'\x00' * 32)
            open(os.path.join(d, '下载.删3'), 'wb').write(b'\x00' * 32)
            result = collect_legacy_pattern(d, os.path.join(d, '下载.part2掉'))
            self.assertIsNone(result)
            self.assertTrue(os.path.exists(os.path.join(d, '下载.part2掉')))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_partial_pre_renamed_part2(self):
        d = self._tmpdir()
        try:
            open(os.path.join(d, '下载.part2'), 'wb').write(b'\x00' * 64)
            open(os.path.join(d, '下载.吗1对'), 'wb').write(b'Rar!\x1a\x07\x00' + b'\x00' * 64)
            open(os.path.join(d, '下载.删3'), 'wb').write(b'\x00' * 64)
            clear_index_cache()
            vols = resolve_volume_archives(os.path.join(d, '下载.删3'))
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names, ['下载.part1', '下载.part2', '下载.part3'])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_stem_index_mixed(self):
        from volume.stem_index import collect_by_stem

        d = self._tmpdir()
        try:
            for name in ['下载.吗1对', '下载.part2掉', '下载.删3']:
                open(os.path.join(d, name), 'wb').write(b'\x00' * 32)
            cluster = collect_by_stem(d, os.path.join(d, '下载.part2掉'))
            self.assertEqual(len(cluster), 3)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_standalone_7z_not_volume_part(self):
        from volume import parse
        from volume.stem_index import build_stem_groups

        d = self._tmpdir()
        try:
            open(os.path.join(d, '123.1对吗'), 'wb').write(b'Rar!\x1a\x07\x00' + b'\x00' * 32)
            open(os.path.join(d, '123.part2嗯'), 'wb').write(b'\x00' * 32)
            open(os.path.join(d, '123.不3对'), 'wb').write(b'\x00' * 32)
            open(os.path.join(d, '123.7z'), 'wb').write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            self.assertIsNone(parse.parse_disguised_split('123.7z'))
            groups = build_stem_groups(d)
            self.assertIn('123', groups)
            self.assertEqual(set(groups['123'].keys()), {1, 2, 3})
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_incomplete_volume_group(self):
        from volume.resolver import is_complete_volume_group

        d = self._tmpdir()
        try:
            open(os.path.join(d, '123.part2'), 'wb').write(b'\x00' * 32)
            open(os.path.join(d, '123.part3'), 'wb').write(b'\x00' * 32)
            vols = [os.path.join(d, '123.part2'), os.path.join(d, '123.part3')]
            self.assertFalse(is_complete_volume_group(vols))
            open(os.path.join(d, '123.part1'), 'wb').write(b'Rar!\x1a\x07\x00' + b'\x00' * 32)
            vols.insert(0, os.path.join(d, '123.part1'))
            self.assertTrue(is_complete_volume_group(vols))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_rar_part_group_in_crowded_directory(self):
        from volume.resolver import VolumeResolver, clear_index_cache
        from volume.validate import accept_volume_group
        from volume.collect import collect_rar_part
        from file_ops import is_volume_zip, volume_zip_list, resolve_volume_archives

        d = self._tmpdir()
        try:
            name1 = '一小央泽 - 绯雪 [110P1V 2.7G].part1.rar'
            name2 = '一小央泽 - 绯雪 [110P1V 2.7G].part2.rar'
            p1 = os.path.join(d, name1)
            p2 = os.path.join(d, name2)
            open(p1, 'wb').write(b'Rar!\x1a\x07\x00' + b'\x00' * 32)
            open(p2, 'wb').write(b'\x00' * 32)
            for i in range(5):
                open(os.path.join(d, f'other{i}.part1.rar'), 'wb').write(
                    b'Rar!\x1a\x07\x00' + b'\x00' * 8,
                )
                open(os.path.join(d, f'other{i}.part2.rar'), 'wb').write(b'\x00' * 8)
            raw = collect_rar_part(d, p1)
            self.assertEqual(len(raw), 2)
            self.assertTrue(accept_volume_group(raw))
            clear_index_cache()
            self.assertTrue(is_volume_zip(p1))
            listed = volume_zip_list(p1)
            self.assertEqual(len(listed), 2)
            self.assertIn(p1, listed)
            self.assertIn(p2, listed)
            clear_index_cache()
            resolved = resolve_volume_archives(p1)
            self.assertIsNotNone(resolved)
            self.assertEqual(len(resolved), 2)
            peeked = VolumeResolver.peek_volumes(p1)
            self.assertEqual(len(peeked), 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_rar_oldstyle_skips_part_rar(self):
        from volume.parse import parse_rar_oldstyle, parse_rar_part

        name = 'album.part1.rar'
        self.assertIsNotNone(parse_rar_part(name))
        self.assertIsNone(parse_rar_oldstyle(name))

    def test_standard_volume_group(self):
        from volume.resolver import is_standard_volume_group

        d = self._tmpdir()
        try:
            standard = [
                os.path.join(d, 'foo.part1'),
                os.path.join(d, 'foo.part2'),
            ]
            self.assertTrue(is_standard_volume_group(standard))
            numeric = [
                os.path.join(d, 'bar.001'),
                os.path.join(d, 'bar.002'),
            ]
            self.assertTrue(is_standard_volume_group(numeric))
            disguised = [
                os.path.join(d, 'MAC.0删01'),
                os.path.join(d, 'MAC.00删2'),
            ]
            self.assertFalse(is_standard_volume_group(disguised))
            self.assertFalse(is_standard_volume_group([standard[0]]))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_zip_namelist_scan_cache(self):
        from zip import Zip

        d = self._tmpdir()
        try:
            path = os.path.join(d, 'test.zip')
            open(path, 'wb').write(b'PK\x03\x04' + b'\x00' * 32)
            z = Zip(path, password_list=['pw'])
            z.volumes = [path]
            z.file_list = ['a.txt']
            self.assertFalse(z.is_namelist_current())
            z.mark_namelist_scanned('pw')
            self.assertTrue(z.is_namelist_current())
            z.pw_list.insert(0, 'other')
            self.assertTrue(z.is_namelist_current())
            self.assertEqual(z.verified_password(), 'pw')
            z.invalidate_namelist_scan()
            self.assertFalse(z.is_namelist_current())
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_trailing_numeric_7z_group(self):
        d = self._tmpdir()
        try:
            for i in range(1, 6):
                data = b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32 if i == 1 else b'\x00' * 32
                open(os.path.join(d, f'测试{i}'), 'wb').write(data)
            vols = resolve_volume_archives(os.path.join(d, '测试3'))
            self.assertIsNotNone(vols)
            self.assertEqual(len(vols), 5)
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names[0], '测试.7z.001')
            self.assertEqual(names[4], '测试.7z.005')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_restore_original_names_after_normalize(self):
        from volume.rename import clear_rename_registry, restore_renamed_volumes

        d = self._tmpdir()
        try:
            for i in range(1, 4):
                data = b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32 if i == 1 else b'\x00' * 32
                open(os.path.join(d, f'测试{i}'), 'wb').write(data)
            vols = resolve_volume_archives(os.path.join(d, '测试2'))
            self.assertIsNotNone(vols)
            self.assertEqual(
                [os.path.basename(v) for v in vols],
                ['测试.7z.001', '测试.7z.002', '测试.7z.003'],
            )
            restored = restore_renamed_volumes(vols)
            self.assertEqual(
                sorted(os.path.basename(v) for v in restored),
                ['测试1', '测试2', '测试3'],
            )
            for i in range(1, 4):
                self.assertTrue(os.path.isfile(os.path.join(d, f'测试{i}')))
            self.assertFalse(any(name.endswith('.7z.001') for name in os.listdir(d)))
        finally:
            clear_rename_registry()
            shutil.rmtree(d, ignore_errors=True)

    def test_disguised_7z_mixed_naming(self):
        from volume import parse

        d = self._tmpdir()
        try:
            files = {
                '测试.7z你1': b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32,
                '测试.2啥': b'\x00' * 32,
                '测试.是03': b'\x00' * 32,
                '测试我4': b'\x00' * 32,
                '测试.猜5': b'\x00' * 32,
            }
            for name, data in files.items():
                open(os.path.join(d, name), 'wb').write(data)
            self.assertEqual(parse.parse_disguised_split('测试.7z你1'), ('测试', 1))
            vols = resolve_volume_archives(os.path.join(d, '测试.7z你1'))
            self.assertIsNotNone(vols)
            self.assertEqual(len(vols), 5)
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names[0], '测试.7z.001')
            self.assertEqual(names[3], '测试.7z.004')
            self.assertEqual(names[4], '测试.7z.005')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_disguised_first_volume_without_number(self):
        """混字首卷省略卷号：乌拉拉.哈 + 乌拉拉.气002"""
        d = self._tmpdir()
        try:
            open(os.path.join(d, '乌拉拉.哈'), 'wb').write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            open(os.path.join(d, '乌拉拉.气002'), 'wb').write(b'\x00' * 32)
            self.assertEqual(parse.parse_disguised_split('乌拉拉.哈'), ('乌拉拉', 1))
            self.assertEqual(parse.parse_disguised_split('乌拉拉.气002'), ('乌拉拉', 2))
            vols = resolve_volume_archives(os.path.join(d, '乌拉拉.哈'))
            self.assertIsNotNone(vols)
            self.assertEqual(len(vols), 2)
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names[0], '乌拉拉.7z.001')
            self.assertEqual(names[1], '乌拉拉.7z.002')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_disguised_last_volume_without_number(self):
        """混字末卷省略卷号：乌拉拉.哈001 + 乌拉拉.气"""
        d = self._tmpdir()
        try:
            open(os.path.join(d, '乌拉拉.哈001'), 'wb').write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            open(os.path.join(d, '乌拉拉.气'), 'wb').write(b'\x00' * 32)
            self.assertTrue(parse.disguised_suffix_is_implicit('乌拉拉.气'))
            self.assertFalse(parse.disguised_suffix_is_implicit('乌拉拉.哈001'))
            assigned = parse.assign_implicit_disguised_parts([
                os.path.join(d, '乌拉拉.哈001'),
                os.path.join(d, '乌拉拉.气'),
            ])
            self.assertEqual(
                [(1, '乌拉拉.哈001'), (2, '乌拉拉.气')],
                [(p, os.path.basename(path)) for p, path in assigned],
            )
            vols = resolve_volume_archives(os.path.join(d, '乌拉拉.哈001'))
            self.assertIsNotNone(vols)
            self.assertEqual(len(vols), 2)
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names[0], '乌拉拉.7z.001')
            self.assertEqual(names[1], '乌拉拉.7z.002')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cross_stem_disguised_7z_group(self):
        d = self._tmpdir()
        try:
            files = {
                '咦嘻.7z你1': b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32,
                '哈.2啥': b'\x00' * 32,
                '呵.是03': b'\x00' * 32,
                '顶我4': b'\x00' * 32,
                '一物.猜5': b'\x00' * 32,
            }
            for name, data in files.items():
                open(os.path.join(d, name), 'wb').write(data)
            vols = resolve_volume_archives(os.path.join(d, '哈.2啥'))
            self.assertIsNotNone(vols)
            self.assertEqual(len(vols), 5)
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names[0], '咦嘻.7z.001')
            self.assertEqual(names[4], '咦嘻.7z.005')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cross_stem_no_false_merge(self):
        d = self._tmpdir()
        try:
            open(os.path.join(d, '下载.吗1对'), 'wb').write(b'Rar!\x1a\x07\x00' + b'\x00' * 32)
            open(os.path.join(d, '123.part2'), 'wb').write(b'\x00' * 32)
            open(os.path.join(d, '123.part3'), 'wb').write(b'\x00' * 32)
            vols = resolve_volume_archives(os.path.join(d, '下载.吗1对'))
            names = [os.path.basename(v) for v in vols] if vols else []
            self.assertNotIn('123.part2', names)
            self.assertNotIn('123.part3', names)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cross_stem_7z_split_suffix(self):
        """异名但保留 .7z.NNN：乌拉拉.7z.001 / 啊呀啊呀.7z.002"""
        d = self._tmpdir()
        try:
            open(os.path.join(d, '乌拉拉.7z.001'), 'wb').write(
                b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32,
            )
            open(os.path.join(d, '啊呀啊呀.7z.002'), 'wb').write(b'\x00' * 32)
            vols = resolve_volume_archives(os.path.join(d, '乌拉拉.7z.001'))
            self.assertIsNotNone(vols)
            self.assertEqual(len(vols), 2)
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names[0], '乌拉拉.7z.001')
            self.assertEqual(names[1], '乌拉拉.7z.002')
            from volume.resolver import is_complete_volume_group
            self.assertTrue(is_complete_volume_group(vols))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cross_stem_trailing_numeric(self):
        """异名无后缀尾数分卷：猫1 / 老2"""
        d = self._tmpdir()
        try:
            open(os.path.join(d, '猫1'), 'wb').write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            open(os.path.join(d, '老2'), 'wb').write(b'\x00' * 32)
            vols = resolve_volume_archives(os.path.join(d, '老2'))
            self.assertIsNotNone(vols)
            self.assertEqual(len(vols), 2)
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names[0], '猫.7z.001')
            self.assertEqual(names[1], '猫.7z.002')
            from volume.rename import current_path_for_drag, restore_renames_in_directory
            self.assertIsNotNone(current_path_for_drag(os.path.join(d, '老2')))
            restore_renames_in_directory(d)
            self.assertTrue(os.path.isfile(os.path.join(d, '猫1')))
            self.assertTrue(os.path.isfile(os.path.join(d, '老2')))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_resolve_stale_volume_paths_after_restore(self):
        """还原分卷原名后，stale 的规范化路径应能重新同步到磁盘。"""
        d = self._tmpdir()
        try:
            open(os.path.join(d, '老1'), 'wb').write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            open(os.path.join(d, '2猫'), 'wb').write(b'\x00' * 32)
            vols = resolve_volume_archives(os.path.join(d, '老1'))
            self.assertEqual(
                [os.path.basename(v) for v in vols],
                ['老.7z.001', '老.7z.002'],
            )
            from volume.rename import restore_renames_in_directory, resolve_volume_paths_on_disk
            restore_renames_in_directory(d)
            stale = [os.path.join(d, name) for name in ('老.7z.001', '老.7z.002')]
            self.assertFalse(any(os.path.isfile(path) for path in stale))
            synced = resolve_volume_paths_on_disk(stale)
            self.assertTrue(all(os.path.isfile(path) for path in synced))
            self.assertEqual(
                [os.path.basename(v) for v in synced],
                ['老.7z.001', '老.7z.002'],
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cross_stem_leading_numeric(self):
        """异名前置卷号：老1 / 2猫"""
        d = self._tmpdir()
        try:
            open(os.path.join(d, '老1'), 'wb').write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            open(os.path.join(d, '2猫'), 'wb').write(b'\x00' * 32)
            self.assertEqual(parse.parse_leading_numeric('2猫'), ('猫', 2))
            vols = resolve_volume_archives(os.path.join(d, '2猫'))
            self.assertIsNotNone(vols)
            self.assertEqual(len(vols), 2)
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names[0], '老.7z.001')
            self.assertEqual(names[1], '老.7z.002')
            from volume.collect import volume_group_identity_for_anchor
            id1 = volume_group_identity_for_anchor(os.path.join(d, '老1'))
            id2 = volume_group_identity_for_anchor(os.path.join(d, '2猫'))
            self.assertEqual(id1, id2)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cross_stem_leading_dot_disguised(self):
        """首字符为点的异名分卷：.哈001 / .气002"""
        d = self._tmpdir()
        try:
            open(os.path.join(d, '.哈001'), 'wb').write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 32)
            open(os.path.join(d, '.气002'), 'wb').write(b'\x00' * 32)
            self.assertEqual(parse.parse_leading_dot_disguised('.哈001'), ('.哈', 1))
            vols = resolve_volume_archives(os.path.join(d, '.哈001'))
            self.assertIsNotNone(vols)
            self.assertEqual(len(vols), 2)
            names = [os.path.basename(v) for v in vols]
            self.assertEqual(names[0], '.哈.7z.001')
            self.assertEqual(names[1], '.哈.7z.002')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_validate_rejects_independent_archives(self):
        """同目录两个独立包（尾数命名）不应误绑为分卷。"""
        from unittest.mock import patch
        from volume.validate import accept_volume_group

        d = self._tmpdir()
        try:
            open(os.path.join(d, '作品A1'), 'wb').write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 64)
            open(os.path.join(d, '作品B2'), 'wb').write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 64)
            group = [os.path.join(d, '作品A1'), os.path.join(d, '作品B2')]
            with patch('volume.validate.classify_archive_probe', return_value='complete'):
                self.assertFalse(accept_volume_group(group))
                self.assertIsNone(resolve_volume_archives(os.path.join(d, '作品A1')))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_validate_accepts_split_cross_stem(self):
        """试读为分卷碎片时，异名组应通过验证。"""
        from unittest.mock import patch

        d = self._tmpdir()
        try:
            open(os.path.join(d, '作品A1'), 'wb').write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 64)
            open(os.path.join(d, '作品B2'), 'wb').write(b'\x00' * 32)
            with patch('volume.validate.classify_archive_probe', return_value='split'):
                vols = resolve_volume_archives(os.path.join(d, '作品A1'))
            self.assertIsNotNone(vols)
            self.assertEqual(len(vols), 2)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_try_expand_no_unrelated_group(self):
        from volume.probe import try_expand_volumes

        d = self._tmpdir()
        try:
            open(os.path.join(d, '123.part1'), 'wb').write(b'Rar!\x1a\x07\x00' + b'\x00' * 32)
            open(os.path.join(d, '123.part2'), 'wb').write(b'\x00' * 32)
            open(os.path.join(d, '123.part3'), 'wb').write(b'\x00' * 32)
            unrelated = os.path.join(d, '测试1')
            open(unrelated, 'wb').write(b'PK\x03\x04' + b'\x00' * 32)
            expanded = try_expand_volumes(
                [unrelated],
                'ERROR: Can not open the file as archive\n',
            )
            self.assertIsNone(expanded)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_readonly_resolve_scans_directory_once(self):
        """同一次分卷解析应让所有收集器共享一份目录快照。"""
        d = self._tmpdir()
        try:
            first = os.path.join(d, 'work.part1.rar')
            second = os.path.join(d, 'work.part2.rar')
            with open(first, 'wb') as stream:
                stream.write(b'Rar!\x1a\x07\x00' + b'\x00' * 32)
            with open(second, 'wb') as stream:
                stream.write(b'\x00' * 32)

            clear_index_cache()
            real_listdir = os.listdir
            with mock.patch('os.listdir', wraps=real_listdir) as listdir:
                volumes = VolumeResolver.peek_volumes(first)

            self.assertEqual(volumes, [first, second])
            self.assertEqual(listdir.call_count, 1)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
