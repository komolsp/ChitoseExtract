"""分卷重命名（rename 失败时 copy+delete）。"""

import os
import re
import shutil

import pk_logger

_logger = pk_logger.Pk_logger('file_ops_logger', 'log.txt').add_log_handler().get_logger()

# 规范化后的路径 -> 拖入时的原始路径
_rename_registry: dict[str, str] = {}


def unique_dest_path(dirname: str, new_name: str) -> str:
    new_path = os.path.join(dirname, new_name)
    if not os.path.exists(new_path):
        return new_path
    match = re.match(r'^(?P<stem>.+)\.(?P<part>\d{3})$', new_name)
    if not match:
        base, ext = os.path.splitext(new_name)
        suffix_no = 1
        while os.path.exists(new_path):
            new_name = f'{base}({suffix_no}){ext}'
            new_path = os.path.join(dirname, new_name)
            suffix_no += 1
        return new_path
    stem, part = match.group('stem'), match.group('part')
    suffix_no = 1
    while True:
        alt_name = f'{stem}({suffix_no}).{part}'
        alt_path = os.path.join(dirname, alt_name)
        if not os.path.exists(alt_path):
            return alt_path
        suffix_no += 1


def _safe_copy_replace(src: str, dest: str) -> bool:
    """copy+delete 回退：dest 已存在或复制后大小不一致时不删除 src。"""
    if os.path.normcase(src) == os.path.normcase(dest):
        return True
    if os.path.exists(dest):
        return False
    try:
        src_size = os.path.getsize(src)
    except OSError:
        return False
    try:
        shutil.copy2(src, dest)
    except OSError as err:
        _logger.debug('分卷复制替换失败 [{}] -> [{}]: {}'.format(src, dest, err))
        return False
    try:
        if os.path.getsize(dest) != src_size:
            try:
                os.remove(dest)
            except OSError:
                pass
            return False
        os.remove(src)
        return True
    except OSError as err:
        _logger.debug('复制后删除源文件失败 [{}]: {}'.format(src, err))
        try:
            os.remove(dest)
        except OSError:
            pass
        return False


def safe_rename(src: str, dest: str) -> bool:
    if os.path.normcase(src) == os.path.normcase(dest):
        return True
    try:
        os.replace(src, dest)
        return True
    except OSError:
        pass
    return _safe_copy_replace(src, dest)


def _register_rename(new_path: str, old_path: str):
    new_key = os.path.normcase(new_path)
    old_key = os.path.normcase(old_path)
    original = _rename_registry.pop(old_key, old_path)
    _rename_registry[new_key] = original


def clear_rename_registry():
    _rename_registry.clear()


def current_path_for_drag(path: str) -> str | None:
    """拖入路径若已被分卷规范化重命名，返回当前磁盘路径。"""
    if not path:
        return None
    if os.path.exists(path):
        return path
    orig_key = os.path.normcase(os.path.abspath(path))
    for new_key, original in _rename_registry.items():
        if os.path.normcase(original) != orig_key:
            continue
        if os.path.isfile(new_key):
            return new_key
    return None


def find_volume_anchor_in_directory(dirname: str) -> str | None:
    """目录内任一分卷成员路径，用于 stale 路径失效后的重新聚组。"""
    if not dirname:
        return None
    try:
        names = os.listdir(dirname)
    except OSError:
        return None
    from volume.resolver import VolumeResolver

    for name in sorted(names):
        path = os.path.join(dirname, name)
        if os.path.isfile(path) and VolumeResolver.peek_volumes(path):
            return path
    return None


def resolve_volume_paths_on_disk(volumes: list[str]) -> list[str]:
    """将可能已过期的分卷路径同步为当前磁盘路径（必要时重新聚组并规范化）。"""
    if not volumes:
        return volumes
    if all(os.path.isfile(path) for path in volumes):
        return volumes

    resolved: list[str] = []
    for path in volumes:
        current = current_path_for_drag(path)
        if current and os.path.isfile(current):
            resolved.append(current)
        elif os.path.isfile(path):
            resolved.append(path)
        else:
            resolved.append(path)
    if all(os.path.isfile(path) for path in resolved):
        return resolved

    anchor = next((path for path in resolved if os.path.isfile(path)), None)
    if not anchor:
        anchor = find_volume_anchor_in_directory(
            os.path.dirname(os.path.abspath(volumes[0])),
        )
    if not anchor:
        return resolved

    from volume import resolve_volume_archives

    fresh = resolve_volume_archives(anchor)
    return fresh if fresh else resolved


def restore_renames_in_directory(dirname: str) -> int:
    """还原该目录下所有已记录的分卷重命名。"""
    if not dirname:
        return 0
    abs_dir = os.path.normcase(os.path.abspath(dirname))
    restored = 0
    for key in list(_rename_registry.keys()):
        if os.path.normcase(os.path.dirname(key)) != abs_dir:
            continue
        before = key in _rename_registry
        _restore_single(key)
        if before and key not in _rename_registry:
            restored += 1
    if restored:
        try:
            from volume.resolver import clear_index_cache
            clear_index_cache()
        except ImportError:
            pass
    return restored


def restore_renamed_volumes(volumes: list[str]) -> list[str]:
    """将规范化后的分卷路径还原为拖入时的原始文件名。"""
    if not volumes:
        return volumes
    dirname = os.path.dirname(os.path.abspath(volumes[0]))
    originals = {
        path: _rename_registry.get(os.path.normcase(path))
        for path in volumes
    }
    restore_renames_in_directory(dirname)
    return [originals.get(path) or path for path in volumes]


def _restore_single(path: str) -> str:
    key = os.path.normcase(path)
    original = _rename_registry.get(key)
    if not original:
        return path
    if not os.path.isfile(path):
        _rename_registry.pop(key, None)
        return path
    if os.path.exists(original) and os.path.normcase(original) != key:
        _logger.warning(
            '还原分卷原名跳过，目标已存在：[{}] -> [{}]'.format(path, original),
        )
        return path
    if safe_rename(path, original):
        _rename_registry.pop(key, None)
        _logger.info('还原分卷原名[ {} ] -> [ {} ]'.format(path, original))
        return original
    _logger.warning('还原分卷原名失败，沿用现名：[{}]'.format(path))
    return path


def rename_volume(src: str, dest: str) -> bool:
    if os.path.normcase(src) == os.path.normcase(dest):
        return True
    if safe_rename(src, dest):
        _register_rename(dest, src)
        _logger.info('修改分卷名为7zip易识别格式[ {} ] -> [ {} ]'.format(src, dest))
        try:
            from volume.resolver import clear_index_cache
            clear_index_cache()
        except ImportError:
            pass
        return True
    _logger.warning('分卷重命名失败，沿用原名：[{}]'.format(src))
    return False
