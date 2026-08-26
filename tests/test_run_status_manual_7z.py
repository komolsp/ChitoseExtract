import unittest

import task_runner
from gui import _format_run_status_summary, _ops_label
from timeline import Archive, Timeline
from zip import Zip


class RunStatusManual7zTests(unittest.TestCase):
    def test_failure_ops_have_readable_labels(self):
        self.assertEqual(_ops_label('archive_failed'), '归档失败')
        self.assertEqual(_ops_label('filter_failed'), '过滤失败')
        self.assertEqual(_ops_label('rename_failed'), '重命名失败')
        self.assertEqual(_ops_label('convert_audio_failed'), '转换失败')
        self.assertEqual(_ops_label('tag_audio_failed'), '写入元数据失败')
        self.assertEqual(_ops_label('convert_audio_skip'), '已跳过转换')
        self.assertEqual(_ops_label('tag_audio_skip'), '已跳过写入元数据')

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

    def test_successful_last_audio_step_is_complete(self):
        root = r'D:\音声库\RJ01665169'
        timeline = Timeline(Archive(root), 'tag_audio', Archive(root))

        self.assertEqual(
            _format_run_status_summary([timeline], last_step='tag_audio'),
            ('已完成', '已写入元数据 1', ''),
        )

    def test_incomplete_shadow_is_not_hidden_by_successful_last_step(self):
        root = r'D:\音声库\RJ01665169'
        completed = Timeline(Archive(root), 'tag_audio', Archive(root))
        shadow = Timeline(Archive(root), 'unnest', Archive(root))

        main, detail, banner = _format_run_status_summary(
            [completed, shadow],
            last_step='tag_audio',
        )

        self.assertEqual(main, '部分完成')
        self.assertIn('已写入元数据 1', detail)
        self.assertIn('已解压 1', detail)
        self.assertEqual(banner, '')


if __name__ == '__main__':
    unittest.main()
