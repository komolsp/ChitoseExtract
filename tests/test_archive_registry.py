import unittest

import archive_registry
from volume import rename as volume_rename


class ArchiveRegistryTests(unittest.TestCase):
    def setUp(self):
        archive_registry.clear()
        volume_rename.clear_rename_registry()

    def tearDown(self):
        volume_rename.clear_rename_registry()

    def test_discovered_vs_unzipped(self):
        archive_registry.note_discovered(r'D:\work\a.7z')
        self.assertTrue(archive_registry.is_discovered(r'D:\work\a.7z'))
        self.assertFalse(archive_registry.is_unzipped(r'D:\work\a.7z'))

        archive_registry.mark_unzipped(r'D:\work\a.7z')
        self.assertTrue(archive_registry.is_unzipped(r'D:\work\a.7z'))

    def test_case_insensitive(self):
        archive_registry.note_discovered(r'D:\Work\A.7Z')
        self.assertTrue(archive_registry.is_discovered(r'd:\work\a.7z'))

    def test_rename_sync_does_not_mark_discovered_volume_unzipped(self):
        original = r'D:\work\album.7z.001.pdf'
        normalized = r'D:\work\album.7z.001'
        volume_rename._rename_registry[
            archive_registry._norm_path(normalized)
        ] = original
        archive_registry.note_discovered(original)

        archive_registry.sync_rename_registry()

        self.assertTrue(archive_registry.is_discovered(normalized))
        self.assertFalse(archive_registry.is_unzipped(original))
        self.assertFalse(archive_registry.is_unzipped(normalized))

    def test_rename_sync_preserves_existing_unzipped_state(self):
        original = r'D:\work\album.7z.001.pdf'
        normalized = r'D:\work\album.7z.001'
        volume_rename._rename_registry[
            archive_registry._norm_path(normalized)
        ] = original
        archive_registry.mark_unzipped(original)

        archive_registry.sync_rename_registry()

        self.assertTrue(archive_registry.is_unzipped(original))
        self.assertTrue(archive_registry.is_unzipped(normalized))


    def test_pending_discovered_under(self):
        archive_registry.note_discovered(r'D:\work\album\inner.zip')
        archive_registry.mark_unzipped(r'D:\work\album.7z')
        pending = archive_registry.pending_discovered_under(r'D:\work\album')
        self.assertEqual(len(pending), 1)


if __name__ == '__main__':
    unittest.main()
