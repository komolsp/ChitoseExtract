"""分卷收集：同目录下聚组。"""

import os
import re
from collections import defaultdict

from volume import parse


def _directory_names(dirname: str, names: list[str] | None) -> list[str]:
    """复用解析器提供的目录快照；独立调用时保持原有扫描行为。"""
    return names if names is not None else os.listdir(dirname)


def _sorted_paths(volumes: list[tuple[int, str]]) -> list[str]:
    volumes.sort(key=lambda item: (item[0], os.path.basename(item[1]).lower()))
    return [path for _, path in volumes]


def _is_disguised_volume_candidate(basename: str) -> bool:
    """改后缀/插字/尾数分卷候选，排除已是标准命名的文件。"""
    if parse.parse_trailing_numeric(basename) or parse.parse_leading_numeric(basename):
        return True
    if not parse.parse_disguised_split(basename):
        return False
    if re.fullmatch(r'.+\.part\d+$', basename, re.IGNORECASE):
        return False
    if re.fullmatch(r'.+\.\d{3}$', basename):
        return False
    if re.fullmatch(r'.+\.7z\.\d{3}$', basename, re.IGNORECASE):
        return False
    return True


def _disguised_part_info(basename: str) -> tuple[str, int] | None:
    if not _is_disguised_volume_candidate(basename):
        return None
    parsed = (
        parse.parse_trailing_numeric(basename)
        or parse.parse_leading_numeric(basename)
        or parse.parse_disguised_split(basename)
    )
    return parsed if parsed else None


def _collect_by_parser(
    dirname: str,
    file_path: str,
    parser,
    *,
    stem_prefix: bool = True,
    names: list[str] | None = None,
) -> list[str] | None:
    parsed = parser(os.path.basename(file_path))
    if not parsed:
        return None
    stem, _ = parsed
    volumes: list[tuple[int, str]] = []
    for name in _directory_names(dirname, names):
        if stem_prefix and not name.startswith(stem + '.'):
            continue
        if name == stem:
            continue
        part_info = parser(name)
        if not part_info or part_info[0] != stem:
            continue
        path = os.path.join(dirname, name)
        if os.path.isfile(path):
            volumes.append((part_info[1], path))
    if len(volumes) < 2:
        return None
    if parser is parse.parse_disguised_split:
        assigned = parse.assign_implicit_disguised_parts([path for _, path in volumes])
        if assigned:
            return [path for _, path in assigned]
    return _sorted_paths(volumes)


def collect_7z(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    from volume import rename as vol_rename

    basename = os.path.basename(file_path)
    parsed = parse.parse_7z_split(basename)
    if not parsed:
        return None
    base_name = parsed[0]
    base_esc = re.escape(base_name)
    part_re = re.compile(rf'^{base_esc}\.(\d{{3}}|补)$', re.IGNORECASE)
    found: list[tuple[int, str]] = []
    first_part_path = os.path.join(dirname, f'{base_name}.001')

    for file in _directory_names(dirname, names):
        match = part_re.match(file)
        if not match:
            continue
        part = match.group(1)
        path = os.path.join(dirname, file)
        if part.casefold() == '补':
            if os.path.exists(first_part_path):
                continue
            if vol_rename.rename_volume(path, first_part_path):
                path = first_part_path
        found.append((parse.order_7z_part(part), path))

    if len(found) < 2:
        return None
    return _sorted_paths(found)


def collect_7z_readonly(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    """collect_7z 的只读版本：不将 *.补 重命名为 *.001。"""
    basename = os.path.basename(file_path)
    parsed = parse.parse_7z_split(basename)
    if not parsed:
        return None
    base_name = parsed[0]
    base_esc = re.escape(base_name)
    part_re = re.compile(rf'^{base_esc}\.(\d{{3}}|补)$', re.IGNORECASE)
    found: list[tuple[int, str]] = []
    first_part_path = os.path.join(dirname, f'{base_name}.001')

    for file in _directory_names(dirname, names):
        match = part_re.match(file)
        if not match:
            continue
        part = match.group(1)
        path = os.path.join(dirname, file)
        if part.casefold() == '补':
            if os.path.exists(first_part_path):
                path = first_part_path
            else:
                continue
        found.append((parse.order_7z_part(part), path))

    if len(found) < 2:
        return None
    return _sorted_paths(found)


def collect_trailing_numeric(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    return _collect_by_parser(
        dirname, file_path, parse.parse_trailing_numeric,
        stem_prefix=False, names=names,
    )


def collect_disguised_split(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    return _collect_by_parser(
        dirname, file_path, parse.parse_disguised_split, names=names,
    )


def _7z_cross_stem_part_info(basename: str) -> tuple[str, int] | None:
    """异名但保留 .7z.NNN：乌拉拉.7z.001 / 啊呀啊呀.7z.002。"""
    parsed = parse.parse_7z_split(basename)
    if not parsed:
        return None
    base = parsed[0]
    stem = base[:-3] if base.lower().endswith('.7z') else base
    return stem, parse.order_7z_part(parsed[1])


def _score_7z_cross_stem_candidate(basename: str) -> float:
    score = 0.0
    lower = basename.lower()
    if lower.rsplit('.', 1)[-1] in parse._NON_VOLUME_EXTENSIONS:
        return -100.0
    if parse.parse_7z_split(basename):
        score += 5.0
    return score


def collect_cross_stem_7z_split(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    """每卷 stem 不同但后缀仍为 .7z.NNN 时按卷号 1..N 聚组。"""
    anchor_name = os.path.basename(file_path)
    anchor_info = _7z_cross_stem_part_info(anchor_name)
    if not anchor_info:
        return None

    part_candidates: dict[int, list[tuple[str, str, str]]] = defaultdict(list)
    for name in _directory_names(dirname, names):
        path = os.path.join(dirname, name)
        if not os.path.isfile(path):
            continue
        info = _7z_cross_stem_part_info(name)
        if not info:
            continue
        stem, part = info
        part_candidates[part].append((stem, path, name))

    anchor_part = anchor_info[1]
    if anchor_part not in part_candidates:
        return None

    chosen: dict[int, str] = {}
    chosen[anchor_part] = file_path
    for part, candidates in part_candidates.items():
        if part == anchor_part:
            continue
        _stem, best_path, best_name = max(
            candidates,
            key=lambda item: _score_7z_cross_stem_candidate(item[2]),
        )
        if _score_7z_cross_stem_candidate(best_name) < 0:
            return None
        chosen[part] = best_path

    if sorted(chosen.keys()) != list(range(1, len(chosen) + 1)):
        return None
    if 1 not in chosen:
        return None

    stems = []
    for part in sorted(chosen.keys()):
        info = _7z_cross_stem_part_info(os.path.basename(chosen[part]))
        if not info:
            return None
        stems.append(info[0])
    if len(stems) != len(set(stems)):
        return None
    if len(chosen) < 2:
        return None

    return [chosen[part] for part in sorted(chosen.keys())]


def _cross_stem_part_info(basename: str) -> tuple[str, int] | None:
    """跨 stem 聚组：尾数/混字/已规范化的 .7z.NNN 分卷。"""
    info = _disguised_part_info(basename)
    if info:
        return info
    return _7z_cross_stem_part_info(basename)


def peek_cross_stem_group(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> bool:
    """无副作用判断是否存在跨 stem 分卷组（供 probe 使用）。"""
    from volume.validate import accept_volume_group
    for collector in (collect_cross_stem_7z_split, collect_cross_stem_disguised):
        raw = collector(dirname, file_path, names=names)
        if raw and len(raw) >= 2 and accept_volume_group(raw, names=names):
            return True
    return False


def _score_disguised_candidate(basename: str) -> float:
    score = 0.0
    lower = basename.lower()
    if lower.rsplit('.', 1)[-1] in parse._NON_VOLUME_EXTENSIONS:
        return -100.0
    if parse.parse_trailing_numeric(basename):
        score += 3.0
    if parse.parse_leading_numeric(basename):
        score += 3.0
    if parse.parse_7z_split(basename):
        score += 4.0
    if re.search(r'7z|part', basename, re.IGNORECASE):
        score += 2.0
    if re.search(r'[^\w.\-]', basename):
        score += 1.0
    return score


def collect_cross_stem_disguised(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    """每卷文件名完全不同（咦嘻.7z你1 / 哈.2啥 / 猫1 / 老2 …）时按卷号 1..N 聚组。"""
    anchor_name = os.path.basename(file_path)
    anchor_info = _cross_stem_part_info(anchor_name)
    if not anchor_info:
        return None

    part_candidates: dict[int, list[tuple[str, str, str]]] = defaultdict(list)
    for name in _directory_names(dirname, names):
        path = os.path.join(dirname, name)
        if not os.path.isfile(path):
            continue
        info = _cross_stem_part_info(name)
        if not info:
            continue
        stem, part = info
        part_candidates[part].append((stem, path, name))

    anchor_part = anchor_info[1]
    if anchor_part not in part_candidates:
        return None

    chosen: dict[int, str] = {}
    chosen[anchor_part] = file_path
    for part, candidates in part_candidates.items():
        if part == anchor_part:
            continue
        _stem, best_path, best_name = max(
            candidates,
            key=lambda item: _score_disguised_candidate(item[2]),
        )
        if _score_disguised_candidate(best_name) < 0:
            return None
        chosen[part] = best_path

    if sorted(chosen.keys()) != list(range(1, len(chosen) + 1)):
        return None
    if 1 not in chosen:
        return None

    stems = []
    for part in sorted(chosen.keys()):
        info = _cross_stem_part_info(os.path.basename(chosen[part]))
        if not info:
            return None
        stems.append(info[0])
    if len(stems) != len(set(stems)):
        return None
    if len(chosen) < 2:
        return None

    return [chosen[part] for part in sorted(chosen.keys())]


def collect_simple_numeric(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    return _collect_by_parser(
        dirname, file_path, parse.parse_simple_numeric, names=names,
    )


def collect_rar_part(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    return _collect_by_parser(dirname, file_path, parse.parse_rar_part, names=names)


def collect_rar_oldstyle(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    return _collect_by_parser(
        dirname, file_path, parse.parse_rar_oldstyle, names=names,
    )


def collect_fuzzy_zip(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    basename = os.path.basename(file_path)
    prefix = parse.fuzzy_zip_split_prefix(basename)
    if not prefix:
        return None
    prefix_name = os.path.basename(prefix)
    volumes: list[tuple[int, str]] = []
    for name in _directory_names(dirname, names):
        if not name.startswith(prefix_name + '.'):
            continue
        if name == prefix_name:
            continue
        suffix = name[len(prefix_name) + 1:]
        volumes.append((parse.fuzzy_digit_order(suffix), os.path.join(dirname, name)))
    if len(volumes) < 2:
        return None
    return _sorted_paths(volumes)


def collect_zip001_family(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    basename = os.path.basename(file_path)
    if re.match(r'^.+\.zip\.\d{3}$', basename, re.IGNORECASE):
        prefix_name = re.sub(r'\.\d{3}$', '', basename, flags=re.IGNORECASE)
    elif re.match(r'^.+\.zip$', basename, re.IGNORECASE) and '.zip.' not in basename.lower():
        prefix_name = basename
    else:
        return None

    volumes: list[str] = []
    for name in _directory_names(dirname, names):
        path = os.path.join(dirname, name)
        if not os.path.isfile(path):
            continue
        if name == prefix_name:
            volumes.append(path)
        elif re.fullmatch(re.escape(prefix_name) + r'\.\d{3}', name, re.IGNORECASE):
            volumes.append(path)
    return volumes if len(volumes) >= 2 else None


def collect_classic_zip(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    basename = os.path.basename(file_path)
    if not re.match(r'^.+\.zip$', basename, re.IGNORECASE):
        return None
    if re.search(r'\.zip\.', basename, re.IGNORECASE):
        return None
    stem = basename[:-4]
    first = os.path.join(dirname, basename)
    if not os.path.isfile(first):
        return None
    volumes = [first]
    for part in range(1, 100):
        cont = os.path.join(dirname, f'{stem}.z{part:02d}')
        if not os.path.isfile(cont):
            break
        volumes.append(cont)
    return volumes if len(volumes) >= 2 else None


def collect_disguised_classic_zip(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    """经典 ZIP 分卷的主卷被追加无点后缀：archive.zip删 + archive.z01。"""
    basename = os.path.basename(file_path)
    z_match = re.fullmatch(r'(?P<stem>.+)\.z(?P<part>\d{2})', basename, re.IGNORECASE)
    main_match = re.fullmatch(
        r'(?P<stem>.+)\.zip(?P<junk>[^.]+)', basename, re.IGNORECASE,
    )
    if z_match:
        stem = z_match.group('stem')
    elif main_match:
        stem = main_match.group('stem')
    else:
        return None

    if names is None:
        try:
            names = os.listdir(dirname)
        except OSError:
            return None

    # 已有标准 .zip 时交给 collect_classic_zip，避免把旁边的备份文件误当主卷。
    if any(name.casefold() == f'{stem}.zip'.casefold() for name in names):
        return None

    disguised_mains: list[str] = []
    main_re = re.compile(rf'^{re.escape(stem)}\.zip[^.]+$', re.IGNORECASE)
    for name in names:
        if not main_re.fullmatch(name):
            continue
        parsed = parse.parse_disguised_split(name)
        if not parsed or parsed[0].casefold() != stem.casefold() or parsed[1] != 1:
            continue
        path = os.path.join(dirname, name)
        if os.path.isfile(path):
            disguised_mains.append(path)
    if len(disguised_mains) != 1:
        return None

    z_parts: dict[int, str] = {}
    z_re = re.compile(rf'^{re.escape(stem)}\.z(?P<part>\d{{2}})$', re.IGNORECASE)
    for name in names:
        match = z_re.fullmatch(name)
        if not match:
            continue
        part = int(match.group('part'))
        if part in z_parts:
            return None
        z_parts[part] = os.path.join(dirname, name)
    if not z_parts or sorted(z_parts) != list(range(1, max(z_parts) + 1)):
        return None
    return disguised_mains + [z_parts[part] for part in sorted(z_parts)]


def collect_legacy_pattern(
    dirname: str, file_path: str, *, names: list[str] | None = None,
) -> list[str] | None:
    basename = os.path.basename(file_path)
    pattern_7z = r'(.*)\.\d{3}\b'
    pattern_rar = r'(.*)\.part\d+'
    pattern_zip = r'(.*)\.z\d{2}\b'
    re_7z = re.search(pattern_7z, basename)
    re_rar = re.search(pattern_rar, basename)
    re_zip = re.search(pattern_zip, basename)
    if re_7z:
        filename = re.escape(re_7z.group(1))
        pattern = r'{}\.\d{{3}}\b'.format(filename)
    elif re_rar:
        filename = re.escape(re_rar.group(1))
        pattern = r'{}\.part\d+'.format(filename)
    elif re_zip:
        filename = re.escape(re_zip.group(1))
        pattern = r'{}\.z\d{{2}}\b'.format(filename)
    else:
        return None

    zip_list: list[str] = []
    if re_zip:
        # 经典 ZIP 分卷以 stem.zip 为主卷，.z01/.z02 只是前置数据卷。
        # 从任意 .zNN 扫描时也必须把主卷纳入，主卷尚未出现则不能建立残缺任务。
        main_zip = os.path.join(dirname, re_zip.group(1) + '.zip')
        if not os.path.isfile(main_zip):
            return None
        zip_list.append(main_zip)
    for file in _directory_names(dirname, names):
        match = re.search(pattern, file)
        if not match:
            continue
        zip_list.append(os.path.join(dirname, file))
    if len(zip_list) < 2:
        return None
    zip_list.sort()
    return zip_list


def _part_signature(path: str) -> tuple[str, int] | tuple[str, str]:
    basename = os.path.basename(path)
    info = _cross_stem_part_info(basename)
    if info:
        return info
    parsed_7z = parse.parse_7z_split(basename)
    if parsed_7z:
        base = parsed_7z[0]
        stem = base[:-3] if base.lower().endswith('.7z') else base
        return stem, parse.order_7z_part(parsed_7z[1])
    return basename, 0


def volume_group_identity(volumes: list[str]) -> tuple | None:
    """同组分卷的稳定标识（目录 + 各卷 stem/part），与规范化路径大小写无关。"""
    if len(volumes) < 2:
        return None
    abs_paths = [os.path.abspath(v) for v in volumes]
    dir_key = os.path.normcase(os.path.dirname(abs_paths[0]))
    return (
        dir_key,
        tuple(sorted(_part_signature(p) for p in abs_paths)),
    )


def volume_group_identity_for_anchor(file_path: str) -> tuple | None:
    """从拖入路径解析同组分卷标识（无副作用，不重命名）。"""
    if not file_path:
        return None
    from volume.rename import current_path_for_drag

    anchor = current_path_for_drag(file_path) or file_path
    if not os.path.isfile(anchor):
        return None
    dirname = os.path.dirname(os.path.abspath(anchor))
    try:
        names = os.listdir(dirname)
    except OSError:
        return None
    for collector in (collect_cross_stem_7z_split, collect_cross_stem_disguised):
        raw = collector(dirname, anchor, names=names)
        if raw and len(raw) >= 2:
            return volume_group_identity(raw)
    return None
