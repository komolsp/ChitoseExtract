import unittest

from scraper.scraper import Scraper
from scraper.locale import Locale


class TestScraperRequestDedup(unittest.TestCase):
    def test_single_scrape_deduplicates_api_calls(self):
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
                'genres': [{'name': 'tag'}],
                'creaters': {'voice_by': [{'name': 'cv'}]},
                'image_main': {'url': '//example.com/a.jpg'},
                'options': 'SND#JPN#DLP',
                'translation_info': {'is_original': True, 'child_worknos': []},
                'language_editions': [
                    {'workno': 'RJ01352699', 'lang': 'JPN'},
                    {'workno': 'RJ01387553', 'lang': 'CHI_HANS'},
                    {'workno': 'RJ01387563', 'lang': 'CHI_HANT'},
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
            'RJ01387563': {
                'workno': 'RJ01387563',
                'work_name': 'traditional title',
                'maker_id': 'RG9',
                'maker_name': 'translator',
                'regist_date': '2024-02-01T00:00:00+09:00',
                'series_id': '',
                'series_name': '',
                'age_category': 3,
                'genres': [{'name': '繁體標籤'}],
                'creaters': {'voice_by': [{'name': '繁體CV'}]},
                'image_main': {'url': '//example.com/c.jpg'},
                'options': 'SND#TRI#DLP#CHI_HANT',
                'translation_info': {
                    'lang': 'CHI_HANT',
                    'is_parent': True,
                    'original_workno': 'RJ01352699',
                    'child_worknos': ['RJ01387564'],
                },
            },
        }
        api_calls = 0

        class CountingScraper(Scraper):
            def __init__(self):
                super().__init__(Locale.zh_cn, proxies={'http': 'http://127.0.0.1:9', 'https': 'http://127.0.0.1:9'}, sleep_interval=0)

            def _Scraper__request_product_api(self, workno: str):
                nonlocal api_calls
                api_calls += 1
                workno = workno.upper()
                if workno not in store:
                    from requests.exceptions import HTTPError
                    response = type('R', (), {'status_code': 404, 'reason': 'Not Found'})()
                    raise HTTPError('404', response=response)
                return store[workno]

        metadata = CountingScraper().scrape_metadata('RJ01352699')
        self.assertEqual(api_calls, 3)
        self.assertEqual(metadata['work_name'], 'chinese title')
        self.assertEqual(metadata['rjcodes_by_locale']['zh_cn'], 'RJ01387553')
        self.assertEqual(metadata['rjcodes_by_locale']['zh_tw'], 'RJ01387563')
