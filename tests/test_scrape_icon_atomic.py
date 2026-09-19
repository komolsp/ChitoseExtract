"""scrape_icon 的原子性测试。

覆盖 review 发现的 C8 另一半：原实现先 unlink 旧 .ico 与 cover.jpg 再下载，
网络或转码一旦失败，旧图标已被删除、cover.jpg 只剩 urlretrieve 建出的空文件，
属于不可逆损失。

这里 stub 掉 requests / PIL 与 scraper 包依赖（peewee 等），
以便在没有完整依赖的环境下也能验证「失败不破坏既有产物」。
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAIL_DOWNLOAD = {'fail': False}


class _FakeImage:
    def __init__(self, size=(64, 64)):
        self.size = size

    def convert(self, mode):
        return self

    def paste(self, other, box):
        pass

    def save(self, path, **kwargs):
        with open(path, 'wb') as handle:
            handle.write(b'ICO')


class _FakeImageModule:
    @staticmethod
    def open(path):
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return _FakeImage()

    @staticmethod
    def new(mode, size, color):
        return _FakeImage(size=size)


def _install_stubs():
    saved = {}

    def put(name, module):
        saved.setdefault(name, sys.modules.get(name))
        sys.modules[name] = module

    pil = types.ModuleType('PIL')
    pil.Image = _FakeImageModule
    put('PIL', pil)
    put('PIL.Image', pil)

    # scraper 包的 __init__ 会导入 cached_scraper → db → peewee；必须先占位。
    pkg = types.ModuleType('scraper')
    pkg.__path__ = [os.path.join(_ROOT, 'scraper')]
    put('scraper', pkg)

    wm = types.ModuleType('scraper.work_metadata')
    wm.WorkMetadata = dict
    wm.CV_LIST_SEPARATOR = ';'
    put('scraper.work_metadata', wm)

    for name in ('scraper.dlsite', 'scraper.locale', 'scraper.rjcode_locales'):
        put(name, types.ModuleType(name))
    # scraper.scraper 在导入期就要 from ... import Dlsite / Locale。
    sys.modules['scraper.dlsite'].Dlsite = type('Dlsite', (), {})
    sys.modules['scraper.locale'].Locale = type('Locale', (), {})
    locales = sys.modules['scraper.rjcode_locales']
    locales.METADATA_SCHEMA_VERSION = 1
    locales.collect_rjcodes_by_locale = lambda *a, **k: {}
    locales.normalize_workno = lambda value: value
    locales.resolve_edition_workno_for_locale = lambda *a, **k: None

    requests_mod = types.ModuleType('requests')
    requests_mod.get = lambda *a, **k: None
    put('requests', requests_mod)
    exc_mod = types.ModuleType('requests.exceptions')
    exc_mod.RequestException = Exception
    put('requests.exceptions', exc_mod)

    return saved


def _load_scraper_module():
    saved = _install_stubs()
    spec = importlib.util.spec_from_file_location(
        'scraper.scraper', os.path.join(_ROOT, 'scraper', 'scraper.py'))
    module = importlib.util.module_from_spec(spec)
    saved.setdefault('scraper.scraper', sys.modules.get('scraper.scraper'))
    sys.modules['scraper.scraper'] = module
    spec.loader.exec_module(module)

    def restore():
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    return module, restore


class TestScrapeIconAtomic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod, cls._restore = _load_scraper_module()

        def fake_urlretrieve(self, url, filename):
            if FAIL_DOWNLOAD['fail']:
                raise OSError('模拟网络失败')
            with open(filename, 'wb') as handle:
                handle.write(b'\xff\xd8JPEGDATA')
            return filename, {}

        cls.mod.Scraper.urlretrieve = fake_urlretrieve

    @classmethod
    def tearDownClass(cls):
        cls._restore()

    def setUp(self):
        FAIL_DOWNLOAD['fail'] = False
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _scraper(self):
        scraper = self.mod.Scraper.__new__(self.mod.Scraper)
        scraper._Scraper__proxies = None
        scraper._Scraper__connect_timeout = 10
        scraper._Scraper__read_timeout = 30
        return scraper

    def _seed(self):
        """预置「已有封面与图标」，返回 (ico, jpg)。"""
        ico = os.path.join(self.tmp, '@folder-icon-RJ1.ico')
        jpg = os.path.join(self.tmp, 'cover.jpg')
        with open(ico, 'wb') as handle:
            handle.write(b'OLD_ICO')
        with open(jpg, 'wb') as handle:
            handle.write(b'OLD_JPG')
        return ico, jpg

    @staticmethod
    def _read(path):
        with open(path, 'rb') as handle:
            return handle.read()

    def _leftovers(self):
        return [n for n in os.listdir(self.tmp) if n.startswith('.cover-')]

    def test_download_failure_keeps_existing_assets(self):
        """下载失败时不得删除既有封面与图标（原实现会先 unlink）。"""
        ico, jpg = self._seed()
        FAIL_DOWNLOAD['fail'] = True
        with self.assertRaises(OSError):
            self._scraper().scrape_icon('RJ1', 'https://x/cover.jpg', self.tmp, force=True)
        self.assertEqual(self._read(ico), b'OLD_ICO', '旧图标被破坏')
        self.assertEqual(self._read(jpg), b'OLD_JPG', '旧封面被破坏')
        self.assertEqual(self._leftovers(), [], '临时文件未清理')

    def test_success_replaces_both_assets(self):
        ico, jpg = self._seed()
        name, jpg_name = self._scraper().scrape_icon(
            'RJ1', 'https://x/cover.jpg', self.tmp, force=True)
        self.assertEqual(name, '@folder-icon-RJ1.ico')
        self.assertEqual(jpg_name, 'cover.jpg')
        self.assertEqual(self._read(ico), b'ICO')
        self.assertEqual(self._read(jpg), b'\xff\xd8JPEGDATA')
        self.assertEqual(self._leftovers(), [], '临时文件未清理')

    def test_force_off_keeps_existing_icon(self):
        ico, jpg = self._seed()
        self._scraper().scrape_icon('RJ1', 'https://x/cover.jpg', self.tmp, force=False)
        self.assertEqual(self._read(ico), b'OLD_ICO')
        self.assertEqual(self._read(jpg), b'OLD_JPG')

    def test_force_off_generates_when_missing(self):
        self._scraper().scrape_icon('RJ1', 'https://x/cover.jpg', self.tmp, force=False)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, '@folder-icon-RJ1.ico')))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, 'cover.jpg')))

    def test_convert_failure_keeps_existing_assets(self):
        """转码失败（非下载失败）同样不得破坏既有产物。"""
        ico, jpg = self._seed()
        # scraper 里是 `from PIL import Image as img`，导入期已绑定，
        # 因此必须替换模块属性而非 sys.modules。
        original = self.mod.img

        class _Boom:
            @staticmethod
            def open(path):
                raise OSError('模拟图片损坏')

            @staticmethod
            def new(mode, size, color):
                return _FakeImage(size=size)

        self.mod.img = _Boom
        try:
            with self.assertRaises(OSError):
                self._scraper().scrape_icon(
                    'RJ1', 'https://x/cover.jpg', self.tmp, force=True)
        finally:
            self.mod.img = original
        self.assertEqual(self._read(ico), b'OLD_ICO')
        self.assertEqual(self._read(jpg), b'OLD_JPG')
        self.assertEqual(self._leftovers(), [], '临时文件未清理')


if __name__ == '__main__':
    unittest.main()
