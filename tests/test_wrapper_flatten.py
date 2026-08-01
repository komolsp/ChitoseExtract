import os
import shutil
import tempfile
import unittest

from file_ops import flatten_wrapper_dirs


class WrapperFlattenTest(unittest.TestCase):
    def _tmpdir(self) -> str:
        return tempfile.mkdtemp(prefix='wrapper_test_')

    def test_top_level_wrapper_chain_collapsed(self):
        """顶层连续套娃层应拍平。"""
        d = self._tmpdir()
        try:
            os.makedirs(os.path.join(d, 'w1', 'w2', 'sub'))
            open(os.path.join(d, 'w1', 'w2', 'sub', 'track.wav'), 'wb').write(b'RIFF')
            flatten_wrapper_dirs(d)
            self.assertTrue(os.path.isfile(os.path.join(d, 'sub', 'track.wav')))
            self.assertFalse(os.path.exists(os.path.join(d, 'w1')))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_internal_single_child_folder_preserved(self):
        """作品根下仅单层内部结构（如 Freetalk/wav）不应被拍平。"""
        d = self._tmpdir()
        try:
            os.makedirs(os.path.join(d, 'Freetalk', 'wav'))
            open(os.path.join(d, 'Freetalk', 'wav', 'freetalk.wav'), 'wb').write(b'RIFF')
            open(os.path.join(d, 'Finishtime.txt'), 'w', encoding='utf-8').write('ok')
            os.makedirs(os.path.join(d, 'wav'))
            open(os.path.join(d, 'wav', 'tr01.wav'), 'wb').write(b'RIFF')

            flatten_wrapper_dirs(d)

            self.assertTrue(os.path.isdir(os.path.join(d, 'Freetalk')))
            self.assertTrue(os.path.isfile(os.path.join(d, 'Freetalk', 'wav', 'freetalk.wav')))
            self.assertTrue(os.path.isfile(os.path.join(d, 'Finishtime.txt')))
            self.assertTrue(os.path.isfile(os.path.join(d, 'wav', 'tr01.wav')))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_internal_wrapper_not_collapsed_under_project_root(self):
        """作品根含多个条目时，内部单层子目录即使满足 wrapper 条件也不应被拍平。"""
        d = self._tmpdir()
        try:
            os.makedirs(os.path.join(d, 'Freetalk', 'wav'))
            open(os.path.join(d, 'readme.txt'), 'w', encoding='utf-8').write('x')
            flatten_wrapper_dirs(d)
            self.assertTrue(os.path.isdir(os.path.join(d, 'Freetalk', 'wav')))
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
