"""WinZip AES (WzAES) ZIP 解压：7-Zip 对部分 WzAES/Zip64 包无法验密或解压，改用 pyzipper。"""
from __future__ import annotations

import os

import file_ops

try:
    import pyzipper
except ImportError:  # pragma: no cover - runtime guard
    pyzipper = None  # type: ignore


class WzAesZipError(Exception):
    pass


def _require_pyzipper():
    if pyzipper is None:
        raise WzAesZipError('未安装 pyzipper，无法解压 WinZip AES 加密 ZIP')


def _password_bytes(password: str) -> bytes:
    return password.encode('utf-8')


def test_password(compress_file: str, password: str) -> tuple[bool, str]:
    """试探密码：仅读取首条目少量字节，避免整包解压。"""
    if not file_ops.zip_uses_wz_aes(compress_file):
        return False, 'not WzAES zip'
    _require_pyzipper()
    if not password:
        return False, 'empty password'
    try:
        with pyzipper.AESZipFile(compress_file, 'r') as zf:
            names = zf.namelist()
            if not names:
                return False, 'empty archive'
            with zf.open(names[0], pwd=_password_bytes(password)) as fh:
                fh.read(4)
    except RuntimeError as err:
        return False, str(err)
    except (OSError, KeyError, ValueError) as err:
        return False, str(err)
    except Exception as err:
        # pyzipper 对不支持的 ZIP 变体（尤其跨卷）会抛出自身的 BadZipFile；
        # 验密探测不应让该异常越过驱动层。
        return False, str(err)
    return True, ''


def extract(
    compress_file: str,
    output_path: str,
    password: str = '',
    member: str | None = None,
) -> None:
    """解压 WzAES ZIP；member 为 None 时解压全部条目。"""
    _require_pyzipper()
    if not os.path.isdir(output_path):
        os.makedirs(output_path, exist_ok=True)
    pwd = _password_bytes(password) if password else None
    try:
        with pyzipper.AESZipFile(compress_file, 'r') as zf:
            if member:
                zf.extract(member, output_path, pwd=pwd)
            else:
                zf.extractall(output_path, pwd=pwd)
    except RuntimeError as err:
        msg = str(err)
        if 'Bad password' in msg or 'password' in msg.lower():
            raise WzAesZipError('Wrong password') from err
        raise WzAesZipError(msg) from err
    except (OSError, KeyError, ValueError) as err:
        raise WzAesZipError(str(err)) from err
    except Exception as err:
        raise WzAesZipError(str(err)) from err
