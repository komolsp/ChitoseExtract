import os
import struct
import tempfile
import unittest
import wave

import audio_convert


class AudioConvertTests(unittest.TestCase):
    def test_wav_format_tag_pcm(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'pcm.wav')
            with wave.open(path, 'w') as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(44100)
                handle.writeframes(struct.pack('<h', 0) * 100)
            self.assertEqual(audio_convert._wav_format_tag(path), 1)
            self.assertFalse(audio_convert._needs_ffmpeg_for_source(path))

    def test_wav_format_tag_float(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'float.wav')
            with open(path, 'wb') as handle:
                handle.write(b'RIFF')
                handle.write(struct.pack('<I', 36))
                handle.write(b'WAVEfmt ')
                handle.write(struct.pack('<IHHIIHH', 16, 3, 1, 44100, 176400, 4, 32))
                handle.write(b'data')
                handle.write(struct.pack('<I', 4))
                handle.write(struct.pack('<f', 0.0))
            self.assertEqual(audio_convert._wav_format_tag(path), 3)
            self.assertTrue(audio_convert._needs_ffmpeg_for_source(path))

    def test_subprocess_detail_prefers_error_line(self):
        result = type('R', (), {
            'stdout': '',
            'stderr': 'flac 1.5.0\nsample.wav: ERROR: unsupported format type 3\n',
        })()
        self.assertEqual(
            audio_convert._subprocess_detail(result),
            'sample.wav: ERROR: unsupported format type 3',
        )

    def test_clamp_workers(self):
        self.assertEqual(audio_convert._clamp_workers(0), 1)
        self.assertEqual(audio_convert._clamp_workers(4), 4)
        self.assertEqual(audio_convert._clamp_workers(99), 32)
        self.assertEqual(audio_convert._clamp_workers('bad'), 4)


if __name__ == '__main__':
    unittest.main()
