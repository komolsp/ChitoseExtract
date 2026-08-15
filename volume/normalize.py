"""分卷规范化为 7-Zip 可识别命名。"""

import os
import re

from volume import detect, parse, rename as vol_rename


def _zip_prefix_from_volumes(volumes: list[str]) -> str | None:
    if not volumes:
        return None
    first = os.path.basename(volumes[0])
    fuzzy = parse.fuzzy_zip_split_prefix(first)
    if fuzzy:
        return os.path.basename(fuzzy)
    disguised = parse.parse_disguised_split(first)
    if disguised:
        return f'{disguised[0]}.zip'
    simple = parse.parse_simple_numeric(first)
    if simple:
        return f'{simple[0]}.zip'
    if re.match(r'^.+\.zip\.\d{3}$', first, re.IGNORECASE):
        return re.sub(r'\.\d{3}$', '', first, flags=re.IGNORECASE)
    if re.match(r'^.+\.zip$', first, re.IGNORECASE) and '.zip.' not in first.lower():
        return first
    return None


def _zip_part_order(prefix_name: str, basename: str) -> int:
    if basename == prefix_name:
        return 1
    disguised = parse.parse_disguised_split(basename)
    if disguised and prefix_name.lower() == f'{disguised[0]}.zip'.lower():
        return disguised[1]
    simple = parse.parse_simple_numeric(basename)
    if simple and prefix_name.lower() == f'{simple[0]}.zip'.lower():
        return simple[1]
    if basename.startswith(prefix_name + '.'):
        suffix = basename[len(prefix_name) + 1:]
        if re.fullmatch(r'\d{3}', suffix):
            return int(suffix)
        z_match = re.fullmatch(r'z(\d{2})', suffix, re.IGNORECASE)
        if z_match:
            return int(z_match.group(1)) + 1
        order = parse.fuzzy_digit_order(suffix)
        if order > 0:
            return order
    return 0


def normalize_zip001(dirname: str, volumes: list[str]) -> list[str]:
    if len(volumes) < 2:
        return volumes
    prefix_name = _zip_prefix_from_volumes(volumes)
    if not prefix_name:
        return volumes

    # 经典 split ZIP 的物理顺序是 .z01, .z02, ..., .zip（中央目录在末卷）。
    # 转成 7-Zip 的 .001 序列时不能把 .zip 放在首位。
    classic_z_parts: dict[str, int] = {}
    for path in volumes:
        basename = os.path.basename(path)
        match = re.fullmatch(
            re.escape(prefix_name[:-4]) + r'\.z(\d{2})',
            basename,
            re.IGNORECASE,
        ) if prefix_name.lower().endswith('.zip') else None
        if match:
            classic_z_parts[os.path.normcase(path)] = int(match.group(1))

    ordered: list[tuple[int, str]] = []
    for path in volumes:
        path_key = os.path.normcase(path)
        if classic_z_parts:
            if path_key in classic_z_parts:
                order = classic_z_parts[path_key]
            elif os.path.basename(path).lower() == prefix_name.lower():
                order = max(classic_z_parts.values()) + 1
            else:
                order = _zip_part_order(prefix_name, os.path.basename(path))
        else:
            order = _zip_part_order(prefix_name, os.path.basename(path))
        ordered.append((order, path))
    ordered.sort(key=lambda item: (item[0] if item[0] > 0 else 999, os.path.basename(item[1]).lower()))

    normalized: list[str] = []
    for index, (_, path) in enumerate(ordered, start=1):
        new_name = f'{prefix_name}.{index:03d}'
        ideal_path = os.path.join(dirname, new_name)
        # 已经规范化时直接复用自身。若先调用 unique_dest_path，它会把当前文件
        # 当作同名冲突，第二次扫描便错误改成 archive.zip(1).001。
        if os.path.normcase(os.path.abspath(path)) == os.path.normcase(
            os.path.abspath(ideal_path),
        ):
            normalized.append(path)
            continue
        new_path = vol_rename.unique_dest_path(dirname, new_name)
        if vol_rename.rename_volume(path, new_path):
            path = new_path
        normalized.append(path)
    return normalized


def normalize_disguised_classic_zip(dirname: str, volumes: list[str]) -> list[str]:
    """仅还原经典 ZIP 的主卷名；.z01/.z02 数据卷必须保持原名。"""
    z_parts: list[tuple[int, str]] = []
    for path in volumes:
        match = re.fullmatch(r'(?P<stem>.+)\.z(?P<part>\d{2})', os.path.basename(path), re.IGNORECASE)
        if match:
            z_parts.append((int(match.group('part')), path))
    if not z_parts:
        return volumes
    z_parts.sort(key=lambda item: item[0])
    stem = re.fullmatch(
        r'(?P<stem>.+)\.z\d{2}', os.path.basename(z_parts[0][1]), re.IGNORECASE,
    ).group('stem')
    main = next(
        (
            path for path in volumes
            if re.fullmatch(
                rf'{re.escape(stem)}\.zip[^.]+', os.path.basename(path), re.IGNORECASE,
            )
        ),
        None,
    )
    if not main:
        return volumes
    ideal = os.path.join(dirname, f'{stem}.zip')
    if os.path.normcase(os.path.abspath(main)) != os.path.normcase(os.path.abspath(ideal)):
        if vol_rename.rename_volume(main, ideal):
            main = ideal
    return [main] + [path for _, path in z_parts]


def normalize_rar_part(dirname: str, volumes: list[str]) -> list[str]:
    normalized: list[str] = []
    for path in volumes:
        basename = os.path.basename(path)
        parsed = parse.parse_rar_part(basename)
        if not parsed:
            normalized.append(path)
            continue
        stem, part = parsed
        ideal = f'{stem}.part{part}'
        if basename == ideal:
            normalized.append(path)
            continue
        new_path = vol_rename.unique_dest_path(dirname, ideal)
        if vol_rename.rename_volume(path, new_path):
            path = new_path
        normalized.append(path)
    return normalized


def _canonical_volume_stem(
    stems: list[str],
    ordered: list[tuple[int, str]] | None = None,
) -> str:
    """混用命名时取最短公共 stem；完全异名时取首卷 stem。"""
    if not stems:
        return ''
    unique = list(dict.fromkeys(stems))
    if len(unique) == 1:
        return unique[0]
    unique.sort(key=len)
    for candidate in unique:
        if all(s == candidate or s.startswith(candidate) for s in unique):
            return candidate
    prefix = unique[0]
    for stem in unique[1:]:
        while prefix and not stem.startswith(prefix):
            prefix = prefix[:-1]
    if prefix and len(prefix) > 1:
        return prefix
    if ordered:
        for part, stem in sorted(ordered):
            if part == 1:
                return stem
    return unique[0]


def normalize_disguised_rar(dirname: str, volumes: list[str]) -> list[str]:
    ordered: list[tuple[int, str, str]] = []
    for path in volumes:
        basename = os.path.basename(path)
        parsed = (parse.parse_disguised_split(basename)
                  or parse.parse_trailing_numeric(basename)
                  or parse.parse_leading_numeric(basename)
                  or parse.parse_simple_numeric(basename))
        if not parsed:
            return volumes
        ordered.append((parsed[1], parsed[0], path))
    ordered.sort(key=lambda item: (item[0], os.path.basename(item[2]).lower()))
    part_stems = [(part, stem) for part, stem, _ in ordered]
    canonical_stem = _canonical_volume_stem([stem for _, stem in part_stems], part_stems)
    normalized: list[str] = []
    for part_num, _stem, path in ordered:
        ideal = f'{canonical_stem}.part{part_num}'
        if os.path.basename(path) == ideal:
            normalized.append(path)
            continue
        new_path = vol_rename.unique_dest_path(dirname, ideal)
        if vol_rename.rename_volume(path, new_path):
            path = new_path
        normalized.append(path)
    return normalized


def normalize_rar_oldstyle(dirname: str, volumes: list[str]) -> list[str]:
    ordered: list[tuple[int, str, str]] = []
    for path in volumes:
        parsed = parse.parse_rar_oldstyle(os.path.basename(path))
        if not parsed:
            return volumes
        ordered.append((parsed[1], parsed[0], path))
    ordered.sort(key=lambda item: item[0])
    normalized: list[str] = []
    for part_num, stem, path in ordered:
        if part_num == 1:
            ideal = f'{stem}.rar'
        else:
            ideal = f'{stem}.r{(part_num - 2):02d}'
        if os.path.basename(path) == ideal:
            normalized.append(path)
            continue
        new_path = vol_rename.unique_dest_path(dirname, ideal)
        if vol_rename.rename_volume(path, new_path):
            path = new_path
        normalized.append(path)
    return normalized


def _part_info_from_basename(basename: str) -> tuple[str, int] | None:
    parsed_7z = parse.parse_7z_split(basename)
    if parsed_7z:
        base = parsed_7z[0]
        stem = base[:-3] if base.lower().endswith('.7z') else base
        return stem, parse.order_7z_part(parsed_7z[1])
    for parser in (
        parse.parse_trailing_numeric,
        parse.parse_leading_numeric,
        parse.parse_disguised_split,
        parse.parse_simple_numeric,
        parse.parse_rar_oldstyle,
        parse.parse_rar_part,
    ):
        parsed = parser(basename)
        if parsed:
            return parsed[0], parsed[1]
    return None


def normalize_7z_volumes(dirname: str, volumes: list[str]) -> list[str]:
    ordered: list[tuple[int, str, str]] = []
    assigned = None
    if any(parse.disguised_suffix_is_implicit(os.path.basename(path)) for path in volumes):
        assigned = parse.assign_implicit_disguised_parts(volumes)
    if assigned:
        for part_num, path in assigned:
            parsed = _part_info_from_basename(os.path.basename(path))
            stem = parsed[0] if parsed else ''
            ordered.append((part_num, stem, path))
    else:
        for path in volumes:
            parsed = _part_info_from_basename(os.path.basename(path))
            if not parsed:
                return volumes
            ordered.append((parsed[1], parsed[0], path))
        ordered.sort(key=lambda item: (item[0], os.path.basename(item[2]).lower()))
    part_stems = [(part, stem) for part, stem, _ in ordered]
    canonical_stem = _canonical_volume_stem([stem for _, stem in part_stems], part_stems)
    normalized: list[str] = []
    for part_num, _stem, path in ordered:
        ideal = f'{canonical_stem}.7z.{part_num:03d}'
        if os.path.basename(path) == ideal:
            normalized.append(path)
            continue
        new_path = vol_rename.unique_dest_path(dirname, ideal)
        if vol_rename.rename_volume(path, new_path):
            path = new_path
        normalized.append(path)
    return normalized


def normalize_disguised_split(dirname: str, volumes: list[str]) -> list[str]:
    fmt = detect.detect_split_format(volumes)
    if fmt == 'rar':
        return normalize_disguised_rar(dirname, volumes)
    if fmt == '7z':
        return normalize_7z_volumes(dirname, volumes)
    return normalize_zip001(dirname, volumes)


def normalize_simple_numeric(dirname: str, volumes: list[str]) -> list[str]:
    fmt = detect.detect_split_format(volumes)
    if fmt == 'rar':
        return normalize_disguised_rar(dirname, volumes)
    if fmt == '7z':
        return normalize_7z_volumes(dirname, volumes)
    return normalize_zip001(dirname, volumes)
