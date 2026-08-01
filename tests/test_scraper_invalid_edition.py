import unittest

from scraper.scraper import Scraper
from scraper.locale import Locale


class TestScraperInvalidEditionFallback(unittest.TestCase):
    def test_rj01561298_falls_back_when_zh_edition_missing(self):
        store = {
            'RJ01561298': {
                'workno': 'RJ01561298',
                'work_name': 'succubus homestay',
                'maker_id': 'RG1',
                'maker_name': 'Ogre illust',
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
                    {'workno': 'RJ01561298', 'lang': 'JPN'},
                    {'workno': 'RJ01583337', 'lang': 'CHI_HANS'},
                ],
            },
        }
        api_calls: list[str] = []

        class FakeScraper(Scraper):
            def __init__(self):
                super().__init__(Locale.zh_cn, proxies={'http': 'http://127.0.0.1:9', 'https': 'http://127.0.0.1:9'}, sleep_interval=0)

            def _Scraper__request_product_api(self, workno: str):
                workno = workno.upper()
                api_calls.append(workno)
                if workno not in store:
                    from requests.exceptions import HTTPError
                    response = type('R', (), {'status_code': 404, 'reason': 'Not Found'})()
                    raise HTTPError('404', response=response)
                return store[workno]

        metadata = FakeScraper().scrape_metadata('RJ01561298')
        self.assertEqual(metadata['rjcode'], 'RJ01561298')
        self.assertEqual(metadata['work_name'], 'succubus homestay')
        self.assertEqual(metadata['rjcodes_by_locale'], {'ja_jp': 'RJ01561298'})
        self.assertEqual(api_calls, ['RJ01561298', 'RJ01583337'])
