"""分卷组试读校验：同目录多候选时避免误绑独立压缩包。"""

import os
import re
import subprocess
import sys

from volume import collect, magic, parse, stem_index

_SUBPROCESS_FLAGS = {}
if sys.platform == 'win32':
    _SUBPROCESS_FLAGS['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)

_SPLIT_ERROR_MARKERS = (
    'unexpected end of archive',
    'missing volume',
    'there are data after the end of the payload',
    'error in',
)
_INVALID_MARKERS = (
    'can not open the file as',
    'cannot open the file as',
    'is not archive',
)
_ENCRYPTED_MARKERS = (
    'wrong password',
    'encrypted',
    '7zaes',
)


def is_volume_like_basename(basename: str) -> bool:
    if parse.parse_7z_split(basename):
        return True
    if parse.parse_trailing_numeric(basename):
        return True
    if parse.parse_leading_numeric(basename):
        return True
    if parse.parse_simple_numeric(basename):
        return True
    if parse.parse_rar_part(basename):
        return True
    if parse.parse_rar_oldstyle(basename):
        return True
    return collect._is_disguised_volume_candidate(basename)


def count_volume_like_files(dirname: str) -> int:
    try:
        names = os.listdir(dirname)
    except OSError:
        return 0
    count = 0
    for name in names:
        path = os.path.join(dirname, name)
        if os.path.isfile(path) and is_volume_like_basename(name):
            count += 1
    return count


def _part_one_path(volumes: list[str]) -> str | None:
    ordered: list[tuple[int, str]] = []
    for path in volumes:
        basename = os.path.basename(path)
        info = stem_index.extract_part_info(basename) or collect._cross_stem_part_info(basename)
        if info:
            ordered.append((info[1], path))
            continue
        ordered.append((999, path))
    if not ordered:
        return None
    ordered.sort(key=lambda item: (item[0], os.path.basename(item[1]).lower()))
    return ordered[0][1]


def _is_cross_stem_group(volumes: list[str]) -> bool:
    stems: set[str] = set()
    for path in volumes:
        info = collect._cross_stem_part_info(os.path.basename(path))
        if not info:
            return False
        stems.add(info[0])
    return len(stems) > 1


def _first_volume_has_magic(first_path: str) -> bool:
    return (
        magic.is_7z_file(first_path)
        or magic.is_rar_file(first_path)
        or magic.is_zip_file(first_path)
    )


def _seven_zip_exe() -> str | None:
    try:
        import app_paths
        path = app_paths.seven_zip_exe()
    except Exception:
        return None
    return path if path and os.path.isfile(path) else None


def classify_archive_probe(first_path: str) -> str:
    """
    对首卷做 7z l 试读分类。

    返回:
      split     — 首卷为分卷碎片，需后续卷
      complete  — 首卷可独立列出（独立包，不应与异名卷拼组）
      encrypted — 加密压缩包（结构可读）
      invalid   — 非压缩或不可读
    """
    if not first_path or not os.path.isfile(first_path):
        return 'invalid'
    if not _first_volume_has_magic(first_path):
        return 'invalid'

    exe = _seven_zip_exe()
    if not exe:
        return 'split' if _first_volume_has_magic(first_path) else 'invalid'

    cmd = [exe, 'l', first_path, '-p']
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.dirname(exe) or None,
            close_fds=True,
            **_SUBPROCESS_FLAGS,
        )
    except OSError:
        return 'invalid'

    out = (proc.stdout or b'').decode('gbk', errors='ignore')
    err = (proc.stderr or b'').decode('gbk', errors='ignore')
    text = f'{out}\n{err}'.lower()

    if any(marker in text for marker in _INVALID_MARKERS):
        if magic.is_7z_file(first_path):
            return 'split'
        return 'invalid'
    if 'type = split' in text:
        return 'split'
    if re.search(r'\bvolumes\s*=\s*(\d+)', text):
        vol_count = int(re.search(r'\bvolumes\s*=\s*(\d+)', text).group(1))
        if vol_count > 1:
            return 'split'
    if proc.returncode == 0 and re.search(r'^\s*\d{4}-\d{2}-\d{2}', out, re.MULTILINE):
        return 'complete'
    if any(marker in text for marker in _ENCRYPTED_MARKERS):
        return 'encrypted'
    if any(marker in text for marker in _SPLIT_ERROR_MARKERS):
        return 'split'
    if proc.returncode != 0:
        return 'invalid'
    return 'complete'


def needs_probe_validation(volumes: list[str]) -> bool:
    """异名分卷，或同目录有多余卷号样文件时，须试读验证。"""
    if len(volumes) < 2:
        return False
    if _is_cross_stem_group(volumes):
        return True
    dirname = os.path.dirname(os.path.abspath(volumes[0]))
    return count_volume_like_files(dirname) > len(volumes)


def _is_unambiguous_rar_part_group(volumes: list[str]) -> bool:
    """标准 .partN.rar 命名且同 stem，无需 7z 试读否决。"""
    stem: str | None = None
    for path in volumes:
        parsed = parse.parse_rar_part(os.path.basename(path))
        if not parsed:
            return False
        if stem is None:
            stem = parsed[0]
        elif parsed[0] != stem:
            return False
    return len(volumes) >= 2


def accept_volume_group(volumes: list[str]) -> bool:
    """判定候选分卷组是否通过魔数 + 7z 试读验证。"""
    if len(volumes) < 2:
        return False

    # .z01/.z02 只是经典 ZIP 分卷的数据卷；缺少同 stem 的 .zip 主卷时，
    # 不得建立残缺任务，否则套娃扫描会抢先改名并误报密码错误。
    z_parts = [
        re.fullmatch(r'(.+)\.z\d{2}', os.path.basename(path), re.IGNORECASE)
        for path in volumes
    ]
    if all(z_parts):
        return False

    first = _part_one_path(volumes)
    if not first or not _first_volume_has_magic(first):
        return False

    if _is_unambiguous_rar_part_group(volumes):
        return True

    if not needs_probe_validation(volumes):
        return True

    status = classify_archive_probe(first)
    if _is_cross_stem_group(volumes):
        return status in ('split', 'encrypted')
    if status in ('split', 'encrypted'):
        return True
    return False
