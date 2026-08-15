import queue
import unittest

from gui import Console


class GuiLogBufferTests(unittest.TestCase):
    def test_write_only_enqueues_until_ui_flush(self):
        console = object.__new__(Console)
        console._pending_gui_log = queue.Queue()

        console.write('first\n')
        console.write('second\n')

        self.assertEqual(console._pending_gui_log.get_nowait(), 'first\n')
        self.assertEqual(console._pending_gui_log.get_nowait(), 'second\n')
        with self.assertRaises(queue.Empty):
            console._pending_gui_log.get_nowait()


if __name__ == '__main__':
    unittest.main()
