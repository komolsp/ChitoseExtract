import json
import unittest

from scraper.cached_scraper import CachedScraper
from scraper.locale import Locale
from scraper.rjcode_locales import (
    METADATA_SCHEMA_VERSION,
    collect_rjcodes_by_locale,
    metadata_needs_locale_refresh,
)


class TestMetadataLocaleRefresh(unittest.TestCase):
    def test_old_cache_needs_refresh(self):
        self.assertTrue(metadata_needs_locale_refresh({'rjcode': 'RJ000001'}))
        self.assertTrue(metadata_needs_locale_refresh({
            'metadata_schema_version': 1,
            'rjcode': 'RJ000001',
            'rjcodes_by_locale': {'ja_jp': 'RJ000001'},
        }))

    def test_current_cache_is_fresh(self):
        self.assertFalse(metadata_needs_locale_refresh({
            'metadata_schema_version': METADATA_SCHEMA_VERSION,
            'scraper_locale': 'ja_jp',
            'rjcode': 'RJ000001',
            'rjcodes_by_locale': {'ja_jp': 'RJ000001'},
        }, scraper_locale='ja_jp'))

    def test_locale_mismatch_needs_refresh(self):
        self.assertTrue(metadata_needs_locale_refresh({
            'metadata_schema_version': METADATA_SCHEMA_VERSION,
            'scraper_locale': 'ja_jp',
            'rjcode': 'RJ000001',
            'rjcodes_by_locale': {'ja_jp': 'RJ000001'},
        }, scraper_locale='zh_cn'))


class TestCachedScraperRefresh(unittest.TestCase):
    def test_refreshes_stale_cache_entry(self):
        from scraper.db import WorkMetadataCache, db

        db.connect(reuse_if_open=True)
        stale = {
            'rjcode': 'RJ01352699',
            'work_name': 'test',
            'maker_id': 'RG1',
            'maker_name': 'M',
            'release_date': '2024-01-01',
            'series_id': '',
            'series_name': '',
            'age_category': 'R18',
            'tags': [],
            'cvs': [],
            'cover_url': '',
            'rjcodes_by_locale': {'ja_jp': 'RJ01352699'},
        }
        WorkMetadataCache.delete().where(WorkMetadataCache.rjcode == 'RJ01352699').execute()
        WorkMetadataCache.create(rjcode='RJ01352699', metadata=json.dumps(stale))

        store = {
            'RJ01352699': {
                'workno': 'RJ01352699',
                'work_name': 'test',
                'maker_id': 'RG1',
                'maker_name': 'M',
                'regist_date': '2024-01-01T00:00:00+09:00',
                'series_id': '',
                'series_name': '',
                'age_category': 3,
                'genres': [],
                'creaters': {},
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
                'translation_info': {
                    'lang': 'CHI_HANS',
                    'original_workno': 'RJ01352699',
                    'child_worknos': ['RJ01387554'],
                },
            },
            'RJ01387563': {
                'workno': 'RJ01387563',
                'translation_info': {
                    'lang': 'CHI_HANT',
                    'original_workno': 'RJ01352699',
                    'child_worknos': ['RJ01387564'],
                },
            },
        }

        class FakeScraper(CachedScraper):
            def __init__(self):
                super().__init__(Locale.ja_jp, proxies={'http': 'http://127.0.0.1:9', 'https': 'http://127.0.0.1:9'})

            def _Scraper__request_product_api(self, workno: str):
                return store[workno.upper()]

        scraper = FakeScraper()
        metadata = scraper.scrape_metadata('RJ01352699')
        self.assertEqual(metadata['metadata_schema_version'], METADATA_SCHEMA_VERSION)
        self.assertEqual(metadata['rjcodes_by_locale'], {
            'ja_jp': 'RJ01352699',
            'zh_cn': 'RJ01387553',
            'zh_tw': 'RJ01387563',
        })

        WorkMetadataCache.delete().where(WorkMetadataCache.rjcode == 'RJ01352699').execute()

    def test_refreshes_when_scraper_locale_changes(self):
        from scraper.db import WorkMetadataCache, db

        db.connect(reuse_if_open=True)
        cached = {
            'metadata_schema_version': METADATA_SCHEMA_VERSION,
            'scraper_locale': 'ja_jp',
            'rjcode': 'RJ000001',
            'work_name': 'japanese title',
            'maker_id': 'RG1',
            'maker_name': 'M',
            'release_date': '2024-01-01',
            'series_id': '',
            'series_name': '',
            'age_category': 'R18',
            'tags': [],
            'cvs': [],
            'cover_url': '',
            'rjcodes_by_locale': {'ja_jp': 'RJ000001'},
        }
        WorkMetadataCache.delete().where(WorkMetadataCache.rjcode == 'RJ000001').execute()
        WorkMetadataCache.create(rjcode='RJ000001', metadata=json.dumps(cached))

        product = {
            'workno': 'RJ000001',
            'work_name': 'chinese title',
            'maker_id': 'RG1',
            'maker_name': 'M',
            'regist_date': '2024-01-01T00:00:00+09:00',
            'series_id': '',
            'series_name': '',
            'age_category': 3,
            'genres': [],
            'creaters': {},
            'image_main': {'url': '//example.com/a.jpg'},
            'options': 'SND#JPN#DLP',
            'translation_info': {'is_original': True, 'child_worknos': []},
            'language_editions': [{'workno': 'RJ000001', 'lang': 'JPN'}],
        }

        class FakeScraper(CachedScraper):
            def __init__(self, locale: Locale):
                super().__init__(locale, proxies={'http': 'http://127.0.0.1:9', 'https': 'http://127.0.0.1:9'})

            def _Scraper__request_product_api(self, workno: str):
                return product

        metadata = FakeScraper(Locale.zh_cn).scrape_metadata('RJ000001')
        self.assertEqual(metadata['scraper_locale'], 'zh_cn')
        self.assertEqual(metadata['work_name'], 'chinese title')

        WorkMetadataCache.delete().where(WorkMetadataCache.rjcode == 'RJ000001').execute()
