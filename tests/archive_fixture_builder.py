"""测试用压缩包样本构建（调用内置 7-Zip / 系统 WinRAR）。"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

import app_paths

_SUBPROCESS_FLAGS = {}
if sys.platform == 'win32':
    _SUBPROCESS_FLAGS['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

_WINRAR_PATHS = (
    r'C:\Program Files\WinRAR\Rar.exe',
    r'C:\Program Files (x86)\WinRAR\Rar.exe',
)


def seven_zip_exe() -> str:
    return app_paths.seven_zip_exe()


def winrar_exe() -> str | None:
    for path in _WINRAR_PATHS:
        if os.path.isfile(path):
            return path
    return shutil.which('rar')


def _run(cmd: list[str], *, cwd: str | None = None) -> None:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        **_SUBPROCESS_FLAGS,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or b'').decode('utf-8', errors='replace')
        raise RuntimeError(f'命令失败 {cmd!r}: {err}')


def write_payload_file(directory: str, name: str = 'payload.txt', content: bytes | None = None) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, name)
    payload = content or (b'Prekikoeru archive test payload\n' * 64)
    with open(path, 'wb') as fh:
        fh.write(payload)
    return path


def create_zip_bytes(inner_name: str = 'payload.txt', content: bytes | None = None) -> bytes:
    payload = content or b'nested-inner-payload\n'
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
    try:
        with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(inner_name, payload)
        with open(tmp_path, 'rb') as fh:
            return fh.read()
    finally:
        os.unlink(tmp_path)


def seven_zip_add(
    archive_path: str,
    source_path: str,
    *,
    password: str | None = None,
    volume_size: str | None = None,
    format_flag: str | None = None,
    store_only: bool = False,
) -> None:
    cmd = [seven_zip_exe(), 'a', '-y']
    if password is not None:
        cmd.append(f'-p{password}')
    if volume_size:
        cmd.append(f'-v{volume_size}')
    if store_only:
        cmd.append('-mx=0')
    if format_flag:
        cmd.extend(['-t' + format_flag])
    cmd.extend([archive_path, source_path])
    _run(cmd)


def winrar_add(
    archive_path: str,
    source_path: str,
    *,
    password: str | None = None,
    volume_size: str | None = None,
) -> None:
    rar = winrar_exe()
    if not rar:
        raise RuntimeError('未找到 WinRAR/Rar.exe，无法创建 RAR 测试样本')
    cmd = [rar, 'a', '-ep', '-y', '-m0']
    if password:
        cmd.append(f'-p{password}')
    if volume_size:
        cmd.append(f'-v{volume_size}')
    cmd.extend([archive_path, source_path])
    _run(cmd)


def build_plain_archives(work_dir: str) -> dict[str, str]:
    payload = write_payload_file(work_dir, 'payload.bin', bytes(range(256)) * 8)
    out: dict[str, str] = {}
    for ext, fmt in (('.zip', 'zip'), ('.7z', '7z')):
        path = os.path.join(work_dir, f'plain{ext}')
        seven_zip_add(path, payload, format_flag=fmt, store_only=True)
        out[ext.lstrip('.')] = path
    if winrar_exe():
        rar_path = os.path.join(work_dir, 'plain.rar')
        winrar_add(rar_path, payload)
        out['rar'] = rar_path
    return out


def build_password_archives(work_dir: str, password: str = 'testpw') -> dict[str, str]:
    os.makedirs(work_dir, exist_ok=True)
    payload = write_payload_file(work_dir, 'secret.txt', b'secret payload\n')
    out: dict[str, str] = {}
    zip_path = os.path.join(work_dir, 'locked.zip')
    seven_zip_add(zip_path, payload, password=password, format_flag='zip')
    out['zip'] = zip_path
    seven_path = os.path.join(work_dir, 'locked.7z')
    seven_zip_add(seven_path, payload, password=password, format_flag='7z')
    out['7z'] = seven_path
    if winrar_exe():
        rar_path = os.path.join(work_dir, 'locked.rar')
        winrar_add(rar_path, payload, password=password)
        out['rar'] = rar_path
    return out


def build_disguised_copies(work_dir: str, source_path: str, aliases: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for alias in aliases:
        dest = os.path.join(work_dir, alias)
        shutil.copy2(source_path, dest)
        out[alias] = dest
    return out


def build_stego_jpeg(work_dir: str, zip_bytes: bytes | None = None) -> str:
    data = zip_bytes or create_zip_bytes('stego.txt', b'stego-in-jpeg\n')
    # minimal JPEG-like header; archive magic must be at offset > 0
    prefix = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00' + b'\x00' * 512
    path = os.path.join(work_dir, 'cover.jpg')
    with open(path, 'wb') as fh:
        fh.write(prefix + data)
    return path


def build_stego_mp4(work_dir: str, zip_bytes: bytes | None = None) -> str:
    data = zip_bytes or create_zip_bytes('stego.txt', b'stego-in-mp4\n')
    box_size = 32
    ftyp = struct.pack('>I', box_size) + b'ftyp' + b'isom\x00\x00\x02\x00'
    ftyp += b'\x00' * max(0, box_size - len(ftyp))
    path = os.path.join(work_dir, 'movie.mp4')
    with open(path, 'wb') as fh:
        fh.write(ftyp + data)
    return path


def build_stego_pdf(work_dir: str, zip_bytes: bytes | None = None) -> str:
    data = zip_bytes or create_zip_bytes('stego.txt', b'stego-in-pdf\n' * 64)
    path = os.path.join(work_dir, 'doc.pdf')
    with open(path, 'wb') as fh:
        fh.write(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n' + b' ' * 1200 + data)
    return path


def build_nested_chain(work_dir: str, layers: list[str]) -> str:
    """layers 如 ['zip', '7z', 'zip']，返回最外层路径。"""
    os.makedirs(work_dir, exist_ok=True)
    payload = write_payload_file(work_dir, 'leaf.txt', b'deepest payload\n')
    current = payload
    current_name = 'leaf.txt'
    outer_path = payload
    for index, fmt in enumerate(layers):
        inner_archive = os.path.join(work_dir, f'nested_{index}.{fmt}')
        if fmt == 'rar':
            winrar_add(inner_archive, current)
        else:
            seven_zip_add(inner_archive, current, format_flag=fmt)
        current = inner_archive
        current_name = os.path.basename(inner_archive)
        outer_path = inner_archive
    return outer_path


def build_split_volumes(work_dir: str, stem: str = 'split') -> dict[str, list[str]]:
    """在同一目录生成 zip/7z/rar 三套分卷（测试批量场景用）。"""
    os.makedirs(work_dir, exist_ok=True)
    payload = write_payload_file(work_dir, 'big.bin', bytes(range(256)) * 400)
    volumes = {
        'zip': _build_zip_volumes(work_dir, stem, payload),
        '7z': _build_7z_volumes(work_dir, stem, payload),
    }
    if winrar_exe():
        volumes['rar'] = _build_rar_volumes(work_dir, stem, payload)
    return volumes


def _build_zip_volumes(work_dir: str, stem: str, payload: str) -> list[str]:
    zip_base = os.path.join(work_dir, f'{stem}.zip')
    seven_zip_add(zip_base, payload, volume_size='50k', format_flag='zip', store_only=True)
    return sorted(
        os.path.join(work_dir, name)
        for name in os.listdir(work_dir)
        if name.lower().startswith(f'{stem}.zip') and name.lower() != f'{stem}.zip'
    )


def _build_7z_volumes(work_dir: str, stem: str, payload: str) -> list[str]:
    seven_base = os.path.join(work_dir, f'{stem}.7z')
    seven_zip_add(seven_base, payload, volume_size='50k', format_flag='7z', store_only=True)
    return sorted(
        os.path.join(work_dir, name)
        for name in os.listdir(work_dir)
        if name.lower().startswith(f'{stem}.7z') and name.lower() != f'{stem}.7z'
    )


def _build_rar_volumes(work_dir: str, stem: str, payload: str) -> list[str]:
    rar_base = os.path.join(work_dir, f'{stem}.rar')
    winrar_add(rar_base, payload, volume_size='50k')
    return sorted(
        os.path.join(work_dir, name)
        for name in os.listdir(work_dir)
        if name.lower().startswith(f'{stem}.part') and name.lower().endswith('.rar')
    )


def build_fixture_tree(base_dir: str) -> dict:
    plain_dir = os.path.join(base_dir, 'plain')
    disguise_dir = os.path.join(base_dir, 'disguise')
    stego_dir = os.path.join(base_dir, 'stego')
    nested_dir = os.path.join(base_dir, 'nested')
    volume_dir = os.path.join(base_dir, 'volumes')
    for path in (plain_dir, disguise_dir, stego_dir, nested_dir, volume_dir):
        os.makedirs(path, exist_ok=True)

    plain = build_plain_archives(plain_dir)
    disguised = build_disguised_copies(
        disguise_dir,
        plain['zip'],
        ['game.dat', 'pack.r', 'audio.mp3', 'readme.txt', 'noext'],
    )
    extensionless = os.path.join(disguise_dir, 'noext')
    wrong_ext = os.path.join(disguise_dir, 'fake.dat')
    shutil.copy2(plain['7z'], wrong_ext)

    stego = {
        'jpg': build_stego_jpeg(stego_dir),
        'mp4': build_stego_mp4(stego_dir),
        'pdf': build_stego_pdf(stego_dir),
    }
    nested = {
        'zip_7z_zip': build_nested_chain(nested_dir, ['zip', '7z', 'zip']),
        'zip_zip': build_nested_chain(nested_dir, ['zip', 'zip']),
    }
    if winrar_exe():
        nested['7z_rar_zip'] = build_nested_chain(nested_dir, ['7z', 'rar', 'zip'])
    zip_sub = os.path.join(volume_dir, 'zip')
    seven_sub = os.path.join(volume_dir, '7z')
    rar_sub = os.path.join(volume_dir, 'rar')
    for sub in (zip_sub, seven_sub, rar_sub):
        os.makedirs(sub, exist_ok=True)
    payload_zip = write_payload_file(zip_sub, 'big.bin', bytes(range(256)) * 400)
    payload_7z = os.path.join(seven_sub, 'big.bin')
    shutil.copy2(payload_zip, payload_7z)
    payload_rar = os.path.join(rar_sub, 'big.bin')
    shutil.copy2(payload_zip, payload_rar)
    volumes = {
        'zip': _build_zip_volumes(zip_sub, 'zipvol', payload_zip),
        '7z': _build_7z_volumes(seven_sub, 'sevenvol', payload_7z),
    }
    if winrar_exe():
        volumes['rar'] = _build_rar_volumes(rar_sub, 'rarvol', payload_rar)
    return {
        'plain': plain,
        'disguised': disguised,
        'wrong_ext': wrong_ext,
        'stego': stego,
        'nested': nested,
        'volumes': volumes,
        'password': build_password_archives(os.path.join(base_dir, 'password')),
    }
