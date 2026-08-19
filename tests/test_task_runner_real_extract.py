"""task_runner 真实解压端到端回归测试。"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import app_paths
import password
import task_runner
from gui import _format_run_status_summary
from tests import archive_fixture_builder as fixtures
from unzip_process_pool import ProcessResourceManager
from unzipper import Unzipper


def _has_seven_zip() -> bool:
    return os.path.isfile(app_paths.seven_zip_exe())


class _Progress:
    def add2lis(self, _timelines):
        return None


@unittest.skipUnless(_has_seven_zip(), '需要内置或系统 7-Zip')
class TaskRunnerRealExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._fixtures_tmp = tempfile.TemporaryDirectory(prefix='runner_fixtures_')
        cls.fixtures = fixtures.build_fixture_tree(cls._fixtures_tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._fixtures_tmp.cleanup()

    def setUp(self):
        self._services = {
            'logger_service': task_runner.logger,
            'configuration': task_runner.conf,
            'password_list': task_runner.passwords,
            'unzip_service': task_runner.unzipper,
            'filter_service': task_runner.filter,
            'rename_service': task_runner.renamer,
            'progress_service': task_runner.progress_ui,
        }
        task_runner.clear()
        self._tmp = tempfile.TemporaryDirectory(prefix='runner_extract_')
        self.resource = ProcessResourceManager(2)
        self.logger = mock.MagicMock()
        self.passwords = []
        self.unzipper = Unzipper(self.logger, self.resource)
        self.conf = SimpleNamespace(
            del_after_unzip=False,
            del_after_reunzip=False,
            blacklist=[],
            thread_threshold_mb=10 ** 9,
            thread_compression_ratio=10 ** 9,
            output_path=os.path.join(self._tmp.name, 'output'),
            resource_path=os.path.join(self._tmp.name, 'resource'),
            recycle_path=os.path.join(self._tmp.name, 'recycle'),
            logical_deletion=False,
        )
        for path in (
            self.conf.output_path,
            self.conf.resource_path,
            self.conf.recycle_path,
        ):
            os.makedirs(path)
        task_runner.bind_runtime_services(
            logger_service=self.logger,
            configuration=self.conf,
            password_list=self.passwords,
            unzip_service=self.unzipper,
            filter_service=mock.MagicMock(),
            rename_service=None,
            progress_service=_Progress(),
        )

    def tearDown(self):
        task_runner.clear()
        task_runner.bind_runtime_services(**self._services)
        self.resource.shutdown()
        self._tmp.cleanup()

    def _copy_case(self, name: str, sources: list[str]) -> list[str]:
        case_dir = os.path.join(self._tmp.name, name)
        os.makedirs(case_dir)
        copied = []
        for source in sources:
            target = os.path.join(case_dir, os.path.basename(source))
            shutil.copy2(source, target)
            copied.append(target)
        return copied

    def _run_case(self, name: str, sources: list[str], *, passwords=()):
        task_runner.clear()
        self.logger.reset_mock()
        self.passwords[:] = [password.Password(value) for value in passwords]
        copied = self._copy_case(name, sources)
        task_runner.create_timeline([copied[0]])
        with mock.patch.object(password, 'write_password'):
            task_runner.unzip_loop()

        ops = [timeline.get_current_record().ops for timeline in task_runner.timelines]
        self.assertTrue(ops, name)
        self.assertFalse(
            any(op == 'scan_failed' or op.endswith('_failed') for op in ops),
            f'{name}: {ops}; errors={self.logger.error.call_args_list}; '
            f'debug={self.logger.debug.call_args_list[-3:]}',
        )
        self.assertTrue(
            all(any(record.ops == 'unzip' for record in timeline.records)
                for timeline in task_runner.timelines),
            f'{name}: 缺少成功 unzip 记录',
        )
        status = _format_run_status_summary(
            task_runner.timelines,
            last_step='unzip',
        )
        self.assertEqual(status[0], '已完成', f'{name}: {status}, ops={ops}')
        task_runner.prune_after_step('unzip')
        self.assertEqual(task_runner.timelines, [], name)

    def _run_failure_case(self, name: str, sources: list[str]):
        task_runner.clear()
        self.logger.reset_mock()
        self.passwords.clear()
        copied = self._copy_case(name, sources)
        task_runner.create_timeline([copied[0]])
        with mock.patch.object(password, 'write_password'):
            task_runner.unzip_loop()

        ops = [timeline.get_current_record().ops for timeline in task_runner.timelines]
        self.assertTrue(ops, name)
        self.assertTrue(
            any(op == 'scan_failed' or op.endswith('_failed') for op in ops),
            f'{name}: {ops}',
        )
        status = _format_run_status_summary(
            task_runner.timelines,
            last_step='unzip',
        )
        self.assertNotEqual(status[0], '已完成', f'{name}: {status}')

    def test_plain_zip_7z_and_rar_finish_without_false_error(self):
        for fmt, source in self.fixtures['plain'].items():
            with self.subTest(fmt=fmt):
                self._run_case(f'plain_{fmt}', [source])

    def test_password_archives_finish_without_false_error(self):
        for fmt, source in self.fixtures['password'].items():
            with self.subTest(fmt=fmt):
                self._run_case(f'password_{fmt}', [source], passwords=('testpw',))

    def test_encrypted_zip_renamed_to_s_finishes_without_false_success(self):
        source_dir = os.path.join(self._tmp.name, 'renamed_password_source')
        os.makedirs(source_dir)
        disguised = fixtures.build_disguised_copies(
            source_dir,
            self.fixtures['password']['zip'],
            ['locked.S'],
        )
        self._run_case(
            'password_renamed_s',
            [disguised['locked.S']],
            passwords=('testpw',),
        )

    def test_disguised_and_extensionless_finish_without_false_error(self):
        cases = {
            'dat': self.fixtures['disguised']['game.dat'],
            'mp3': self.fixtures['disguised']['audio.mp3'],
            'txt': self.fixtures['disguised']['readme.txt'],
            'noext': self.fixtures['disguised']['noext'],
            'wrong_ext': self.fixtures['wrong_ext'],
        }
        for name, source in cases.items():
            with self.subTest(name=name):
                self._run_case(name, [source])

    def test_split_volumes_finish_without_false_error(self):
        for fmt, sources in self.fixtures['volumes'].items():
            with self.subTest(fmt=fmt):
                self._run_case(f'volumes_{fmt}', sources)

    def test_steganography_carriers_finish_without_false_error(self):
        for name, source in self.fixtures['stego'].items():
            with self.subTest(name=name):
                self._run_case(f'stego_{name}', [source])

    def test_nested_archives_finish_without_false_error(self):
        for name, source in self.fixtures['nested'].items():
            with self.subTest(name=name):
                self._run_case(f'nested_{name}', [source])

    def test_nested_encrypted_zip_renamed_to_s_finishes(self):
        source_dir = os.path.join(self._tmp.name, 'nested_renamed_source')
        os.makedirs(source_dir)
        payload = fixtures.write_payload_file(
            source_dir,
            'secret.txt',
            b'nested secret payload',
        )
        encrypted_zip = os.path.join(source_dir, 'inner.zip')
        fixtures.seven_zip_add(
            encrypted_zip,
            payload,
            password='testpw',
            format_flag='zip',
        )
        inner_s = os.path.join(source_dir, 'inner.S')
        shutil.copy2(encrypted_zip, inner_s)
        outer_zip = os.path.join(source_dir, 'outer.zip')
        fixtures.seven_zip_add(outer_zip, inner_s, format_flag='zip')

        self._run_case(
            'nested_encrypted_renamed_s',
            [outer_zip],
            passwords=('testpw',),
        )

    def test_wrong_password_and_corrupt_archive_remain_failures(self):
        self._run_failure_case(
            'wrong_password',
            [self.fixtures['password']['zip']],
        )
        corrupt_dir = os.path.join(self._tmp.name, 'corrupt_source')
        os.makedirs(corrupt_dir)
        corrupt = os.path.join(corrupt_dir, 'broken.zip')
        with open(corrupt, 'wb') as handle:
            handle.write(b'PK\x03\x04' + b'\xff' * 256)
        self._run_failure_case('corrupt', [corrupt])


if __name__ == '__main__':
    unittest.main()
