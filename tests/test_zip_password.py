import unittest

import tempfile

import os

from unittest import mock



from zip import Zip



class ZipPasswordTests(unittest.TestCase):

    def test_extract_password_verification_can_be_explicitly_invalidated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'archive.zip')
            with open(path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 16)
            archive = Zip(path, ['secret'])
            archive.mark_extract_password_verified('secret')

            archive.invalidate_extract_password_verification()

            self.assertFalse(archive.is_extract_password_verified())
            self.assertEqual(archive.verified_password(), '')

    def test_encrypted_container_requires_password(self):

        archive = Zip(r'D:\work\RJ01620216.7z', ['secret'])

        archive.file_list = ['inner.zip']

        archive.compression_ratio_info = {'encrypted': True}

        archive.mark_namelist_scanned('secret')

        self.assertTrue(archive.is_encrypted())

        self.assertTrue(archive.container_requires_password())



    def test_encrypted_7z_empty_password_scan_not_current(self):

        archive = Zip(r'D:\work\RJ01620216.7z', ['secret'])

        archive.file_list = ['inner.zip']

        archive.compression_ratio_info = {'encrypted': True}

        archive.mark_namelist_scanned('')

        self.assertTrue(archive.container_requires_password())

        self.assertFalse(archive.is_namelist_current())



    def test_encrypted_container_with_password_requires_password(self):

        archive = Zip(r'D:\work\secret.7z', ['secret'])

        archive.file_list = ['track.wav']

        archive.compression_ratio_info = {'encrypted': True}

        archive.mark_namelist_scanned('secret')

        self.assertTrue(archive.container_requires_password())



    def test_plain_empty_password_still_current(self):

        archive = Zip(r'D:\work\outer.7z', [''])

        archive.file_list = ['track.wav']

        archive.compression_ratio_info = {'encrypted': False}

        archive.mark_namelist_scanned('')

        self.assertTrue(archive.is_namelist_current())

        self.assertFalse(archive.container_requires_password())

    def test_namelist_and_extract_password_states_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'archive.zip')
            with open(path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 16)

            archive = Zip(path, ['candidate', 'correct'])
            archive.file_list = ['payload.bin']
            archive.compression_ratio_info = {'encrypted': True}
            archive.mark_namelist_scanned('candidate')

            self.assertTrue(archive.is_namelist_current())
            self.assertEqual(archive.namelist_password(), 'candidate')
            self.assertFalse(archive.is_extract_password_verified())
            self.assertEqual(archive.verified_password(), '')

            archive.mark_extract_password_verified('correct')

            self.assertTrue(archive.is_extract_password_verified())
            self.assertEqual(archive.verified_password(), 'correct')
            self.assertEqual(archive.namelist_password(), 'candidate')

            archive.invalidate_namelist_scan()

            self.assertFalse(archive.is_namelist_current())
            self.assertTrue(archive.is_extract_password_verified())
            self.assertEqual(archive.verified_password(), 'correct')

            with open(path, 'ab') as fh:
                fh.write(b'changed')

            self.assertFalse(archive.is_extract_password_verified())
            self.assertEqual(archive.verified_password(), '')
            self.assertEqual(archive.namelist_password(), '')





class FileOpsCoveredStrategyTests(unittest.TestCase):

    def test_smallest_zip_member_returns_none_without_a_match(self):
        import file_ops
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, 'probe.zip')
            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('present.txt', b'x')

            self.assertIsNone(file_ops.zip_smallest_probe_member(zip_path, []))
            self.assertIsNone(file_ops.zip_smallest_probe_member(
                zip_path, ['missing.txt'],
            ))

    def test_smallest_zip_member_prefers_encrypted_candidate(self):
        import file_ops
        import zipfile

        plain = zipfile.ZipInfo('plain.bin')
        plain.file_size = 1
        plain.flag_bits = 0
        encrypted = zipfile.ZipInfo('encrypted.bin')
        encrypted.file_size = 1024
        encrypted.flag_bits = 1
        opened = mock.MagicMock()
        opened.__enter__.return_value.infolist.return_value = [plain, encrypted]

        with mock.patch.object(zipfile, 'ZipFile', return_value=opened):
            selected = file_ops.zip_smallest_probe_member(
                'probe.zip', ['plain.bin', 'encrypted.bin'],
            )

        self.assertEqual(selected, 'encrypted.bin')

    def test_smallest_zip_member_matches_seven_zip_question_wildcard(self):
        import file_ops
        import zipfile

        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, 'probe.zip')
            with zipfile.ZipFile(zip_path, 'w') as zf:
                zf.writestr('small_file.txt', b'x')
                zf.writestr('large.bin', b'x' * 1024)

            selected = file_ops.zip_smallest_probe_member(
                zip_path, ['small?file.txt', 'large.bin'],
            )

            self.assertEqual(selected, 'small?file.txt')

    def test_standard_zip_disallows_covered_strategy(self):

        import file_ops

        with tempfile.TemporaryDirectory() as tmp:

            zip_path = os.path.join(tmp, 'inner.zip')

            with open(zip_path, 'wb') as fh:

                fh.write(b'PK\x03\x04' + b'\x00' * 16)

            probe = file_ops.ArchiveProbe(True, covered=False)

            strategies = file_ops.build_archive_open_strategies(

                probe, '.zip', zip_path,

            )

            self.assertTrue(any(not covered for _, covered in strategies))

            self.assertFalse(any(covered for _, covered in strategies))



    def test_numeric_zst_is_covered_junk(self):

        import file_ops

        self.assertTrue(file_ops.is_covered_extract_junk_basename('2.zst'))

        self.assertTrue(file_ops.is_covered_extract_junk_basename('1'))





class UnzipperOuterPasswordTests(unittest.TestCase):

    def test_resolve_encrypted_password_reuses_extract_verified_password(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'archive.7z')
            with open(path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 16)
            archive = Zip(path, ['secret'])
            archive.compression_ratio_info = {'encrypted': True}
            archive.mark_extract_password_verified('secret')
            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))

            with mock.patch.object(unzipper, 'load_namelist') as load_namelist:
                self.assertTrue(unzipper._resolve_encrypted_password(archive))

            load_namelist.assert_not_called()

    def test_covered_password_probe_uses_carrier_member(self):
        from unzipper import Unzipper

        archive = Zip('carrier.jpg', ['secret'], covered=True)
        archive.file_list = ['payload.bin']

        self.assertEqual(Unzipper._password_probe_member(archive), '2.zip')

    def test_verified_7z_accepts_usable_partial_inner_extract(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'outer.7z')
            output_path = os.path.join(tmp, 'out')
            os.makedirs(output_path)
            with open(path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 16)
            archive = Zip(path, ['secret'])
            archive.file_list = ['inner.zip']
            archive.compression_ratio_info = {'encrypted': True}
            archive.mark_namelist_scanned('secret')
            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper._run_single_unzip = mock.MagicMock(return_value=(
                1,
                'ERROR: CRC Failed in encrypted file: inner.zip',
            ))
            unzipper._output_has_usable_partial_extract = mock.MagicMock(
                return_value=True,
            )

            self.assertTrue(unzipper.single_threaded_unzip(
                archive, output_path, known_password=True,
            ))

            self.assertTrue(archive.is_extract_password_verified())
            self.assertEqual(archive.verified_password(), 'secret')

    def test_encrypted_extract_skips_garbage_success_and_tries_next_password(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'archive.7z')
            output_path = os.path.join(tmp, 'out')
            os.makedirs(output_path)
            with open(path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 16)
            archive = Zip(path, ['first', 'second'])
            archive.file_list = ['payload.bin']
            archive.compression_ratio_info = {'encrypted': True}
            archive.mark_namelist_scanned('first')
            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper._run_single_unzip = mock.MagicMock(
                side_effect=[(0, ''), (0, '')],
            )
            unzipper._extract_is_wrong_password_garbage = mock.MagicMock(
                side_effect=[True, False],
            )

            self.assertTrue(unzipper.single_threaded_unzip(
                archive, output_path, known_password=True,
            ))

            self.assertEqual(
                [call.args[2] for call in unzipper._run_single_unzip.call_args_list],
                ['first', 'second'],
            )
            self.assertEqual(archive.verified_password(), 'second')


    def test_encrypted_zip_nonempty_candidate_avoids_full_archive_test(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'inner.zip')
            with open(path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 16)
            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            archive = Zip(path, ['secret'])
            unzipper.driver.test_archive = mock.MagicMock(return_value=(True, 'Everything is Ok'))

            self.assertTrue(unzipper._confirm_standard_encrypted_password(
                archive, archive.path, 'secret', covered=False,
                format_type=None, encrypted=True,
            ))
            unzipper.driver.test_archive.assert_not_called()

    def test_encrypted_zip_empty_password_is_never_accepted(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'inner.zip')
            with open(path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 16)
            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            archive = Zip(path, ['secret'])
            unzipper.driver.test_archive = mock.MagicMock(return_value=(True, 'Everything is Ok'))

            self.assertFalse(unzipper._confirm_standard_encrypted_password(
                archive, archive.path, '', covered=False,
                format_type=None, encrypted=True,
            ))
            unzipper.driver.test_archive.assert_not_called()

    def test_corrupt_inner_zip_is_not_genuine(self):

        from unzip_process_pool import ProcessResourceManager

        from unzipper import Unzipper



        with tempfile.TemporaryDirectory() as tmp:

            corrupt = os.path.join(tmp, 'RJ01620216.zip')

            with open(corrupt, 'wb') as fh:

                fh.write(b'PK\x03\x04' + b'\xff' * 64)



            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))

            unzipper.driver.get_namelist = mock.MagicMock(

                return_value=(['1', '2.zst'], {'encrypted': False}),

            )

            unzipper.driver.test_archive = mock.MagicMock(return_value=(False, 'Headers Error'))



            self.assertFalse(unzipper._extracted_archive_is_genuine(corrupt))



    def test_7z_outer_wrong_password_does_not_partial_succeed(self):

        from unzip_process_pool import ProcessResourceManager

        from unzipper import Unzipper



        with tempfile.TemporaryDirectory() as tmp:

            outer_path = os.path.join(tmp, 'RJ01620216.7z')

            output_path = os.path.join(tmp, 'out')

            os.makedirs(output_path)

            with open(outer_path, 'wb') as fh:

                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 16)

            with open(os.path.join(output_path, 'RJ01620216.zip'), 'wb') as fh:

                fh.write(b'PK\x03\x04' + b'\xff' * 64)



            zip_obj = Zip(outer_path, ['wrong'], False)

            zip_obj.file_list = ['RJ01620216.zip']

            zip_obj.compression_ratio_info = {'encrypted': True}

            zip_obj.mark_namelist_scanned('wrong')



            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))

            unzipper._run_single_unzip = mock.MagicMock(

                return_value=(1, 'ERROR: CRC Failed in encrypted file. Wrong password? : RJ01620216.zip'),

            )

            unzipper._output_has_usable_partial_extract = mock.MagicMock(return_value=False)



            self.assertFalse(unzipper.single_threaded_unzip(zip_obj, output_path, known_password=True))

    def test_zip_list_only_password_does_not_accept_partial_extract(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'outer.zip')
            output_path = os.path.join(tmp, 'out')
            os.makedirs(output_path)
            with open(outer_path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 16)

            zip_obj = Zip(outer_path, ['wrong', 'correct'], False)
            zip_obj.file_list = ['inner.zip', 'payload.bin']
            zip_obj.compression_ratio_info = {'encrypted': True}
            zip_obj.mark_namelist_scanned('wrong')

            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper._run_single_unzip = mock.MagicMock(
                side_effect=[
                    (1, 'ERROR: CRC Failed in encrypted file. Wrong password? : payload.bin'),
                    (0, ''),
                ],
            )
            unzipper._output_has_usable_partial_extract = mock.MagicMock(
                return_value=True,
            )
            unzipper._extract_is_wrong_password_garbage = mock.MagicMock(
                return_value=False,
            )

            self.assertTrue(
                unzipper.single_threaded_unzip(
                    zip_obj, output_path, known_password=True,
                )
            )
            self.assertEqual(
                [call.args[2] for call in unzipper._run_single_unzip.call_args_list],
                ['wrong', 'correct'],
            )
            self.assertEqual(zip_obj.verified_password(), 'correct')

    def test_password_collision_confirms_listable_zip_candidate_by_extracting(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'outer.zip')
            output_path = os.path.join(tmp, 'out')
            os.makedirs(output_path)
            with open(outer_path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 16)

            zip_obj = Zip(outer_path, ['wrong', 'correct'], False)
            zip_obj.file_list = ['payload.bin']
            zip_obj.compression_ratio_info = {'encrypted': True}
            zip_obj.mark_namelist_scanned('wrong')

            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper.driver.unzip = mock.MagicMock(
                side_effect=[
                    (1, 'Wrong password'),
                    (1, 'Wrong password'),
                    (0, ''),
                ],
            )
            unzipper._extract_is_wrong_password_garbage = mock.MagicMock(
                return_value=False,
            )

            self.assertEqual(
                unzipper.password_collision(zip_obj, output_path), 'correct',
            )
            self.assertEqual(
                [call.args[2] for call in unzipper.driver.unzip.call_args_list],
                ['wrong', '', 'correct'],
            )
            self.assertTrue(zip_obj.is_extract_password_verified())
            self.assertEqual(zip_obj.verified_password(), 'correct')

    def test_disguised_mp3_tries_password_library(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            carrier = os.path.join(tmp, 'lala.mp3')
            output_path = os.path.join(tmp, 'out')
            os.makedirs(output_path)
            with open(carrier, 'wb') as fh:
                fh.write(b'ID3' + b'\x00' * 32)

            zip_obj = Zip(carrier, ['pw1', 'pw2'], False)
            zip_obj.file_list = ['track.wav']
            zip_obj.compression_ratio_info = {'encrypted': False}
            zip_obj.covered = True

            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper._run_single_unzip = mock.MagicMock(
                side_effect=[
                    (1, 'Wrong password'),
                    (1, 'Wrong password'),
                    (0, ''),
                ],
            )

            self.assertTrue(unzipper.single_threaded_unzip(zip_obj, output_path))
            self.assertEqual(unzipper._run_single_unzip.call_count, 3)
            self.assertEqual(
                [call.args[2] for call in unzipper._run_single_unzip.call_args_list],
                ['', 'pw1', 'pw2'],
            )

    def test_covered_carrier_uses_one_full_unzip_path(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            carrier = os.path.join(tmp, 'carrier.mp4')
            with open(carrier, 'wb') as fh:
                fh.write(b'\x00' * 32)
            archive = Zip(carrier, ['secret'], False)
            archive.covered = True
            archive.file_list = ['inner.zip', 'cover.jpg']
            archive.compression_ratio_info = {'encrypted': False}

            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper.single_threaded_unzip = mock.MagicMock(return_value=True)
            unzipper.password_collision = mock.MagicMock()

            result = unzipper.unzip(archive, os.path.join(tmp, 'out'), 100, 50)

            self.assertEqual(result, os.path.join(tmp, 'out'))
            unzipper.single_threaded_unzip.assert_called_once_with(
                archive, os.path.join(tmp, 'out'),
            )
            unzipper.password_collision.assert_not_called()

    def test_standard_zip_keeps_existing_password_collision_path(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'normal.zip')
            with open(path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 16)
            archive = Zip(path, ['secret'], False)
            archive.file_list = ['a.txt', 'b.txt']
            archive.compression_ratio_info = {
                'encrypted': False, 'compression_ratio': 10,
            }

            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper.password_collision = mock.MagicMock(return_value='secret')
            unzipper.single_threaded_unzip = mock.MagicMock(return_value=True)

            unzipper.unzip(archive, os.path.join(tmp, 'out'), 100, 50)

            unzipper.password_collision.assert_called_once()

    def test_split_zip_namelist_probe_does_not_hide_correct_candidate(self):
        from seven_z_driver import UnzipError
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'split.zip.001')
            second_path = os.path.join(tmp, 'split.zip.002')
            output_path = os.path.join(tmp, 'out')
            os.makedirs(output_path)
            with open(path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 16)
            with open(second_path, 'wb') as fh:
                fh.write(b'\x00' * 16)

            archive = Zip(
                path,
                ['wrong', 'correct'],
                False,
                volumes=[path, second_path],
            )
            archive.pw_list = ['wrong', 'correct']
            archive.file_list = ['RJ01593274.7z.002']
            archive.compression_ratio_info = {
                'encrypted': False, 'compression_ratio': 10,
            }

            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper.driver.get_namelist = mock.MagicMock(
                return_value=(
                    ['RJ01593274.7z.002'],
                    {'encrypted': False, 'compression_ratio': 10},
                ),
            )
            unzipper.driver.unzip = mock.MagicMock(
                side_effect=[
                    UnzipError('ERROR: Wrong password : RJ01593274.7z.002'),
                    UnzipError('ERROR: Wrong password : RJ01593274.7z.002'),
                    (0, ''),
                ],
            )
            unzipper._extract_is_wrong_password_garbage = mock.MagicMock(
                return_value=False,
            )

            self.assertTrue(unzipper._resolve_password_with_namelist(archive))
            self.assertEqual(archive.pw_list, ['wrong', 'correct'])
            self.assertEqual(
                unzipper.password_collision(archive, output_path),
                'correct',
            )
            self.assertEqual(
                [call.args[2] for call in unzipper.driver.unzip.call_args_list],
                ['wrong', '', 'correct'],
            )

    def test_zip_list_match_does_not_drop_earlier_password_candidates(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'archive.zip')
            output_path = os.path.join(tmp, 'out')
            os.makedirs(output_path)
            with open(path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 16)
            archive = Zip(path, ['correct', 'list-only'], False)
            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))

            def fake_namelist(*_args, password='', **_kwargs):
                if password in ('', 'correct'):
                    return [], {'encrypted': True}
                return ['payload.bin'], {'encrypted': True}

            unzipper._namelist_strategies = mock.MagicMock(
                return_value=[(None, False)],
            )
            unzipper.driver.get_namelist = mock.MagicMock(
                side_effect=fake_namelist,
            )

            self.assertTrue(unzipper.load_namelist(archive))
            self.assertEqual(archive.namelist_password(), 'list-only')
            self.assertFalse(archive.is_extract_password_verified())
            self.assertIn('correct', archive.pw_list)

            unzipper._run_single_unzip = mock.MagicMock(
                side_effect=[(1, 'Wrong password'), (0, '')],
            )
            unzipper._extract_is_wrong_password_garbage = mock.MagicMock(
                return_value=False,
            )
            self.assertTrue(
                unzipper.single_threaded_unzip(
                    archive, output_path, known_password=True,
                )
            )
            self.assertEqual(
                [call.args[2] for call in unzipper._run_single_unzip.call_args_list],
                ['list-only', 'correct'],
            )

    def test_load_namelist_rejects_7z_list_only_password(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, '01646431.7z')
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 16)

            zip_obj = Zip(outer_path, ['RJ01646431', 'yisiki'], False)
            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper.driver.probe_content_encrypted_single_block = mock.MagicMock(
                return_value={
                    'content_encrypted_solid': False,
                    'content_only_encryption': True,
                },
            )
            unzipper.driver.get_namelist = mock.MagicMock(
                side_effect=[
                    (['RJ01646431.zip'], {'encrypted': True}),
                    (['RJ01646431.zip'], {'encrypted': True}),
                ],
            )
            unzipper.driver.test_archive = mock.MagicMock(
                side_effect=[(False, 'Wrong password'), (True, '')],
            )

            self.assertTrue(unzipper.load_namelist(zip_obj))
            self.assertEqual(zip_obj.namelist_password(), 'yisiki')
            self.assertFalse(zip_obj.is_extract_password_verified())

    def test_header_encrypted_7z_skips_full_test_but_extract_confirms_password(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, 'header-encrypted.7z')
            output_path = os.path.join(tmp, 'out')
            os.makedirs(output_path)
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 16)

            zip_obj = Zip(outer_path, ['wrong', 'correct'], False)
            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper.driver.probe_content_encrypted_single_block = mock.MagicMock(
                return_value={
                    'content_encrypted_solid': False,
                    'content_only_encryption': False,
                },
            )
            unzipper.driver.get_namelist = mock.MagicMock(
                return_value=(['inner.zip'], {'encrypted': True}),
            )
            unzipper.driver.test_archive = mock.MagicMock()
            unzipper._run_single_unzip = mock.MagicMock(
                side_effect=[(1, 'Wrong password'), (0, '')],
            )
            unzipper._extract_is_wrong_password_garbage = mock.MagicMock(
                return_value=False,
            )

            self.assertTrue(unzipper.load_namelist(zip_obj))
            unzipper.driver.test_archive.assert_not_called()
            self.assertEqual(zip_obj.namelist_password(), 'wrong')
            self.assertFalse(zip_obj.is_extract_password_verified())

            self.assertTrue(
                unzipper.single_threaded_unzip(
                    zip_obj, output_path, known_password=True,
                )
            )
            self.assertEqual(
                [call.args[2] for call in unzipper._run_single_unzip.call_args_list],
                ['wrong', 'correct'],
            )
            self.assertEqual(zip_obj.verified_password(), 'correct')

    def test_password_collision_uses_smallest_zip_member_first(self):
        import file_ops
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'normal.zip')
            output_path = os.path.join(tmp, 'out')
            os.makedirs(output_path)
            with open(path, 'wb') as fh:
                fh.write(b'PK\x03\x04' + b'\x00' * 16)
            archive = Zip(path, ['secret'], False)
            archive.file_list = ['large.bin', 'small.txt']
            archive.compression_ratio_info = {'encrypted': False}

            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper.driver.unzip = mock.MagicMock(return_value=(0, ''))
            unzipper._extract_is_wrong_password_garbage = mock.MagicMock(
                return_value=False,
            )

            with mock.patch.object(
                file_ops, 'zip_smallest_probe_member', return_value='small.txt',
            ):
                self.assertEqual(
                    unzipper.password_collision(archive, output_path), 'secret',
                )

            self.assertEqual(archive.file_list, ['small.txt', 'large.bin'])
            self.assertEqual(unzipper.driver.unzip.call_args.args[3], 'small.txt')
            self.assertFalse(archive.is_extract_password_verified())

    def test_load_namelist_skips_library_for_content_encrypted_solid_7z(self):
        from unzip_process_pool import ProcessResourceManager
        from unzipper import Unzipper

        with tempfile.TemporaryDirectory() as tmp:
            outer_path = os.path.join(tmp, '01646431.7z')
            with open(outer_path, 'wb') as fh:
                fh.write(b'7z\xbc\xaf\x27\x1c' + b'\x00' * 16)

            zip_obj = Zip(outer_path, ['library_pw1', 'library_pw2'], False)
            unzipper = Unzipper(mock.MagicMock(), ProcessResourceManager(4))
            unzipper.driver.probe_content_encrypted_single_block = mock.MagicMock(
                return_value={'content_encrypted_solid': True},
            )
            unzipper.driver.get_namelist = mock.MagicMock(
                return_value=([], {'encrypted': True}),
            )
            unzipper.driver.test_archive = mock.MagicMock()

            self.assertFalse(unzipper.load_namelist(zip_obj))
            self.assertTrue(zip_obj.requires_manual_password())
            unzipper.driver.get_namelist.assert_not_called()

            zip_obj.set_note('yisiki')
            unzipper.driver.get_namelist = mock.MagicMock(
                return_value=(['RJ01646431.zip'], {'encrypted': True}),
            )
            unzipper.driver.test_archive = mock.MagicMock(return_value=(True, ''))

            self.assertTrue(unzipper.load_namelist(zip_obj))
            self.assertEqual(zip_obj.namelist_password(), 'yisiki')
            self.assertFalse(zip_obj.is_extract_password_verified())
            unzipper.driver.get_namelist.assert_called_once()
            self.assertEqual(
                unzipper.driver.get_namelist.call_args.kwargs.get('password'), 'yisiki',
            )

    def test_format_manual_7z_status_detail(self):
        probe = {
            'listable_without_password': True,
            'blocks': 1,
            'store_encrypted': True,
            'file_size': 3775660497,
        }
        detail = Zip.format_manual_7z_status_detail(r'D:\下载\RJ01620216.7z', probe)
        self.assertIn('RJ01620216.7z', detail)
        self.assertIn('仅内容加密', detail)
        self.assertIn('单Block', detail)
        self.assertIn('Copy存储', detail)
        self.assertIn('3.5GB', detail)
        self.assertIn('双击任务填密码', detail)

    def test_requeue_skips_manual_7z_until_note_is_set(self):
        import task_runner
        from timeline import Archive, Timeline

        task_runner.timelines.clear()
        task_runner.passwords = []
        zip_obj = Zip(r'D:\work\RJ01620216.7z', [], False)
        zip_obj.manual_password_only = True
        zip_obj.compression_ratio_info['manual_password_only'] = True
        timeline = Timeline(Archive(r'D:\work\RJ01620216.7z'), 'unzip_failed', zip_obj)
        task_runner.timelines.append(timeline)

        self.assertFalse(task_runner.requeue_unzip_failure(timeline))
        self.assertEqual(timeline.get_current_record().ops, 'unzip_failed')

        zip_obj.set_note('0721')
        self.assertTrue(task_runner.requeue_unzip_failure(timeline))
        self.assertEqual(timeline.get_current_record().ops, 'find_zip')
        self.assertEqual(zip_obj.note, '0721')

    def test_get_namelist_detects_zip_aes_encryption(self):
        import subprocess
        from seven_z_driver import SevenZDriver
        from app_paths import seven_zip_exe

        with tempfile.TemporaryDirectory() as tmp:
            payload = os.path.join(tmp, 'inner.txt')
            with open(payload, 'w', encoding='utf-8') as fh:
                fh.write('payload')
            path = os.path.join(tmp, 'locked.zip')
            exe = seven_zip_exe()
            cmd = [exe, 'a', '-tzip', '-psecret', path, payload]
            subprocess.run(cmd, check=True, capture_output=True)

            driver = SevenZDriver(exe)
            names, info = driver.get_namelist(path, password='')
            self.assertEqual(len(names), 1)
            self.assertTrue(info.get('encrypted'))

    def test_wz_aes_zip_password_via_pyzipper(self):
        import pyzipper
        from seven_z_driver import SevenZDriver
        from app_paths import seven_zip_exe
        import file_ops

        with tempfile.TemporaryDirectory() as tmp:
            payload = os.path.join(tmp, 'inner.bin')
            with open(payload, 'wb') as fh:
                fh.write(b'Rar!\x1a\x07\x00' + b'payload')
            path = os.path.join(tmp, 'wz_aes.zip')
            with pyzipper.AESZipFile(
                path, 'w', compression=pyzipper.ZIP_DEFLATED,
                encryption=pyzipper.WZ_AES,
            ) as zf:
                zf.setpassword(b'secret')
                zf.writestr('folder/', b'')
                zf.write(payload, 'inner.bin')

            self.assertTrue(file_ops.zip_uses_wz_aes(path))
            driver = SevenZDriver(seven_zip_exe())
            ok, _msg = driver.test_archive(path, password='wrong')
            self.assertFalse(ok)
            ok, _msg = driver.test_archive(path, password='secret')
            self.assertTrue(ok)

            out_dir = os.path.join(tmp, 'out')
            os.makedirs(out_dir)
            rc, _pw = driver.unzip(path, out_dir, password='secret')
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(os.path.join(out_dir, 'inner.bin')))


if __name__ == '__main__':

    unittest.main()

