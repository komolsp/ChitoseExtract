"""解压去重：区分「已发现/已入队」与「已成功解压」。"""
from __future__ import annotations

import os

_discovered_paths: set[str] = set()
_discovered_volume_ids: set[tuple] = set()
_unzipped_paths: set[str] = set()
_unzipped_volume_ids: set[tuple] = set()


def _norm_path(path: str | None) -> str | None:
    if not path:
        return None
    return os.path.normcase(os.path.normpath(path))


def _volume_identity(volumes: list[str] | None) -> tuple | None:
    if not volumes or len(volumes) < 2:
        return None
    try:
        from volume.collect import volume_group_identity
        return volume_group_identity(volumes)
    except ImportError:
        return None


def clear() -> None:
    _discovered_paths.clear()
    _discovered_volume_ids.clear()
    _unzipped_paths.clear()
    _unzipped_volume_ids.clear()


def _note_path(path: str | None, bucket: set[str]) -> None:
    norm = _norm_path(path)
    if norm:
        bucket.add(norm)
    try:
        from volume.rename import current_path_for_drag
        alt = current_path_for_drag(path) if path else None
    except ImportError:
        alt = None
    alt_norm = _norm_path(alt)
    if alt_norm:
        bucket.add(alt_norm)


def _remove_path(path: str | None, bucket: set[str]) -> None:
    norm = _norm_path(path)
    if norm:
        bucket.discard(norm)
    try:
        from volume.rename import current_path_for_drag
        alt = current_path_for_drag(path) if path else None
    except ImportError:
        alt = None
    alt_norm = _norm_path(alt)
    if alt_norm:
        bucket.discard(alt_norm)


def forget(path: str | None, volumes: list[str] | None = None) -> None:
    """清除路径/分卷组的已发现、已解压标记，允许用户再次拖入时重新解压。"""
    identity = _volume_identity(volumes)
    if not identity and path:
        try:
            from volume.collect import volume_group_identity_for_anchor
            identity = volume_group_identity_for_anchor(path)
        except ImportError:
            identity = None
    if identity:
        _discovered_volume_ids.discard(identity)
        _unzipped_volume_ids.discard(identity)
    targets = list(volumes or [])
    if path and path not in targets:
        targets.append(path)
    for item in targets:
        _remove_path(item, _discovered_paths)
        _remove_path(item, _unzipped_paths)


def forget_under(root: str | None) -> None:
    """清除某文件或目录树下所有压缩包的去重标记（含分卷组 identity）。"""
    root_norm = _norm_path(root)
    if not root_norm:
        return
    prefix = root_norm + os.sep
    for bucket in (_discovered_paths, _unzipped_paths):
        for path in list(bucket):
            if path == root_norm or path.startswith(prefix):
                bucket.discard(path)
    for bucket_id in (_discovered_volume_ids, _unzipped_volume_ids):
        stale = set()
        for identity in bucket_id:
            if not isinstance(identity, tuple) or not identity:
                continue
            dir_key = identity[0]
            if isinstance(dir_key, str) and (
                dir_key == root_norm or dir_key.startswith(prefix)
            ):
                stale.add(identity)
        bucket_id.difference_update(stale)

    if not root or not os.path.exists(root):
        return
    if os.path.isfile(root):
        forget(root)
        return
    try:
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if os.path.isfile(path):
                forget(path)
    except OSError:
        pass


def note_discovered(path: str | None, volumes: list[str] | None = None) -> None:
    identity = _volume_identity(volumes)
    if identity:
        _discovered_volume_ids.add(identity)
        for item in volumes or []:
            _note_path(item, _discovered_paths)
    else:
        _note_path(path, _discovered_paths)


def mark_unzipped(path: str | None, volumes: list[str] | None = None) -> None:
    note_discovered(path, volumes)
    identity = _volume_identity(volumes)
    if identity:
        _unzipped_volume_ids.add(identity)
        for item in volumes or []:
            _note_path(item, _unzipped_paths)
    else:
        _note_path(path, _unzipped_paths)


def is_discovered(path: str | None, volumes: list[str] | None = None) -> bool:
    identity = _volume_identity(volumes)
    if identity and identity in _discovered_volume_ids:
        return True
    if volumes and len(volumes) >= 2:
        if all(_norm_path(item) in _discovered_paths for item in volumes):
            return True
    norm = _norm_path(path)
    return bool(norm and norm in _discovered_paths)


def is_unzipped(path: str | None, volumes: list[str] | None = None) -> bool:
    identity = _volume_identity(volumes)
    if identity and identity in _unzipped_volume_ids:
        return True
    if volumes and len(volumes) >= 2:
        if all(_norm_path(item) in _unzipped_paths for item in volumes):
            return True
    norm = _norm_path(path)
    return bool(norm and norm in _unzipped_paths)


def is_volume_part_unzipped(path: str | None) -> bool:
    """分卷非首卷路径是否属于已解压的分卷组（按组标识匹配，不依赖当前聚组结果）。"""
    if not path:
        return False
    if is_unzipped(path):
        return True
    try:
        from volume.collect import volume_group_identity_for_anchor
        identity = volume_group_identity_for_anchor(path)
    except ImportError:
        identity = None
    return bool(identity and identity in _unzipped_volume_ids)


def sync_rename_registry() -> None:
    try:
        from volume.rename import _rename_registry
    except ImportError:
        return
    for new_key, original in list(_rename_registry.items()):
        for bucket in (_discovered_paths, _unzipped_paths):
            new_norm = _norm_path(new_key)
            original_norm = _norm_path(original)
            # A rename only transfers an archive's existing state.  Merely
            # normalizing a disguised volume name must not promote it from
            # "discovered" to "successfully extracted".
            if new_norm not in bucket and original_norm not in bucket:
                continue
            _note_path(new_key, bucket)
            _note_path(original, bucket)


def merge_already_add(already_add: list[str]) -> None:
    for path in already_add:
        note_discovered(path)


def pending_discovered_under(root: str | None) -> list[str]:
    """返回作品目录下已发现、尚未解压的压缩包路径（规范化）。"""
    root_norm = _norm_path(root)
    if not root_norm:
        return []
    pending: list[str] = []
    prefix = root_norm + os.sep
    for path in _discovered_paths:
        if path in _unzipped_paths:
            continue
        if path == root_norm or path.startswith(prefix):
            pending.append(path)
    pending.sort(key=len)
    return pending
