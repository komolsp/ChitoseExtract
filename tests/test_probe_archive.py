"""probe_archive 识别逻辑单元测试。"""

import gzip
import io
import os
import shutil
import struct
import tempfile
import unittest
import zipfile

from file_ops import probe_archive


def _write_min_pe(path: str, extra: bytes = b'') -> None:
    pe_offset = 128
    header = bytearray(pe_offset + 64)
    header[0:2] = b'MZ'
    struct.pack_into('<I', header, 0x3C, pe_offset)
    header[pe_offset:pe_offset + 4] = b'PE\x00\x00'
    with open(path, 'wb') as f:
        f.write(header)
        if extra:
            f.write(extra)


class ProbeArchiveApkTest(unittest.TestCase):

    def _tmpdir(self) -> str:
        return tempfile.mkdtemp(prefix='probe_apk_')

    def _write_real_apk(self, path: str):
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('AndroidManifest.xml', b'<manifest/>')
            zf.writestr('classes.dex', b'dex')
            zf.writestr('META-INF/CERT.SF', b'sig')

    def _write_zip_disguised_apk(self, path: str):
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr('data/chapter1.txt', b'hello')
            zf.writestr('nested/file.bin', b'\x00' * 64)

    def test_real_apk_not_candidate(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'app.apk')
            self._write_real_apk(path)
            probe = probe_archive(path, nested=False)
            self.assertFalse(probe.is_candidate)
            probe_nested = probe_archive(path, nested=True)
            self.assertFalse(probe_nested.is_candidate)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_zip_renamed_to_apk_is_candidate(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'game.apk')
            self._write_zip_disguised_apk(path)
            probe = probe_archive(path, nested=False)
            self.assertTrue(probe.is_candidate)
            self.assertEqual(probe.format_type, 'zip')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_7z_renamed_to_apk_is_candidate(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'secret.apk')
            with open(path, 'wb') as f:
                f.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 256)
            probe = probe_archive(path, nested=False)
            self.assertTrue(probe.is_candidate)
            self.assertEqual(probe.format_type, '7z')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_rar_renamed_to_apk_is_candidate(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'pack.apk')
            with open(path, 'wb') as f:
                f.write(b'Rar!\x1a\x07\x00' + b'\x00' * 256)
            probe = probe_archive(path, nested=False)
            self.assertTrue(probe.is_candidate)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ProbeArchiveExeTest(unittest.TestCase):

    def _tmpdir(self) -> str:
        return tempfile.mkdtemp(prefix='probe_exe_')

    def test_real_pe_exe_not_candidate(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'laowang.exe')
            _write_min_pe(path)
            self.assertFalse(probe_archive(path, nested=False).is_candidate)
            self.assertFalse(probe_archive(path, nested=True).is_candidate)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_7z_renamed_to_exe_is_candidate(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'game.exe')
            with open(path, 'wb') as f:
                f.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 256)
            probe = probe_archive(path, nested=False)
            self.assertTrue(probe.is_candidate)
            self.assertEqual(probe.format_type, '7z')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_pe_with_sfx_tail_is_candidate(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'sfx.exe')
            _write_min_pe(path, b'7z\xbc\xaf\x27\x1c' + b'\x00' * 256)
            probe = probe_archive(path, nested=False)
            self.assertTrue(probe.is_candidate)
            self.assertTrue(probe.covered)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ProbeArchiveOoxmlTest(unittest.TestCase):

    @staticmethod
    def _write_docx(path: str) -> None:
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('[Content_Types].xml', b'<Types/>')
            zf.writestr('_rels/.rels', b'<Relationships/>')
            zf.writestr('word/document.xml', b'<document/>')

    def test_valid_docx_is_not_archive_candidate(self):
        with tempfile.TemporaryDirectory(prefix='probe_docx_') as tmp:
            path = os.path.join(tmp, '台本.docx')
            self._write_docx(path)

            self.assertFalse(probe_archive(path, nested=False).is_candidate)
            self.assertFalse(probe_archive(path, nested=True).is_candidate)

    def test_regular_zip_renamed_to_docx_remains_candidate(self):
        with tempfile.TemporaryDirectory(prefix='probe_docx_') as tmp:
            path = os.path.join(tmp, '伪装.docx')
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('payload/audio.wav', b'audio')

            probe = probe_archive(path, nested=True)
            self.assertTrue(probe.is_candidate)
            self.assertEqual(probe.format_type, 'zip')


class ProbeArchiveMp4StegoTest(unittest.TestCase):

    def _tmpdir(self) -> str:
        return tempfile.mkdtemp(prefix='probe_mp4_')

    def _write_moov_mdat_mp4(self, path: str, zip_offset_in_mdat: int) -> None:
        """模拟 ftyp + moov + mdat，压缩包藏在 mdat 深处。"""
        ftyp_size = 32
        ftyp = struct.pack('>I', ftyp_size) + b'ftyp' + b'isom\x00\x00\x02\x00'
        ftyp += b'\x00' * max(0, ftyp_size - len(ftyp))
        moov_size = 4096
        moov = struct.pack('>I', moov_size) + b'moov' + b'\x00' * (moov_size - 8)
        padding = b'\x00' * zip_offset_in_mdat
        zip_tail = b'PK\x05\x06' + b'\x00' * 16
        mdat_payload = padding + zip_tail
        mdat_header = struct.pack('>I', 8 + len(mdat_payload)) + b'mdat'
        with open(path, 'wb') as f:
            f.write(ftyp + moov + mdat_header + mdat_payload)

    def test_moov_mdat_zip_detected_when_nested(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'episode.mp4')
            self._write_moov_mdat_mp4(path, zip_offset_in_mdat=300 * 1024)
            probe = probe_archive(path, nested=True)
            self.assertTrue(probe.is_candidate)
            self.assertTrue(probe.covered)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_real_video_with_zip_appended_after_mdat_is_detected(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'video-with-hidden-archive.mp4')
            ftyp = struct.pack('>I', 24) + b'ftyp' + b'isom\x00\x00\x02\x00' + b'isomiso2'
            moov = struct.pack('>I', 16) + b'moov' + b'\x00' * 8
            # 有效的长度前缀 H.264 NAL，使 mdat 被判定为真实视频码流。
            nal = b'\x06\x05\x00\x00'
            video = struct.pack('>I', len(nal)) + nal
            mdat = struct.pack('>I', 8 + len(video)) + b'mdat' + video
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('hidden.txt', b'payload')
            with open(path, 'wb') as f:
                f.write(ftyp + moov + mdat + archive.getvalue())

            for nested in (False, True):
                probe = probe_archive(path, nested=nested)
                self.assertTrue(probe.is_candidate)
                self.assertTrue(probe.covered)
                self.assertEqual(probe.format_type, 'zip')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_moov_gzip_bytes_do_not_false_positive(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'real.mp4')
            ftyp_size = 32
            ftyp = struct.pack('>I', ftyp_size) + b'ftyp' + b'isom\x00\x00\x02\x00'
            ftyp += b'\x00' * max(0, ftyp_size - len(ftyp))
            moov_size = 2048
            moov = struct.pack('>I', moov_size) + b'moov' + b'\x1f\x8b' + b'\x00' * (moov_size - 10)
            mdat = struct.pack('>I', 16) + b'mdat' + b'\x00' * 8
            with open(path, 'wb') as f:
                f.write(ftyp + moov + mdat)
            probe = probe_archive(path, nested=False)
            self.assertFalse(probe.is_candidate)
            probe_nested = probe_archive(path, nested=True)
            self.assertFalse(probe_nested.is_candidate)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_real_h264_mdat_ignores_deep_rar_false_positive(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'clip.mp4')
            ftyp_size = 32
            ftyp = struct.pack('>I', ftyp_size) + b'ftyp' + b'isom\x00\x00\x02\x00'
            ftyp += b'\x00' * max(0, ftyp_size - len(ftyp))
            moov_size = 4096
            moov = struct.pack('>I', moov_size) + b'moov' + b'\x00' * (moov_size - 8)
            video_head = b'\xde\x02\x00Lavc58.54.100\x00'
            deep_rar_at = 160 * 1024 * 1024
            mdat_payload = video_head + b'\x00' * (deep_rar_at - len(video_head))
            mdat_payload += b'Rar!\x1a\x07\x00' + b'\x00' * 64
            mdat = struct.pack('>I', 8 + len(mdat_payload)) + b'mdat' + mdat_payload
            with open(path, 'wb') as f:
                f.write(ftyp + moov + mdat)
            probe = probe_archive(path, nested=True)
            self.assertFalse(probe.is_candidate)
            self.assertFalse(probe_archive(path, nested=False).is_candidate)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_length_prefixed_nal_mdat_ignores_deep_archive_bytes(self):
        d = self._tmpdir()
        try:
            path = os.path.join(d, 'x264.mp4')
            ftyp_size = 32
            ftyp = struct.pack('>I', ftyp_size) + b'ftyp' + b'isom\x00\x00\x02\x00'
            ftyp += b'\x00' * max(0, ftyp_size - len(ftyp))
            moov_size = 48
            moov = struct.pack('>I', moov_size) + b'moov' + b'\x00' * (moov_size - 8)
            sei = b'\x06\x05\xff\xff' + b'x264 - core 133 - H.264/MPEG-4 AVC codec'
            video_head = struct.pack('>I', len(sei)) + sei
            deep_rar_at = 4 * 1024 * 1024
            mdat_payload = video_head + b'\x00' * (deep_rar_at - len(video_head))
            mdat_payload += b'PK\x03\x04' + b'\x00' * 64
            mdat = struct.pack('>I', 8 + len(mdat_payload)) + b'mdat' + mdat_payload
            with open(path, 'wb') as f:
                f.write(ftyp + moov + mdat)
            probe = probe_archive(path, nested=True)
            self.assertFalse(probe.is_candidate)
            self.assertFalse(probe_archive(path, nested=False).is_candidate)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class ProbeArchiveMpegTsTest(unittest.TestCase):

    @staticmethod
    def _transport_stream(packet_count: int = 8) -> bytearray:
        data = bytearray(b'\xff' * (188 * packet_count))
        for packet in range(packet_count):
            offset = packet * 188
            data[offset:offset + 4] = b'\x47\x40\x00\x10'
        return data

    def test_real_ts_with_random_gzip_magic_is_not_candidate(self):
        with tempfile.TemporaryDirectory(prefix='probe_ts_') as tmp:
            path = os.path.join(tmp, 'recording.ts')
            data = self._transport_stream()
            # 与实测误判相同：CM=8，但 FLG=0x7a 使用了 RFC 1952 保留位。
            data[64:80] = bytes.fromhex('1f8b087ac822fc33c35450401e83d3e8')
            with open(path, 'wb') as f:
                f.write(data)

            self.assertFalse(probe_archive(path, nested=False).is_candidate)
            self.assertFalse(probe_archive(path, nested=True).is_candidate)

    def test_ts_with_appended_zip_is_detected_at_all_depths(self):
        with tempfile.TemporaryDirectory(prefix='probe_ts_') as tmp:
            path = os.path.join(tmp, 'carrier.ts')
            archive = io.BytesIO()
            with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('hidden.txt', b'payload')
            with open(path, 'wb') as f:
                f.write(self._transport_stream())
                f.write(archive.getvalue())

            for nested in (False, True):
                probe = probe_archive(path, nested=nested)
                self.assertTrue(probe.is_candidate)
                self.assertTrue(probe.covered)

    def test_ts_with_appended_gzip_is_detected(self):
        with tempfile.TemporaryDirectory(prefix='probe_ts_') as tmp:
            path = os.path.join(tmp, 'gzip-carrier.ts')
            with open(path, 'wb') as f:
                f.write(self._transport_stream())
                f.write(gzip.compress(b'hidden payload' * 64))

            probe = probe_archive(path, nested=True)
            self.assertTrue(probe.is_candidate)
            self.assertTrue(probe.covered)

    def test_zip_renamed_to_ts_is_still_detected(self):
        with tempfile.TemporaryDirectory(prefix='probe_ts_') as tmp:
            path = os.path.join(tmp, 'disguised.ts')
            with zipfile.ZipFile(path, 'w') as zf:
                zf.writestr('payload.bin', b'data')

            probe = probe_archive(path, nested=True)
            self.assertTrue(probe.is_candidate)
            self.assertEqual(probe.format_type, 'zip')


if __name__ == '__main__':
    unittest.main()
