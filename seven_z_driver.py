import os
import re
import subprocess
import sys

import app_paths
import file_ops
import zip_wz_aes

# 用于探测「仅内容加密」：错密码仍能通过 7z l 列目录
_PROBE_INVALID_PASSWORD = '__pk_invalid_probe_password__'
# 单个大文件：未压缩大小下限（50 MiB）
_LARGE_SINGLE_FILE_MIN_BYTES = 50 * 1024 * 1024

# Windows 下隐藏 7-Zip 子进程命令行窗口
_SUBPROCESS_FLAGS = {}
if sys.platform == 'win32':
    _SUBPROCESS_FLAGS['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)


def _decode_7z_text(data: bytes) -> str:
    if not data:
        return ''
    return data.decode('gbk', errors='replace')


def _method_is_store_encrypted(method: str) -> bool:
    """Copy 7zAES：无压缩仅加密，错密 7z t 须解密整块，验密极慢。"""
    if '7zAES' not in method:
        return False
    head = method.strip().split(None, 1)[0]
    return head == 'Copy'


class SevenZDriver:
    def __init__(self, location_path=None, mmt: int = 0):
        if location_path is None:
            location_path = app_paths.seven_zip_exe()
        self.location_path = location_path
        self.work_dir = os.path.dirname(location_path) or None
        self.mmt = max(0, int(mmt or 0))
        if not os.path.isfile(self.location_path):
            raise UnzipError(
                f'未找到 7-Zip：{self.location_path}。请重新打包或安装 7-Zip。'
            )

    def _append_open_flags(self, cmd: list, jap: bool, covered: bool,
                           format_type: str | None, *, multithread: bool = False):
        if jap:
            cmd.append('-mcp=932')
        if covered:
            cmd.append('-t#')
        elif format_type:
            cmd.append(f'-t{format_type}')
        if multithread and self.mmt > 0:
            cmd.append(f'-mmt={self.mmt}')

    def _unzip_wz_aes_zip(
        self,
        compress_file: str,
        output_path: str,
        password: str = '',
        output_file: str | None = None,
    ):
        if output_file:
            parent = output_file.split('\\')[0]
            if os.path.join(output_path, parent) == compress_file:
                parent += '(1)'
                output_path = os.path.join(output_path, parent)
        try:
            zip_wz_aes.extract(
                compress_file, output_path, password, member=output_file,
            )
        except zip_wz_aes.WzAesZipError as err:
            raise UnzipError(str(err)) from err
        return 0, password

    @staticmethod
    def _should_use_pyzipper(compress_file: str, covered: bool) -> bool:
        """pyzipper 仅处理单文件 WzAES ZIP；经典跨卷 ZIP 必须交给 7-Zip。"""
        return (
            not covered
            and compress_file.lower().endswith('.zip')
            and not file_ops.is_volume_zip(compress_file, readonly=True)
            and file_ops.zip_uses_wz_aes(compress_file)
        )

    def unzip(self, compress_file: str, output_path: str, password: str = '', output_file: str = None,
              jap: bool = False,
              covered: bool = False, format_type: str | None = None):
        if not compress_file:
            raise UnzipError('压缩文件未设置')
        if not output_path:
            raise UnzipError('输出路径未设置')
        if self._should_use_pyzipper(compress_file, covered):
            return self._unzip_wz_aes_zip(
                compress_file, output_path, password, output_file,
            )
        cmd = [self.location_path, 'x', '-p{}'.format(password), '-y', compress_file]
        if output_file:
            cmd.append(output_file)
            parent = output_file.split("\\")[0]
            if os.path.join(output_path, parent) == compress_file:
                parent += "(1)"
                output_path = os.path.join(output_path, parent)
        self._append_open_flags(
            cmd, jap, covered, format_type,
            multithread=output_file is None,
        )
        cmd.append('-o{}'.format(output_path))

        result = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.work_dir, close_fds=True, **_SUBPROCESS_FLAGS)
        out, err = result.communicate()
        if err:
            msg = _decode_7z_text(err)
            if "Cannot delete output file" in msg:
                raise CannotDeleteOutputFile(msg)
            if "No files to process" in msg:
                raise NoFile2ProcessError(msg)
            if "Wrong password" in msg:
                raise UnzipError(msg)
            raise UnzipError(msg)
        if result.returncode != 0:
            raise UnzipError(f'7-Zip 退出码 {result.returncode}')
        return result.returncode, password

    def get_namelist(self, compress_file: str, password: str = '', jap: bool = False, covered: bool = False,
                     format_type: str | None = None):
        pattern = r'^20\d{2}-[01]\d-[0-3]\d [0-2]\d:[0-6]\d:[0-6]\d \.\S{4}.{28}(.+?)[\r\n]'
        ratio_pattern = r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+(\d+)\s+(\d+)\s+\d+\s+files(?:,\s+\d+\s+folders)?\s*$'
        namelist = []
        compression_ratio_info = {}
        cmd = [self.location_path, 'l', compress_file, '-p{}'.format(password)]
        self._append_open_flags(cmd, jap, covered, format_type, multithread=False)
        if covered:
            pattern = r'^ {20}.\S{4}.{28}(.+?)[\r\n]'
        out, err = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.work_dir, close_fds=True, **_SUBPROCESS_FLAGS).communicate()
        if err:
            msg = _decode_7z_text(err)
            raise GetNamelistError(f'获取文件list错误:{msg}')
        if out:
            compression_ratio_info = {"encrypted": False}
            for line in _decode_7z_text(out).strip().split('\n'):
                match = re.search(pattern, line)
                if match:
                    file = match.group(1)
                    if not jap and file_ops.encode_detect(file):
                        raise JapDecodeError(f'文件名乱码:{file}')
                    if file not in namelist:
                        file = re.sub(r'[〜？！_ ′]', "?", file)
                        file = file.replace(u'\u3000', "?").replace(u'\xa0', "?")
                        namelist.append(file)
                else:
                    match = re.match(ratio_pattern, line)
                    if match:
                        size = int(match.group(1))
                        compressed = int(match.group(2))
                        compression_ratio = (compressed / size * 100) if size > 0 else 0
                        compression_ratio_info.update({
                            "size": size,
                            "compressed": compressed,
                            "compression_ratio": round(compression_ratio, 2)
                        })
                    elif '7zAES' in line or 'WzAES' in line or 'AES-256' in line:
                        compression_ratio_info["encrypted"] = True

        if namelist and compress_file.lower().endswith('.zip'):
            if file_ops.zip_has_encrypted_entries(compress_file):
                compression_ratio_info["encrypted"] = True

        return namelist, compression_ratio_info

    def test_archive(self, compress_file: str, password: str = '', jap: bool = False,
                     covered: bool = False, format_type: str | None = None) -> tuple[bool, str]:
        """运行 7z t 校验压缩包结构/完整性（比 7z l 更严格）。"""
        if self._should_use_pyzipper(compress_file, covered):
            ok, msg = zip_wz_aes.test_password(compress_file, password)
            return ok, msg
        cmd = [self.location_path, 't', '-p{}'.format(password), compress_file]
        self._append_open_flags(cmd, jap, covered, format_type, multithread=False)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.work_dir, close_fds=True, **_SUBPROCESS_FLAGS)
        out, err = proc.communicate()
        err_text = _decode_7z_text(err) if err else ''
        out_text = _decode_7z_text(out) if out else ''
        combined = (err_text + '\n' + out_text).strip()
        if proc.returncode != 0:
            return False, combined or f'7-Zip test exit {proc.returncode}'
        if combined and any(
            marker in combined
            for marker in ('ERROR', 'ERRORS', 'Wrong password', 'Headers Error', 'Can not open')
        ):
            return False, combined
        return True, combined

    def probe_content_encrypted_single_block(
        self,
        compress_file: str,
        password: str = '',
        jap: bool = False,
        covered: bool = False,
        format_type: str | None = None,
    ) -> dict:
        """
        识别 7z 特殊压缩包，须同时满足：
        1. 能列目录：空/错密码下 7z l 仍可列出相同内容（仅内容加密）
        2. Blocks = 1
        3. 单个大文件：包内仅 1 个文件，且 Size >= 50 MiB
        4. Copy 7zAES：无压缩存储加密，错密验密须解密整块（极慢）
        """
        result = {
            'content_encrypted_solid': False,
            'encrypted': False,
            'solid': False,
            'blocks': None,
            'file_count': 0,
            'file_size': None,
            'store_encrypted': False,
            'content_only_encryption': False,
            'listable_without_password': False,
        }
        cmd = [self.location_path, 'l', '-slt', '-p{}'.format(password), compress_file]
        self._append_open_flags(cmd, jap, covered, format_type, multithread=False)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.work_dir, close_fds=True, **_SUBPROCESS_FLAGS)
        out, err = proc.communicate()
        if proc.returncode != 0:
            return result
        out_text = _decode_7z_text(out) if out else ''
        if not out_text.strip():
            return result

        solid = False
        blocks: int | None = None
        encrypted = False
        method_has_aes = False
        archive_method = ''
        file_methods: list[str] = []
        file_sizes: list[int] = []
        in_file_section = False
        for line in out_text.splitlines():
            stripped = line.strip()
            if stripped == '----------':
                in_file_section = True
                continue
            if stripped.startswith('Solid ='):
                solid = stripped.split('=', 1)[1].strip() == '+'
            elif stripped.startswith('Blocks ='):
                try:
                    blocks = int(stripped.split('=', 1)[1].strip())
                except ValueError:
                    blocks = None
            elif stripped.startswith('Method ='):
                method_value = stripped.split('=', 1)[1].strip()
                if in_file_section:
                    file_methods.append(method_value)
                else:
                    archive_method = method_value
                if '7zAES' in method_value:
                    method_has_aes = True
            elif stripped.startswith('Encrypted ='):
                if stripped.split('=', 1)[1].strip() == '+':
                    encrypted = True
            elif '7zAES' in stripped:
                method_has_aes = True
            elif in_file_section and stripped.startswith('Size ='):
                try:
                    file_sizes.append(int(stripped.split('=', 1)[1].strip()))
                except ValueError:
                    pass

        if len(file_methods) == 1:
            store_encrypted = _method_is_store_encrypted(file_methods[0])
        else:
            store_encrypted = _method_is_store_encrypted(archive_method)
        result['store_encrypted'] = store_encrypted

        result['encrypted'] = encrypted or method_has_aes
        result['solid'] = solid
        result['blocks'] = blocks
        result['file_count'] = len(file_sizes)
        result['file_size'] = file_sizes[0] if len(file_sizes) == 1 else None
        if not result['encrypted']:
            return result

        try:
            namelist_empty, _info = self.get_namelist(
                compress_file, '', jap, covered, format_type,
            )
        except GetNamelistError:
            return result

        if not namelist_empty:
            return result

        try:
            namelist_wrong, _info = self.get_namelist(
                compress_file, _PROBE_INVALID_PASSWORD, jap, covered, format_type,
            )
        except GetNamelistError:
            return result

        listable = bool(namelist_wrong) and set(namelist_wrong) == set(namelist_empty)
        result['listable_without_password'] = listable
        result['content_only_encryption'] = listable
        if not listable:
            return result

        single_large_file = (
            len(file_sizes) == 1
            and file_sizes[0] >= _LARGE_SINGLE_FILE_MIN_BYTES
        )
        result['content_encrypted_solid'] = (
            blocks == 1 and single_large_file and store_encrypted
        )
        return result


class JapDecodeError(Exception):
    def __init__(self, error_info):
        super(JapDecodeError, self).__init__(error_info)
        self.error_info = error_info

    def __str__(self):
        return self.error_info


class GetNamelistError(Exception):
    def __init__(self, error_info):
        super(GetNamelistError, self).__init__(error_info)
        self.error_info = error_info

    def __str__(self):
        return self.error_info


class UnzipError(Exception):
    def __init__(self, error_info):
        super(UnzipError, self).__init__(error_info)
        self.error_info = error_info

    def __str__(self):
        return self.error_info


class NoFile2ProcessError(EOFError):
    def __init__(self, error_info):
        super(NoFile2ProcessError, self).__init__(error_info)
        self.error_info = error_info

    def __str__(self):
        return self.error_info


class CannotDeleteOutputFile(EOFError):
    def __init__(self, error_info):
        super(CannotDeleteOutputFile, self).__init__(error_info)
        self.error_info = error_info

    def __str__(self):
        return self.error_info


def is_password_error_message(message: str) -> bool:
    if not message:
        return False
    markers = (
        'Wrong password',
        'CRC Failed',
        'Data Error in encrypted',
        'password is incorrect',
    )
    return any(marker in message for marker in markers)
