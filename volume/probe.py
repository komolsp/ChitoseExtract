"""7-Zip 分卷缺失提示反推补卷。"""

import difflib
import os
import re

from volume import parse, stem_index
from volume.resolver import VolumeResolver, clear_index_cache

_MISSING_VOLUME_RE = re.compile(r'Missing volume\s*:\s*(.+?)(?:\r|\n|$)', re.IGNORECASE)
_CANNOT_OPEN_NEXT_RE = re.compile(
    r'(?:Can(?:not|\'t) open)(?:\s+file)?\s*["\']?(.+?)["\']?\s*(?:\r|\n|$)',
    re.IGNORECASE,
)


def parse_missing_volume(message: str) -> str | None:
    """从 7-Zip  stderr 解析期望的下一卷文件名。"""
    if not message:
        return None
    for pattern in (_MISSING_VOLUME_RE, _CANNOT_OPEN_NEXT_RE):
        match = pattern.search(message)
        if match:
            name = match.group(1).strip().strip('"').strip("'")
            if name and name not in ('', '.'):
                return os.path.basename(name)
    return None


def _basename_similar(a: str, b: str) -> bool:
    if a == b:
        return True
    return difflib.SequenceMatcher(None, a, b).quick_ratio() >= 0.72


def _same_part_different_name(expected: str, candidate: str) -> bool:
    for parser in (parse.parse_disguised_split, parse.parse_rar_part, parse.parse_simple_numeric):
        exp = parser(expected)
        cand = parser(candidate)
        if exp and cand and exp[0] == cand[0] and exp[1] == cand[1]:
            return True
    return False


def find_file_for_expected(dirname: str, expected_basename: str) -> str | None:
    """在同目录按名称/序号模糊匹配 7-Zip 期望的分卷文件。"""
    direct = os.path.join(dirname, expected_basename)
    if os.path.isfile(direct):
        return direct
    try:
        names = os.listdir(dirname)
    except OSError:
        return None
    for name in names:
        path = os.path.join(dirname, name)
        if not os.path.isfile(path):
            continue
        if _basename_similar(name, expected_basename):
            return path
        if _same_part_different_name(expected_basename, name):
            return path
    return None


def _stem_of_volumes(volumes: list[str]) -> str | None:
    for path in volumes:
        info = stem_index.extract_part_info(os.path.basename(path))
        if info:
            return info[0]
    return None


def anchor_relates_to_volumes(anchor_path: str, volumes: list[str]) -> bool:
    """补卷结果须与当前文件同 stem 或包含当前文件，避免误绑同目录其它分卷组。"""
    if not anchor_path or not volumes:
        return False
    anchor_name = os.path.basename(anchor_path)
    volume_names = {os.path.basename(v) for v in volumes}
    if anchor_name in volume_names:
        return True
    anchor_info = stem_index.extract_part_info(anchor_name)
    if not anchor_info:
        return False
    anchor_stem = anchor_info[0]
    for path in volumes:
        info = stem_index.extract_part_info(os.path.basename(path))
        if info and info[0] == anchor_stem:
            return True
    return False


def _accept_expanded(anchor_path: str, current: list[str], expanded: list[str]) -> bool:
    if not expanded or expanded == current:
        return False
    if len(expanded) <= len(current):
        return False
    return anchor_relates_to_volumes(anchor_path, expanded)


def try_expand_volumes(current_volumes: list[str], error_message: str) -> list[str] | None:
    """根据 7-Zip 缺失提示重新聚组并规范命名。"""
    if not current_volumes:
        return None
    missing_name = parse_missing_volume(error_message)
    if not missing_name:
        return None

    anchor = current_volumes[0]
    dirname = os.path.dirname(os.path.abspath(anchor))
    clear_index_cache()

    found = find_file_for_expected(dirname, missing_name)
    if found:
        expanded = VolumeResolver.resolve(found)
        if _accept_expanded(anchor, current_volumes, expanded or []):
            return expanded

    for path in current_volumes:
        cluster = stem_index.collect_by_stem(dirname, path)
        if cluster and len(cluster) > len(current_volumes):
            expanded = VolumeResolver.resolve(cluster[0])
            if _accept_expanded(anchor, current_volumes, expanded or []):
                return expanded

    for path in current_volumes:
        expanded = VolumeResolver.resolve(path)
        if _accept_expanded(anchor, current_volumes, expanded or []):
            return expanded

    expected_stem = _stem_of_volumes(current_volumes)
    if not expected_stem:
        return None

    for name in os.listdir(dirname):
        path = os.path.join(dirname, name)
        if not os.path.isfile(path):
            continue
        info = stem_index.extract_part_info(name)
        if not info or info[0] != expected_stem:
            continue
        expanded = VolumeResolver.resolve(path)
        if _accept_expanded(anchor, current_volumes, expanded or []):
            return expanded
    return None
