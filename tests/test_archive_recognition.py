"""统一压缩包识别接口契约与真实可行性测试。"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
import zipfile

import app_paths
from archive_recognition import (
    ArchiveEncryption,
    ArchiveLayout,
    ExtractBackend,
    RecognitionContext,
    recognize_archive,
)
from seven_z_driver import SevenZDriver
from tests import archive_fixture_builder as fixtures
from zip import Zip


def _has_seven_zip() -> bool:
    return os.path.isfile(app_paths.seven_zip_exe())


class ArchiveRecognitionContractTests(unittest.TestCase):
    def _write_zip(self, directory: str, name: str = 'plain.zip') -> str:
        path = os.path.join(directory, name)
        with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('payload.txt', b'payload')
        return path

    def test_plain_zip_returns_complete_stable_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_zip(tmp)
            result = recognize_archive(path)

            self.assertTrue(result.is_candidate)
            self.assertEqual(result.actual_format, 'zip')
            self.assertEqual(result.layout, ArchiveLayout.PLAIN)
            self.assertEqual(result.encryption, ArchiveEncryption.NONE)
            self.assertEqual(result.backend, ExtractBackend.SEVEN_ZIP)
            self.assertFalse(result.password_required)
            self.assertTrue(result.matches_current_file(path))
            self.assertTrue(result.strategy_pairs())

    def test_nested_context_keeps_plain_media_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'cover.jpg')
            with open(path, 'wb') as handle:
                handle.write(b'\xff\xd8\xff\xe0\x00\x10JFIF\x00' + b'\x00' * 2048)

            result = recognize_archive(
                path,
                context=RecognitionContext.NESTED,
            )

            self.assertFalse(result.is_candidate)
            self.assertEqual(result.context, RecognitionContext.NESTED)

    def test_volume_contract_only_uses_automatic_opening(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, 'split.zip.001')
            second = os.path.join(tmp, 'split.zip.002')
            with open(first, 'wb') as handle:
                handle.write(b'PK\x03\x04' + b'\x00' * 64)
            with open(second, 'wb') as handle:
                handle.write(b'\x00' * 64)

            result = recognize_archive(first, volumes=[first, second])

            self.assertTrue(result.is_candidate)
            self.assertEqual(result.layout, ArchiveLayout.VOLUME)
            self.assertEqual(result.volumes, (first, second))
            self.assertEqual(result.strategy_pairs(), [(None, False)])

    def test_recognition_fingerprint_expires_after_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_zip(tmp)
            result = recognize_archive(path)
            with open(path, 'ab') as handle:
                handle.write(b'replaced')

            self.assertFalse(result.matches_current_file(path))

    def test_zip_entity_consumes_and_enriches_recognition(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_zip(tmp, 'renamed.S')
            result = recognize_archive(path)
            archive = Zip(path, ['secret'], recognition=result)

            self.assertTrue(archive.is_format('zip'))
            archive.apply_open_result('zip', False, encrypted=True)
            self.assertTrue(archive.is_encrypted())
            self.assertEqual(
                archive.current_recognition().encryption,
                ArchiveEncryption.ZIP_CRYPTO,
            )


@unittest.skipUnless(_has_seven_zip(), '需要内置或系统 7-Zip')
class ArchiveRecognitionRealExtractTests(unittest.TestCase):
    def test_zipcrypto_renamed_to_s_is_identified_by_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = fixtures.write_payload_file(tmp, 'secret.txt', b'secret')
            original = os.path.join(tmp, 'locked.zip')
            fixtures.seven_zip_add(
                original,
                payload,
                password='testpw',
                format_flag='zip',
            )
            renamed = os.path.join(tmp, 'locked.S')
            shutil.copy2(original, renamed)

            result = recognize_archive(renamed)

            self.assertEqual(result.actual_format, 'zip')
            self.assertEqual(result.encryption, ArchiveEncryption.ZIP_CRYPTO)
            self.assertEqual(result.layout, ArchiveLayout.PLAIN)
            self.assertTrue(result.password_required)
            self.assertIn(('zip', False), result.strategy_pairs())

    def test_wzaes_renamed_to_s_selects_backend_and_extracts(self):
        import pyzipper

        with tempfile.TemporaryDirectory() as tmp:
            renamed = os.path.join(tmp, 'wz_aes.S')
            with pyzipper.AESZipFile(
                renamed,
                'w',
                compression=pyzipper.ZIP_DEFLATED,
                encryption=pyzipper.WZ_AES,
            ) as archive:
                archive.setpassword(b'secret')
                archive.writestr('inner.txt', b'payload')

            result = recognize_archive(renamed)
            self.assertEqual(result.actual_format, 'zip')
            self.assertEqual(result.encryption, ArchiveEncryption.WZ_AES)
            self.assertEqual(result.backend, ExtractBackend.WZ_AES)

            output = os.path.join(tmp, 'output')
            os.makedirs(output)
            driver = SevenZDriver()
            returncode, _password = driver.unzip(
                renamed,
                output,
                password='secret',
                format_type=result.format_type,
                covered=result.covered,
                recognition=result,
            )
            self.assertEqual(returncode, 0)
            self.assertTrue(os.path.isfile(os.path.join(output, 'inner.txt')))


if __name__ == '__main__':
    unittest.main()
