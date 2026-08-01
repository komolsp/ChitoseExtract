import os

import pk_logger
from timeline import Archive

# logger = pk_logger.Pk_logger('unzip_logger', 'log.txt').add_log_handler().get_logger()


class Zip(Archive):

    def __init__(self, file, password_list: list = [], del_after_unzip: bool = False, jap: bool = False
                 , covered: bool = False, format_type: str | None = None,
                 volumes: list = None):
        super(Zip, self).__init__(file)
        self.pw_list = []
        self.compression_ratio_info = {}
        self.del_after_unzip = del_after_unzip
        self.jap = jap
        self.covered = covered
        self.format_type = format_type
        self.set_password(password_list)
        self.volumes = volumes
        self.namelist_scanned = False
        self.scan_fingerprint = None
        self.scan_password = None
        self.manual_password_only = False

    def _scan_fingerprint(self):
        paths = tuple(sorted(self.volumes or [self.path]))
        mtimes: list[float] = []
        for path in paths:
            try:
                mtimes.append(os.path.getmtime(path))
            except OSError:
                mtimes.append(0.0)
        return (paths, tuple(mtimes))

    def mark_namelist_scanned(self, password=None):
        self.namelist_scanned = True
        self.scan_fingerprint = self._scan_fingerprint()
        if password is not None:
            self.scan_password = password

    def invalidate_namelist_scan(self):
        self.namelist_scanned = False
        self.scan_fingerprint = None
        self.scan_password = None

    def is_encrypted(self) -> bool:
        return bool(self.compression_ratio_info.get('encrypted'))

    def container_requires_password(self) -> bool:
        """压缩包标记为加密时，须先通过 7z l 验证密码。"""
        return self.is_encrypted()

    def requires_manual_password(self) -> bool:
        return bool(
            self.manual_password_only
            or self.compression_ratio_info.get('manual_password_only')
        )

    def manual_7z_probe_info(self) -> dict:
        probe = self.compression_ratio_info.get('manual_7z_probe')
        return probe if isinstance(probe, dict) else {}

    @staticmethod
    def format_manual_7z_status_detail(path: str, probe: dict | None = None) -> str:
        """运行状态栏补充说明：特殊 7z 特征 + 操作提示。"""
        name = os.path.basename(path or '')
        probe = probe or {}
        traits: list[str] = []
        if probe.get('listable_without_password'):
            traits.append('仅内容加密')
        if probe.get('blocks') == 1:
            traits.append('单Block')
        if probe.get('store_encrypted'):
            traits.append('Copy存储')
        file_size = probe.get('file_size')
        if isinstance(file_size, int) and file_size > 0:
            if file_size >= 1024 ** 3:
                traits.append(f'{file_size / (1024 ** 3):.1f}GB')
            else:
                traits.append(f'{file_size // (1024 * 1024)}MB')
        tail = '双击任务填密码'
        if traits:
            return f'{name} · {" · ".join(traits)} · {tail}'
        return f'{name} · {tail}'

    def manual_password_candidates(self) -> list[str]:
        """用户侧密码：特殊 7z 仅认备注；其它场景含 RJ / 文件名。"""
        if self.requires_manual_password():
            return [self.note] if self.note else []
        ordered: list[str] = []
        for pw in (self.note, self.RJ_code, self.filename):
            if pw and pw not in ordered:
                ordered.append(pw)
        return ordered

    def is_namelist_current(self) -> bool:
        if not (
            self.namelist_scanned
            and bool(self.file_list)
            and self.scan_fingerprint == self._scan_fingerprint()
        ):
            return False
        if self.scan_password is not None and self.pw_list:
            if self.scan_password == '':
                return not self.is_encrypted()
            return self.scan_password in self.pw_list
        return True

    def verified_password(self):
        if self.scan_password is not None:
            return self.scan_password
        return self.pw_list[0] if self.pw_list else ''

    def set_password(self, password_list):
        self.pw_list = [pw for pw in password_list if pw]
        if self.RJ_code and self.RJ_code not in self.pw_list:
            self.pw_list.insert(0, self.RJ_code)
        if self.filename and self.filename not in self.pw_list:
            self.pw_list.append(self.filename)

    def set_note(self, note):
        self.pw_list.insert(0, note)
        self.invalidate_namelist_scan()
        is_rj = self.RJ_code is not None
        super(Zip, self).set_note(note)
        if not is_rj and self.RJ_code:
            self.pw_list.insert(0, self.RJ_code)
            self.invalidate_namelist_scan()

    def extend(self, old: Archive):
        if isinstance(old, Zip) and old.pw_list:
            for pw in reversed(old.pw_list[:3]):
                if pw and pw not in self.pw_list:
                    self.pw_list.insert(0, pw)
            self.invalidate_namelist_scan()
        if self.RJ_code is None and old.RJ_code:
            self.RJ_code = old.RJ_code
            if self.RJ_code not in self.pw_list:
                self.pw_list.insert(0, self.RJ_code)
        if old.note:
            self.set_note(old.note)
