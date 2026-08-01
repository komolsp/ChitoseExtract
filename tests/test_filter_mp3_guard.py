"""过滤：有无损音源时才删 MP3 文件；无 SE カット版可命中；filter_dir 时移除 mp3 文件夹并上提其余文件。"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from filter import Filter
from filter_rules import build_filter_keywords, DEFAULT_FILTER_RULES


class _Log:
    def __init__(self):
        self.infos = []

    def info(self, msg, *a, **k):
        self.infos.append(msg if not a else msg.format(*a))

    def warning(self, *a, **k):
        pass


class TestExtensionOnlyFileTypeRules(unittest.TestCase):
    """整类文件规则只删匹配扩展名的文件；filter_dir 时处理匹配文件夹并上提其余文件。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, '[RJ999]sample')
        mixed = os.path.join(self.root, '01_mp3')
        os.makedirs(mixed)
        open(os.path.join(mixed, 'a.mp3'), 'w', encoding='utf-8').close()
        open(os.path.join(mixed, 'b.flac'), 'w', encoding='utf-8').close()
        open(os.path.join(self.root, 'track.mp3'), 'w', encoding='utf-8').close()
        self.log = _Log()
        self.rules = {
            **dict(DEFAULT_FILTER_RULES),
            'mp3': True,
            'no_se_folder': False,
            'no_se_all': False,
            'no_se_wav': False,
        }
        self.keywords = build_filter_keywords(self.rules)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_removes_mp3_folder_and_hoists_other_formats(self):
        flt = Filter(self.keywords, True, self.log)
        with mock.patch('task_runner.delete_file', side_effect=lambda p: os.remove(p) if os.path.isfile(p) else shutil.rmtree(p, ignore_errors=True)):
            hit = flt.post_filter(self.root)
        self.assertTrue(hit)
        self.assertFalse(os.path.exists(os.path.join(self.root, '01_mp3')))
        self.assertTrue(os.path.exists(os.path.join(self.root, 'b.flac')))
        self.assertFalse(os.path.exists(os.path.join(self.root, '01_mp3', 'a.mp3')))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'track.mp3')))

    def test_keeps_folder_when_filter_dir_disabled(self):
        flt = Filter(self.keywords, False, self.log)
        with mock.patch('task_runner.delete_file', side_effect=lambda p: os.remove(p) if os.path.isfile(p) else None):
            hit = flt.post_filter(self.root)
        self.assertTrue(hit)
        self.assertTrue(os.path.isdir(os.path.join(self.root, '01_mp3')))
        self.assertTrue(os.path.exists(os.path.join(self.root, '01_mp3', 'b.flac')))
        self.assertFalse(os.path.exists(os.path.join(self.root, '01_mp3', 'a.mp3')))
        self.assertFalse(os.path.exists(os.path.join(self.root, 'track.mp3')))

class TestPostFilterWithFlac(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, '[RJ999]sample')
        os.makedirs(os.path.join(self.root, '01_mp3'))
        os.makedirs(os.path.join(self.root, '02_wav'))
        os.makedirs(os.path.join(self.root, '03_効果音カット版', '01_mp3'))
        open(os.path.join(self.root, '01_mp3', 'a.mp3'), 'w', encoding='utf-8').close()
        open(os.path.join(self.root, '02_wav', 'a.flac'), 'w', encoding='utf-8').close()
        open(
            os.path.join(self.root, '03_効果音カット版', '01_mp3', 'b.mp3'),
            'w',
            encoding='utf-8',
        ).close()
        self.log = _Log()
        keywords = build_filter_keywords({
            **dict(DEFAULT_FILTER_RULES),
            'mp3': True,
            'no_se_folder': True,
            'no_se_all': True,
            'no_se_wav': True,
        })
        self.flt = Filter(keywords, True, self.log)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_deletes_mp3_and_cut_folder_when_flac_present(self):
        with mock.patch('task_runner.delete_file', side_effect=lambda p: shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p) if os.path.exists(p) else None):
            hit = self.flt.post_filter(self.root)
        self.assertTrue(hit)
        self.assertFalse(os.path.exists(os.path.join(self.root, '01_mp3')))
        self.assertFalse(os.path.exists(os.path.join(self.root, '03_効果音カット版')))
        self.assertTrue(os.path.exists(os.path.join(self.root, '02_wav', 'a.flac')))

    def test_keeps_mp3_when_only_mp3(self):
        """仅有 MP3 时保留音频文件，但 filter_dir 会拆掉 mp3 文件夹并把内容上提。"""
        only = os.path.join(self.tmp, '[RJ888]mp3only')
        os.makedirs(os.path.join(only, '01_mp3'))
        open(os.path.join(only, '01_mp3', 'a.mp3'), 'w', encoding='utf-8').close()
        open(os.path.join(only, '01_mp3', 'cover.jpg'), 'w', encoding='utf-8').close()
        with mock.patch(
            'task_runner.delete_file',
            side_effect=lambda p: os.remove(p) if os.path.isfile(p) else shutil.rmtree(p, ignore_errors=True),
        ) as delete_mock:
            hit = self.flt.post_filter(only)
        self.assertTrue(hit)
        delete_mock.assert_called_once()
        deleted = delete_mock.call_args[0][0]
        self.assertTrue(deleted.rstrip('\\/').endswith('01_mp3'))
        self.assertFalse(os.path.exists(os.path.join(only, '01_mp3')))
        self.assertTrue(os.path.exists(os.path.join(only, 'a.mp3')))
        self.assertTrue(os.path.exists(os.path.join(only, 'cover.jpg')))

    def test_deleted_folder_not_reprocessed(self):
        """父文件夹删掉后，不应再对其中子文件调用 delete_file。"""
        deleted = []

        def _delete(p):
            deleted.append(p)
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.exists(p):
                os.remove(p)

        with mock.patch('task_runner.delete_file', side_effect=_delete):
            hit = self.flt.post_filter(self.root)
        self.assertTrue(hit)
        # 无 SE 文件夹整夹删除；MP3 文件夹删除并上提非过滤文件
        self.assertTrue(any(p.rstrip('\\/').endswith('03_効果音カット版') for p in deleted))
        self.assertTrue(any(p.rstrip('\\/').endswith('a.mp3') for p in deleted))
        self.assertTrue(any(p.rstrip('\\/').endswith('01_mp3') for p in deleted))
        under_cut = [p for p in deleted if '03_効果音カット版' in p and p.rstrip('\\/').endswith(('mp3', 'flac', 'jpg'))]
        self.assertEqual(under_cut, [])


if __name__ == '__main__':
    unittest.main()
