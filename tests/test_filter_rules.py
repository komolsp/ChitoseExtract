"""无 SE / 无效果音过滤规则单元测试。"""

import re
import unittest

from filter_rules import DEFAULT_FILTER_RULES, build_filter_keywords


def _matches_any(path: str, keywords: list[str]) -> bool:
    upper = path.upper()
    return any(re.search(keyword, upper) for keyword in keywords)


class TestNoSeFilterRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keywords = build_filter_keywords(dict(DEFAULT_FILTER_RULES))

    def test_no_sfx_chinese_folder_wav(self):
        path = (
            r'曾经当作自慰配菜的憧憬的JK偶像组成了淫乱的女仆后宫'
            r'\正篇(无效果音ver)\WAV\01：序章.wav'
        )
        self.assertTrue(_matches_any(path, self.keywords))

    def test_no_sfx_chinese_folder_segment(self):
        path = r'D:\work\正篇(无效果音ver)\WAV'
        self.assertTrue(_matches_any(path, self.keywords))

    def test_normal_version_not_matched_by_no_se_wav(self):
        path = r'work\正篇(普通ver)\WAV\01.wav'
        self.assertFalse(_matches_any(path, self.keywords))

    def test_japanese_koukaon_nashi_still_matches(self):
        path = r'ダブルメイドまとめ\2.効果音無し本編\wav\NonSE.wav'
        self.assertTrue(_matches_any(path, self.keywords))

    def test_no_se_folder_name(self):
        path = r'D:\音声\[RJ12345678]秘书\wav_NO SE\01.wav'
        self.assertTrue(_matches_any(path, self.keywords))

    def test_koukaon_cut_folder(self):
        """日文「効果音カット版」应命中无 SE 规则。"""
        path = r'D:\work\[RJ01330941]title\03_効果音カット版\02_wav\01.flac'
        self.assertTrue(_matches_any(path, self.keywords))

    def test_koukaon_cut_folder_segment(self):
        path = r'D:\work\title\03_効果音カット版'
        self.assertTrue(_matches_any(path, self.keywords))

    def test_chinese_sfx_cut_folder(self):
        """中文「效果音删减版」应命中无 SE 规则。"""
        path = r'D:\work\title\03_效果音删减版\02_wav\01.flac'
        self.assertTrue(_matches_any(path, self.keywords))

    def test_chinese_sfx_cut_folder_segment(self):
        path = r'D:\work\title\03_效果音删减版'
        self.assertTrue(_matches_any(path, self.keywords))

    def test_chinese_sfx_jianxiao_variant(self):
        path = r'D:\work\title\效果音削減版'
        self.assertTrue(_matches_any(path, self.keywords))


class TestMp3LosslessGuard(unittest.TestCase):
    """有 FLAC 等无损时允许删 MP3；仅有 MP3 时保护。"""

    def test_skip_mp3_when_no_lossless(self):
        from filter import Filter

        f = Filter([r'\.MP3$'], True, logger=_NullLogger())
        self.assertTrue(f._skip_mp3_without_lossless(r'D:\a\track.mp3', has_lossless=False))
        self.assertFalse(f._skip_mp3_without_lossless(r'D:\a\track.mp3', has_lossless=True))
        self.assertFalse(f._skip_mp3_without_lossless(r'D:\a\cover.jpg', has_lossless=False))

    def test_scope_detects_flac(self):
        from filter import Filter

        self.assertTrue(Filter._scope_has_lossless(['02_wav', '01.flac', 'cover.jpg']))
        self.assertFalse(Filter._scope_has_lossless(['01_mp3', '01.mp3', 'cover.jpg']))


class _NullLogger:
    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


if __name__ == '__main__':
    unittest.main()
