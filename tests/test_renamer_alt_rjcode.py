import unittest

from config_file import DEFAULT_CONFIG
from dlrenamer.runner import create_renamer_from_dict
from renamer import detect_rjcode_wrap_style, format_rjcode_replacement
from scraper.rjcode_locales import (
    classify_workno_locale,
    collect_rjcodes_by_locale,
    normalize_display_locales,
    translation_lang_to_locale,
)


class TestRjcodeLocaleHelpers(unittest.TestCase):
    def test_translation_lang_to_locale(self):
        self.assertEqual(translation_lang_to_locale('zh-CN'), 'zh_cn')
        self.assertEqual(translation_lang_to_locale('zh_TW'), 'zh_tw')
        self.assertEqual(translation_lang_to_locale('ja_JP'), 'ja_jp')
        self.assertEqual(translation_lang_to_locale('CHI_HANS'), 'zh_cn')
        self.assertEqual(translation_lang_to_locale('CHI_HANT'), 'zh_tw')

    def test_options_to_locale(self):
        from scraper.rjcode_locales import options_to_locale
        self.assertEqual(options_to_locale('SND#TRI#DLP#CHI_HANS'), 'zh_cn')
        self.assertEqual(options_to_locale('SND#JPN#DLP'), 'ja_jp')

    def test_collects_original_and_child_worknos(self):
        store = {
            'RJ01256556': {
                'workno': 'RJ01256556',
                'translation_info': {
                    'lang': 'zh_CN',
                    'original_workno': 'RJ01256505',
                    'child_worknos': [],
                },
            },
            'RJ01256505': {
                'workno': 'RJ01256505',
                'translation_info': {
                    'is_original': True,
                    'child_worknos': ['RJ01256556', 'RJ09999999'],
                },
            },
            'RJ09999999': {
                'workno': 'RJ09999999',
                'translation_info': {
                    'lang': 'zh_TW',
                    'original_workno': 'RJ01256505',
                },
            },
        }

        def fetch(workno: str) -> dict:
            return store[workno.upper()]

        by_locale = collect_rjcodes_by_locale(
            store['RJ01256556'],
            store['RJ01256505'],
            fetch,
            scraper_locale='zh_cn',
        )
        self.assertEqual(by_locale, {
            'zh_cn': 'RJ01256556',
            'ja_jp': 'RJ01256505',
            'zh_tw': 'RJ09999999',
        })

    def test_rj01352699_original_via_language_editions(self):
        store = {
            'RJ01352699': {
                'workno': 'RJ01352699',
                'options': 'SND#JPN#DLP#REV#TRI',
                'translation_info': {
                    'is_original': True,
                    'is_child': False,
                    'original_workno': None,
                    'child_worknos': [],
                },
                'language_editions': [
                    {'workno': 'RJ01352699', 'lang': 'JPN'},
                    {'workno': 'RJ01387553', 'lang': 'CHI_HANS'},
                    {'workno': 'RJ01387563', 'lang': 'CHI_HANT'},
                ],
            },
            'RJ01387553': {
                'workno': 'RJ01387553',
                'options': 'SND#TRI#DLP#CHI_HANS',
                'translation_info': {
                    'lang': 'CHI_HANS',
                    'is_parent': True,
                    'original_workno': 'RJ01352699',
                    'child_worknos': ['RJ01387554', 'RJ01387567'],
                },
            },
            'RJ01387563': {
                'workno': 'RJ01387563',
                'options': 'SND#TRI#DLP#CHI_HANT',
                'translation_info': {
                    'lang': 'CHI_HANT',
                    'is_parent': True,
                    'original_workno': 'RJ01352699',
                    'child_worknos': ['RJ01387564'],
                },
            },
        }

        by_locale = collect_rjcodes_by_locale(
            store['RJ01352699'],
            None,
            lambda workno: store.get(workno.upper()),
            scraper_locale='ja_jp',
        )
        self.assertEqual(by_locale, {
            'ja_jp': 'RJ01352699',
            'zh_cn': 'RJ01387553',
            'zh_tw': 'RJ01387563',
        })

    def test_rj01387554_scenario(self):
        store = {
            'RJ01387554': {
                'workno': 'RJ01387554',
                'options': 'SND#TRI#DLP#CHI_HANS',
                'translation_info': {
                    'lang': 'CHI_HANS',
                    'is_child': True,
                    'is_original': False,
                    'original_workno': 'RJ01352699',
                    'parent_workno': 'RJ01387553',
                    'child_worknos': [],
                },
            },
            'RJ01352699': {
                'workno': 'RJ01352699',
                'options': 'SND#JPN#DLP#REV#TRI',
                'translation_info': {
                    'is_original': True,
                    'is_child': False,
                    'original_workno': None,
                    'child_worknos': [],
                },
            },
            'RJ01387553': {
                'workno': 'RJ01387553',
                'options': 'SND#TRI#DLP#CHI_HANS',
                'translation_info': {
                    'lang': 'CHI_HANS',
                    'is_parent': True,
                    'original_workno': 'RJ01352699',
                    'child_worknos': ['RJ01387554', 'RJ01387567'],
                },
            },
        }

        by_locale = collect_rjcodes_by_locale(
            store['RJ01387554'],
            store['RJ01352699'],
            lambda workno: store.get(workno.upper()),
            scraper_locale='ja_jp',
        )
        self.assertEqual(by_locale, {
            'zh_cn': 'RJ01387554',
            'ja_jp': 'RJ01352699',
        })


class TestFormatRjcodeReplacement(unittest.TestCase):
    def _metadata(self):
        return {
            'rjcode': 'RJ01256556',
            'work_name': 'test',
            'maker_id': 'RG1',
            'maker_name': 'M',
            'release_date': '2024-03-15',
            'series_id': '',
            'series_name': '',
            'age_category': 'R18',
            'tags': [],
            'cvs': [],
            'cover_url': '',
            'rjcodes_by_locale': {
                'ja_jp': 'RJ01256505',
                'zh_cn': 'RJ01256556',
                'zh_tw': 'RJ09999999',
            },
        }

    def test_no_locale_selected_uses_primary(self):
        value = format_rjcode_replacement(
            self._metadata(),
            display_locales=[],
            delimiter=' ',
            wrap_style='square',
        )
        self.assertEqual(value, 'RJ01256556')

    def test_selected_locales_skip_missing(self):
        metadata = dict(self._metadata())
        metadata['rjcodes_by_locale'] = {'ja_jp': 'RJ01256505', 'zh_cn': 'RJ01256556'}
        value = format_rjcode_replacement(
            metadata,
            display_locales=['ja_jp', 'zh_cn', 'zh_tw'],
            delimiter=' ',
            wrap_style='square',
        )
        self.assertEqual(value, 'RJ01256505][RJ01256556')

    def test_missing_selected_falls_back_to_primary(self):
        metadata = dict(self._metadata())
        metadata['rjcodes_by_locale'] = {'ja_jp': 'RJ01256505'}
        value = format_rjcode_replacement(
            metadata,
            display_locales=['zh_tw'],
            delimiter=' ',
            wrap_style='none',
        )
        self.assertEqual(value, 'RJ01256556')

    def test_missing_selected_zh_cn_falls_back_to_primary(self):
        metadata = dict(self._metadata())
        metadata['rjcode'] = 'RJ01352699'
        metadata['rjcodes_by_locale'] = {'ja_jp': 'RJ01352699'}
        value = format_rjcode_replacement(
            metadata,
            display_locales=['zh_cn'],
            delimiter=' ',
            wrap_style='square',
        )
        self.assertEqual(value, 'RJ01352699')

    def test_normalize_display_locales(self):
        self.assertEqual(
            normalize_display_locales(['zh_cn', 'invalid', 'ja_jp', 'zh_cn']),
            ['zh_cn', 'ja_jp'],
        )


class TestRenamerAltRjcodeIntegration(unittest.TestCase):
    def test_preview_with_selected_locales(self):
        metadata = {
            'rjcode': 'RJ01256556',
            'work_name': 'test',
            'maker_id': 'RG1',
            'maker_name': 'M',
            'release_date': '2024-03-15',
            'series_id': '',
            'series_name': '',
            'age_category': 'R18',
            'tags': [],
            'cvs': [],
            'cover_url': '',
            'rjcodes_by_locale': {
                'ja_jp': 'RJ01256505',
                'zh_cn': 'RJ01256556',
            },
        }
        cfg = dict(DEFAULT_CONFIG)
        cfg['renamer_template'] = '[maker_name] work_name [rjcode]'
        cfg['renamer_rjcode_display_locales'] = ['ja_jp', 'zh_cn']
        renamer, errors = create_renamer_from_dict(cfg)
        self.assertEqual(errors, [])
        name = renamer.preview_folder_name(metadata)
        self.assertEqual(name, '[M] test [RJ01256505][RJ01256556]')

    def test_detect_wrap_style(self):
        self.assertEqual(detect_rjcode_wrap_style('[rjcode] work_name'), 'square')
        self.assertEqual(detect_rjcode_wrap_style('(rjcode) work_name'), 'round')
        self.assertEqual(detect_rjcode_wrap_style('rjcode work_name'), 'none')
