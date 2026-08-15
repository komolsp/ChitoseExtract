import os
import re

from enum import Enum

import file_ops
import archive_registry
from seven_z_driver import (
    SevenZDriver,
    JapDecodeError,
    GetNamelistError,
    NoFile2ProcessError,
    CannotDeleteOutputFile,
    UnzipError,
    is_password_error_message,
)
from unzip_process_pool import ProcessResourceManager

from zip import Zip


class Prefix(Enum):
    PASSWORD_COLLISION = 'PC_'
    UNZIP = 'unzip_'


# 套娃内层加密包扫描时，先用 7z l 快速试探的密码数量上限（RJ/备注/文件名优先）
NESTED_PASSWORD_PROBE_LIMIT = 16


class Unzipper():

    def __init__(self, logger, resource_manager: ProcessResourceManager, seven_z_path=None,
                 seven_z_mmt: int = 0):
        self.logger = logger
        self.driver = SevenZDriver(seven_z_path, mmt=seven_z_mmt) if seven_z_path else SevenZDriver(mmt=seven_z_mmt)

        self.resource = resource_manager
        self.progress_ui = None

    def set_seven_z_mmt(self, mmt: int):
        self.driver.mmt = max(0, int(mmt or 0))

    def _namelist_strategies(self, zip: Zip, is_volume: bool):
        from volume.resolver import is_standard_volume_group
        if is_volume and zip.volumes and is_standard_volume_group(zip.volumes):
            return [(None, False)]
        return file_ops.build_archive_open_strategies(
            file_ops.ArchiveProbe(True, covered=zip.covered, format_type=zip.format_type),
            zip.extension,
            zip.path,
            is_volume=is_volume,
        )

    def _reject_covered_namelist(self, zip: Zip, covered: bool) -> bool:
        """标准压缩包禁止接受 covered 策略的伪列目录结果。"""
        return covered and not file_ops.should_allow_covered_open_strategy(
            zip.path, zip.extension or '',
        )

    @staticmethod
    def _is_nested_encrypted_item_error(message: str) -> bool:
        """外层密码正确但内层加密项解压失败（如 CRC Failed in encrypted file: inner.zip）。"""
        if not message:
            return False
        lowered = message.lower()
        return 'encrypted file' in lowered and ':' in message

    def _extracted_archive_is_genuine(self, archive_path: str) -> bool:
        """解出的内层压缩包须结构有效，防止外层错误密码产生的损坏碎片。"""
        if not os.path.isfile(archive_path):
            return False
        try:
            if os.path.getsize(archive_path) < 22:
                return False
        except OSError:
            return False

        ext = os.path.splitext(archive_path)[1].lower()
        if ext in ('.zip', '.7z', '.rar') and not file_ops.has_leading_archive_magic(archive_path):
            return False

        for format_type, covered in ((None, False),):
            try:
                namelist, info = self.driver.get_namelist(
                    compress_file=archive_path,
                    password='',
                    jap=False,
                    covered=covered,
                    format_type=format_type,
                )
            except (GetNamelistError, JapDecodeError):
                continue
            if not namelist:
                continue
            if info.get('encrypted'):
                return True
            ok, _msg = self.driver.test_archive(
                compress_file=archive_path,
                password='',
                jap=False,
                covered=covered,
                format_type=format_type,
            )
            if ok:
                return True
        if not file_ops.is_standard_archive_file(archive_path):
            return file_ops.probe_archive(archive_path, nested=True).is_candidate
        return False

    def _output_has_usable_partial_extract(self, output_path: str) -> bool:
        """外层因内层加密项报错时，是否已解出可继续处理的真实内层压缩包。"""
        if not output_path or not os.path.isdir(output_path):
            return False
        try:
            names = os.listdir(output_path)
        except OSError:
            return False
        for name in names:
            full = os.path.join(output_path, name)
            if not os.path.isfile(full):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in ('.zip', '.7z', '.rar', '.001', '.ha'):
                continue
            if self._extracted_archive_is_genuine(full):
                return True
        return False

    def work_root_has_valid_inner_archive(self, work_root: str | None) -> bool:
        return self._output_has_usable_partial_extract(work_root or '')

    def _can_accept_partial_inner_extract(self, zip: Zip, msg: str, *, verified: bool) -> bool:
        """外层密码已由 7z l 验证，仅内层加密项解压报错。"""
        if not verified or not self._is_nested_encrypted_item_error(msg or ''):
            return False
        password = zip.verified_password()
        return bool(password)

    def _extract_is_wrong_password_garbage(self, output_path: str) -> bool:
        """加密包在错误密码/隐写模式下解出的纯碎片垃圾。"""
        if not output_path or not os.path.isdir(output_path):
            return False
        try:
            names = os.listdir(output_path)
        except OSError:
            return False
        if not names:
            return False
        for name in names:
            full = os.path.join(output_path, name)
            if os.path.isdir(full):
                return False
            if not file_ops.is_covered_extract_junk_basename(name):
                return False
        return True

    def _resolve_password_with_namelist(self, zip: Zip) -> bool:
        """用 7z l 试密码，避免整包 7z x 逐个试错。"""
        if zip.is_namelist_current():
            return True

        compress = zip.path
        is_volume = len(zip.volumes or []) > 1 or file_ops.is_volume_zip(compress)
        strategies = self._namelist_strategies(zip, is_volume)
        original_passwords = list(zip.pw_list)

        if zip.file_list and zip.pw_list:
            password = zip.pw_list[0]
            for format_type, covered in strategies:
                try:
                    namelist, info = self.driver.get_namelist(
                        compress_file=compress,
                        password=password,
                        jap=zip.jap,
                        covered=covered,
                        format_type=format_type,
                    )
                except GetNamelistError as err:
                    if 'Wrong password' in err.error_info:
                        break
                    continue
                except JapDecodeError:
                    zip.jap = True
                    continue
                if namelist:
                    if self._reject_covered_namelist(zip, covered):
                        continue
                    zip.compression_ratio_info = info
                    zip.covered = covered
                    zip.format_type = format_type
                    if self._namelist_password_needs_extract_confirmation(zip):
                        zip.pw_list = original_passwords
                    else:
                        zip.pw_list = [password]
                    zip.mark_namelist_scanned(password)
                    return True
            zip.invalidate_namelist_scan()

        for password in zip.pw_list:
            for format_type, covered in strategies:
                try:
                    namelist, info = self.driver.get_namelist(
                        compress_file=compress,
                        password=password,
                        jap=zip.jap,
                        covered=covered,
                        format_type=format_type,
                    )
                except GetNamelistError as err:
                    if 'Wrong password' in err.error_info:
                        break
                    continue
                except JapDecodeError:
                    zip.jap = True
                    continue
                if namelist:
                    if self._reject_covered_namelist(zip, covered):
                        continue
                    zip.file_list = list(set(namelist))
                    zip.compression_ratio_info = info
                    zip.covered = covered
                    zip.format_type = format_type
                    if self._namelist_password_needs_extract_confirmation(zip):
                        zip.pw_list = original_passwords
                    else:
                        zip.pw_list = [password]
                    zip.mark_namelist_scanned(password)
                    return True
        return False

    def _volume_can_multiprocess(self, zip: Zip, size: float,
                                 thread_threshold_mb: float,
                                 thread_compression_ratio: float) -> bool:
        if zip.covered or not zip.file_list or len(zip.file_list) <= 1:
            return False
        if zip.compression_ratio_info.get('encrypted'):
            return False
        avg_size = size / len(zip.file_list)
        if avg_size > 200:
            return True
        return (avg_size > thread_threshold_mb
                and zip.compression_ratio_info.get('compression_ratio', 0)
                > thread_compression_ratio)

    def _emit_unzip_progress(self, current: int, total: int):
        if total <= 0:
            total = 1
        current = min(max(current, 0), total)
        self.resource.log_queue.put(('unzip', f'解压中 {current}/{total}'))

    def _will_use_single_operation_unzip(self, zip: Zip, size: float,
                                         thread_threshold_mb: float,
                                         thread_compression_ratio: float) -> bool:
        """整包一次解压（进度显示为 0/1 → 1/1）。"""
        file_count = len(zip.file_list)
        if file_count <= 1 or zip.covered:
            return True
        if zip.compression_ratio_info.get('encrypted'):
            return True
        avg_size = size / file_count
        if avg_size > 200:
            return False
        if (avg_size > thread_threshold_mb
                and zip.compression_ratio_info.get('compression_ratio', 0) > thread_compression_ratio):
            return False
        return True

    def _confirm_standard_encrypted_password(
        self,
        zip: Zip,
        compress_file: str,
        password: str,
        *,
        covered: bool,
        format_type: str | None,
        encrypted: bool,
    ) -> bool:
        """7z l 对加密 .7z/.rar 可能误接受错密码，须再用 7z t 确认。"""
        if not encrypted:
            return True
        ext = (zip.extension or os.path.splitext(compress_file)[1]).lower()
        if ext == '.zip':
            # ZIP（包括 AES/传统加密及分卷）常可在无密码时列出目录；
            # 此处只排除空密码，不做全包 7z t。正式解压会按候选密码
            # 依次尝试，并以 7-Zip 的退出码完成最终校验，避免整包读取两次。
            return bool(password)
        if not file_ops.is_standard_archive_file(compress_file):
            return True
        if ext in ('.7z', '.rar', '.zip'):
            ok, _msg = self.driver.test_archive(
                compress_file=compress_file,
                password=password,
                jap=zip.jap,
                covered=covered,
                format_type=format_type,
            )
            return ok
        return True

    _MANUAL_7Z_LOG = (
        '特殊 7z 压缩包（仅内容加密 + 单 Block 大包），已跳过密码库试密；'
        '请双击任务在备注中填写密码后重试：{}'
    )

    def _mark_manual_password_7z(self, zip: Zip, probe: dict | None = None):
        zip.manual_password_only = True
        zip.compression_ratio_info['manual_password_only'] = True
        if probe:
            zip.compression_ratio_info['manual_7z_probe'] = probe

    def _notify_manual_7z_status(self, zip: Zip, scan_path: str, probe: dict | None = None):
        """特殊 7z 提示延后至任务全部结束后，由运行状态栏统一展示。"""
        return

    def _passwords_for_load_namelist(
        self,
        zip: Zip,
        *,
        password_probe_limit: int | None,
        scan_path: str,
    ) -> list[str] | None:
        """
        返回试密列表；None 表示沿用常规 pw_list；
        空列表表示已识别为特殊 7z 且无用户侧密码，应中止。
        """
        ext = (zip.extension or os.path.splitext(scan_path)[1]).lower()
        if ext != '.7z' or not file_ops.is_standard_archive_file(scan_path):
            return None

        if zip.requires_manual_password():
            manual = zip.manual_password_candidates()
            if not manual:
                self.logger.warning(self._MANUAL_7Z_LOG.format(os.path.normpath(scan_path)))
                self._notify_manual_7z_status(zip, scan_path)
            return manual

        probe = self.driver.probe_content_encrypted_single_block(
            scan_path,
            jap=zip.jap,
            covered=zip.covered,
            format_type=zip.format_type,
        )
        if not probe.get('content_encrypted_solid'):
            return None

        self._mark_manual_password_7z(zip, probe)
        manual = zip.manual_password_candidates()
        self.logger.warning(self._MANUAL_7Z_LOG.format(os.path.normpath(scan_path)))
        self._notify_manual_7z_status(zip, scan_path, probe)
        return manual

    def load_namelist(self, zip: Zip, *, password_probe_limit: int | None = None):
        from volume.probe import try_expand_volumes

        if zip.is_namelist_current():
            return True

        scan_path = zip.path
        manual_passwords = self._passwords_for_load_namelist(
            zip, password_probe_limit=password_probe_limit, scan_path=scan_path,
        )
        if manual_passwords is not None:
            if not manual_passwords:
                return False
            passwords = manual_passwords
        else:
            passwords = self._ordered_password_candidates(zip, password_probe_limit)
        if file_ops.is_standard_archive_file(zip.path):
            ext = (zip.extension or '').lower()
            if ext in ('.7z', '.rar'):
                passwords = [pw for pw in passwords if pw]
            elif '' not in passwords:
                passwords.insert(0, '')
        elif '' not in passwords:
            passwords.insert(0, '')
        if not zip.volumes:
            zip.volumes = [zip.path]

        namelist = []
        compression_ratio_info = {}
        matched_password = None
        scan_anchor = zip.path
        for _expand_retry in range(3):
            namelist = []
            compression_ratio_info = {}
            is_volume = len(zip.volumes) > 1 or file_ops.is_volume_zip(zip.path)
            strategies = self._namelist_strategies(zip, is_volume)
            scan_volumes = zip.volumes[:1] if is_volume else zip.volumes
            expanded = False
            for volume in scan_volumes:
                for password in passwords:
                    wrong_password = False
                    volume_namelist = []
                    strategy_ratio_info = {}
                    for format_type, covered in strategies:
                        retry = True
                        while retry:
                            try:
                                volume_namelist, strategy_ratio_info = self.driver.get_namelist(
                                    compress_file=volume,
                                    password=password,
                                    jap=zip.jap,
                                    covered=covered,
                                    format_type=format_type,
                                )
                            except GetNamelistError as err:
                                if 'Wrong password' in err.error_info:
                                    wrong_password = True
                                    break
                                elif not zip.jap and file_ops.encode_detect(
                                    err.error_info.split(':', 1)[-1] if ':' in err.error_info else err.error_info
                                ):
                                    zip.jap = True
                                    continue
                                from volume.probe import anchor_relates_to_volumes
                                current = list(zip.volumes) if zip.volumes else [volume]
                                new_volumes = try_expand_volumes(current, err.error_info)
                                if (new_volumes and new_volumes != current
                                        and anchor_relates_to_volumes(scan_anchor, new_volumes)):
                                    zip.volumes = new_volumes
                                    zip.path = new_volumes[0]
                                    self.logger.info(
                                        '  7-Zip 提示缺少分卷，已补全： [{}]'.format(
                                            '],['.join(new_volumes),
                                        )
                                    )
                                    expanded = True
                                retry = False
                                break
                            except JapDecodeError:
                                zip.jap = True
                            else:
                                if volume_namelist and self._reject_covered_namelist(zip, covered):
                                    volume_namelist = []
                                    retry = False
                                    continue
                                if volume_namelist and not self._confirm_standard_encrypted_password(
                                    zip,
                                    volume,
                                    password,
                                    covered=covered,
                                    format_type=format_type,
                                    encrypted=bool(strategy_ratio_info.get('encrypted')),
                                ):
                                    volume_namelist = []
                                    retry = False
                                    continue
                                zip.covered = covered
                                zip.format_type = format_type
                                compression_ratio_info = strategy_ratio_info
                                if covered or format_type:
                                    self.logger.debug(
                                        '压缩包打开策略成功："{}" format={} covered={}'.format(
                                            os.path.normpath(volume), format_type, covered,
                                        )
                                    )
                                retry = False
                                break
                        if wrong_password:
                            break
                        if volume_namelist:
                            break
                    if wrong_password:
                        continue
                    if volume_namelist:
                        namelist.extend(volume_namelist)
                        matched_password = password
                        start_at = passwords.index(password)
                        passwords = passwords[start_at:]
                        break
                else:
                    break
                if namelist:
                    break

            if expanded and not namelist:
                continue
            break
        if len(namelist) > 0:
            if compression_ratio_info.get('encrypted') and matched_password is None:
                return False
            if (
                compression_ratio_info.get('encrypted')
                and matched_password == ''
                and file_ops.is_standard_archive_file(zip.path)
                and (zip.extension or '').lower() in ('.7z', '.rar', '.zip')
            ):
                return False
            namelist = list(set(namelist))
            if len(namelist) == 1:
                rj = re.compile(r'[RBV]J(\d{6}|\d{8})(?!\d+)').search(namelist[0].upper())
                if rj and rj.group() not in passwords:
                    passwords.insert(0, rj.group())
            zip.pw_list = passwords
            zip.file_list = namelist
            zip.compression_ratio_info = compression_ratio_info
            zip.mark_namelist_scanned(matched_password)
            return True
        return False

    @staticmethod
    def _ordered_password_candidates(zip: Zip, limit: int | None = None) -> list[str]:
        if zip.requires_manual_password():
            ordered = zip.manual_password_candidates()
        else:
            ordered = []
            for pw in zip.pw_list:
                if pw and pw not in ordered:
                    ordered.append(pw)
        if limit is not None and limit > 0:
            return ordered[:limit]
        return ordered

    def _resolve_encrypted_password(self, zip: Zip) -> bool:
        """取得可用的非空密码候选；最终正确性由正式解压退出码确认。"""
        if zip.is_namelist_current():
            return bool(zip.verified_password())
        return self.load_namelist(zip)

    def unzip(self, zip: Zip, output_path, thread_threshold_mb, thread_compression_ratio):
        if zip.volumes and len(zip.volumes) > 1:
            from volume.rename import resolve_volume_paths_on_disk
            synced = resolve_volume_paths_on_disk(list(zip.volumes))
            if synced != zip.volumes:
                zip.volumes = synced
                zip.path = synced[0]
                zip.invalidate_namelist_scan()
        size = 0
        if zip.volumes:
            for volume in zip.volumes:
                if not os.path.isfile(volume):
                    self.logger.info(
                        '  分卷文件不存在，跳过解压 [{}]'.format(volume),
                    )
                    return None
                size += os.path.getsize(volume) / (1024 * 1024)
        self.logger.debug('尝试解压[{}]'.format(zip.path))
        single_op = self._will_use_single_operation_unzip(
            zip, size, thread_threshold_mb, thread_compression_ratio)
        if single_op:
            self._emit_unzip_progress(0, 1)

        # 列目录时已验证密码：优先整包解压，避免对每个候选密码重复试探
        if zip.is_namelist_current() and zip.pw_list:
            if self.single_threaded_unzip(zip, output_path, known_password=True):
                return output_path
            zip.invalidate_namelist_scan()

        # 加密包：7z l + 7z t 找密码，禁止 password_collision 逐文件试探（极慢）
        if zip.container_requires_password():
            if not self._resolve_encrypted_password(zip):
                self.logger.info(
                    " 文件[' {} ']解压失败,无匹配的解压码".format(zip.path)
                )
                return None
            if self.single_threaded_unzip(zip, output_path):
                return output_path
            self.logger.info(
                " 文件[' {} ']解压失败,密码验证未通过".format(zip.path)
            )
            return None

        if zip.covered:
            # 隐写载体以前先局部解出探测文件，再整包解压，导致探测文件被重复
            # 读取和写入。这里仅对已由 probe 标记为 covered 的载体改为一次
            # 整包解压；标准 ZIP/7z/RAR、分卷仍走原有分支。
            if self.single_threaded_unzip(zip, output_path):
                return output_path
            return None

        if zip.volumes and len(zip.volumes) > 1:
            self.logger.info(
                '  分卷组解压 [' + '],['.join(zip.volumes) + ']',
            )
            if not self._resolve_password_with_namelist(zip):
                self.logger.info(f" 文件[' {zip.path} ']解压失败,无匹的解压码")
                return None
            if self._volume_can_multiprocess(
                    zip, size, thread_threshold_mb, thread_compression_ratio):
                self.logger.info(f"  分卷组多进程逐文件解压 [' {zip.path} ']")
                try:
                    password = self.password_collision(zip, output_path)
                except NoFile2ProcessError:
                    if not self.single_threaded_unzip(zip, output_path, known_password=True):
                        return None
                    return output_path
                if not password:
                    self.logger.info(f" 文件[' {zip.path} ']解压失败,无匹的解压码")
                    return None
                if len(zip.file_list) == 1 and not zip.covered:
                    self._emit_unzip_progress(1, 1)
                    self.logger.info(f" 文件[' {zip.path} ']解压完成")
                elif not self.multi_threaded_unzip(zip, output_path):
                    if not self.single_threaded_unzip(zip, output_path, known_password=True):
                        return None
                return output_path
            if not self.single_threaded_unzip(zip, output_path, known_password=True):
                return None
            return output_path

        if not zip.file_list and not zip.covered:
            # 之前从未用现有密码取得过文件列表（例如刚拖入时密码库里没有匹配项，
            # 被放入任务队列等待手动补充密码），没有具体文件名可做局部密码试探，
            # 直接对整个压缩包逐个尝试密码。
            self.logger.debug(f'文件[{zip.path}]文件列表未知，改用整包解压逐个尝试密码')
            if not single_op:
                self._emit_unzip_progress(0, 1)
            if not self.single_threaded_unzip(zip, output_path):
                return None
            return output_path

        try:
            password = self.password_collision(zip, output_path)
        except NoFile2ProcessError:
            self.logger.debug('文件[{}]中含有特殊字符无法逐个解压，使用单进程完整解压'.format(zip.path))
            if not single_op:
                self._emit_unzip_progress(0, 1)
            if not self.single_threaded_unzip(zip, output_path):
                return None
        else:
            if not password:
                self.logger.info(f" 文件[' {zip.path} ']解压失败,无匹的解压码")
                return None
            elif len(zip.file_list) == 1 and not zip.covered:
                # 单文件普通压缩包：password_collision 已解压该文件，无需再跑完整解压。
                self._emit_unzip_progress(1, 1)
                self.logger.info(f" 文件[' {zip.path} ']解压完成")
            elif not zip.compression_ratio_info["encrypted"] and \
                    (size / len(zip.file_list) > 200 or (size / len(zip.file_list) > thread_threshold_mb and zip.compression_ratio_info[
                         "compression_ratio"] > thread_compression_ratio)):  # 判断压缩文件加密、平均文件size、文件压缩率
                # 由于前置过滤的存在，计算并不完全准确
                self.logger.info(f" 使用多线程解压 [' {zip.path} ']")
                if not self.multi_threaded_unzip(zip, output_path):
                    return None
            else:
                self.logger.info(f"{size}MB/{len(zip.file_list)} Files "
                                 f"压缩率： {zip.compression_ratio_info.get('compression_ratio')} "
                                 f"加密： {zip.compression_ratio_info.get('encrypted')} ,"
                                 f"使用单线程解压 [' {zip.path} ']")
                if not self.single_threaded_unzip(zip, output_path):
                    return None
        return output_path

    def password_collision(self, zip: Zip, output_path):
        from volume.probe import try_expand_volumes

        if zip.container_requires_password():
            if self._resolve_encrypted_password(zip):
                return zip.verified_password()
            return None

        if zip.is_namelist_current():
            verified = zip.verified_password()
            if self._namelist_password_needs_extract_confirmation(zip):
                password_candidates = self._unzip_password_candidates(zip)
                if verified in password_candidates:
                    password_candidates = [verified] + [
                        password for password in password_candidates
                        if password != verified
                    ]
            else:
                password_candidates = [verified]
        else:
            password_candidates = list(zip.pw_list)

        first_file = '2.zip' if zip.covered else zip.file_list[0]
        for password in password_candidates:
            args = [zip.path, output_path, password, first_file, zip.jap, zip.covered, zip.format_type]
            try:
                returncode, msg = self.driver.unzip(*args)
            except UnzipError as err:
                self.logger.debug('密码试探解压失败 [{}]: {}'.format(zip.path, err))
                current = list(zip.volumes) if zip.volumes else [zip.path]
                new_volumes = try_expand_volumes(current, str(err))
                if new_volumes and new_volumes != current:
                    zip.volumes = new_volumes
                    zip.path = new_volumes[0]
                    self.logger.info(
                        '  7-Zip 提示缺少分卷，已补全： [{}]'.format(
                            '],['.join(new_volumes),
                        )
                    )
                    return self.password_collision(zip, output_path)
                continue
            if returncode == 0:
                if self._extract_is_wrong_password_garbage(output_path):
                    self.logger.debug(
                        '密码试探产出碎片垃圾，已跳过：{} [{}]'.format(
                            os.path.normpath(zip.path), password,
                        )
                    )
                    continue
                zip.pw_list = [password]
                return password
            current = list(zip.volumes) if zip.volumes else [zip.path]
            new_volumes = try_expand_volumes(current, msg or '')
            if new_volumes and new_volumes != current:
                zip.volumes = new_volumes
                zip.path = new_volumes[0]
                self.logger.info(
                    '  7-Zip 提示缺少分卷，已补全： [{}]'.format(
                        '],['.join(new_volumes),
                    )
                )
                return self.password_collision(zip, output_path)

    @staticmethod
    def _namelist_password_needs_extract_confirmation(zip: Zip) -> bool:
        """ZIP 的目录通常无需密码即可读取，不能据此锁死密码候选。"""
        extension = (zip.extension or os.path.splitext(zip.path)[1]).lower()
        if extension == '.zip' or zip.format_type == 'zip':
            return True
        return any(
            re.search(r'\.zip\.\d{3}$', os.path.basename(path), re.IGNORECASE)
            for path in (zip.volumes or [zip.path])
            if path
        )

    def multi_threaded_unzip(self, zip: Zip, output_path):
        list_id = Prefix.UNZIP.value + zip.name
        file_list = zip.file_list
        password = zip.pw_list[0]
        total = len(file_list)
        self.resource.set_list_total(list_id, total)
        self._emit_unzip_progress(1, total)
        progress = 1
        for file in file_list[1:]:
            args = [zip.path, output_path, password, file, zip.jap, zip.covered, zip.format_type]

            self.resource.submit(list_id, self.driver.unzip, *args)
            self.logger.debug(f"提交解压任务至进程池：{file} | {progress}/{len(file_list)} ")
            progress += 1
        else:
            # 阻塞等待该任务组完成
            progress = self.resource.list_progress[list_id]
            self.logger.info(f"等待解压进程 | {progress}/{len(file_list)} ")
            event = self.resource.list_events[list_id]
            event.wait()
            status = self.resource.list_status[list_id]
            if status['completed']:
                self.logger.info(
                    f"解压完成： [' {zip.path} '] 密码： [' {password} '] ，删除源文件：{zip.del_after_unzip}")
                return True
            else:
                self.logger.info(f" 文件[' {zip.path} ']解压失败,{status['error']}")
                self.resource.cancel_list(list_id)
                return False

    def _run_single_unzip(self, zip: Zip, output_path, password: str):
        saved_mmt = self.driver.mmt
        is_volume = zip.volumes and len(zip.volumes) > 1
        if is_volume:
            self.driver.mmt = 0
        try:
            return self.driver.unzip(
                zip.path, output_path, password, None, zip.jap, zip.covered,
                zip.format_type,
            )
        except UnzipError as err:
            return 1, str(err)
        finally:
            self.driver.mmt = saved_mmt

    @staticmethod
    def _unzip_password_candidates(zip: Zip) -> list[str]:
        candidates = Unzipper._ordered_password_candidates(zip)
        if '' not in candidates:
            candidates.insert(0, '')
        return candidates

    def single_threaded_unzip(self, zip: Zip, output_path, *, known_password=False):
        from volume.probe import try_expand_volumes

        if zip.container_requires_password():
            if known_password and zip.is_namelist_current() and zip.verified_password():
                verified = zip.verified_password()
                password_candidates = [verified] + [
                    pw for pw in self._ordered_password_candidates(zip) if pw != verified
                ]
            elif not self._resolve_encrypted_password(zip):
                self.logger.info(f" 文件[' {zip.path} ']密码匹配失败")
                return False
            else:
                password_candidates = self._ordered_password_candidates(zip)
                verified_pw = zip.verified_password()
                if verified_pw and verified_pw in password_candidates:
                    password_candidates = [verified_pw] + [
                        pw for pw in password_candidates if pw != verified_pw
                    ]

            for _expand_retry in range(3):
                retry_outer = False
                verified = zip.is_namelist_current()
                for password in password_candidates:
                    returncode, msg = self._run_single_unzip(zip, output_path, password)
                    if returncode == 0:
                        if self._extract_is_wrong_password_garbage(output_path):
                            self.logger.info(
                                " 文件[' {} ']解压产出为碎片垃圾，密码未通过".format(zip.path),
                            )
                            continue
                        zip.pw_list = [password]
                        zip.mark_namelist_scanned(password)
                        self._emit_unzip_progress(1, 1)
                        self.logger.info(f'[{zip.path}]解压完成')
                        return True
                    if is_password_error_message(msg or ''):
                        if self._can_accept_partial_inner_extract(zip, msg or '', verified=verified) and (
                            self._output_has_usable_partial_extract(output_path)
                        ):
                            zip.pw_list = [password]
                            zip.mark_namelist_scanned(password)
                            self._emit_unzip_progress(1, 1)
                            self.logger.info(
                                '[{}] 外层解压完成（内层加密项待单独处理）'.format(zip.path),
                            )
                            return True
                        self.logger.debug(
                            '密码错误，已跳过：{} [{}]'.format(
                                os.path.normpath(zip.path), password,
                            )
                        )
                        continue
                    current = list(zip.volumes) if zip.volumes else [zip.path]
                    new_volumes = try_expand_volumes(current, msg or '')
                    if new_volumes and new_volumes != current:
                        zip.volumes = new_volumes
                        zip.path = new_volumes[0]
                        zip.invalidate_namelist_scan()
                        if not self._resolve_encrypted_password(zip):
                            retry_outer = True
                            break
                        password_candidates = self._ordered_password_candidates(zip)
                        retry_outer = True
                        break
                else:
                    self.logger.info(f" 文件[' {zip.path} ']密码匹配失败")
                    return False
                if retry_outer:
                    continue
                break
            self.logger.info(f" 文件[' {zip.path} ']密码匹配失败")
            return False

        if known_password and not self._resolve_password_with_namelist(zip):
            known_password = False

        if known_password:
            password_candidates = [zip.verified_password()]
        else:
            password_candidates = self._unzip_password_candidates(zip)

        for _expand_retry in range(3):
            expanded = False
            for password in password_candidates:
                returncode, msg = self._run_single_unzip(zip, output_path, password)
                if returncode == 0:
                    zip.pw_list = [password]
                    zip.mark_namelist_scanned(password)
                    self._emit_unzip_progress(1, 1)
                    self.logger.info(f'[{zip.path}]解压完成')
                    return True
                if is_password_error_message(msg or ''):
                    self.logger.debug(
                        '密码错误，已跳过：{} [{}]'.format(
                            os.path.normpath(zip.path), password,
                        )
                    )
                    if known_password:
                        zip.invalidate_namelist_scan()
                        known_password = False
                        password_candidates = self._unzip_password_candidates(zip)
                        expanded = True
                        break
                    continue
                current = list(zip.volumes) if zip.volumes else [zip.path]
                new_volumes = try_expand_volumes(current, msg or '')
                if new_volumes and new_volumes != current:
                    zip.volumes = new_volumes
                    zip.path = new_volumes[0]
                    zip.invalidate_namelist_scan()
                    self.logger.info(
                        '  7-Zip 提示缺少分卷，已补全： [{}]'.format(
                            '],['.join(new_volumes),
                        )
                    )
                    expanded = True
                    break
            if expanded:
                continue
            break
        self.logger.info(f" 文件[' {zip.path} ']密码匹配失败")
        return False

    @staticmethod
    def _reached_unresolved_limit(unresolved_list, unresolved_limit) -> bool:
        return (unresolved_limit is not None
                and unresolved_list is not None
                and len(unresolved_list) >= unresolved_limit)

    @staticmethod
    def _volume_group_already_represented(path, volumes, already_add) -> bool:
        """分卷组已由首卷或其它分卷代表入队/已解压时，非首卷勿再入失败队列。"""
        if not volumes or len(volumes) < 2:
            return False
        if archive_registry.is_unzipped(volumes[0], volumes):
            return True
        if archive_registry.is_volume_part_unzipped(path):
            return True
        already_norm = {os.path.normcase(item) for item in already_add}
        if all(os.path.normcase(item) in already_norm for item in volumes):
            return True
        head_norm = os.path.normcase(volumes[0])
        path_norm = os.path.normcase(path)
        if path_norm != head_norm and archive_registry.is_discovered(volumes[0], volumes):
            return True
        return False

    @staticmethod
    def _note_unresolved_probe(path, zip_entity, unresolved_list, collect_unresolved,
                               unresolved_limit, already_add, logger):
        """记录一次「疑似压缩包但打不开」的探测；内层扫描仅计数，顶层扫描才入失败队列。"""
        if unresolved_list is None:
            return
        volumes = zip_entity.volumes if zip_entity.volumes else None
        if not volumes or volumes == [zip_entity.path]:
            volumes = file_ops.resolve_volume_archives(path)
        if Unzipper._volume_group_already_represented(path, volumes, already_add):
            logger.debug(
                '跳过分卷失败项（整组已由首卷代表）："{}"'.format(os.path.normpath(path)),
            )
            return
        if collect_unresolved:
            if (
                isinstance(zip_entity, Zip)
                and zip_entity.requires_manual_password()
                and not (zip_entity.note or '').strip()
            ):
                logger.info(
                    '  已加入任务队列（特殊 7z，请双击填写密码）：{}'.format(path),
                )
            else:
                logger.info(
                    f'  已加入任务队列（状态：解压失败），可双击补充密码后重试：{path}'
                )
            unresolved_list.append(zip_entity)
            if not zip_entity.volumes or zip_entity.volumes == [zip_entity.path]:
                if zip_entity.path not in already_add:
                    already_add.append(zip_entity.path)
            return
        if unresolved_limit is not None:
            unresolved_list.append(path)

    @staticmethod
    def _is_probable_nested_archive(zip_entity: Zip, probe: file_ops.ArchiveProbe) -> bool:
        """内层扫描：区分真实压缩包与素材文件误判。"""
        if zip_entity.file_list:
            return True
        if zip_entity.is_encrypted() or zip_entity.compression_ratio_info.get('encrypted'):
            return True
        ext = (zip_entity.extension or '').lower()
        if probe.is_candidate and ext in ('.zip', '.7z', '.rar'):
            return True
        if probe.format_type in ('7z', 'zip', 'rar', 'gzip', 'bzip2', 'xz', 'tar'):
            return True
        return False

    def find_zip(self, path, passwords, delete_after_unzip, already_add: list, zip_list: list,
                 depth: int = 0, max_depth: int = 8, unresolved_list: list | None = None,
                 collect_unresolved: bool = False,
                 unresolved_limit: int | None = None):
        self.logger.debug('检查:' + path)
        from volume.rename import current_path_for_drag
        redirected = current_path_for_drag(path)
        if redirected:
            path = redirected
        already_norm = {os.path.normcase(p) for p in already_add}
        volumes_probe = (
            file_ops.resolve_volume_archives(path) if os.path.isfile(path) else None
        )
        # resolve_volume_archives 可能会把经典 .zip/.z01/.z02 原地规范化为
        # .zip.001/.002/.003。解析完成后必须立即同步当前路径，否则本轮仍拿
        # 已不存在的 .z01 继续 probe，最终静默跳过整组分卷。
        if volumes_probe:
            redirected = current_path_for_drag(path)
            if redirected:
                path = redirected
            elif not os.path.exists(path):
                path = volumes_probe[0]
        # 路径是文件夹，递归扫描其中的压缩文件
        # 分卷只添加一次避免被反复添加解压
        # 路径不存在或无法识别，尝试相似路径
        if not os.path.exists(path):
            similar = file_ops.get_similar_path(path)
            if similar:
                self.logger.debug(' 尝试相似路径 [{}]'.format(similar))
                return self.find_zip(similar, passwords, delete_after_unzip, already_add, zip_list,
                                     depth, max_depth, unresolved_list, collect_unresolved,
                                     unresolved_limit)

        if path in already_add:
            if depth == 0 or archive_registry.is_unzipped(path, volumes_probe):
                return False
        elif os.path.normcase(path) in already_norm:
            if depth == 0 or archive_registry.is_unzipped(path, volumes_probe):
                return False

        if (archive_registry.is_unzipped(path, volumes_probe)
                or archive_registry.is_volume_part_unzipped(path)):
            self.logger.debug('跳过已解压压缩包："{}"'.format(os.path.normpath(path)))
            return False
        if archive_registry.is_discovered(path, volumes_probe) and depth == 0:
            self.logger.debug('跳过已入队压缩包："{}"'.format(os.path.normpath(path)))
            return False
        if archive_registry.is_discovered(path, volumes_probe) and depth > 0:
            self.logger.debug(
                '内层压缩包待解压，重新扫描："{}"'.format(os.path.normpath(path)),
            )

        if os.path.isdir(path):
            if depth > max_depth:
                return False
            find = False
            try:
                entries = os.listdir(path)
            except OSError as err:
                self.logger.error(f'无法读取目录 [{path}]: {err}')
                return False
            for name in entries:
                if self._reached_unresolved_limit(unresolved_list, unresolved_limit):
                    # 已累计过多“疑似压缩包但打不开”的文件：内层大概率是解压出的正常素材
                    # 被误判成压缩包，继续逐个用 7-Zip 探测非常慢，直接停止扫描本目录剩余项。
                    self.logger.info(
                        ' 内层无法识别的文件已达 {} 个，停止继续探测以提升性能：{}'.format(
                            unresolved_limit, path,
                        )
                    )
                    break
                entry = os.path.join(path, name)
                if os.path.isdir(entry):
                    find = self.find_zip(entry, passwords, delete_after_unzip, already_add, zip_list,
                                         depth + 1, max_depth, unresolved_list, collect_unresolved,
                                         unresolved_limit) or find
                else:
                    find = self.find_zip(entry, passwords, delete_after_unzip, already_add, zip_list,
                                         depth, max_depth, unresolved_list, collect_unresolved,
                                         unresolved_limit) or find
            return find

        if self._reached_unresolved_limit(unresolved_list, unresolved_limit):
            # 已达无法识别上限，不再对后续文件做昂贵的 7-Zip 探测。
            return False

        # 改后缀分卷常在首卷带 PK 魔数、后续卷无魔数；须先于 probe 解析整组分卷。
        # 复用入口处已经解析并规范化的结果；再次用重命名后的中间卷解析，
        # 可能因重命名登记/目录遍历仍持有旧路径而暂时得不到同组文件。
        volumes = volumes_probe or file_ops.resolve_volume_archives(path)
        if not volumes:
            from volume.resolver import VolumeResolver
            volumes = VolumeResolver.peek_volumes(path)
        if volumes:
            from volume.resolver import is_complete_volume_group
            from volume.rename import restore_renames_in_directory
            vol_dir = os.path.dirname(os.path.abspath(path))
            if not is_complete_volume_group(volumes):
                restore_renames_in_directory(vol_dir)
                self.logger.debug(
                    '分卷组缺少首卷，暂不加入队列："{}" -> [{}]'.format(
                        os.path.normpath(path),
                        '],['.join(volumes),
                    ),
                )
                return False
            if all(os.path.normcase(v) in already_norm for v in volumes):
                return False
            zip_entity = Zip(volumes[0], passwords, delete_after_unzip, covered=False,
                             format_type=None)
            zip_entity.path = volumes[0]
            zip_entity.volumes = volumes
            archive_registry.note_discovered(zip_entity.path, volumes)
            already_add.extend(volumes)
            log = ' 发现分卷压缩文件： [{}]'.format('],['.join(volumes))
            probe_limit = NESTED_PASSWORD_PROBE_LIMIT if depth > 0 else None
            if self.load_namelist(zip_entity, password_probe_limit=probe_limit):
                zip_list.append(zip_entity)
                self.logger.info(log)
                return True
            if depth > 0 and not collect_unresolved:
                zip_entity.invalidate_namelist_scan()
                zip_list.append(zip_entity)
                self.logger.info(
                    '  发现内层分卷压缩文件（解压时再试完整密码库）： [{}]'.format(
                        '],['.join(volumes),
                    ),
                )
                return True
            if not collect_unresolved or unresolved_list is None:
                restore_renames_in_directory(vol_dir)
            self.logger.info(' 文件 [{}] 无法识别,请检查文件是否可解压及密码是否匹配'.format(path))
            self._note_unresolved_probe(
                path, zip_entity, unresolved_list, collect_unresolved,
                unresolved_limit, already_add, self.logger,
            )
            return False

        probe = file_ops.probe_archive(path, nested=depth > 0)
        if not probe.is_candidate:
            self.logger.debug('跳过非压缩文件："{}"'.format(os.path.normpath(path)))
            return False

        covered = probe.covered
        if file_ops.is_standard_archive_file(path):
            covered = False
        zip_entity = Zip(path, passwords, delete_after_unzip, covered=covered,
                         format_type=probe.format_type)
        # 路径是压缩文件，分卷只把头卷加入队列
        log = None
        if file_ops.is_volume_zip(path):
            volumes = file_ops.volume_zip_list(path)
            if not volumes:
                self.logger.debug(
                    '分卷命名但未找到同组文件，跳过："{}"'.format(os.path.normpath(path)),
                )
                return False
            from volume.resolver import is_complete_volume_group
            if not is_complete_volume_group(volumes):
                self.logger.debug(
                    '分卷组缺少首卷，暂不加入队列："{}"'.format(os.path.normpath(path)),
                )
                return False
            zip_entity.path = volumes[0]
            zip_entity.volumes = volumes
            zip_entity.format_type = None  # 分卷由 7-Zip 按首卷自动识别，避免 -t7z 误判
            archive_registry.note_discovered(zip_entity.path, volumes)
            already_add.extend(volumes)
            log = ' 发现分卷压缩文件： [{}]'.format('],['.join(volumes))

            log = ' 发现分卷压缩文件： [{}]'.format('],['.join(volumes))

        probe_limit = NESTED_PASSWORD_PROBE_LIMIT if depth > 0 else None
        if self.load_namelist(zip_entity, password_probe_limit=probe_limit):
            if probe.covered and file_ops.is_disguised_archive_extension(
                zip_entity.extension or '',
            ):
                zip_entity.covered = True
            if not log:
                log = ' 发现压缩文件： [{}]'.format(path)

            zip_list.append(zip_entity)
            if zip_entity.volumes and len(zip_entity.volumes) > 1:
                archive_registry.note_discovered(zip_entity.path, zip_entity.volumes)
                already_add.extend(zip_entity.volumes)
            elif zip_entity.path not in already_add:
                archive_registry.note_discovered(zip_entity.path)
                already_add.append(zip_entity.path)
            self.logger.info(log)
            return True

        if depth > 0 and not collect_unresolved:
            if self._is_probable_nested_archive(zip_entity, probe) or (
                archive_registry.is_discovered(path, volumes_probe)
                and not archive_registry.is_unzipped(path, volumes_probe)
            ):
                zip_entity.invalidate_namelist_scan()
                zip_list.append(zip_entity)
                if zip_entity.volumes and len(zip_entity.volumes) > 1:
                    archive_registry.note_discovered(zip_entity.path, zip_entity.volumes)
                    already_add.extend(zip_entity.volumes)
                elif zip_entity.path not in already_add:
                    archive_registry.note_discovered(zip_entity.path)
                    already_add.append(zip_entity.path)
                self.logger.info(
                    '  发现内层压缩文件（解压时再试完整密码库）： [{}]'.format(path),
                )
                return True
            self._note_unresolved_probe(
                path, zip_entity, unresolved_list, collect_unresolved,
                unresolved_limit, already_add, self.logger,
            )
            self.logger.debug(
                '内层文件无法作为压缩包打开，已跳过：{}'.format(os.path.normpath(path)),
            )
            return False

        if (
            zip_entity.requires_manual_password()
            and not (zip_entity.note or '').strip()
        ):
            self.logger.info(
                ' 特殊 7z 已识别，等待手动密码： [{}]'.format(path),
            )
        else:
            self.logger.info(' 文件 [{}] 无法识别,请检查文件是否可解压及密码是否匹配'.format(path))
            if file_ops.is_disguised_archive_extension(os.path.splitext(path)[1]):
                self.logger.info(
                    '  提示：该文件使用改后缀/隐写压缩（{}），已尝试多种 7-Zip 打开方式仍失败'.format(
                        os.path.splitext(path)[1] or '(无后缀)',
                    )
                )
        self._note_unresolved_probe(
            path, zip_entity, unresolved_list, collect_unresolved,
            unresolved_limit, already_add, self.logger,
        )
        if not collect_unresolved:
            self.logger.debug(
                '内层文件无法作为压缩包打开，已跳过：{}'.format(os.path.normpath(path)),
            )
        return False

