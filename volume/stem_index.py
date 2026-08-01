"""按 stem 预聚类目录内分卷文件。"""

import os

from volume import normalize, parse

_PART_PARSERS = (
    parse.parse_rar_oldstyle,
    parse.parse_rar_part,
    parse.parse_disguised_split,
    parse.parse_simple_numeric,
)


def extract_part_info(basename: str) -> tuple[str, int] | None:
    """从文件名提取 (stem, part_index)。"""
    parsed = parse.parse_rar_oldstyle(basename)
    if parsed:
        return parsed[0], parsed[1]
    parsed_7z = parse.parse_7z_split(basename)
    if parsed_7z:
        return parsed_7z[0], parse.order_7z_part(parsed_7z[1])
    parsed_trailing = parse.parse_trailing_numeric(basename)
    if parsed_trailing:
        return parsed_trailing
    parsed_leading = parse.parse_leading_numeric(basename)
    if parsed_leading:
        return parsed_leading
    for parser in _PART_PARSERS[1:]:
        parsed = parser(basename)
        if parsed:
            return parsed[0], parsed[1]
    return None


def build_stem_groups(dirname: str) -> dict[str, dict[int, str]]:
    """扫描目录，返回 stem -> {part_index: path}。"""
    groups: dict[str, dict[int, str]] = {}
    try:
        names = os.listdir(dirname)
    except OSError:
        return groups
    for name in names:
        path = os.path.join(dirname, name)
        if not os.path.isfile(path):
            continue
        info = extract_part_info(name)
        if not info:
            continue
        stem, part = info
        groups.setdefault(stem, {})[part] = path
    return groups


def _stems_related(anchor_stem: str, stem: str) -> bool:
    """合并「测试」与「测试我」等同一组分卷的不同 stem 写法。"""
    if anchor_stem == stem:
        return True
    shorter, longer = (
        (anchor_stem, stem) if len(anchor_stem) <= len(stem) else (stem, anchor_stem)
    )
    return len(shorter) >= 2 and longer.startswith(shorter)


def collect_by_stem(dirname: str, file_path: str) -> list[str] | None:
    """目录级 stem 聚类：混用命名（吗1对/part2掉/删3）一次收齐。"""
    info = extract_part_info(os.path.basename(file_path))
    if not info:
        return None
    anchor_stem, _ = info
    groups = build_stem_groups(dirname)
    merged: dict[int, str] = {}
    for stem, parts in groups.items():
        if not _stems_related(anchor_stem, stem):
            continue
        for part, path in parts.items():
            merged.setdefault(part, path)
    if len(merged) < 2:
        return None
    return [path for _, path in sorted(merged.items())]


def collect_stem_cluster(dirname: str, file_path: str) -> list[str] | None:
    return collect_by_stem(dirname, file_path)


def normalize_stem_cluster(dirname: str, volumes: list[str]) -> list[str]:
    return normalize.normalize_disguised_split(dirname, volumes)
