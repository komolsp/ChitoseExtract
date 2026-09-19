"""过滤正则的编译期防护测试（review 条目 C-7）。

过滤关键词来自 config.yaml，可被手写成嵌套量词（(x+)+ 形态），
在长路径上引发灾难性回溯。Python 的 re 没有匹配超时，
唯一可行的防护是在编译期拦截。
"""

import unittest

from filter_rules import (
    DEFAULT_FILTER_RULES,
    FILTER_RULES,
    MAX_FILTER_PATTERN_LENGTH,
    build_filter_keywords,
    compile_directory_name_patterns,
    compile_filter_pattern,
    looks_catastrophic,
)


class _RecordingLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


class TestCatastrophicPatternDetection(unittest.TestCase):
    def test_nested_quantifiers_are_detected(self):
        for pattern in (r'(a+)+$', r'(a*)*b', r'(x+){2,}', r'(?:ab+)+c'):
            with self.subTest(pattern=pattern):
                self.assertTrue(looks_catastrophic(pattern))

    def test_ordinary_patterns_are_not_flagged(self):
        for pattern in (r'(abc)+', r'(?:WAV|FLAC)+', r'(a|b)+',
                        r'\d{3}', r'(x+)(y+)', r'(?:a+b)', r'\.WAV$'):
            with self.subTest(pattern=pattern):
                self.assertFalse(looks_catastrophic(pattern))

    def test_no_builtin_rule_is_falsely_flagged(self):
        """内置规则绝不能被误判，否则会静默丢失过滤能力。"""
        flagged = [
            (rule['id'], pattern)
            for rule in FILTER_RULES
            for pattern in rule['patterns']
            if looks_catastrophic(pattern)
        ]
        self.assertEqual(flagged, [], '内置规则被误判为嵌套量词')

    def test_all_builtin_patterns_still_compile(self):
        """加固后所有内置规则必须仍能正常编译（无告警）。"""
        logger = _RecordingLogger()
        keywords = build_filter_keywords(dict(DEFAULT_FILTER_RULES))
        compiled = compile_directory_name_patterns(keywords, logger)
        self.assertEqual(logger.warnings, [])
        for keyword in keywords:
            with self.subTest(keyword=keyword[:40]):
                self.assertIsNotNone(compile_filter_pattern(keyword, logger))
        self.assertEqual(logger.warnings, [])


class TestCompileFilterPattern(unittest.TestCase):
    def test_catastrophic_pattern_is_skipped_with_warning(self):
        logger = _RecordingLogger()
        self.assertIsNone(compile_filter_pattern(r'(a+)+$', logger))
        self.assertEqual(len(logger.warnings), 1)
        self.assertIn('嵌套量词', logger.warnings[0])

    def test_overlong_pattern_is_skipped_with_warning(self):
        logger = _RecordingLogger()
        pattern = 'a' * (MAX_FILTER_PATTERN_LENGTH + 1)
        self.assertIsNone(compile_filter_pattern(pattern, logger))
        self.assertEqual(len(logger.warnings), 1)
        self.assertIn('过长', logger.warnings[0])

    def test_invalid_pattern_is_skipped_with_warning(self):
        logger = _RecordingLogger()
        self.assertIsNone(compile_filter_pattern('(unclosed', logger))
        self.assertEqual(len(logger.warnings), 1)

    def test_valid_pattern_compiles_case_insensitively(self):
        compiled = compile_filter_pattern('FULL', None)
        self.assertIsNotNone(compiled)
        self.assertTrue(compiled.search('full track'))

    def test_compile_never_raises(self):
        """任何输入都不得让编译抛出，否则会中断整个过滤流程。"""
        for pattern in (r'(a+)+$', '(unclosed', '[z-a]', '', 'a' * 20000):
            with self.subTest(pattern=pattern[:20]):
                compile_filter_pattern(pattern, None)

    def test_logger_is_optional(self):
        self.assertIsNone(compile_filter_pattern(r'(a+)+$', None))


if __name__ == '__main__':
    unittest.main()
