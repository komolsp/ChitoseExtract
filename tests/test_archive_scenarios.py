"""压缩包识别与解压全场景集成测试。

覆盖：zip/rar/7z、改后缀、无后缀、错误后缀、隐写载体、套娃组合、分卷。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest import mock

import archive_registry
import file_ops
from seven_z_driver import SevenZDriver
from tests import archive_fixture_builder as fixtures
from unzip_process_pool import ProcessResourceManager
from unzipper import Unzipper
from volume import resolve_volume_archives
from volume.resolver import clear_index_cache
from zip import Zip


def _has_seven_zip() -> bool:
    try:
        return os.path.isfile(fixtures.seven_zip_exe())
    except Exception:
        return False


def _has_winrar() -> bool:
    return fixtures.winrar_exe() is not None


_SHARED_FIXTURE_BASE: str | None = None
_SHARED_FIXTURES: dict | None = None


def _shared_fixture_tree() -> tuple[str, dict]:
    global _SHARED_FIXTURE_BASE, _SHARED_FIXTURES
    if _SHARED_FIXTURES is None:
        _SHARED_FIXTURE_BASE = tempfile.mkdtemp(prefix='archive_scenarios_')
        _SHARED_FIXTURES = fixtures.build_fixture_tree(_SHARED_FIXTURE_BASE)
    return _SHARED_FIXTURE_BASE, _SHARED_FIXTURES


def _build_small_zip_volumes(work_dir: str, stem: str) -> list[str]:
    payload = fixtures.write_payload_file(
        work_dir, f'{stem}.bin', bytes(range(256)) * 400,
    )
    return fixtures._build_zip_volumes(work_dir, stem, payload)


def _write_plain_zip(work_dir: str, name: str = 'plain.zip') -> str:
    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, name)
    with open(path, 'wb') as handle:
        handle.write(fixtures.create_zip_bytes(content=os.urandom(2048)))
    return path


def tearDownModule():
    global _SHARED_FIXTURE_BASE, _SHARED_FIXTURES
    if _SHARED_FIXTURE_BASE:
        shutil.rmtree(_SHARED_FIXTURE_BASE, ignore_errors=True)
    _SHARED_FIXTURE_BASE = None
    _SHARED_FIXTURES = None


@unittest.skipUnless(_has_seven_zip(), '需要内置或系统 7-Zip')
class ProbeArchiveScenarioTest(unittest.TestCase):
    """probe_archive 对各种伪装/隐写/分卷场景的识别。"""

    @classmethod
    def setUpClass(cls):
        cls._base, cls.fixtures = _shared_fixture_tree()

    def _probe(self, rel_path: str, *, nested: bool = False):
        path = rel_path
        if not os.path.isabs(rel_path):
            path = os.path.join(self._base, rel_path)
        return file_ops.probe_archive(path, nested=nested)

    def test_standard_formats_are_candidates(self):
        for fmt, path in self.fixtures['plain'].items():
            probe = self._probe(path)
            self.assertTrue(probe.is_candidate, fmt)

    def test_disguised_extensions(self):
        disguised = self.fixtures['disguised']
        for alias in ('game.dat', 'pack.r', 'audio.mp3', 'readme.txt'):
            probe = self._probe(disguised[alias])
            self.assertTrue(probe.is_candidate, alias)

    def test_extensionless_with_magic(self):
        probe = self._probe(self.fixtures['disguised']['noext'])
        self.assertTrue(probe.is_candidate)

    def test_wrong_extension_7z_as_dat(self):
        probe = self._probe(self.fixtures['wrong_ext'])
        self.assertTrue(probe.is_candidate)
        self.assertEqual(probe.format_type, '7z')

    def test_steganography_carriers(self):
        for key in ('jpg', 'mp4', 'pdf'):
            probe = self._probe(self.fixtures['stego'][key])
            self.assertTrue(probe.is_candidate, key)
            self.assertTrue(probe.covered, key)

    def test_nested_scan_skips_plain_jpeg_without_magic(self):
        jpg = self.fixtures['stego']['jpg']
        probe_nested = file_ops.probe_archive(jpg, nested=True)
        self.assertFalse(probe_nested.is_candidate)

    def test_volume_parts_are_candidates(self):
        clear_index_cache()
        for fmt, vols in self.fixtures['volumes'].items():
            for vol in vols:
                probe = file_ops.probe_archive(vol)
                self.assertTrue(probe.is_candidate, f'{fmt}:{os.path.basename(vol)}')

    def test_real_text_file_not_candidate(self):
        path = os.path.join(self._base, 'nested', 'leaf.txt')
        self.assertTrue(os.path.isfile(path))
        self.assertFalse(file_ops.probe_archive(path).is_candidate)
        self.assertFalse(file_ops.probe_archive(path, nested=True).is_candidate)

    def test_tiny_extensionless_not_candidate(self):
        d = tempfile.mkdtemp(prefix='tiny_noext_')
        try:
            path = os.path.join(d, 'tiny')
            with open(path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 32)
            self.assertFalse(file_ops.probe_archive(path).is_candidate)
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(_has_seven_zip(), '需要内置或系统 7-Zip')
class ArchiveOpenStrategyTest(unittest.TestCase):
    """7-Zip 打开策略：标准包禁用 covered，伪装包启用 covered。"""

    def test_standard_zip_no_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_plain_zip(tmp)
            probe = file_ops.probe_archive(path)
            strategies = file_ops.build_archive_open_strategies(
                probe, '.zip', path,
            )
            self.assertTrue(any(not c for _, c in strategies))
            self.assertFalse(any(c for _, c in strategies))

    def test_disguised_dat_allows_covered(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = _write_plain_zip(tmp)
            disguised = fixtures.build_disguised_copies(tmp, plain, ['x.dat'])
            path = disguised['x.dat']
            probe = file_ops.probe_archive(path)
            strategies = file_ops.build_archive_open_strategies(
                probe, '.dat', path,
            )
            self.assertTrue(any(c for _, c in strategies))

    def test_volume_only_auto_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            vols = _build_small_zip_volumes(tmp, 'strategy')
            probe = file_ops.probe_archive(vols[0])
            strategies = file_ops.build_archive_open_strategies(
                probe, '.001', vols[0], is_volume=True,
            )
            self.assertEqual(strategies, [(None, False)])


@unittest.skipUnless(_has_seven_zip(), '需要内置或系统 7-Zip')
class SevenZipExtractIntegrationTest(unittest.TestCase):
    """真实 7-Zip 解压：标准格式、伪装、隐写、分卷。"""

    @classmethod
    def setUpClass(cls):
        cls._base, cls.fixtures = _shared_fixture_tree()
        cls.driver = SevenZDriver()

    def _extract(self, archive_path: str, out_dir: str, *, password: str = '') -> list[str]:
        probe = file_ops.probe_archive(archive_path)
        strategies = file_ops.build_archive_open_strategies(
            probe,
            os.path.splitext(archive_path)[1],
            archive_path,
            is_volume=file_ops.is_volume_zip(archive_path, readonly=True),
        )
        last_err = ''
        for format_type, covered in strategies:
            try:
                self.driver.unzip(
                    archive_path,
                    out_dir,
                    password=password,
                    covered=covered,
                    format_type=format_type,
                )
                return os.listdir(out_dir)
            except Exception as err:
                last_err = str(err)
        self.fail(f'解压失败 {archive_path}: {last_err}')

    def test_extract_plain_zip_7z(self):
        for fmt in ('zip', '7z'):
            out = os.path.join(self._base, f'out_plain_{fmt}')
            os.makedirs(out)
            names = self._extract(self.fixtures['plain'][fmt], out)
            self.assertTrue(any('payload' in name for name in names), fmt)

    @unittest.skipUnless(_has_winrar(), '需要 WinRAR 创建 RAR 样本')
    def test_extract_plain_rar(self):
        out = os.path.join(self._base, 'out_plain_rar')
        os.makedirs(out)
        names = self._extract(self.fixtures['plain']['rar'], out)
        self.assertTrue(any('payload' in name for name in names))

    def test_extract_disguised_dat(self):
        out = os.path.join(self._base, 'out_disguised')
        os.makedirs(out)
        names = self._extract(self.fixtures['disguised']['game.dat'], out)
        self.assertTrue(names)

    def test_extract_stego_jpeg(self):
        out = os.path.join(self._base, 'out_stego_jpg')
        os.makedirs(out)
        names = self._extract(self.fixtures['stego']['jpg'], out)
        # covered 模式可能先解出编号碎片与内层 .zip
        self.assertTrue(
            any(
                'stego' in name or 'payload' in name or name.endswith('.zip')
                for name in names
            ),
            names,
        )

    def test_extract_zip_split_volume(self):
        out = os.path.join(self._base, 'out_vol_zip')
        os.makedirs(out)
        vols = self.fixtures['volumes']['zip']
        names = self._extract(vols[0], out)
        self.assertTrue(any('big' in name or 'bin' in name for name in names))

    def test_extract_7z_split_volume(self):
        out = os.path.join(self._base, 'out_vol_7z')
        os.makedirs(out)
        vols = self.fixtures['volumes']['7z']
        names = self._extract(vols[0], out)
        self.assertTrue(any('big' in name or 'bin' in name for name in names))

    @unittest.skipUnless(_has_winrar(), '需要 WinRAR 创建 RAR 分卷')
    def test_extract_rar_split_volume(self):
        out = os.path.join(self._base, 'out_vol_rar')
        os.makedirs(out)
        vols = self.fixtures['volumes']['rar']
        names = self._extract(vols[0], out)
        self.assertTrue(any('big' in name or 'bin' in name for name in names))

    def test_extract_password_zip(self):
        out = os.path.join(self._base, 'out_pw_zip')
        os.makedirs(out)
        names = self._extract(self.fixtures['password']['zip'], out, password='testpw')
        self.assertTrue(any('secret' in name for name in names))


@unittest.skipUnless(_has_seven_zip(), '需要内置或系统 7-Zip')
class UnzipperFindZipIntegrationTest(unittest.TestCase):
    """find_zip / load_namelist 在真实样本上的行为。"""

    def setUp(self):
        archive_registry.clear()
        clear_index_cache()
        self.unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(2))

    def test_find_zip_discovers_disguised_and_volumes(self):
        base = tempfile.mkdtemp(prefix='find_zip_')
        try:
            disguise_dir = os.path.join(base, 'disguise')
            volume_dir = os.path.join(base, 'volumes')
            plain_zip = _write_plain_zip(disguise_dir)
            disguised = fixtures.build_disguised_copies(
                disguise_dir, plain_zip, ['game.dat'],
            )
            os.remove(plain_zip)
            zip_vols = _build_small_zip_volumes(volume_dir, 'zipvol')
            zip_list: list[Zip] = []
            self.unzipper.find_zip(
                base, [''], False, [], zip_list,
            )
            discovered_paths = {os.path.normcase(z.path) for z in zip_list}
            self.assertTrue(
                os.path.normcase(disguised['game.dat']) in discovered_paths
                or any('game.dat' in p for p in discovered_paths),
            )
            vol_paths = {
                os.path.normcase(v) for z in zip_list if z.volumes for v in z.volumes
            }
            self.assertTrue(
                any(os.path.normcase(v) in vol_paths for v in zip_vols)
                or any('zipvol.zip' in p for p in vol_paths),
                f'应发现 zip 分卷组，vol_paths 样本: {list(vol_paths)[:5]}',
            )
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_find_zip_follows_classic_zip_path_after_normalize(self):
        base = tempfile.mkdtemp(prefix='find_classic_zip_')
        try:
            z01 = os.path.join(base, 'archive.z01')
            z02 = os.path.join(base, 'archive.z02')
            main_zip = os.path.join(base, 'archive.zip')
            with open(z01, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 64)
            with open(z02, 'wb') as fh:
                fh.write(b'\x00' * 64)
            with open(main_zip, 'wb') as fh:
                fh.write(b'\x00' * 64)

            zip_list: list[Zip] = []
            with mock.patch.object(self.unzipper, 'load_namelist', return_value=True):
                found = self.unzipper.find_zip(
                    z01, ['password'], False, [], zip_list, depth=1,
                )

            self.assertTrue(found, self.unzipper.logger.method_calls)
            self.assertEqual(len(zip_list), 1)
            self.assertEqual(
                [os.path.basename(path) for path in zip_list[0].volumes],
                ['archive.zip', 'archive.z01', 'archive.z02'],
            )
            # 经典跨卷 ZIP 保留原名，并以 .zip 末卷作为 7-Zip 入口。
            self.assertEqual(
                [os.path.getsize(path) for path in zip_list[0].volumes],
                [64, 68, 64],
            )
            self.assertEqual(os.path.basename(zip_list[0].path), 'archive.zip')
            self.assertTrue(os.path.isfile(zip_list[0].path))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_load_namelist_on_nested_outer(self):
        base = tempfile.mkdtemp(prefix='namelist_nested_')
        try:
            nested = fixtures.build_nested_chain(
                os.path.join(base, 'n'),
                ['zip', '7z'],
            )
            zip_obj = Zip(nested, [''], False)
            self.assertTrue(self.unzipper.load_namelist(zip_obj))
            self.assertTrue(zip_obj.file_list)
        finally:
            shutil.rmtree(base, ignore_errors=True)


@unittest.skipUnless(_has_seven_zip(), '需要内置或系统 7-Zip')
class NestedMatryoshkaExtractTest(unittest.TestCase):
    """多层套娃：外层解压后内层可被识别并列出。"""

    @classmethod
    def setUpClass(cls):
        cls._base, cls.fixtures = _shared_fixture_tree()
        cls.driver = SevenZDriver()
        cls.unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(2))

    def _extract_with_strategies(self, path: str, out_dir: str) -> None:
        probe = file_ops.probe_archive(path)
        ext = os.path.splitext(path)[1]
        is_vol = file_ops.is_volume_zip(path, readonly=True)
        for format_type, covered in file_ops.build_archive_open_strategies(
            probe, ext, path, is_volume=is_vol,
        ):
            try:
                self.driver.unzip(path, out_dir, covered=covered, format_type=format_type)
                return
            except Exception:
                continue
        self.fail(f'无法解压: {path}')

    def test_zip_7z_zip_chain_lists_inner(self):
        outer = self.fixtures['nested']['zip_7z_zip']
        work = os.path.join(self._base, 'work_zip_7z_zip')
        os.makedirs(work)
        self._extract_with_strategies(outer, work)
        zip_list: list[Zip] = []
        archive_registry.clear()
        self.unzipper.find_zip(work, [''], False, [], zip_list, depth=1)
        inner_names = [os.path.basename(z.path) for z in zip_list]
        self.assertTrue(
            any(name.endswith('.7z') or name.endswith('.zip') for name in inner_names),
            f'应发现内层压缩包，实际: {inner_names}',
        )

    @unittest.skipUnless(_has_winrar(), '需要 WinRAR')
    def test_7z_rar_zip_chain(self):
        outer = self.fixtures['nested']['7z_rar_zip']
        work = os.path.join(self._base, 'work_7z_rar_zip')
        os.makedirs(work)
        self._extract_with_strategies(outer, work)
        zip_list: list[Zip] = []
        archive_registry.clear()
        self.unzipper.find_zip(work, [''], False, [], zip_list, depth=1)
        self.assertGreaterEqual(len(zip_list), 1)


@unittest.skipUnless(_has_seven_zip(), '需要内置或系统 7-Zip')
class VolumeResolverRealFileTest(unittest.TestCase):
    """真实分卷文件的分卷解析与完整性。"""

    def setUp(self):
        clear_index_cache()

    def test_resolve_real_zip_volumes(self):
        base = tempfile.mkdtemp(prefix='vol_resolve_')
        try:
            vols = _build_small_zip_volumes(base, 'onlyzip')
            resolved = resolve_volume_archives(vols[1])
            self.assertIsNotNone(resolved)
            self.assertEqual(len(resolved), len(vols))
            self.assertTrue(all(os.path.isfile(path) for path in resolved))
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_classify_real_zip_split_as_split_not_complete(self):
        """标准 .zip.001 分卷：7z l 能列出内容但仍应判为 split。"""
        from volume.validate import classify_archive_probe

        base = tempfile.mkdtemp(prefix='vol_classify_')
        try:
            vols = _build_small_zip_volumes(base, 'probezip')
            self.assertGreaterEqual(len(vols), 2)
            self.assertEqual(classify_archive_probe(vols[0]), 'split')
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_incomplete_volume_group_rejected(self):
        base = tempfile.mkdtemp(prefix='vol_incomplete_')
        try:
            vols = _build_small_zip_volumes(base, 'split')
            orphan = vols[-1]
            for path in vols[:-1]:
                os.remove(path)
            clear_index_cache()
            resolved = resolve_volume_archives(orphan)
            self.assertIsNone(resolved)
        finally:
            shutil.rmtree(base, ignore_errors=True)


@unittest.skipUnless(_has_seven_zip(), '需要内置或系统 7-Zip')
class NegativeScenarioTest(unittest.TestCase):
    """边界与误判防护。"""

    def test_corrupt_zip_not_genuine(self):
        unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(2))
        with tempfile.TemporaryDirectory() as tmp:
            bad = os.path.join(tmp, 'bad.zip')
            with open(bad, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\xff' * 128)
            # 有 ZIP 魔数时 probe 会视为候选，但结构无效
            self.assertTrue(file_ops.probe_archive(bad).is_candidate)
            self.assertFalse(unzipper._extracted_archive_is_genuine(bad))

    def test_random_binary_not_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'noise.xyz')
            with open(path, 'wb') as fh:
                fh.write(b'\xde\xad\xbe\xef' * 1024)
            self.assertFalse(file_ops.probe_archive(path).is_candidate)

    def test_disguised_bin_without_magic_still_candidate(self):
        """.bin 属于伪装扩展名：无魔数时仍交给 7-Zip 用 -t# 尝试。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'data.bin')
            with open(path, 'wb') as fh:
                fh.write(b'\xde\xad\xbe\xef' * 512)
            probe = file_ops.probe_archive(path)
            self.assertTrue(probe.is_candidate)
            self.assertTrue(probe.covered)

    def test_nested_mp3_without_magic_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'bgm.mp3')
            with open(path, 'wb') as fh:
                fh.write(b'ID3' + b'\x00' * 2048)
            self.assertFalse(file_ops.probe_archive(path, nested=True).is_candidate)


if __name__ == '__main__':
    unittest.main()
