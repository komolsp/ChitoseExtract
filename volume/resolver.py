"""分卷解析入口：目录索引 + 多策略聚组。"""

import os
import re

from volume import collect, normalize, parse, score, stem_index

# (收集器, 规范化器)；legacy 已在 collect 内规范化，normalizer 传 None
_PIPELINE = (
    (collect.collect_cross_stem_7z_split, normalize.normalize_disguised_split),
    (collect.collect_cross_stem_disguised, normalize.normalize_disguised_split),
    (collect.collect_disguised_classic_zip, normalize.normalize_disguised_classic_zip),
    (stem_index.collect_by_stem, normalize.normalize_disguised_split),
    (collect.collect_trailing_numeric, normalize.normalize_disguised_split),
    (collect.collect_disguised_split, normalize.normalize_disguised_split),
    (collect.collect_simple_numeric, normalize.normalize_simple_numeric),
    (collect.collect_rar_oldstyle, normalize.normalize_rar_oldstyle),
    (collect.collect_fuzzy_zip, normalize.normalize_zip001),
    # 经典 .z01/.z02/.../.zip 由 7-Zip 原生按卷名关联；改成 .001 会让
    # ZIP 中央目录继续寻找原 .zNN 并报 Missing volume。
    (collect.collect_classic_zip, None),
    (collect.collect_zip001_family, normalize.normalize_zip001),
    (collect.collect_rar_part, normalize.normalize_rar_part),
)


def _legacy_normalizer(dirname: str, volumes: list[str]) -> list[str]:
    basename = os.path.basename(volumes[0])
    if re.search(r'\.part\d+', basename, re.IGNORECASE):
        return normalize.normalize_rar_part(dirname, volumes)
    if re.search(r'\.z\d{2}\b', basename, re.IGNORECASE):
        main_zip = next(
            (
                path for path in volumes
                if re.fullmatch(r'.+\.zip', os.path.basename(path), re.IGNORECASE)
            ),
            None,
        )
        if main_zip:
            z_parts = sorted(
                (path for path in volumes if path != main_zip),
                key=lambda path: os.path.basename(path).lower(),
            )
            return [main_zip] + z_parts
        return normalize.normalize_zip001(dirname, volumes)
    return volumes


class VolumeIndex:
    """同目录分卷索引（每次 resolve 刷新 listdir）。"""

    __slots__ = ('dirname', '_names')

    def __init__(self, dirname: str):
        self.dirname = dirname
        self._refresh_names()

    def _refresh_names(self):
        try:
            self._names = os.listdir(self.dirname)
        except OSError:
            self._names = []

    def resolve(self, file_path: str) -> list[str] | None:
        self._refresh_names()
        basename = os.path.basename(file_path)
        parsed_7z = parse.parse_7z_split(basename)
        if parsed_7z:
            volumes = collect.collect_7z(self.dirname, file_path)
            if volumes and len(volumes) >= 2:
                from volume.validate import accept_volume_group
                if accept_volume_group(volumes):
                    return volumes

        candidates: list[tuple[float, list[str], object]] = []
        for collector, normalizer in _PIPELINE:
            raw = collector(self.dirname, file_path)
            if raw and len(raw) >= 2:
                from volume.validate import accept_volume_group
                if not accept_volume_group(raw):
                    continue
                candidates.append((score.score_volume_group(raw), raw, normalizer))

        legacy = collect.collect_legacy_pattern(self.dirname, file_path)
        if legacy and len(legacy) >= 2:
            from volume.validate import accept_volume_group
            if accept_volume_group(legacy):
                candidates.append((score.score_volume_group(legacy), legacy, _legacy_normalizer))

        seen: set[tuple[str, ...]] = set()
        unique: list[tuple[float, list[str], object]] = []
        for item_score, vols, normalizer in candidates:
            key = tuple(sorted(v.lower() for v in vols))
            if key not in seen:
                seen.add(key)
                unique.append((item_score, vols, normalizer))

        picked = score.pick_best_candidate([(s, v) for s, v, _ in unique])
        if not picked:
            cluster = stem_index.collect_by_stem(self.dirname, file_path)
            if cluster and len(cluster) >= 2:
                from volume.validate import accept_volume_group
                if accept_volume_group(cluster):
                    return normalize.normalize_disguised_split(self.dirname, cluster)
            return None

        picked_key = tuple(sorted(v.lower() for v in picked))
        for _item_score, vols, normalizer in unique:
            if tuple(sorted(v.lower() for v in vols)) == picked_key:
                if normalizer is None:
                    return vols
                return normalizer(self.dirname, vols)
        return normalize.normalize_disguised_split(self.dirname, picked)

    def peek_group(self, file_path: str) -> list[str] | None:
        """只读聚组：与 resolve 相同但不调用会改文件名的 normalizer。"""
        self._refresh_names()
        basename = os.path.basename(file_path)
        parsed_7z = parse.parse_7z_split(basename)
        if parsed_7z:
            volumes = collect.collect_7z_readonly(self.dirname, file_path)
            if volumes and len(volumes) >= 2:
                from volume.validate import accept_volume_group
                if accept_volume_group(volumes):
                    return volumes

        candidates: list[tuple[float, list[str]]] = []
        for collector, _normalizer in _PIPELINE:
            raw = collector(self.dirname, file_path)
            if raw and len(raw) >= 2:
                from volume.validate import accept_volume_group
                if not accept_volume_group(raw):
                    continue
                candidates.append((score.score_volume_group(raw), raw))

        legacy = collect.collect_legacy_pattern(self.dirname, file_path)
        if legacy and len(legacy) >= 2:
            from volume.validate import accept_volume_group
            if accept_volume_group(legacy):
                candidates.append((score.score_volume_group(legacy), legacy))

        seen: set[tuple[str, ...]] = set()
        unique: list[tuple[float, list[str]]] = []
        for item_score, vols in candidates:
            key = tuple(sorted(v.lower() for v in vols))
            if key not in seen:
                seen.add(key)
                unique.append((item_score, vols))

        picked = score.pick_best_candidate([(s, v) for s, v in unique])
        if picked:
            return picked

        cluster = stem_index.collect_by_stem(self.dirname, file_path)
        if cluster and len(cluster) >= 2:
            from volume.validate import accept_volume_group
            if accept_volume_group(cluster):
                return cluster
        return None

    def has_group(self, file_path: str) -> bool:
        return self.resolve(file_path) is not None


_index_cache: dict[str, VolumeIndex] = {}


def _index_for(path: str) -> VolumeIndex:
    dirname = os.path.dirname(os.path.abspath(path))
    cached = _index_cache.get(dirname)
    if cached is None:
        cached = VolumeIndex(dirname)
        _index_cache[dirname] = cached
    return cached


def clear_index_cache():
    _index_cache.clear()


class VolumeResolver:
    @staticmethod
    def resolve(file_path: str) -> list[str] | None:
        if not file_path:
            return None
        from volume.rename import current_path_for_drag
        actual = current_path_for_drag(file_path) or file_path
        if not os.path.isfile(actual):
            return None
        return _index_for(actual).resolve(actual)

    @staticmethod
    def peek_volumes(file_path: str) -> list[str] | None:
        if not file_path:
            return None
        from volume.rename import current_path_for_drag
        actual = current_path_for_drag(file_path) or file_path
        if not os.path.isfile(actual):
            return None
        return _index_for(actual).peek_group(actual)

    @staticmethod
    def is_volume_readonly(file_path: str) -> bool:
        """判断是否为分卷压缩包（探测阶段只读，不重命名）。"""
        if not file_path or not os.path.exists(file_path):
            return False
        basename = os.path.basename(file_path)
        father = os.path.dirname(file_path)
        if parse.parse_7z_split(basename):
            return True
        if VolumeResolver.peek_volumes(file_path):
            return True
        if re.search(r'(.*)\.zip\b', basename):
            stem, _ = os.path.splitext(basename)
            return (os.path.exists(os.path.join(father, stem + '.z01'))
                    or os.path.exists(os.path.join(father, stem + '.002')))
        for pattern, sibling_re in (
            (r'(.*)\.\d{3}\b', r'{}\.\d{{3}}\b'),
            (r'(.*)\.part\d+', r'{}\.part\d+'),
            (r'(.*)\.z\d{2}\b', r'{}\.z\d{{2}}\b'),
        ):
            match = re.search(pattern, basename)
            if not match:
                continue
            stem = match.group(1)
            try:
                names = os.listdir(father)
            except OSError:
                continue
            esc = re.escape(stem)
            for name in names:
                if name == basename:
                    continue
                if re.search(sibling_re.format(esc), name):
                    return True
        return False

    @staticmethod
    def is_volume(file_path: str) -> bool:
        if not file_path or not os.path.exists(file_path):
            return False
        basename = os.path.basename(file_path)
        father = os.path.dirname(file_path)
        if parse.parse_7z_split(basename):
            return True
        if VolumeResolver.resolve(file_path):
            return True
        index = _index_for(file_path)
        if index.has_group(file_path):
            return True
        if re.search(r'(.*)\.zip\b', basename):
            stem, _ = os.path.splitext(basename)
            return (os.path.exists(os.path.join(father, stem + '.z01'))
                    or os.path.exists(os.path.join(father, stem + '.002')))
        for pattern, sibling_re in (
            (r'(.*)\.\d{3}\b', r'{}\.\d{{3}}\b'),
            (r'(.*)\.part\d+', r'{}\.part\d+'),
            (r'(.*)\.z\d{2}\b', r'{}\.z\d{{2}}\b'),
        ):
            match = re.search(pattern, basename)
            if not match:
                continue
            stem = match.group(1)
            try:
                names = os.listdir(father)
            except OSError:
                continue
            esc = re.escape(stem)
            for name in names:
                if name == basename:
                    continue
                if re.search(sibling_re.format(esc), name):
                    return True
        return False


_STANDARD_VOLUME_NAME = (
    re.compile(r'\.part\d+$', re.IGNORECASE),
    re.compile(r'\.\d{3}$'),
    re.compile(r'\.z\d{2}$', re.IGNORECASE),
)


def is_standard_volume_group(volumes: list[str]) -> bool:
    """分卷已规范化为 7-Zip 标准命名，可跳过改后缀/隐写打开策略。"""
    if len(volumes) < 2:
        return False
    for path in volumes:
        basename = os.path.basename(path)
        if not any(pattern.search(basename) for pattern in _STANDARD_VOLUME_NAME):
            return False
    return True


def _volume_part_indices(volumes: list[str]) -> list[int]:
    indices: list[int] = []
    for path in volumes:
        basename = os.path.basename(path)
        info = stem_index.extract_part_info(basename)
        if info:
            indices.append(info[1])
            continue
        parsed_7z = parse.parse_7z_split(basename)
        if parsed_7z:
            indices.append(parse.order_7z_part(parsed_7z[1]))
            continue
        parsed = parse.parse_trailing_numeric(basename) or parse.parse_leading_numeric(
            basename,
        ) or parse.parse_simple_numeric(basename)
        if parsed:
            indices.append(parsed[1])
    return indices


def is_complete_volume_group(volumes: list[str]) -> bool:
    """分卷组须含首卷（part1 / .001 等），否则首拖入时易误建残缺任务。"""
    if len(volumes) < 2:
        return False
    basenames = [os.path.basename(path) for path in volumes]
    classic_main = next(
        (name[:-4] for name in basenames if name.lower().endswith('.zip')),
        None,
    )
    if classic_main is not None:
        # 经典 ZIP 的 .z01 是第一数据卷；通用编号把它映射为 2 是为了旧的
        # .001 转换逻辑，保留原名模式下不能因此误判“缺少首卷”。
        expected_z01 = (classic_main + '.z01').lower()
        if any(name.lower() == expected_z01 for name in basenames):
            return True
    indices = _volume_part_indices(volumes)
    if not indices:
        return True
    return min(indices) == 1


def resolve_volume_archives(file_path: str) -> list[str] | None:
    return VolumeResolver.resolve(file_path)


def is_volume_archive(file_path: str) -> bool:
    return VolumeResolver.is_volume(file_path)
