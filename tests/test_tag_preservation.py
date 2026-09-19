"""标签写入的「不可逆损失」防护测试。

覆盖 review 发现的 C8：force_retag 此前是死配置，无论其值如何都会
对 FLAC 调 delete()、对 MP3/WAV 调 clear()，把用户已有标签与内嵌封面
原地清空且不可恢复。

这里用 stub 注入 mutagen，以便在未安装 mutagen 的环境下也能验证判定逻辑；
关键是 stub 必须忠实模拟真实 mutagen 的 FLAC 语义：__len__ 返回元数据块
数量且 STREAMINFO 恒存在，因此「有无用户标签」不能由 len() 判断。
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeTags:
    """模拟 mutagen 的 VorbisComment：len() 是用户标签条目数。"""

    def __init__(self, items=None):
        self.items = list(items or [])

    def add(self, item):
        self.items.append(item)

    def clear(self):
        self.items.clear()

    def __len__(self):
        return len(self.items)

    def __bool__(self):
        return bool(self.items)


class _FakePicture:
    def __init__(self):
        self.type = self.mime = self.desc = self.data = None


class _FakeAudio:
    # 记录所有发生过的 save()，用于断言「未写入」。
    saved_paths = []

    def __init__(self, path, *args, **kwargs):
        self.path = path
        self.pictures = []
        # 文件名含 'tagged' 表示已有用户标签；'pictured' 表示仅有内嵌封面。
        name = os.path.basename(path)
        if 'tagged' in name:
            self.tags = _FakeTags(['TITLE=既有标题'])
        elif 'pictured' in name:
            self.tags = _FakeTags()
            self.pictures.append(_FakePicture())
        else:
            self.tags = None

    def __setitem__(self, key, value):
        pass

    def add_picture(self, picture):
        self.pictures.append(picture)

    def add_tags(self):
        self.tags = _FakeTags()

    def save(self):
        type(self).saved_paths.append(self.path)


class _FakeFLAC(_FakeAudio):
    """忠实模拟 mutagen.flac.FLAC。"""

    def __init__(self, path, *args, **kwargs):
        super().__init__(path, *args, **kwargs)
        if self.tags is None:
            self.tags = _FakeTags()

    def __len__(self):
        # STREAMINFO 恒存在：这正是不该用 len() 判「有无标签」的原因。
        return 1 + len(self.tags) + len(self.pictures)

    def delete(self):
        self.pictures.clear()
        self.tags.clear()


class _FakeMP3(_FakeAudio):
    pass


class _FakeWAVE(_FakeAudio):
    pass


class _FakeID3(_FakeAudio):
    pass


def _install_mutagen_stub():
    mutagen = types.ModuleType('mutagen')
    flac_mod = types.ModuleType('mutagen.flac')
    id3_mod = types.ModuleType('mutagen.id3')
    mp3_mod = types.ModuleType('mutagen.mp3')
    wave_mod = types.ModuleType('mutagen.wave')

    flac_mod.FLAC = _FakeFLAC
    flac_mod.Picture = _FakePicture
    mp3_mod.MP3 = _FakeMP3
    wave_mod.WAVE = _FakeWAVE
    id3_mod.ID3 = _FakeID3
    for name in ('APIC', 'TALB', 'TCON', 'TDRC', 'TIT2', 'TPE1', 'TPE2', 'TXXX'):
        setattr(id3_mod, name, type(name, (), {'__init__': lambda self, **kw: None}))
    mutagen.flac = flac_mod
    mutagen.id3 = id3_mod
    mutagen.mp3 = mp3_mod
    mutagen.wave = wave_mod
    return {
        'mutagen': mutagen,
        'mutagen.flac': flac_mod,
        'mutagen.id3': id3_mod,
        'mutagen.mp3': mp3_mod,
        'mutagen.wave': wave_mod,
    }


def _load_audio_tagger():
    """在 stub 环境下加载 audio_tagger 模块。"""
    saved = {name: sys.modules.get(name) for name in (
        'audio_tagger', 'file_ops', 'scraper.work_metadata')}
    saved.update({name: sys.modules.get(name) for name in _install_mutagen_stub()})
    sys.modules.update(_install_mutagen_stub())

    file_ops = types.ModuleType('file_ops')
    file_ops.parse_rj_code_for_folder = lambda folder: None
    file_ops.find_rj_for_folder = lambda folder: None
    sys.modules['file_ops'] = file_ops

    wm = types.ModuleType('scraper.work_metadata')
    wm.WorkMetadata = dict
    wm.CV_LIST_SEPARATOR = ';'
    sys.modules['scraper.work_metadata'] = wm

    spec = importlib.util.spec_from_file_location(
        'audio_tagger', os.path.join(_ROOT, 'audio_tagger.py'))
    module = importlib.util.module_from_spec(spec)
    # dataclass 处理字符串注解时按 __module__ 反查 sys.modules，必须先注册。
    sys.modules['audio_tagger'] = module
    spec.loader.exec_module(module)

    def restore():
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    return module, restore


METADATA = {
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

CONFIG = {
    'extensions': ['.flac', '.mp3', '.wav'],
    'embed_cover': False,
    'save_cover_jpg': False,
    'max_workers': 1,
}


class TestTagPreservation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tagger, cls._restore = _load_audio_tagger()

    @classmethod
    def tearDownClass(cls):
        cls._restore()

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _FakeAudio.saved_paths = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, name):
        path = os.path.join(self.tmp, name)
        with open(path, 'wb') as handle:
            handle.write(b'audio')
        return path

    def test_existing_tags_preserved_when_force_retag_off(self):
        """已有标签 + force_retag=false：必须跳过，不得清空后重写。"""
        for ext in ('.flac', '.mp3', '.wav'):
            with self.subTest(ext=ext):
                _FakeAudio.saved_paths = []
                path = self._make(f'tagged{ext}')
                status = self.tagger._tag_file_status(path, METADATA, CONFIG)
                self.assertEqual(status, self.tagger.TAG_SKIPPED)
                # 未调用 save() 即证明未发生原地重写（原实现会 delete()/clear() 后 save）。
                self.assertEqual(_FakeAudio.saved_paths, [], '不应写入')

    def test_existing_tags_overwritten_when_force_retag_on(self):
        """force_retag=true 时应覆盖写入（该配置此前完全无效）。"""
        for ext in ('.flac', '.mp3', '.wav'):
            with self.subTest(ext=ext):
                _FakeAudio.saved_paths = []
                path = self._make(f'tagged{ext}')
                config = dict(CONFIG, force_retag=True)
                status = self.tagger._tag_file_status(path, METADATA, config)
                self.assertEqual(status, self.tagger.TAG_WRITTEN)
                self.assertEqual(_FakeAudio.saved_paths, [path])

    def test_fresh_file_is_written(self):
        for ext in ('.flac', '.mp3', '.wav'):
            with self.subTest(ext=ext):
                path = self._make(f'fresh{ext}')
                status = self.tagger._tag_file_status(path, METADATA, CONFIG)
                self.assertEqual(status, self.tagger.TAG_WRITTEN)

    def test_flac_with_only_picture_is_preserved(self):
        """仅有内嵌封面、无文本标签的 FLAC 也应视为已有内容而保留。"""
        path = self._make('pictured.flac')
        status = self.tagger._tag_file_status(path, METADATA, CONFIG)
        self.assertEqual(status, self.tagger.TAG_SKIPPED)

    def test_flac_len_is_not_used_as_tag_indicator(self):
        """回归：len(FLAC) 含 STREAMINFO，恒 >= 1，不得据此判定「已有标签」。

        若误用 len()，无标签的 FLAC 会被判为已有标签而永不写入。
        """
        path = self._make('fresh.flac')
        audio = _FakeFLAC(path)
        self.assertGreaterEqual(len(audio), 1)
        self.assertFalse(self.tagger._has_existing_tags(path, '.flac'))

    def test_unreadable_file_is_not_treated_as_tagged(self):
        """读取异常不应被当成「已有标签」而跳过写入。"""
        path = self._make('broken.flac')

        class _Boom:
            def __init__(self, *a, **k):
                raise OSError('读取失败')

        original = sys.modules['mutagen.flac'].FLAC
        sys.modules['mutagen.flac'].FLAC = _Boom
        try:
            self.assertFalse(self.tagger._has_existing_tags(path, '.flac'))
        finally:
            sys.modules['mutagen.flac'].FLAC = original

    def test_tag_file_wrapper_keeps_bool_contract(self):
        """tag_file 必须保持布尔返回（向后兼容既有调用方与测试）。"""
        tagged = self._make('tagged.flac')
        fresh = self._make('fresh.flac')
        self.assertFalse(self.tagger.tag_file(tagged, METADATA, CONFIG))
        self.assertTrue(self.tagger.tag_file(fresh, METADATA, CONFIG))

    def test_skipped_files_do_not_fail_work_folder(self):
        """跳过（保留原标签）不得被算作失败，否则整步会被误判为失败。"""
        folder = os.path.join(self.tmp, 'work')
        os.makedirs(folder)
        for name in ('tagged_a.flac', 'tagged_b.flac', 'fresh_c.flac'):
            with open(os.path.join(folder, name), 'wb') as handle:
                handle.write(b'audio')

        ok, total = self.tagger.tag_work_folder(folder, METADATA, CONFIG)
        self.assertEqual(total, 3)
        self.assertEqual(ok, total, '跳过应计为成功，否则步骤被误判失败')

    def test_missing_file_and_wrong_extension_fail(self):
        self.assertEqual(
            self.tagger._tag_file_status(
                os.path.join(self.tmp, 'nope.flac'), METADATA, CONFIG),
            self.tagger.TAG_FAILED,
        )
        self.assertEqual(
            self.tagger._tag_file_status(self._make('x.ogg'), METADATA, CONFIG),
            self.tagger.TAG_FAILED,
        )


if __name__ == '__main__':
    unittest.main()
