import os
import tempfile
import unittest
import wave

from audio_tagger import build_tag_values, _format_cv_list, _format_title, tag_file
from scraper.work_metadata import WorkMetadata

class AudioTaggerTests(unittest.TestCase):
    def test_format_title_strips_brackets(self):
        self.assertEqual(
            _format_title('【限定特典】作品名'),
            '作品名',
        )

    def test_format_cv_list_truncates(self):
        self.assertEqual(
            _format_cv_list(['A', 'B', 'C', 'D', 'E'], 4),
            'A;B;C;D;他',
        )

    def test_build_tag_values(self):
        metadata: WorkMetadata = {
            'rjcode': 'RJ123456',
            'work_name': '【特典】测试作品',
            'maker_id': 'RG001',
            'maker_name': '测试社团',
            'release_date': '2024-03-15',
            'series_id': '',
            'series_name': '系列A',
            'age_category': 'R18',
            'tags': ['癒し', '耳舐め'],
            'cvs': ['声優A', '声優B'],
            'cover_url': 'https://example/cover.jpg',
            'rjcodes_by_locale': {},
        }
        values = build_tag_values(metadata, '01_トラック', type('Cfg', (), {'cv_max_count': 4})())
        self.assertEqual(values['title'], '01_トラック')
        self.assertEqual(values['album'], '测试作品')
        self.assertEqual(values['artist'], '声優A;声優B')
        self.assertEqual(values['albumartist'], '测试社团')
        self.assertEqual(values['year'], '2024')
        self.assertEqual(values['genre'], '癒し')
        self.assertEqual(values['rjcode'], 'RJ123456')

    def test_clamp_workers(self):
        from audio_tagger import _clamp_workers
        self.assertEqual(_clamp_workers(0), 1)
        self.assertEqual(_clamp_workers(4), 4)
        self.assertEqual(_clamp_workers(99), 32)
        self.assertEqual(_clamp_workers('bad'), 4)

    def test_tag_wav_file(self):
        metadata: WorkMetadata = {
            'rjcode': 'RJ123456',
            'work_name': '测试作品',
            'maker_id': 'RG001',
            'maker_name': '测试社团',
            'release_date': '2024-03-15',
            'series_id': '',
            'series_name': '',
            'age_category': 'R18',
            'tags': ['癒し'],
            'cvs': ['声優A'],
            'cover_url': '',
            'rjcodes_by_locale': {},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, '01_track.wav')
            with wave.open(path, 'w') as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(44100)
                handle.writeframes(b'\x00\x00' * 100)

            self.assertTrue(
                tag_file(
                    path,
                    metadata,
                    {'extensions': ['.flac', '.mp3', '.wav'], 'embed_cover': False},
                )
            )

            from mutagen.wave import WAVE
            audio = WAVE(path)
            self.assertIsNotNone(audio.tags)
            self.assertEqual(audio.tags.get('TIT2').text[0], '01_track')
            self.assertEqual(audio.tags.get('TALB').text[0], '测试作品')
            self.assertEqual(audio.tags.get('TPE1').text[0], '声優A')


if __name__ == '__main__':
    unittest.main()
