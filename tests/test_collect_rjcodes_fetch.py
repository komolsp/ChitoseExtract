import unittest

from scraper.rjcode_locales import collect_rjcodes_by_locale


class TestCollectRjcodesFetchCount(unittest.TestCase):
    def test_language_editions_avoids_extra_fetches(self):
        store = {
            'RJ01352699': {
                'workno': 'RJ01352699',
                'translation_info': {'is_original': True, 'child_worknos': []},
                'language_editions': [
                    {'workno': 'RJ01352699', 'lang': 'JPN'},
                    {'workno': 'RJ01387553', 'lang': 'CHI_HANS'},
                    {'workno': 'RJ01387563', 'lang': 'CHI_HANT'},
                    {'workno': 'RJ01549527', 'lang': 'ENG'},
                ],
            },
        }
        fetch_count = 0

        def fetch(workno: str):
            nonlocal fetch_count
            workno = workno.upper()
            if workno not in store:
                return None
            fetch_count += 1
            return store[workno]

        by_locale = collect_rjcodes_by_locale(
            store['RJ01352699'],
            None,
            fetch,
            scraper_locale='ja_jp',
        )
        self.assertEqual(fetch_count, 0)
        self.assertEqual(by_locale, {'ja_jp': 'RJ01352699'})

    def test_invalid_language_edition_is_skipped(self):
        store = {
            'RJ01561298': {
                'workno': 'RJ01561298',
                'translation_info': {'is_original': True, 'child_worknos': []},
                'language_editions': [
                    {'workno': 'RJ01561298', 'lang': 'JPN'},
                    {'workno': 'RJ01583337', 'lang': 'CHI_HANS'},
                    {'workno': 'RJ01583339', 'lang': 'CHI_HANT'},
                ],
            },
        }
        calls: list[str] = []

        def fetch(workno: str):
            workno = workno.upper()
            calls.append(workno)
            if workno not in store:
                return None
            return store[workno]

        by_locale = collect_rjcodes_by_locale(
            store['RJ01561298'],
            None,
            fetch,
            scraper_locale='ja_jp',
        )
        self.assertEqual(by_locale, {'ja_jp': 'RJ01561298'})
        self.assertEqual(calls, ['RJ01583337', 'RJ01583339'])

    def test_translation_chain_still_fetches_when_editions_missing(self):
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
        fetch_count = 0

        def fetch(workno: str):
            nonlocal fetch_count
            workno = workno.upper()
            if workno not in store:
                return None
            fetch_count += 1
            return store[workno]

        by_locale = collect_rjcodes_by_locale(
            store['RJ01256556'],
            store['RJ01256505'],
            fetch,
            scraper_locale='zh_cn',
        )
        self.assertEqual(fetch_count, 1)
        self.assertEqual(by_locale['zh_tw'], 'RJ09999999')
