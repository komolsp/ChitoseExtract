import os
import subprocess
import tempfile
import unittest
from unittest import mock

from seven_z_driver import (
    GetNamelistError,
    NoFile2ProcessError,
    SevenZDriver,
    _decode_7z_text,
    _LARGE_SINGLE_FILE_MIN_BYTES,
    _method_is_store_encrypted,
)


class SevenZProbeTests(unittest.TestCase):
    def test_decode_7z_text_prefers_utf8_for_unicode_filenames(self):
        text = '【音声】中文标题・日本語ファイル.mp3'

        self.assertEqual(_decode_7z_text(text.encode('utf-8')), text)

    def test_decode_7z_text_keeps_gbk_fallback(self):
        text = '错误：无法打开压缩包'

        self.assertEqual(_decode_7z_text(text.encode('gbk')), text)

    def test_real_7z_namelist_keeps_unicode_filename(self):
        driver = SevenZDriver()
        with tempfile.TemporaryDirectory() as tmp:
            file_name = '中文日本語音声.txt'
            source = os.path.join(tmp, file_name)
            archive = os.path.join(tmp, 'unicode.7z')
            with open(source, 'wb') as fh:
                fh.write(b'unicode filename')
            subprocess.run(
                [driver.location_path, 'a', '-y', archive, file_name],
                cwd=tmp,
                check=True,
                capture_output=True,
            )

            names, _info = driver.get_namelist(archive)

            self.assertIn(file_name, names)

    def test_unzip_rejects_stdout_no_files_with_zero_exit_code(self):
        with mock.patch('seven_z_driver.os.path.isfile', return_value=True):
            driver = SevenZDriver(location_path=r'C:\fake\7z.exe')
        with mock.patch('seven_z_driver.subprocess.Popen') as popen:
            proc = mock.MagicMock()
            proc.communicate.return_value = (b'No files to process\r\n', b'')
            proc.returncode = 0
            popen.return_value = proc

            with self.assertRaises(NoFile2ProcessError):
                driver.unzip(
                    'renamed.S',
                    tempfile.gettempdir(),
                    output_file='missing.txt',
                    format_type='zip',
                )
            self.assertIn('-sccUTF-8', popen.call_args.args[0])

    def test_method_is_store_encrypted(self):
        self.assertTrue(_method_is_store_encrypted('Copy 7zAES'))
        self.assertTrue(_method_is_store_encrypted('Copy 7zAES:19'))
        self.assertFalse(_method_is_store_encrypted('LZMA2:24 7zAES'))
        self.assertFalse(_method_is_store_encrypted('LZMA2:24 7zAES:19'))

    def _run_probe(self, slt_output: str, namelist_side_effect):
        with mock.patch('seven_z_driver.os.path.isfile', return_value=True):
            driver = SevenZDriver(location_path=r'C:\fake\7z.exe')
        driver.get_namelist = mock.MagicMock(side_effect=namelist_side_effect)
        with mock.patch('seven_z_driver.subprocess.Popen') as popen:
            proc = mock.MagicMock()
            proc.communicate.return_value = (slt_output.encode('utf-8'), b'')
            proc.returncode = 0
            popen.return_value = proc
            with tempfile.NamedTemporaryFile(suffix='.7z', delete=False) as fh:
                path = fh.name
            try:
                return driver.probe_content_encrypted_single_block(path)
            finally:
                os.unlink(path)

    def test_probe_matches_copy_store_encrypted_single_block(self):
        large_size = _LARGE_SINGLE_FILE_MIN_BYTES + 1024
        slt_output = (
            'Path = sample.7z\n'
            'Type = 7z\n'
            'Blocks = 1\n'
            'Method = Copy 7zAES\n'
            '----------\n'
            'Path = inner.zip\n'
            f'Size = {large_size}\n'
            'Method = Copy 7zAES:19\n'
            'Encrypted = +\n'
        )
        result = self._run_probe(
            slt_output,
            [
                (['inner.zip'], {'encrypted': True}),
                (['inner.zip'], {'encrypted': True}),
            ],
        )
        self.assertTrue(result['content_encrypted_solid'])
        self.assertTrue(result['store_encrypted'])
        self.assertTrue(result['listable_without_password'])
        self.assertEqual(result['blocks'], 1)

    def test_probe_rejects_lzma2_fast_verify_shape(self):
        """01646431.7z: LZMA2 压缩加密，可快速验密。"""
        large_size = _LARGE_SINGLE_FILE_MIN_BYTES + 1024
        slt_output = (
            'Path = 01646431.7z\n'
            'Blocks = 1\n'
            'Method = LZMA2:24 7zAES\n'
            '----------\n'
            'Path = RJ01646431.zip\n'
            f'Size = {large_size}\n'
            'Method = LZMA2:24 7zAES:19\n'
            'Encrypted = +\n'
        )
        result = self._run_probe(
            slt_output,
            [
                (['RJ01646431.zip'], {'encrypted': True}),
                (['RJ01646431.zip'], {'encrypted': True}),
            ],
        )
        self.assertFalse(result['content_encrypted_solid'])
        self.assertFalse(result['store_encrypted'])
        self.assertTrue(result['listable_without_password'])

    def test_probe_rejects_header_encrypted_when_cannot_list_with_wrong_password(self):
        slt_output = (
            'Blocks = 1\nMethod = Copy 7zAES\n'
            '----------\n'
            f'Path = inner.zip\nSize = {_LARGE_SINGLE_FILE_MIN_BYTES + 1}\n'
        )
        result = self._run_probe(
            slt_output,
            [
                (['inner.zip'], {'encrypted': True}),
                GetNamelistError('Wrong password'),
            ],
        )
        self.assertFalse(result['content_encrypted_solid'])
        self.assertFalse(result['listable_without_password'])

    def test_probe_non_solid_rj01620216_shape(self):
        slt_output = (
            'Path = RJ01620216.7z\n'
            'Method = Copy 7zAES\n'
            'Solid = -\n'
            'Blocks = 1\n'
            '----------\n'
            'Path = RJ01620216.zip\n'
            'Size = 3775660497\n'
            'Method = Copy 7zAES:19\n'
            'Encrypted = +\n'
        )
        result = self._run_probe(
            slt_output,
            [
                (['RJ01620216.zip'], {'encrypted': True}),
                (['RJ01620216.zip'], {'encrypted': True}),
            ],
        )
        self.assertTrue(result['content_encrypted_solid'])
        self.assertTrue(result['store_encrypted'])

    def test_probe_rejects_blocks1_single_small_file(self):
        slt_output = (
            'Blocks = 1\nMethod = Copy 7zAES\n'
            '----------\n'
            'Path = inner.zip\n'
            'Size = 123\n'
        )
        result = self._run_probe(
            slt_output,
            [
                (['inner.zip'], {'encrypted': True}),
                (['inner.zip'], {'encrypted': True}),
            ],
        )
        self.assertFalse(result['content_encrypted_solid'])

    def test_probe_rejects_multiple_files_even_when_blocks1(self):
        large = _LARGE_SINGLE_FILE_MIN_BYTES + 1
        slt_output = (
            'Blocks = 1\nMethod = Copy 7zAES\n'
            '----------\n'
            f'Path = a.zip\nSize = {large}\n'
            '----------\n'
            f'Path = b.zip\nSize = {large}\n'
        )
        result = self._run_probe(
            slt_output,
            [
                (['a.zip', 'b.zip'], {'encrypted': True}),
                (['a.zip', 'b.zip'], {'encrypted': True}),
            ],
        )
        self.assertFalse(result['content_encrypted_solid'])
        self.assertEqual(result['file_count'], 2)

    def test_probe_rejects_single_large_file_when_blocks_not_one(self):
        large = _LARGE_SINGLE_FILE_MIN_BYTES + 1
        slt_output = (
            'Blocks = 2\nMethod = Copy 7zAES\n'
            '----------\n'
            f'Path = inner.zip\nSize = {large}\n'
        )
        result = self._run_probe(
            slt_output,
            [
                (['inner.zip'], {'encrypted': True}),
                (['inner.zip'], {'encrypted': True}),
            ],
        )
        self.assertFalse(result['content_encrypted_solid'])


if __name__ == '__main__':
    unittest.main()
