import unittest

import task_runner
from gui import _format_run_status_summary
from timeline import Archive, Timeline
from zip import Zip


class RunStatusManual7zTests(unittest.TestCase):
    def test_run_status_summary_puts_long_manual_detail_in_banner(self):
        zip_obj = Zip(r'D:\下载\RJ01620216.7z', [], False)
        zip_obj.manual_password_only = True
        zip_obj.compression_ratio_info['manual_password_only'] = True
        zip_obj.compression_ratio_info['manual_7z_probe'] = {
            'listable_without_password': True,
            'blocks': 1,
            'store_encrypted': True,
            'file_size': 3775660497,
        }
        timeline = Timeline(Archive(r'D:\下载\RJ01620216.7z'), 'unzip_failed', zip_obj)
        main, inline, banner = _format_run_status_summary([timeline])
        self.assertEqual(main, '特殊7z：待填密码')
        self.assertEqual(inline, '')
        self.assertIn('RJ01620216.7z', banner)
        self.assertIn('双击任务填密码', banner)


if __name__ == '__main__':
    unittest.main()
