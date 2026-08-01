import unittest

from scraper.rjcode_locales import resolve_edition_workno_for_locale


class TestResolveEditionWorkno(unittest.TestCase):
    def _seed(self):
        return {
            'workno': 'RJ01352699',
            'translation_info': {'is_original': True, 'child_worknos': []},
            'language_editions': [
                {'workno': 'RJ01352699', 'lang': 'JPN'},
                {'workno': 'RJ01387553', 'lang': 'CHI_HANS'},
                {'workno': 'RJ01387563', 'lang': 'CHI_HANT'},
            ],
        }

    def test_same_locale_keeps_workno(self):
        self.assertEqual(
            resolve_edition_workno_for_locale(self._seed(), 'ja_jp'),
            'RJ01352699',
        )

    def test_zh_cn_resolves_translation_edition(self):
        self.assertEqual(
            resolve_edition_workno_for_locale(self._seed(), 'zh_cn'),
            'RJ01387553',
        )

    def test_zh_tw_resolves_translation_edition(self):
        self.assertEqual(
            resolve_edition_workno_for_locale(self._seed(), 'zh_tw'),
            'RJ01387563',
        )

    def test_child_workno_keeps_when_locale_matches(self):
        info = {
            'workno': 'RJ01387554',
            'options': 'SND#TRI#DLP#CHI_HANS',
            'translation_info': {
                'lang': 'CHI_HANS',
                'is_child': True,
                'original_workno': 'RJ01352699',
            },
            'language_editions': self._seed()['language_editions'],
        }
        self.assertEqual(
            resolve_edition_workno_for_locale(info, 'zh_cn'),
            'RJ01387554',
        )


class TestScraperLocalizedMetadata(unittest.TestCase):
    def test_japanese_rj_with_zh_cn_uses_chinese_title(self):
        from scraper.scraper import Scraper
        from scraper.locale import Locale

        store = {
            'RJ01352699': {
                'workno': 'RJ01352699',
                'work_name': 'japanese title',
                'maker_id': 'RG1',
                'maker_name': '桜色ピアノ',
                'regist_date': '2024-01-01T00:00:00+09:00',
                'series_id': '',
                'series_name': '',
                'age_category': 3,
                'genres': [{'name': '色诱'}],
                'creaters': {'voice_by': [{'name': 'CV1'}]},
                'image_main': {'url': '//example.com/a.jpg'},
                'options': 'SND#JPN#DLP',
                'translation_info': {'is_original': True, 'child_worknos': []},
                'language_editions': [
                    {'workno': 'RJ01352699', 'lang': 'JPN'},
                    {'workno': 'RJ01387553', 'lang': 'CHI_HANS'},
                ],
            },
            'RJ01387553': {
                'workno': 'RJ01387553',
                'work_name': 'chinese title',
                'maker_id': 'RG9',
                'maker_name': 'translator',
                'regist_date': '2024-02-01T00:00:00+09:00',
                'series_id': '',
                'series_name': '',
                'age_category': 3,
                'genres': [{'name': '中文标签'}],
                'creaters': {'voice_by': [{'name': '中文CV'}]},
                'image_main': {'url': '//example.com/b.jpg'},
                'options': 'SND#TRI#DLP#CHI_HANS',
                'translation_info': {
                    'lang': 'CHI_HANS',
                    'is_parent': True,
                    'original_workno': 'RJ01352699',
                    'child_worknos': ['RJ01387554'],
                },
            },
        }

        class FakeScraper(Scraper):
            def __init__(self):
                super().__init__(Locale.zh_cn, proxies={'http': 'http://127.0.0.1:9', 'https': 'http://127.0.0.1:9'}, sleep_interval=0)

            def _Scraper__request_product_api(self, workno: str):
                return store[workno.upper()]

        metadata = FakeScraper().scrape_metadata('RJ01352699')
        self.assertEqual(metadata['rjcode'], 'RJ01352699')
        self.assertEqual(metadata['work_name'], 'chinese title')
        self.assertEqual(metadata['maker_name'], '桜色ピアノ')
        self.assertEqual(metadata['tags'], ['中文标签'])
        self.assertEqual(metadata['scraper_locale'], 'zh_cn')
