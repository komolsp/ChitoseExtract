import queue
import threading
import unittest
from unittest import mock

from gui import Console


class _FakeTree:
    def __init__(self):
        self.order = []
        self.rows = {}
        self.insert_count = 0
        self.update_count = 0
        self.move_count = 0
        self.delete_count = 0

    def get_children(self):
        return tuple(self.order)

    def insert(self, _parent, index, *, values, tags):
        iid = f'row-{self.insert_count}'
        self.insert_count += 1
        position = len(self.order) if index == 'end' else int(index)
        self.order.insert(position, iid)
        self.rows[iid] = (values, tags)
        return iid

    def item(self, iid, *, values, tags):
        self.update_count += 1
        self.rows[iid] = (values, tags)

    def move(self, iid, _parent, index):
        self.move_count += 1
        self.order.remove(iid)
        self.order.insert(index, iid)

    def delete(self, iid):
        self.delete_count += 1
        self.order.remove(iid)
        self.rows.pop(iid, None)


class GuiTaskTreeUpdateTests(unittest.TestCase):
    def _console(self):
        console = object.__new__(Console)
        console.task_tree = _FakeTree()
        console._task_tree_items = {}
        console._task_tree_rows = {}
        labels = {}
        console._task_tree_row = lambda item, index, legacy: (
            (labels[item], f'step-{index}', ''),
            ('odd' if index % 2 else 'even',),
        )
        return console, labels

    def test_unchanged_refresh_does_not_rebuild_rows(self):
        console, labels = self._console()
        first = object()
        second = object()
        labels[first] = 'first'
        labels[second] = 'second'

        console._sync_task_tree([first, second])
        self.assertEqual(console.task_tree.insert_count, 2)

        console._sync_task_tree([first, second])
        self.assertEqual(console.task_tree.insert_count, 2)
        self.assertEqual(console.task_tree.update_count, 0)
        self.assertEqual(console.task_tree.move_count, 0)
        self.assertEqual(console.task_tree.delete_count, 0)

        labels[second] = 'second-updated'
        console._sync_task_tree([first, second])
        self.assertEqual(console.task_tree.insert_count, 2)
        self.assertEqual(console.task_tree.update_count, 1)

    def test_refresh_requests_coalesce_to_latest_snapshot(self):
        console = object.__new__(Console)
        console._task_tree_refresh_lock = threading.Lock()
        console._pending_task_tree_refresh = None
        console._task_tree_refresh_scheduled = False
        scheduled = []
        console._run_on_ui = scheduled.append

        first = object()
        second = object()
        console._queue_task_tree_refresh([first], legacy=False)
        console._queue_task_tree_refresh([second], legacy=False)

        self.assertEqual(len(scheduled), 1)
        self.assertEqual(console._pending_task_tree_refresh, ([second], False))

    def test_progress_flush_keeps_only_latest_event_per_task(self):
        console = object.__new__(Console)
        console._log_queue = queue.Queue()
        console.update_progress = mock.Mock()
        console._clear_run_status_banner = mock.Mock()
        console.val2 = mock.Mock()
        for current in range(100):
            console._log_queue.put(('unzip-work', f'解压中 {current}/100'))

        console.flush_progress_once()

        console.update_progress.assert_called_once_with(99, 100, '解压中 99/100')
        console.val2.set.assert_not_called()


if __name__ == '__main__':
    unittest.main()
