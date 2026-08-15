import unittest
from datetime import datetime
from unittest import mock

from timeline import Archive, Record


class DynamicDefaultTests(unittest.TestCase):
    def test_record_finish_time_is_evaluated_for_each_record(self):
        first_time = datetime(2026, 8, 15, 12, 0, 0)
        second_time = datetime(2026, 8, 16, 12, 0, 0)
        archive = Archive('archive.zip')

        with mock.patch('timeline.datetime') as mocked_datetime:
            mocked_datetime.now.side_effect = [first_time, second_time]
            first = Record(archive, 'first')
            second = Record(archive, 'second')

        self.assertEqual(first.finish_time, first_time)
        self.assertEqual(second.finish_time, second_time)


if __name__ == '__main__':
    unittest.main()
