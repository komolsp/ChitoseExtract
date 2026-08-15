import copy
import os
import re
import shutil
import traceback

import config
import password
import app_paths
import dlrenamer.ez_client
import disk_io_monitor
import file_ops
import filter as filter_module
import audio_convert
import audio_tagger
import archive_registry

from dlrenamer.runner import get_shared_scraper

from file_ops import mk_if_not_exit, logger

from timeline import Timeline, Archive, Record, extend
from zip import Zip
from renamer import RenameDuplicateError
from workflow_context import WorkflowContext

workflow_context = WorkflowContext()
logger = workflow_context.services.logger
conf = workflow_context.services.conf
passwords = workflow_context.services.passwords
unzipper = workflow_context.services.unzipper
filter = workflow_context.services.filter_service
renamer = workflow_context.services.renamer
progress_ui = workflow_context.services.progress_ui
already_add = workflow_context.state.already_add
timelines = workflow_context.state.timelines

# 顶层压缩包「就地解压」产生的作品工作目录集合（保留原始大小写）。
# 用于让整个流程识别出音声库之外的临时工作目录边界：先在原地解压识别，
# 识别到 RJ 后再移入音声库，未识别则移入资源库，避免跨盘识别+跨盘转资源库。
_work_roots = workflow_context.state.work_roots
# 暂存工作目录 → 移入库时应使用的文件夹名（无后缀压缩包与源文件同名时）
_work_root_preferred_names = workflow_context.state.work_root_preferred_names

_RUNTIME_UNSET = object()


def _sync_context_services() -> WorkflowContext:
    services = workflow_context.services
    services.logger = logger
    services.conf = conf
    services.passwords = passwords
    services.unzipper = unzipper
    services.filter_service = filter
    services.renamer = renamer
    services.progress_ui = progress_ui
    return workflow_context


def bind_runtime_services(
    *,
    logger_service=_RUNTIME_UNSET,
    configuration=_RUNTIME_UNSET,
    password_list=_RUNTIME_UNSET,
    unzip_service=_RUNTIME_UNSET,
    filter_service=_RUNTIME_UNSET,
    rename_service=_RUNTIME_UNSET,
    progress_service=_RUNTIME_UNSET,
) -> WorkflowContext:
    """统一绑定运行时依赖；保留模块级变量供旧调用与测试兼容。"""
    global logger, conf, passwords, unzipper, filter, renamer, progress_ui
    if logger_service is not _RUNTIME_UNSET:
        logger = logger_service
    if configuration is not _RUNTIME_UNSET:
        conf = configuration
    if password_list is not _RUNTIME_UNSET:
        passwords = password_list
    if unzip_service is not _RUNTIME_UNSET:
        unzipper = unzip_service
    if filter_service is not _RUNTIME_UNSET:
        filter = filter_service
    if rename_service is not _RUNTIME_UNSET:
        renamer = rename_service
    if progress_service is not _RUNTIME_UNSET:
        progress_ui = progress_service
    return _sync_context_services()


def get_workflow_context() -> WorkflowContext:
    """返回与兼容模块变量同步后的上下文。"""
    return _sync_context_services()

# 内层/更深层扫描时，累计到此数量的「疑似压缩包但打不开」文件后，
# 直接停止继续探测（视为解压成功），避免逐个起 7-Zip 子进程拖慢整体性能。
NESTED_UNRESOLVED_LIMIT = 5
_MAX_UNZIP_ROUNDS = 64
# 顶层外层作品目录后缀，避免 album.7z / album/ / album.zip 同名冲突
TOP_WORK_ROOT_SUFFIX = '_pk'


def _is_under_output(path: str | None) -> bool:
    if not path or not conf:
        return False
    return file_ops.is_path_under(os.path.normpath(conf.output_path), os.path.normpath(path))


def _resolve_work_root_containing(path: str | None) -> str | None:
    """若 path 位于某外层压缩包已解压的作品目录内，返回该目录并登记。"""
    if not path:
        return None
    path_norm = os.path.normpath(path)
    existing = _under_work_root(path_norm)
    if existing:
        return existing
    if os.path.isfile(path_norm):
        parent = os.path.dirname(path_norm)
        stem = os.path.splitext(os.path.basename(path_norm))[0]
        for folder_name in (stem + TOP_WORK_ROOT_SUFFIX, stem):
            sibling = os.path.join(parent, folder_name)
            if os.path.isdir(sibling) and (
                archive_registry.is_unzipped(path_norm)
                or _has_extracted_content(sibling)
            ):
                _register_work_root(sibling)
                return sibling
    directory = path_norm if os.path.isdir(path_norm) else os.path.dirname(path_norm)
    for _ in range(8):
        if not directory:
            break
        dir_name = os.path.basename(directory)
        parent_dir = os.path.dirname(directory)
        if not dir_name or not parent_dir:
            break
        for ext in ('.7z', '.zip', '.rar'):
            anchors = [os.path.join(parent_dir, dir_name + ext)]
            if dir_name.endswith(TOP_WORK_ROOT_SUFFIX):
                stem = dir_name[:-len(TOP_WORK_ROOT_SUFFIX)]
                anchors.append(os.path.join(parent_dir, stem + ext))
            for anchor in anchors:
                if os.path.isfile(anchor) and (
                    archive_registry.is_unzipped(anchor)
                    or _has_extracted_content(directory)
                ):
                    _register_work_root(directory)
                    return directory
        directory = parent_dir
    return None


def _outer_sibling_work_root(zip_obj: Zip) -> str | None:
    """顶层外层：压缩包与作品目录并列（优先 album_pk/，兼容旧 album/）。"""
    if not isinstance(zip_obj, Zip) or not zip_obj.path or not os.path.isfile(zip_obj.path):
        return None
    ext = (zip_obj.extension or '').lower()
    if ext not in ('.7z', '.zip', '.rar', '.001', '.ha'):
        return None
    containing = _under_work_root(zip_obj.path) or _resolve_work_root_containing(zip_obj.path)
    if containing and os.path.normcase(containing) == os.path.normcase(zip_obj.father):
        return None
    for work_root in _candidate_top_work_roots(zip_obj):
        if os.path.isdir(work_root):
            return work_root
    return None


def _is_nested_archive(zip_obj: Zip) -> bool:
    if not isinstance(zip_obj, Zip) or not zip_obj.path:
        return False
    if _outer_sibling_work_root(zip_obj) is not None:
        return False
    if _is_under_output(zip_obj.path):
        return True
    work_root = _under_work_root(zip_obj.path) or _resolve_work_root_containing(zip_obj.path)
    if not work_root:
        return False
    return file_ops.is_path_under(work_root, zip_obj.path)


def _has_extracted_content(work_root: str | None) -> bool:
    if not work_root or not os.path.isdir(work_root):
        return False
    try:
        return bool(os.listdir(work_root))
    except OSError:
        return False


def _resolve_extracted_work_root(zip_obj: Zip) -> str | None:
    """返回该压缩包已成功解压到的作品目录（若存在）。"""
    if not isinstance(zip_obj, Zip):
        return None
    if _is_nested_archive(zip_obj):
        nested_root = _under_work_root(zip_obj.path) or _resolve_work_root_containing(zip_obj.path)
        if nested_root and os.path.isdir(nested_root):
            return nested_root
        father = zip_obj.father
        return father if father and os.path.isdir(father) else None
    for work_root in _candidate_top_work_roots(zip_obj):
        if os.path.isdir(work_root) and _has_extracted_content(work_root):
            return work_root
    for staging in _outer_staging_dirs(zip_obj):
        if _staging_has_usable_partial_extract(staging):
            return staging
    return None


def _should_resume_nested_only(zip_obj: Zip) -> bool:
    """外层已解压、仅需继续处理内层时返回 True。"""
    if not isinstance(zip_obj, Zip) or _is_nested_archive(zip_obj):
        return False
    for work_root in _candidate_top_work_roots(zip_obj):
        if os.path.isdir(work_root) and _has_extracted_content(work_root):
            return True
    if archive_registry.is_unzipped(zip_obj.path, zip_obj.volumes):
        resolved = _resolve_extracted_work_root(zip_obj)
        return bool(resolved and _has_extracted_content(resolved))
    for staging in _outer_staging_dirs(zip_obj):
        if _staging_has_usable_partial_extract(staging):
            return True
    return False


def _timeline_outer_zip(timeline: Timeline) -> Zip | None:
    """时间线中已成功解压的顶层（非套娃）压缩包。"""
    for record in timeline.records:
        if record.ops != 'unzip' or not isinstance(record.input_file, Zip):
            continue
        if not _is_nested_archive(record.input_file):
            return record.input_file
    return None


def _timeline_outer_work_root(
    timeline: Timeline,
    current_path: str | None = None,
) -> str | None:
    """从时间线中的顶层压缩包恢复作品根，避免套娃子目录覆盖归档边界。"""
    outer_zip = _timeline_outer_zip(timeline)
    if not outer_zip:
        return None
    work_root = _resolve_extracted_work_root(outer_zip)
    if not work_root or not file_ops.is_dir_path(work_root):
        return None
    if current_path and not file_ops.is_path_under(work_root, current_path):
        return None
    return os.path.normpath(work_root)


def _zip_path_already_queued(path: str | None) -> bool:
    if not path:
        return False
    norm = os.path.normcase(os.path.normpath(path))
    for timeline in timelines:
        record = timeline.get_current_record()
        zip_obj = record.output_file
        if not isinstance(zip_obj, Zip):
            continue
        # unzip_failed 仍需重试，不能视为已入队
        if record.ops != 'find_zip':
            continue
        if os.path.normcase(os.path.normpath(zip_obj.path)) == norm:
            return True
    return False


def _is_likely_retryable_unzip_failure(zip_obj: Zip) -> bool:
    """内层误判为非压缩包的失败项不必反复重试。"""
    if not isinstance(zip_obj, Zip):
        return False
    if not _is_nested_archive(zip_obj):
        return True
    if zip_obj.file_list:
        return True
    if zip_obj.is_encrypted() or zip_obj.compression_ratio_info.get('encrypted'):
        return True
    return False


def _scan_inner_archives_in_work_root(
    work_root: str,
    passwords: list[str],
    already_add: list[str],
) -> list:
    """扫描作品目录中的内层压缩包（带误判上限）。"""
    inner_list: list = []
    unresolved_list: list = []
    unzipper.find_zip(
        work_root,
        passwords,
        conf.del_after_reunzip,
        already_add,
        inner_list,
        depth=1,
        collect_unresolved=False,
        unresolved_list=unresolved_list,
        unresolved_limit=NESTED_UNRESOLVED_LIMIT,
    )
    return inner_list


def _snapshot_scan_tree(root: str | None) -> dict[str, tuple[int, int]]:
    """轻量记录文件树；只保存文件尺寸和修改时间，不读取文件内容。"""
    if not root or not os.path.isdir(root):
        return {}
    snapshot: dict[str, tuple[int, int]] = {}
    try:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                snapshot[os.path.normcase(os.path.normpath(path))] = (
                    stat.st_size, stat.st_mtime_ns,
                )
    except OSError:
        return {}
    return snapshot


def _incremental_scan_roots(
    root: str | None,
    before: dict[str, tuple[int, int]] | None,
) -> list[str] | None:
    """返回本轮新增/替换内容的最小扫描根；None 表示必须回退全量扫描。"""
    if not root or not os.path.isdir(root) or before is None:
        return None
    root = os.path.normpath(root)
    changed: list[str] = []
    try:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                key = os.path.normcase(os.path.normpath(path))
                if before.get(key) != (stat.st_size, stat.st_mtime_ns):
                    changed.append(path)
    except OSError:
        return None

    # 成功完成快照比较后，即使没有新增文件，空列表也是可信结果。
    roots: dict[str, str] = {}
    for path in changed:
        rel = os.path.relpath(path, root)
        first = rel.split(os.sep, 1)[0]
        candidate = os.path.join(root, first)
        roots.setdefault(os.path.normcase(candidate), candidate)
    return list(roots.values())


def _normalize_nested_scan_root(
    path: str | None,
    parent_zip: Zip | None = None,
) -> str | None:
    """套娃扫描根目录：必须是作品文件夹，不能是外层压缩包文件路径。"""
    if not path:
        return None
    path_norm = os.path.normpath(path)
    if os.path.isdir(path_norm):
        return path_norm
    if isinstance(parent_zip, Zip):
        work_root = _resolve_extracted_work_root(parent_zip)
        if work_root and os.path.isdir(work_root):
            return work_root
        intended = _intended_top_work_root(parent_zip)
        if os.path.isdir(intended):
            return intended
    contained = _resolve_work_root_containing(path_norm)
    if contained and os.path.isdir(contained):
        return contained
    return path_norm if os.path.isdir(path_norm) else None


def _list_pending_archives_in_work_root(
    work_root: str | None,
    passwords: list[str],
) -> list[Zip]:
    """在作品目录中直接枚举尚未解压的压缩包（find_zip 漏扫时的兜底）。"""
    if not work_root or not os.path.isdir(work_root):
        return []
    pending: list[Zip] = []
    try:
        names = os.listdir(work_root)
    except OSError:
        return []
    for name in names:
        full = os.path.join(work_root, name)
        if not os.path.isfile(full):
            continue
        if archive_registry.is_unzipped(full):
            continue
        probe = file_ops.probe_archive(full, nested=True)
        if not probe.is_candidate:
            ext = os.path.splitext(name)[1].lower()
            if ext not in ('.zip', '.7z', '.rar', '.001', '.ha'):
                continue
        pending.append(
            Zip(
                full,
                passwords,
                conf.del_after_reunzip,
                covered=probe.covered,
                format_type=probe.format_type,
            ),
        )
    return pending


def _advance_past_outer_layer(
    timeline: Timeline,
    outer_zip: Zip,
    work_root: str | None,
) -> bool:
    """外层已解压但无法推进到内层时，结束外层 find_zip 以防重复解压。"""
    if not isinstance(outer_zip, Zip) or _is_nested_archive(outer_zip):
        return False
    if not work_root:
        work_root = _resolve_extracted_work_root(outer_zip)
    if not work_root or not os.path.isdir(work_root):
        return False
    archive_registry.mark_unzipped(outer_zip.path, outer_zip.volumes)
    _register_work_root(work_root)
    timeline.add_record(Record(Archive(work_root), 'unnest', Archive(work_root)))
    if logger:
        logger.warning(
            '外层已解压，未找到待处理内层，已停止重复解压："{}"'.format(
                os.path.normpath(work_root),
            ),
        )
    return True


def _recover_outer_with_pending_inner(
    timeline: Timeline,
    outer_zip: Zip,
) -> bool:
    """外层 7z 因内层加密项报错但作品目录已生成时，转入内层解压。"""
    if not isinstance(outer_zip, Zip) or _is_nested_archive(outer_zip):
        return False
    work_root = _resolve_extracted_work_root(outer_zip)
    if work_root and _is_staging_unzip_path(work_root):
        promoted = _promote_partial_staging_to_work_root(outer_zip, work_root)
        if promoted:
            work_root = promoted
    if not work_root or not _has_extracted_content(work_root):
        return False
    if not unzipper.work_root_has_valid_inner_archive(work_root):
        if logger:
            logger.warning(
                '外层解压未验证成功，内层压缩包无法打开，跳过假成功恢复："{}"'.format(
                    os.path.normpath(work_root),
                ),
            )
        return False
    str_passwords = password.get_str_passwords(password.sort_passwords(passwords))
    inner_list = _scan_inner_archives_in_work_root(work_root, str_passwords, already_add)
    if not inner_list:
        inner_list = _list_pending_archives_in_work_root(work_root, str_passwords)
    if not inner_list:
        return _advance_past_outer_layer(timeline, outer_zip, work_root)
    archive_registry.mark_unzipped(outer_zip.path, outer_zip.volumes)
    _register_work_root(work_root)
    if logger:
        logger.info(
            '外层已解压到作品目录，转入内层处理："{}"'.format(
                os.path.normpath(work_root),
            ),
        )
    _enqueue_nested_archives(timeline, work_root, outer_zip)
    if _timeline_targets_outer_zip(timeline):
        _promote_outer_timeline_to_inner(timeline)
    return not _timeline_targets_outer_zip(timeline)


def _mark_timeline_unzipped_layers(timeline: Timeline):
    """从时间线历史回填已成功解压的压缩包，避免重试时重复解压外层。"""
    for record in timeline.records:
        if record.ops != 'unzip':
            continue
        zip_obj = record.input_file
        if isinstance(zip_obj, Zip):
            archive_registry.mark_unzipped(zip_obj.path, zip_obj.volumes)


def _source_covers_archive(source: str | None, zip_obj: Zip) -> bool:
    """拖入项是否直接包含该压缩包（文件本身或其所在目录）。"""
    if not source or not isinstance(zip_obj, Zip) or not zip_obj.path:
        return False
    source_norm = os.path.normcase(os.path.normpath(source))
    zip_norm = os.path.normcase(os.path.normpath(zip_obj.path))
    if os.path.isfile(source):
        if zip_norm == source_norm:
            return True
        if zip_obj.volumes:
            vol_norms = {os.path.normcase(os.path.normpath(v)) for v in zip_obj.volumes}
            return source_norm in vol_norms
        return False
    if os.path.isdir(source):
        prefix = source_norm + os.sep
        return zip_norm == source_norm or zip_norm.startswith(prefix)
    return False


def _is_user_queued_top_level_archive(source: str | None, zip_obj: Zip) -> bool:
    """用户拖入目录/文件直接发现的压缩包（非作品目录深处的套娃内层）。"""
    if not source or not isinstance(zip_obj, Zip) or not zip_obj.path:
        return False
    if os.path.isfile(source):
        return _source_covers_archive(source, zip_obj)
    if not os.path.isdir(source):
        return False
    source_norm = os.path.normcase(os.path.normpath(source))
    zip_norm = os.path.normcase(os.path.normpath(zip_obj.path))
    if not zip_norm.startswith(source_norm + os.sep):
        return False
    rel = os.path.relpath(zip_obj.path, source)
    rel_dir = os.path.dirname(rel)
    if rel_dir in ('', '.'):
        return True
    # 仅允许拖入目录下直接一层；子目录内视为套娃内层
    return False


def _timeline_requests_reextract(timeline: Timeline, zip_obj: Zip) -> bool:
    """用户再次拖入的顶层任务，应允许重新解压而非仅续扫内层。"""
    if not timeline.records:
        return False
    source = timeline.records[0].input_file.path
    if not _is_user_queued_top_level_archive(source, zip_obj):
        return False
    record = timeline.get_current_record()
    if record.ops not in ('find_zip', 'create_timeline'):
        return False
    return True


def _prepare_user_rescan(source: str) -> None:
    """用户主动拖入工作区：清除该路径下的去重标记。"""
    archive_registry.forget_under(source)
    if os.path.isfile(source):
        volumes = file_ops.resolve_volume_archives(source)
        if volumes:
            archive_registry.forget(volumes[0], volumes)
        else:
            archive_registry.forget(source)
    elif os.path.isdir(source):
        norm = os.path.normpath(source)
        if norm in _work_roots:
            _unregister_work_root(norm)


def _filter_already_extracted_archives(
    zip_list: list,
    passwords: list[str],
    already_add: list[str],
    *,
    allow_reextract: bool = False,
) -> list:
    """已解压的外层压缩包不再入队，仅扫描其目录中的待处理内层。"""
    filtered: list = []
    for zip_obj in zip_list:
        if allow_reextract or not isinstance(zip_obj, Zip) or not _should_resume_nested_only(zip_obj):
            filtered.append(zip_obj)
            continue
        if not archive_registry.is_unzipped(zip_obj.path, zip_obj.volumes):
            archive_registry.mark_unzipped(zip_obj.path, zip_obj.volumes)
        work_root = _resolve_extracted_work_root(zip_obj)
        if not work_root:
            filtered.append(zip_obj)
            continue
        if logger:
            logger.info(
                '外层已解压，跳过重复解压并扫描内层："{}"'.format(
                    os.path.normpath(zip_obj.path or ''),
                ),
            )
        inner_list = _scan_inner_archives_in_work_root(
            work_root, passwords, already_add,
        )
        filtered.extend(inner_list)
    return filtered


def _is_registered_work_root(path: str | None) -> bool:
    """返回路径是否是流程已确认的精确作品根目录。"""
    return workflow_context.state.is_work_root_registered(path)


def _register_work_root(path: str, *, trusted: bool = False):
    """登记作品根目录；trusted 仅用于程序刚创建的解压暂存目录。"""
    if not path:
        return
    norm = os.path.normpath(path)
    if (
        _is_container_or_library_root(norm)
        and not trusted
        and not _is_registered_work_root(norm)
    ):
        if logger:
            logger.warning(
                '拒绝登记过宽的工作目录（避免整夹重命名上层文件夹）："{}"'.format(norm)
            )
        return
    workflow_context.state.register_work_root(norm)


def _register_work_root_preferred_name(work_root: str, preferred_basename: str):
    if (
        work_root
        and preferred_basename
        and (
            _is_registered_work_root(work_root)
            or not _is_container_or_library_root(work_root)
        )
    ):
        workflow_context.state.set_preferred_work_root_name(
            work_root, preferred_basename,
        )


def _preferred_work_root_basename(work_path: str) -> str:
    work_norm = os.path.normpath(work_path)
    preferred = workflow_context.state.preferred_work_root_name(work_norm)
    if preferred:
        return preferred
    return os.path.basename(work_norm.rstrip(' \\'))


def _is_drive_or_shallow_root(path: str) -> bool:
    """盘符根或盘符下仅一层目录（如 D:\\下载）不可当作作品夹整夹改名。"""
    if not path:
        return True
    norm = os.path.normpath(path)
    _drive, tail = os.path.splitdrive(norm)
    tail = tail.lstrip('\\/')
    if not tail:
        return True
    parts = [part for part in re.split(r'[\\/]', tail) if part]
    return len(parts) <= 1


def _is_container_or_library_root(path: str | None) -> bool:
    """音声库/资源库/回收站根，或过浅的盘符目录，禁止整夹重命名。"""
    if not path:
        return True
    norm = os.path.normpath(path)
    if _is_drive_or_shallow_root(norm):
        return True
    if not conf:
        return False
    for special in (
        getattr(conf, 'output_path', None),
        getattr(conf, 'resource_path', None),
        getattr(conf, 'recycle_path', None),
    ):
        special = (special or '').strip()
        if not special:
            continue
        if os.path.normcase(norm) == os.path.normcase(os.path.normpath(special)):
            return True
    return False


def _is_recoverable_generated_work_root(path: str | None) -> bool:
    """识别重启后遗留在盘符根目录下、可安全恢复登记的程序作品夹。"""
    if not path:
        return False
    root = os.path.normpath(path)
    if not _is_drive_or_shallow_root(root) or not _has_extracted_content(root):
        return False
    match = re.fullmatch(r'(.+)_pk(?:_\d+)?', os.path.basename(root), re.IGNORECASE)
    if not match:
        return False

    generated_stem = match.group(1)
    # 解压时若从包内识别到 RJ，会生成「原文件名 + RJxxxxxx_pk」。
    if file_ops.has_prefixed_rj_in_text(generated_stem):
        return True

    parent = os.path.dirname(root)
    try:
        siblings = os.listdir(parent)
    except OSError:
        return False
    generated_key = os.path.normcase(generated_stem)
    for name in siblings:
        sibling = os.path.join(parent, name)
        if not os.path.isfile(sibling):
            continue
        source_stem = os.path.splitext(name)[0]
        source_key = os.path.normcase(source_stem)
        if generated_key == source_key:
            remainder = ''
        elif generated_key.startswith(source_key):
            remainder = generated_stem[len(source_stem):]
        else:
            continue
        if remainder and not re.fullmatch(r'RJ\d{6,12}', remainder, re.IGNORECASE):
            continue
        try:
            if file_ops.probe_archive(sibling).is_candidate:
                return True
        except (OSError, ValueError):
            continue
    return False


def _narrow_rename_root(rename_root: str | None, current_path: str | None) -> str | None:
    """若扫描根过宽（如下载目录），收窄到 timeline 路径下的直接子作品夹。"""
    if not rename_root:
        return None
    root = os.path.normpath(rename_root)
    # 盘符根目录下一级通常是下载容器，但也可能是程序从根目录压缩包
    # 刚解出的真实作品夹。仅放行流程已精确登记的根，保留普通浅层目录保护。
    if _is_registered_work_root(root):
        return root
    is_container = _is_container_or_library_root(root)
    if is_container and _is_recoverable_generated_work_root(root):
        _register_work_root(root, trusted=True)
        if logger:
            logger.info('已恢复登记现有作品目录："{}"'.format(root))
        return root
    if not is_container:
        return root
    if not current_path:
        return None
    cur = os.path.normpath(current_path)
    if not file_ops.is_path_under(root, cur):
        return None
    try:
        rel = os.path.relpath(cur, root)
    except ValueError:
        return None
    parts = [part for part in re.split(r'[\\/]', rel) if part and part != '.']
    if not parts:
        return None
    candidate = os.path.join(root, parts[0])
    if file_ops.is_dir_path(candidate) and not _is_container_or_library_root(candidate):
        return candidate
    return None


def _remap_path_under_root(path: str, old_root: str, new_root: str) -> str:
    if not path or not old_root:
        return path
    path_norm = os.path.normpath(path)
    old_norm = os.path.normpath(old_root)
    new_norm = os.path.normpath(new_root)
    if os.path.normcase(path_norm) == os.path.normcase(old_norm):
        return new_norm
    if file_ops.is_path_under(old_norm, path_norm):
        rel = os.path.relpath(path_norm, old_norm)
        if rel == '.':
            return new_norm
        return os.path.join(new_norm, rel)
    return path


def _unregister_work_root(path: str):
    workflow_context.state.unregister_work_root(path)


def _remap_work_root(old_root: str, new_root: str):
    """作品夹移入音声库/资源库后，同步更新工作目录登记与所有 timeline 路径。"""
    old_norm = os.path.normpath(old_root)
    new_norm = os.path.normpath(new_root)
    if os.path.normcase(old_norm) == os.path.normcase(new_norm):
        return
    workflow_context.state.remap_work_root(old_norm, new_norm)
    for timeline in timelines:
        for record in timeline.records:
            for archive in (record.input_file, record.output_file):
                if archive and getattr(archive, 'path', None):
                    archive.path = _remap_path_under_root(archive.path, old_norm, new_norm)


def _locate_relocated_work_root(target_path: str) -> str | None:
    """就地作品夹已移走或重命名时，在音声库/资源库中按文件夹名或 RJ 号定位。"""
    if not target_path:
        return None
    if file_ops.is_dir_path(target_path):
        return target_path
    basename = os.path.basename(os.path.normpath(target_path.rstrip(' \\'))).rstrip(' .')
    if not basename:
        return None
    rj = file_ops.parse_rj_code(basename)
    search_roots = []
    if conf and conf.output_path:
        search_roots.append(os.path.normpath(conf.output_path))
    resource_root = _resource_library_path()
    if resource_root:
        search_roots.append(os.path.normpath(resource_root))
    for root in search_roots:
        if not file_ops.is_dir_path(root):
            continue
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            candidate = os.path.join(root, name)
            if not file_ops.is_dir_path(candidate):
                continue
            if os.path.normcase(name) == os.path.normcase(basename):
                return candidate
            if rj and file_ops.parse_rj_code(name) == rj:
                return candidate
        for name in names:
            if not name.startswith(basename):
                continue
            candidate = os.path.join(root, name)
            if file_ops.is_dir_path(candidate):
                return candidate
    return None


def _flatten_work_root(root: str) -> str:
    if not root or not file_ops.is_dir_path(root):
        return root
    try:
        new_root = file_ops.flatten_wrapper_dirs(root)
    except Exception as err:
        logger.error(f'拍平套娃文件夹失败: {root}: {err}')
        return root
    if os.path.normcase(os.path.normpath(new_root)) != os.path.normcase(os.path.normpath(root)):
        _remap_work_root(root, new_root)
        root = new_root
    file_ops.cleanup_covered_extract_junk(new_root)
    return new_root


def _under_work_root(path: str | None) -> str | None:
    """返回 path 所属的就地工作目录（若在某个已登记的工作目录之下）。

    同时命中多个登记根时取最深（最具体）的一个，避免误用过宽的上层目录。
    """
    if not path:
        return None
    path_norm = os.path.normpath(path)
    best = None
    best_len = -1
    for root in _work_roots:
        if not file_ops.is_path_under(root, path_norm):
            continue
        root_norm = os.path.normpath(root)
        length = len(os.path.normcase(root_norm))
        if length > best_len:
            best = root_norm
            best_len = length
    return best


def Log_AOP(func):
    def wrapper(timeline):
        input = timeline.get_current_path()
        output = func(timeline)
        logger.info(' [{}]：  [{}] -> [{}]'.format(func.__name__, input, output))
        return output

    return wrapper


def Timeline_AOP(func):
    def wrapper(timeline):
        input = timeline.get_current_record().output_file
        output_path = func(timeline)
        if not output_path:
            # 如果返回None，则不进行任何操作
            return
        ops = func.__name__
        if ops == 'pre_filter':
            output = output_path
        else:
            output = Archive(output_path)
        extend(output, input)
        record = Record(input, ops, output)
        timeline.add_record(record)
        return output_path

    return wrapper


# loop of unzip
# progress:
# 0.find_zip
# 0.1.pre_filter
# 0.2.unzip
# 0.3.unnest
# 1.archive / insert_rj
# 2.post_filter
# 3.rename

_STEP_SUCCESS_OPS = {
    'unzip': 'unnest',
    'archive': 'archive',
    'insert_rj': 'insert_rj',
    'filter': ('post_filter', 'post_filter_skip'),
    'rename': 'rename',
    'convert_audio': 'convert_audio',
    'tag_audio': 'tag_audio',
}


def _timeline_pending_zip(timeline: Timeline) -> Zip | None:
    """返回当前应展示、备注或重试的压缩包对象。"""
    record = timeline.get_current_record()
    zip_obj = record.output_file
    if isinstance(zip_obj, Zip) and record.ops in ('find_zip', 'unzip_failed', 'pre_filter'):
        return zip_obj
    for rec in reversed(timeline.records):
        if rec.ops in ('unzip_failed', 'find_zip') and isinstance(rec.output_file, Zip):
            return rec.output_file
    if isinstance(zip_obj, Zip):
        return zip_obj
    return None


def _timeline_manual_7z_waiting(timeline: Timeline) -> bool:
    """特殊 7z 已识别但尚未通过备注提供密码。"""
    pending = _timeline_pending_zip(timeline)
    if not isinstance(pending, Zip):
        return False
    return pending.requires_manual_password() and not (pending.note or '').strip()


def _timeline_manual_7z_status_detail(timeline: Timeline) -> str:
    pending = _timeline_pending_zip(timeline)
    if not isinstance(pending, Zip):
        return ''
    return Zip.format_manual_7z_status_detail(
        pending.path,
        pending.manual_7z_probe_info(),
    )


def _timeline_input_label(timeline) -> str:
    """任务列表「输入」列：外层文件名；套娃内层时附带当前内层压缩包。"""
    if not timeline.records:
        return ''
    first = timeline.records[0].input_file
    outer_name = ''
    if first and getattr(first, 'name', None):
        outer_name = first.name
    pending = _timeline_pending_zip(timeline)
    if pending and _is_nested_archive(pending) and outer_name and pending.name != outer_name:
        return f'{outer_name} → {pending.name}'
    if outer_name:
        return outer_name
    path = timeline.get_current_path()
    if path:
        work = _resolve_task_work_root(path)
        if work:
            return _preferred_work_root_basename(work)
    return ''


def _timeline_step_failed(timeline: Timeline) -> bool:
    ops = timeline.get_current_record().ops
    return ops == 'unzip_failed' or ops == 'rename_duplicate' or ops.endswith('_failed')


def _timeline_step_succeeded(timeline: Timeline, step: str) -> bool:
    if _timeline_step_failed(timeline):
        return False
    expected = _STEP_SUCCESS_OPS.get(step)
    if not expected:
        return False
    ops = timeline.get_current_record().ops
    if isinstance(expected, tuple):
        return ops in expected
    return ops == expected


def _work_root_key(path: str | None) -> str | None:
    if not path:
        return None
    return os.path.normcase(os.path.normpath(path))


def _pending_unzip_under_work_root(work_root: str | None) -> bool:
    """作品目录下是否仍有待解压或解压失败的内层任务。"""
    root_key = _work_root_key(work_root)
    if not root_key:
        return False
    for timeline in timelines:
        ops = timeline.get_current_record().ops
        if ops not in ('find_zip', 'unzip_failed'):
            continue
        if ops == 'unzip_failed':
            archive = timeline.get_current_record().output_file
            ext = getattr(archive, 'extension', None) if archive else None
            # 改后缀误判（.txt/.mp3 等）不应阻塞 RJ 识别与重命名
            if ext and file_ops.is_disguised_archive_extension(ext):
                continue
        tl_root = _resolve_task_work_root(timeline.get_current_path())
        if _work_root_key(tl_root) == root_key:
            return True
    return False


def _prune_successful_timelines(step: str, *, succeeded_roots: set[str] | None = None):
    """移除本步骤已成功的时间线；失败或未完成则保留。"""
    remaining = []
    for timeline in timelines:
        if _timeline_step_succeeded(timeline, step):
            continue
        # 未识别 RJ 而移入资源库的任务已彻底完成（不会再进入后续步骤），
        # 一并从任务队列中移除，避免成功解压后仍残留占位。
        if not _timeline_step_failed(timeline) and _is_in_resource_library(timeline.get_current_path()):
            continue
        if succeeded_roots and step == 'rename' and not _timeline_step_failed(timeline):
            root = _resolve_task_work_root(timeline.get_current_path())
            if root and os.path.normcase(root) in succeeded_roots:
                continue
        remaining.append(timeline)
    if len(remaining) != len(timelines):
        timelines[:] = remaining
        if progress_ui != 'not initialized':
            progress_ui.add2lis(timelines)
    if not timelines:
        already_add.clear()
        archive_registry.clear()


def _unzip_io_paths() -> list[str]:
    paths = []
    if conf:
        paths.extend([conf.output_path, conf.recycle_path])
        if getattr(conf, 'resource_path', ''):
            paths.append(conf.resource_path)
    for timeline in timelines:
        record = timeline.get_current_record()
        if record.input_file and record.input_file.path:
            paths.append(record.input_file.path)
        if record.output_file and record.output_file.path:
            paths.append(record.output_file.path)
    return paths


def _start_unzip_disk_monitor():
    drives = disk_io_monitor.drives_from_paths(*_unzip_io_paths())
    monitor = disk_io_monitor.DiskSpeedMonitor(drives)
    if hasattr(progress_ui, 'setup_disk_speed_panel'):
        progress_ui.setup_disk_speed_panel(drives)
    if hasattr(progress_ui, 'update_disk_speed_stats'):
        monitor.start(progress_ui.update_disk_speed_stats)
    return monitor


def _audio_io_paths() -> list[str]:
    paths = []
    if conf:
        paths.append(conf.output_path)
        if getattr(conf, 'resource_path', ''):
            paths.append(conf.resource_path)
    for timeline, root in _iter_unique_audio_work_roots():
        paths.append(root)
    return paths


def _start_audio_disk_monitor():
    drives = disk_io_monitor.drives_from_paths(*_audio_io_paths())
    monitor = disk_io_monitor.DiskSpeedMonitor(drives)
    if hasattr(progress_ui, 'setup_disk_speed_panel'):
        progress_ui.setup_disk_speed_panel(drives)
    if hasattr(progress_ui, 'update_disk_speed_stats'):
        monitor.start(progress_ui.update_disk_speed_stats)
    return monitor


def _stop_disk_monitor(monitor):
    if monitor is not None:
        monitor.stop()
    if hasattr(progress_ui, 'clear_disk_speed_panel'):
        progress_ui.clear_disk_speed_panel()


def _merge_zip_passwords(zip_obj: Zip, extra_passwords: list):
    if isinstance(zip_obj, Zip) and zip_obj.requires_manual_password():
        return
    for pw in extra_passwords:
        if pw and pw not in zip_obj.pw_list:
            zip_obj.pw_list.append(pw)


def _prepare_zip_for_unzip(zip_obj: Zip):
    """解压前合并完整密码库；套娃内层/加密包强制重新验证密码。"""
    if not isinstance(zip_obj, Zip):
        return
    if zip_obj.requires_manual_password():
        return
    library = password.get_str_passwords(password.sort_passwords(passwords))
    prior = list(zip_obj.pw_list)
    verified = zip_obj.verified_password() if zip_obj.is_namelist_current() else ''
    ordered: list[str] = []
    # 任务备注/RJ 号与已验证密码是针对当前压缩包的明确候选，
    # 依然优先；其余密码库项严格使用“添加日期、命中次数”顺序。
    explicit = [verified, zip_obj.note, zip_obj.RJ_code]
    remaining = [pw for pw in prior if pw not in library]
    for pw in explicit + library + remaining:
        if pw and pw not in ordered:
            ordered.append(pw)
    changed = ordered != zip_obj.pw_list
    zip_obj.pw_list = ordered
    if (
        changed
        or zip_obj.is_encrypted()
        or zip_obj.compression_ratio_info.get('encrypted')
        or _is_nested_archive(zip_obj)
    ):
        zip_obj.invalidate_namelist_scan()


def _refresh_zip_volumes(zip_obj: Zip, source_path: str | None = None):
    """解压/重试前重新聚组，并纠正分卷临时改名还原后的 stale 路径。"""
    from volume.rename import (
        current_path_for_drag,
        find_volume_anchor_in_directory,
        resolve_volume_paths_on_disk,
    )

    if zip_obj.volumes and len(zip_obj.volumes) > 1:
        synced = resolve_volume_paths_on_disk(list(zip_obj.volumes))
        if synced != zip_obj.volumes:
            zip_obj.volumes = synced
            zip_obj.path = synced[0]
            zip_obj.invalidate_namelist_scan()

    anchor = None
    for candidate in (
        source_path,
        zip_obj.path,
        *((zip_obj.volumes or [])[:1]),
        *((zip_obj.volumes or [])[1:]),
    ):
        if not candidate:
            continue
        resolved = current_path_for_drag(candidate) or candidate
        if os.path.isfile(resolved):
            anchor = resolved
            break
    if not anchor and zip_obj.volumes:
        anchor = find_volume_anchor_in_directory(
            os.path.dirname(os.path.abspath(zip_obj.volumes[0])),
        )
    if not anchor or not os.path.isfile(anchor):
        return

    fresh = file_ops.resolve_volume_archives(anchor)
    if not fresh:
        return
    from volume.resolver import is_complete_volume_group
    if not is_complete_volume_group(fresh):
        return
    if (zip_obj.volumes
            and tuple(sorted(fresh)) == tuple(sorted(zip_obj.volumes))
            and all(os.path.isfile(path) for path in zip_obj.volumes)):
        return
    zip_obj.volumes = fresh
    zip_obj.path = fresh[0]
    zip_obj.invalidate_namelist_scan()


def _unzip_task_priority(timeline: Timeline) -> tuple[int, str]:
    """完整分卷组优先；残缺组靠后，等其它任务可能释出首卷。"""
    record = timeline.get_current_record()
    zip_obj = record.output_file
    path_key = getattr(zip_obj, 'path', '') or ''
    if isinstance(zip_obj, Zip) and zip_obj.volumes and len(zip_obj.volumes) > 1:
        from volume.resolver import is_complete_volume_group
        if is_complete_volume_group(zip_obj.volumes):
            return (0, path_key)
        return (2, path_key)
    return (1, path_key)


def requeue_unzip_failure(timeline: Timeline) -> bool:
    """
    把因无密码/密码错误而失败的解压任务重新标记为待处理（find_zip），
    并补充当前最新密码库，供下次解压时重试。
    避免用户只能清空队列、重新拖入文件才能重试（丢失备注等已录入信息）。
    """
    record = timeline.get_current_record()
    if record.ops == 'find_zip' and _timeline_targets_outer_zip(timeline):
        return _promote_outer_timeline_to_inner(timeline)
    if record.ops != 'unzip_failed':
        return False
    zip_obj = record.output_file
    if isinstance(zip_obj, Zip) and zip_obj.requires_manual_password():
        if not (zip_obj.note or '').strip():
            return False
    if isinstance(zip_obj, Zip) and not _is_nested_archive(zip_obj):
        if _should_resume_nested_only(zip_obj) or archive_registry.is_unzipped(
            zip_obj.path, zip_obj.volumes,
        ):
            archive_registry.mark_unzipped(zip_obj.path, zip_obj.volumes)
            if _promote_outer_timeline_to_inner(timeline):
                return True
            return _advance_past_outer_layer(
                timeline, zip_obj, _resolve_extracted_work_root(zip_obj),
            )
    if not _is_likely_retryable_unzip_failure(zip_obj):
        if logger:
            logger.info(
                '跳过非压缩包误判项，不再重试："{}"'.format(
                    os.path.normpath(getattr(zip_obj, 'path', '') or ''),
                ),
            )
        return False
    _mark_timeline_unzipped_layers(timeline)
    outer = _timeline_outer_zip(timeline)
    if outer:
        archive_registry.mark_unzipped(outer.path, outer.volumes)
    if isinstance(zip_obj, Zip):
        _merge_zip_passwords(
            zip_obj,
            password.get_str_passwords(password.sort_passwords(passwords)),
        )
        _refresh_zip_volumes(zip_obj)
        zip_obj.invalidate_namelist_scan()
    timeline.add_record(Record(record.input_file, 'find_zip', zip_obj))
    return True


def _requeue_unzip_failures():
    for timeline in timelines:
        requeue_unzip_failure(timeline)


def _is_path_queued(path: str) -> bool:
    norm = os.path.normcase(os.path.normpath(path))
    for timeline in timelines:
        if not timeline.records:
            continue
        first = timeline.records[0].input_file
        if first and os.path.normcase(os.path.normpath(first.path)) == norm:
            return True
    return False


def _sync_archive_registry():
    """合并任务队列与全局 already_add，防止套娃扫描重复加入同一压缩包。"""
    archive_registry.merge_already_add(already_add)
    archive_registry.merge_already_add(_collect_already_add_from_timelines())
    archive_registry.sync_rename_registry()
    for timeline in timelines:
        _mark_timeline_unzipped_layers(timeline)
        record = timeline.get_current_record()
        zip_obj = record.output_file
        if isinstance(zip_obj, Zip):
            archive_registry.note_discovered(zip_obj.path, zip_obj.volumes)
    _prune_unzipped_volume_failures()


def _remember_unzipped_archive(zip_obj: Zip):
    if not zip_obj.volumes or len(zip_obj.volumes) < 2:
        fresh = file_ops.resolve_volume_archives(zip_obj.path)
        if not fresh:
            from volume.resolver import VolumeResolver
            fresh = VolumeResolver.peek_volumes(zip_obj.path)
        if fresh and len(fresh) >= 2:
            zip_obj.volumes = fresh
            zip_obj.path = fresh[0]
    archive_registry.mark_unzipped(zip_obj.path, zip_obj.volumes)


def _collect_already_add_from_timelines():
    """从已有解压任务回填 already_add，避免重复扫描分卷。"""
    paths: list[str] = []
    for timeline in timelines:
        record = timeline.get_current_record()
        if record.ops not in ('find_zip', 'unzip_failed'):
            continue
        zip_obj = record.output_file
        if isinstance(zip_obj, Zip) and zip_obj.volumes:
            paths.extend(zip_obj.volumes)
        elif hasattr(zip_obj, 'path') and zip_obj.path:
            paths.append(zip_obj.path)
    return paths


def prepare_archive_queue():
    """归档前登记工作目录，并恢复重启前已解压完成的浅层作品夹。"""
    for timeline in timelines:
        record = timeline.get_current_record()
        path = timeline.get_current_path()
        if not path:
            continue
        root = _resolve_task_work_root(path)
        if not root or not file_ops.is_dir_path(root):
            continue
        recoverable = _is_recoverable_generated_work_root(root)
        if record.ops != 'create_timeline' and not recoverable:
            continue
        _register_work_root(root, trusted=recoverable)
        if recoverable and logger:
            logger.info('已恢复登记现有作品目录："{}"'.format(os.path.normpath(root)))


def scan_work_queue():
    """启动解压前：将工作区拖入项扫描为可解压任务。"""
    queued = [
        t for t in timelines
        if t.get_current_record().ops in ('create_timeline', 'scan_failed')
    ]
    if not queued:
        return 0

    scan_already_add = _collect_already_add_from_timelines()
    new_timelines: list[Timeline] = []
    removed: list[Timeline] = []
    str_passwords = password.get_str_passwords(password.sort_passwords(passwords))
    claimed_volume_groups: set[tuple[str, ...]] = set()
    claimed_volume_identities: set[tuple] = set()

    for timeline in queued:
        source = timeline.records[0].input_file.path
        queued_archive = timeline.records[0].input_file
        _prepare_user_rescan(source)
        zip_list: list = []
        unresolved_list: list = []
        unzipper.find_zip(
            source, str_passwords, conf.del_after_unzip, scan_already_add,
            zip_list, unresolved_list=unresolved_list, collect_unresolved=True,
        )
        zip_list[:] = _filter_already_extracted_archives(
            zip_list, str_passwords, scan_already_add,
            allow_reextract=True,
        )
        unresolved_list[:] = _filter_volume_sibling_unresolved(zip_list, unresolved_list)
        if not zip_list and not unresolved_list:
            if logger:
                logger.warning(
                    '工作区项未发现可处理压缩文件："{}"'.format(
                        os.path.normpath(source),
                    ),
                )
            # 扫描失败不能移除最后一条时间线，否则空队列会被 UI 误报为“已完成”。
            if timeline.get_current_record().ops != 'scan_failed':
                timeline.add_record(Record(queued_archive, 'scan_failed', queued_archive))
            continue

        def _apply_queued_note(zip_obj):
            if getattr(queued_archive, 'note', None):
                zip_obj.set_note(queued_archive.note)
            elif queued_archive.RJ_code and hasattr(zip_obj, 'pw_list'):
                if queued_archive.RJ_code not in zip_obj.pw_list:
                    zip_obj.pw_list.insert(0, queued_archive.RJ_code)
                    zip_obj.invalidate_namelist_scan()

        for zip_obj in zip_list:
            if zip_obj.volumes and len(zip_obj.volumes) > 1:
                vol_key = _volume_group_key(zip_obj.volumes)
                identity = _volume_task_identity(zip_obj, source)
                if vol_key in claimed_volume_groups:
                    continue
                if identity and identity in claimed_volume_identities:
                    continue
                claimed_volume_groups.add(vol_key)
                if identity:
                    claimed_volume_identities.add(identity)
            archive_registry.forget(zip_obj.path, zip_obj.volumes)
            _apply_queued_note(zip_obj)
            # 同一拖入目录可能发现大量压缩包。复用原始 Archive，避免为每个
            # 压缩包再次递归 os.walk 整个目录并复制一份 file_list。
            new_timelines.append(Timeline(queued_archive, 'find_zip', zip_obj))
        for zip_obj in unresolved_list:
            _apply_queued_note(zip_obj)
            new_timelines.append(Timeline(queued_archive, 'unzip_failed', zip_obj))
        removed.append(timeline)

    for timeline in removed:
        timelines.remove(timeline)
    timelines.extend(new_timelines)
    if progress_ui != 'not initialized':
        progress_ui.add2lis(timelines)
    return len(new_timelines)


def unzip_loop():
    scan_work_queue()
    _sync_archive_registry()
    _requeue_unzip_failures()
    _requeue_stuck_outer_timelines()
    monitor = _start_unzip_disk_monitor()
    unzip_round = 0
    try:
        while True:
            unzip_round += 1
            if unzip_round > _MAX_UNZIP_ROUNDS:
                if logger:
                    logger.error(
                        '套娃解压轮次超过 {} 次，已中止以防重复解压；'
                        '请检查是否有压缩包反复被识别或密码错误任务未清除'.format(
                            _MAX_UNZIP_ROUNDS,
                        )
                    )
                break
            pending = [t for t in timelines if t.records[-1].ops == 'find_zip']
            if not pending:
                break
            pending.sort(key=_unzip_task_priority)
            for timeline in pending:
                active_zip = _timeline_pending_zip(timeline)
                try:
                    _process_unzip_timeline(timeline)
                except Exception as err:
                    if isinstance(active_zip, Zip) and _recover_outer_with_pending_inner(timeline, active_zip):
                        if logger:
                            logger.info(
                                '外层解压遇内层加密项报错，已转入内层："{}"'.format(
                                    os.path.normpath(active_zip.path or ''),
                                ),
                            )
                        continue
                    failed_zip = active_zip if isinstance(active_zip, Zip) else timeline.get_current_record().output_file
                    if isinstance(failed_zip, Zip):
                        timeline.add_record(Record(failed_zip, 'unzip_failed', failed_zip))
                    else:
                        record = timeline.get_current_record()
                        timeline.add_record(Record(record.output_file, 'unzip_failed', record.output_file))
                    logger.error(
                        '处理解压任务异常，已标记失败并继续其余任务: {}: {}'.format(
                            getattr(failed_zip, 'path', timeline.get_current_path()),
                            err,
                        )
                    )
                    logger.debug(traceback.format_exc())
            progress_ui.add2lis(timelines)
        password.write_password(password.sort_passwords(passwords))
        # 全部套娃解压完成后再统一拍平，避免内层 zip 尚未解压时过早处理
        # 解压失败的任务仍停留在原压缩包路径（不在音声库内），跳过以避免无意义的告警
        seen_roots = set()
        for timeline in timelines:
            if _timeline_step_failed(timeline):
                continue
            root = _work_root_path(timeline.get_current_path())
            if root and root not in seen_roots and file_ops.is_dir_path(root):
                seen_roots.add(root)
                _flatten_work_root(root)
    finally:
        _stop_disk_monitor(monitor)


def _volume_group_key(volumes: list[str]) -> tuple[str, ...]:
    return tuple(sorted(os.path.normcase(v) for v in volumes))


def _zip_volume_identities(zip_obj: Zip, source_path: str | None = None) -> set[tuple]:
    identities: set[tuple] = set()
    identity = _volume_task_identity(zip_obj, source_path)
    if identity:
        identities.add(identity)
    if zip_obj.volumes and len(zip_obj.volumes) > 1:
        try:
            from volume.collect import volume_group_identity
            group_identity = volume_group_identity(zip_obj.volumes)
            if group_identity:
                identities.add(group_identity)
        except ImportError:
            pass
    return identities


def _filter_volume_sibling_unresolved(zip_list: list, unresolved_list: list) -> list:
    """首卷已在 zip_list 时，剔除同组分卷的失败占位项。"""
    claimed_paths: set[str] = set()
    claimed_identities: set[tuple] = set()
    for zip_obj in zip_list:
        if not isinstance(zip_obj, Zip):
            continue
        if zip_obj.volumes:
            claimed_paths.update(os.path.normcase(p) for p in zip_obj.volumes)
        claimed_identities.update(_zip_volume_identities(zip_obj))

    filtered: list = []
    for zip_obj in unresolved_list:
        if not isinstance(zip_obj, Zip):
            filtered.append(zip_obj)
            continue
        path_norm = os.path.normcase(zip_obj.path or '')
        if path_norm and path_norm in claimed_paths:
            continue
        if zip_obj.volumes:
            if any(os.path.normcase(p) in claimed_paths for p in zip_obj.volumes):
                continue
        if _zip_volume_identities(zip_obj) & claimed_identities:
            continue
        filtered.append(zip_obj)
    return filtered


def _dismiss_volume_sibling_failures(zip_obj: Zip, source_path: str | None = None) -> int:
    """分卷组首卷解压成功后，移除同组仍留在队列中的失败项。"""
    if not isinstance(zip_obj, Zip):
        return 0
    if not zip_obj.volumes or len(zip_obj.volumes) < 2:
        fresh = file_ops.resolve_volume_archives(zip_obj.path)
        if not fresh:
            from volume.resolver import VolumeResolver
            fresh = VolumeResolver.peek_volumes(zip_obj.path)
        if fresh and len(fresh) >= 2:
            zip_obj.volumes = fresh
            zip_obj.path = fresh[0]
    identities = _zip_volume_identities(zip_obj, source_path)
    vol_paths: set[str] = set()
    if zip_obj.volumes:
        vol_paths.update(os.path.normcase(p) for p in zip_obj.volumes)
    if zip_obj.path:
        vol_paths.add(os.path.normcase(zip_obj.path))

    removed: list[Timeline] = []
    for timeline in timelines:
        record = timeline.get_current_record()
        if record.ops != 'unzip_failed':
            continue
        other = record.output_file
        if not isinstance(other, Zip):
            continue
        other_path = os.path.normcase(other.path or '')
        if other_path and other_path in vol_paths:
            removed.append(timeline)
            continue
        if _zip_volume_identities(other) & identities:
            removed.append(timeline)

    for timeline in removed:
        timelines.remove(timeline)
    if removed and logger:
        logger.info(
            '分卷组已解压，已清除 {} 个分卷失败项'.format(len(removed)),
        )
    if removed and progress_ui != 'not initialized':
        progress_ui.add2lis(timelines)
    return len(removed)


def _prune_unzipped_volume_failures():
    """回填：分卷组已解压但兄弟分卷仍标记 unzip_failed 时自动清除。"""
    pruned = 0
    for timeline in list(timelines):
        record = timeline.get_current_record()
        if record.ops != 'unzip_failed':
            continue
        zip_obj = record.output_file
        if not isinstance(zip_obj, Zip):
            continue
        if archive_registry.is_unzipped(zip_obj.path, zip_obj.volumes):
            timelines.remove(timeline)
            pruned += 1
            continue
        if archive_registry.is_volume_part_unzipped(zip_obj.path):
            timelines.remove(timeline)
            pruned += 1
    if pruned and progress_ui != 'not initialized':
        progress_ui.add2lis(timelines)
    return pruned


def _volume_task_identity(zip_obj: Zip, source_path: str | None = None) -> tuple | None:
    from volume import collect as volume_collect
    if source_path:
        identity = volume_collect.volume_group_identity_for_anchor(source_path)
        if identity:
            return identity
    if isinstance(zip_obj, Zip) and zip_obj.volumes and len(zip_obj.volumes) > 1:
        return volume_collect.volume_group_identity(zip_obj.volumes)
    return None


def _timeline_volume_work_root(timeline: Timeline) -> str | None:
    for record in reversed(timeline.records):
        if record.ops != 'unzip' or not record.output_file:
            continue
        path = getattr(record.output_file, 'path', None)
        if path and os.path.exists(path):
            return path
    return None


def _skip_duplicate_volume_unzip(timeline: Timeline) -> bool:
    """同一分卷组已由其它任务解压时，复用结果并跳过重复解压。"""
    record = timeline.get_current_record()
    zip_obj = record.output_file
    if not isinstance(zip_obj, Zip):
        return False
    source = timeline.records[0].input_file.path if timeline.records else None
    identity = _volume_task_identity(zip_obj, source)
    if not identity:
        return False
    for other in timelines:
        if other is timeline or _timeline_step_failed(other):
            continue
        other_source = other.records[0].input_file.path if other.records else None
        other_zip = None
        for item in other.records:
            if item.ops == 'find_zip' and isinstance(item.output_file, Zip):
                other_zip = item.output_file
                break
        if not other_zip:
            continue
        if _volume_task_identity(other_zip, other_source) != identity:
            continue
        work_root = _timeline_volume_work_root(other)
        if not work_root:
            continue
        timeline.add_record(Record(record.output_file, 'unzip', Archive(work_root)))
        if logger:
            logger.info(
                '分卷组已由其它任务解压，跳过重复："{}"'.format(
                    os.path.normpath(source or zip_obj.path),
                ),
            )
        return True
    return False


def _resume_inner_from_timeline_records(
    timeline: Timeline,
    work_root: str | None = None,
) -> Zip | None:
    """从时间线历史中取尚未解压成功的内层压缩包。"""
    work_norm = os.path.normcase(os.path.normpath(work_root)) if work_root else None
    succeeded_paths: set[str] = set()
    succeeded_identities: set[tuple] = set()
    for record in timeline.records:
        if record.ops != 'unzip' or not isinstance(record.input_file, Zip):
            continue
        succeeded = record.input_file
        for path in (succeeded.volumes or [succeeded.path]):
            if path:
                succeeded_paths.add(os.path.normcase(os.path.normpath(path)))
        succeeded_identities.update(_zip_volume_identities(succeeded))

    for record in reversed(timeline.records):
        zip_obj = None
        if record.ops in ('unzip_failed', 'find_zip') and isinstance(record.output_file, Zip):
            zip_obj = record.output_file
        if not zip_obj:
            continue
        is_inner = _is_nested_archive(zip_obj)
        if not is_inner and work_norm and zip_obj.path:
            inner_norm = os.path.normcase(os.path.normpath(zip_obj.path))
            is_inner = inner_norm.startswith(work_norm + os.sep) or inner_norm == work_norm
        if not is_inner:
            continue
        candidate_paths = {
            os.path.normcase(os.path.normpath(path))
            for path in (zip_obj.volumes or [zip_obj.path])
            if path
        }
        if candidate_paths & succeeded_paths:
            continue
        if _zip_volume_identities(zip_obj) & succeeded_identities:
            continue
        if not archive_registry.is_unzipped(zip_obj.path, zip_obj.volumes):
            return zip_obj
    return None


def _resume_inner_from_registry(
    work_root: str | None,
    nested_passwords: list[str],
) -> Zip | None:
    """从注册表恢复作品目录内已发现、尚未解压的内层压缩包。"""
    if not work_root:
        return None
    for inner_path in archive_registry.pending_discovered_under(work_root):
        if not os.path.isfile(inner_path):
            continue
        if archive_registry.is_unzipped(inner_path):
            continue
        inner = Zip(inner_path, nested_passwords, conf.del_after_reunzip)
        _merge_zip_passwords(inner, nested_passwords)
        return inner
    return None


def _timeline_targets_outer_zip(timeline: Timeline) -> bool:
    pending = _timeline_pending_zip(timeline)
    return isinstance(pending, Zip) and not _is_nested_archive(pending)


def _promote_outer_timeline_to_inner(timeline: Timeline) -> bool:
    """外层已解压但时间线仍停在外层时，推进到内层待解压。"""
    record = timeline.get_current_record()
    zip_obj = record.output_file
    if not isinstance(zip_obj, Zip) or _is_nested_archive(zip_obj):
        return False
    if not (
        _should_resume_nested_only(zip_obj)
        or archive_registry.is_unzipped(zip_obj.path, zip_obj.volumes)
    ):
        return False
    work_root = _resolve_extracted_work_root(zip_obj)
    if not work_root:
        return False
    _register_work_root(work_root)
    before = timeline.get_current_record()
    _enqueue_nested_archives(timeline, work_root, zip_obj)
    after = timeline.get_current_record()
    if after is before:
        return False
    if after.ops == 'find_zip' and isinstance(after.output_file, Zip) and _is_nested_archive(after.output_file):
        return True
    return False


def _requeue_stuck_outer_timelines():
    for timeline in timelines:
        record = timeline.get_current_record()
        if record.ops not in ('find_zip', 'unnest'):
            continue
        if record.ops == 'find_zip' and _timeline_targets_outer_zip(timeline):
            _promote_outer_timeline_to_inner(timeline)
            continue
        if record.ops == 'unnest':
            parent_zip = _timeline_outer_zip(timeline)
            if not parent_zip:
                for rec in reversed(timeline.records):
                    if isinstance(rec.output_file, Zip) and not _is_nested_archive(rec.output_file):
                        parent_zip = rec.output_file
                        break
            work_root = _normalize_nested_scan_root(
                timeline.get_current_path(), parent_zip,
            ) or _work_root_path(timeline.get_current_path())
            if not work_root:
                continue
            _register_work_root(work_root)
            _enqueue_nested_archives(timeline, work_root, parent_zip)


def _enqueue_nested_archives(
    timeline: Timeline,
    new_path: str | None,
    parent_zip: Zip | None,
    *,
    incremental_roots: list[str] | None = None,
):
    new_path = _normalize_nested_scan_root(new_path, parent_zip)
    if not new_path:
        new_path = _normalize_nested_scan_root(timeline.get_current_path(), parent_zip)
    if not new_path:
        return

    zip_list: list = []
    nested_passwords = password.get_str_passwords(password.sort_passwords(passwords))
    if isinstance(parent_zip, Zip) and parent_zip.pw_list:
        nested_passwords = [
            pw for pw in parent_zip.pw_list if pw
        ] + [pw for pw in nested_passwords if pw not in parent_zip.pw_list]
    nested_unresolved: list = []
    scan_roots = [new_path] if incremental_roots is None else [
        path for path in incremental_roots
        if path and os.path.exists(path)
    ]
    for scan_root in scan_roots:
        unzipper.find_zip(
            scan_root, nested_passwords, conf.del_after_reunzip, already_add,
            zip_list, depth=1, unresolved_list=nested_unresolved,
            collect_unresolved=False, unresolved_limit=NESTED_UNRESOLVED_LIMIT,
        )
    zip_list[:] = [
        item for item in zip_list
        if not any(item.name.endswith(ext) for ext in conf.blacklist)
        and not _zip_path_already_queued(item.path)
    ]

    if not zip_list:
        pending_inner = _resume_inner_from_timeline_records(timeline, new_path)
        if pending_inner and not _zip_path_already_queued(pending_inner.path):
            zip_list.append(pending_inner)
            if logger:
                logger.info(
                    '从任务历史恢复待解压内层："{}"'.format(
                        os.path.normpath(pending_inner.path or ''),
                    ),
                )
    if not zip_list:
        registry_inner = _resume_inner_from_registry(new_path, nested_passwords)
        if registry_inner and not _zip_path_already_queued(registry_inner.path):
            zip_list.append(registry_inner)
            if logger:
                logger.info(
                    '从注册表恢复待解压内层："{}"'.format(
                        os.path.normpath(registry_inner.path or ''),
                    ),
                )
    if not zip_list:
        for inner in _list_pending_archives_in_work_root(new_path, nested_passwords):
            if _zip_path_already_queued(inner.path):
                continue
            zip_list.append(inner)
            if logger:
                logger.info(
                    '从作品目录枚举待解压内层："{}"'.format(
                        os.path.normpath(inner.path or ''),
                    ),
                )
            break

    new_archive = timeline.get_current_record().output_file
    if not zip_list:
        if logger:
            logger.warning(
                '作品目录内未发现待解压的内层压缩包："{}"'.format(
                    os.path.normpath(new_path or ''),
                ),
            )
        return

    if logger:
        logger.info(
            '发现 {} 个待处理内层压缩包，开始入队'.format(len(zip_list)),
        )

    zip_list[0].extend(new_archive)
    timeline.add_record(Record(new_archive, 'find_zip', zip_list[0]))
    for nested in zip_list[1:]:
        nested.extend(new_archive)
        timelines.append(Timeline(new_archive, 'find_zip', nested))


def _timeline_has_unzipped_ancestor(timeline: Timeline, zip_obj: Zip) -> bool:
    for record in timeline.records:
        if record.ops != 'unzip':
            continue
        parent = record.input_file
        if isinstance(parent, Zip):
            if archive_registry.is_unzipped(parent.path, parent.volumes):
                return True
            if os.path.normcase(os.path.dirname(parent.path)) == os.path.normcase(zip_obj.father):
                return True
    return False


def _process_unzip_timeline(timeline: Timeline):
    record = timeline.get_current_record()
    zip_obj = record.output_file
    parent_zip = zip_obj if isinstance(zip_obj, Zip) else None

    if isinstance(zip_obj, Zip) and not _is_nested_archive(zip_obj):
        reextract = _timeline_requests_reextract(timeline, zip_obj)
        work_root = _resolve_extracted_work_root(zip_obj)
        if (
            not reextract
            and work_root
            and _has_extracted_content(work_root)
            and (
                _should_resume_nested_only(zip_obj)
                or archive_registry.is_unzipped(zip_obj.path, zip_obj.volumes)
            )
        ):
            archive_registry.mark_unzipped(zip_obj.path, zip_obj.volumes)
            if logger:
                logger.info(
                    '外层已解压，跳过重复解压并处理内层："{}"'.format(
                        os.path.normpath(zip_obj.path or ''),
                    ),
                )
            new_path = work_root
            if file_ops.is_dir_path(new_path):
                _register_work_root(new_path)
                new_path = _flatten_work_root(new_path)
            _enqueue_nested_archives(timeline, new_path, zip_obj)
            if _timeline_targets_outer_zip(timeline):
                if not _promote_outer_timeline_to_inner(timeline):
                    _advance_past_outer_layer(timeline, zip_obj, work_root)
            return

    if isinstance(zip_obj, Zip) and _is_nested_archive(zip_obj) and _timeline_has_unzipped_ancestor(timeline, zip_obj):
        if logger:
            logger.info(
                '套娃内层重试，跳过外层："{}"'.format(
                    os.path.normpath(zip_obj.path or ''),
                ),
            )
    if (
        not _timeline_requests_reextract(timeline, zip_obj)
        and isinstance(zip_obj, Zip)
        and archive_registry.is_unzipped(zip_obj.path, zip_obj.volumes)
    ):
        if logger:
            logger.info(
                '压缩包已解压过，跳过重复解压："{}"'.format(
                    os.path.normpath(zip_obj.path or ''),
                )
            )
        new_path = unnest(timeline) or timeline.get_current_path()
        _enqueue_nested_archives(timeline, new_path, parent_zip)
        return

    pre_filter(timeline)
    # 必须在 unzip() 执行前捕获引用：Timeline_AOP 会在成功后把 output_file
    # 替换成普通 Archive，届时再取只能拿到失去 pw_list 的对象，导致密码永远无法继承。
    # unzipper.unzip() 会原地把命中的密码写回同一个 Zip 对象的 pw_list[0]。
    active_zip = _timeline_pending_zip(timeline)
    parent_zip = timeline.get_current_record().output_file
    incremental_base = None
    incremental_before = None
    if isinstance(parent_zip, Zip):
        source = timeline.records[0].input_file.path if timeline.records else None
        _refresh_zip_volumes(parent_zip, source)
        _prepare_zip_for_unzip(parent_zip)
        # 仅套娃内层使用增量扫描。顶层、用户目录和恢复流程仍保持全量扫描，
        # 避免改变标准压缩包及历史任务的发现语义。
        if _is_nested_archive(parent_zip):
            incremental_base = _resolve_work_root_containing(parent_zip.path)
            if incremental_base and os.path.isdir(incremental_base):
                incremental_before = _snapshot_scan_tree(incremental_base)
    if _skip_duplicate_volume_unzip(timeline):
        output_path = timeline.get_current_record().output_file.path
    else:
        output_path = unzip(timeline)
    if not output_path:
        failed_zip = active_zip if isinstance(active_zip, Zip) else timeline.get_current_record().output_file
        if isinstance(failed_zip, Zip) and _recover_outer_with_pending_inner(timeline, failed_zip):
            return
        if isinstance(failed_zip, Zip):
            timeline.add_record(Record(failed_zip, 'unzip_failed', failed_zip))
            if logger:
                label = '内层' if _is_nested_archive(failed_zip) else '压缩包'
                logger.error(
                    '{}解压失败，跳过套娃继续: "{}"'.format(
                        label,
                        os.path.normpath(failed_zip.path or ''),
                    ),
                )
        return
    if isinstance(parent_zip, Zip):
        _remember_unzipped_archive(parent_zip)
        source = timeline.records[0].input_file.path if timeline.records else None
        _dismiss_volume_sibling_failures(parent_zip, source)
    new_path = unnest(timeline)
    if not new_path:
        new_path = timeline.get_current_path()
    if new_path and file_ops.is_dir_path(new_path):
        _register_work_root(new_path)
    incremental_roots = None
    if (
        incremental_base
        and new_path
        and os.path.normcase(os.path.normpath(incremental_base))
        == os.path.normcase(os.path.normpath(new_path))
    ):
        incremental_roots = _incremental_scan_roots(
            incremental_base, incremental_before,
        )
        if logger and incremental_roots is not None:
            logger.debug(
                '套娃增量扫描：{} 个新增根 [{}]'.format(
                    len(incremental_roots),
                    '],['.join(incremental_roots),
                ),
            )
    _enqueue_nested_archives(
        timeline, new_path, parent_zip, incremental_roots=incremental_roots,
    )


def _volume_stem(zip_obj) -> str | None:
    from volume.stem_index import extract_part_info
    info = extract_part_info(os.path.basename(zip_obj.path))
    return info[0] if info else None


def _intended_top_work_root(zip_obj) -> str:
    """顶层外层作品目录（带 _pk 后缀，避免与内层压缩包同名）。"""
    return _legacy_top_work_root(zip_obj) + TOP_WORK_ROOT_SUFFIX


def _legacy_top_work_root(zip_obj) -> str:
    work_root = os.path.join(zip_obj.father, zip_obj.filename)
    if zip_obj.RJ_code and zip_obj.RJ_code not in zip_obj.path:
        work_root += zip_obj.RJ_code
    return work_root


def _candidate_top_work_roots(zip_obj) -> list[str]:
    """优先新后缀目录，兼容旧版无后缀作品夹。"""
    if not isinstance(zip_obj, Zip):
        return []
    preferred = _intended_top_work_root(zip_obj)
    legacy = _legacy_top_work_root(zip_obj)
    roots = [preferred]
    if os.path.normcase(preferred) != os.path.normcase(legacy):
        roots.append(legacy)
    return roots


def _outer_staging_dirs(zip_obj: Zip) -> list[str]:
    """外层压缩包旁路暂存目录（.pk_*）。"""
    dirs: list[str] = []
    if not isinstance(zip_obj, Zip) or not zip_obj.father:
        return dirs
    try:
        for name in os.listdir(zip_obj.father):
            if not name.startswith('.pk_') or zip_obj.name not in name:
                continue
            full = os.path.join(zip_obj.father, name)
            if os.path.isdir(full):
                dirs.append(full)
    except OSError:
        pass
    return dirs


def _staging_has_usable_partial_extract(path: str | None) -> bool:
    """外层 7z 因内层加密项报错时，暂存里是否已有可继续处理的真实内层压缩包。"""
    return unzipper.work_root_has_valid_inner_archive(path)


def _promote_partial_staging_to_work_root(zip_obj: Zip, staging: str) -> str | None:
    """将外层部分解压的暂存目录提升为最终作品目录。"""
    if not isinstance(zip_obj, Zip) or not staging or not os.path.isdir(staging):
        return None
    work_root = _intended_top_work_root(zip_obj)
    base = work_root
    suffix = 1
    while os.path.exists(work_root):
        work_root = f'{base}_{suffix}'
        suffix += 1
    try:
        shutil.move(staging, work_root)
    except OSError as err:
        if logger:
            logger.warning(
                '提升外层暂存目录失败：{} -> {}: {}'.format(staging, work_root, err),
            )
        return staging
    _remap_work_root(staging, work_root)
    _register_work_root(work_root)
    if logger:
        logger.info(
            '外层部分解压已提升到作品目录："{}"'.format(os.path.normpath(work_root)),
        )
    return work_root


def _staging_unzip_path(zip_obj) -> str:
    staging = os.path.join(zip_obj.father, f'.pk_{zip_obj.name}')
    suffix = 1
    while os.path.exists(staging):
        staging = os.path.join(zip_obj.father, f'.pk_{zip_obj.name}_{suffix}')
        suffix += 1
    return staging


def _resolve_unzip_output_path(zip_obj) -> tuple[str, str | bool]:
    """返回 (解压目标路径, 合并模式)。

    合并模式：
      False              — 直接使用解压目标路径
      True               — 解压到暂存目录后合并回压缩包所在目录
      'same_name_staging' — 无后缀压缩包与作品根目录同名：解压到暂存目录，源文件保持不动
      'top_staging'      — 顶层外层：暂存解压后提升到带 _pk 后缀的作品目录
      'volume_staging'   — 分卷：暂存解压后提升到作品目录
    """
    is_nested = _is_nested_archive(zip_obj)
    if not is_nested:
        if zip_obj.volumes and len(zip_obj.volumes) > 1:
            staging = _staging_unzip_path(zip_obj)
            _register_work_root(staging, trusted=True)
            stem = _volume_stem(zip_obj) or zip_obj.filename
            _register_work_root_preferred_name(staging, stem)
            return staging, 'volume_staging'
        work_root = _intended_top_work_root(zip_obj)
        src_norm = os.path.normpath(zip_obj.path)
        work_norm = os.path.normpath(work_root)
        # 无后缀压缩包与作品根目录同名：源文件占据该路径，解压到旁路暂存目录。
        if (os.path.normcase(src_norm) == os.path.normcase(work_norm)
                and os.path.isfile(zip_obj.path)):
            staging = _staging_unzip_path(zip_obj)
            _register_work_root(staging, trusted=True)
            _register_work_root_preferred_name(staging, zip_obj.filename)
            return staging, 'same_name_staging'
        # 顶层外层一律先解压到 .pk_ 暂存，再提升到 *_pk 作品目录，避免与内层压缩包同名。
        staging = _staging_unzip_path(zip_obj)
        _register_work_root(staging, trusted=True)
        _register_work_root_preferred_name(staging, zip_obj.filename)
        return staging, 'top_staging'

    # 套娃内层一律解压到暂存目录，避免与外层已解压文件写入同一路径
    return _staging_unzip_path(zip_obj), True


def _merge_directory_contents(src_dir: str, dest_dir: str):
    if not os.path.isdir(src_dir):
        return
    mk_if_not_exit(dest_dir)
    for item in os.listdir(src_dir):
        src = os.path.join(src_dir, item)
        dest = os.path.join(dest_dir, item)
        while os.path.exists(dest):
            dest += '(1)'
        shutil.move(src, dest)
    shutil.rmtree(src_dir, ignore_errors=True)


def _move_contents_up(src_dir: str, dest_dir: str):
    if not os.path.isdir(src_dir) or not os.path.exists(src_dir):
        return
    mk_if_not_exit(dest_dir)
    for item in os.listdir(src_dir):
        src = os.path.join(src_dir, item)
        dest = os.path.join(dest_dir, item)
        while os.path.exists(dest):
            dest += '(1)'
        shutil.move(src, dest)


def _cleanup_staging_dirs(work_root: str):
    if not os.path.isdir(work_root):
        return
    for name in list(os.listdir(work_root)):
        if not app_paths.is_staging_dir_name(name):
            continue
        staging = os.path.join(work_root, name)
        if not os.path.isdir(staging):
            continue
        _move_contents_up(staging, work_root)
        shutil.rmtree(staging, ignore_errors=True)


@Timeline_AOP
def pre_filter(timeline: Timeline):
    if not _should_apply_content_filter(timeline):
        return None
    zip: Zip = timeline.get_current_record().output_file
    newzip = copy.deepcopy(zip)
    file_list = filter.pre_filter(zip.file_list)
    if file_list is None:
        return None
    newzip.file_list = file_list
    return newzip


def _timeline_has_successful_unzip_ancestor(timeline: Timeline | None) -> bool:
    """时间线中是否已有成功解压的外层（套娃场景）。"""
    if not timeline:
        return False
    for record in timeline.records[:-1]:
        if record.ops == 'unzip' and record.output_file:
            return True
    return False


def _should_only_cleanup_staging_on_failure(
    zip_obj: Zip | None,
    timeline: Timeline | None,
) -> bool:
    """内层/套娃解压失败时，只允许清理暂存目录，不得动已解压内容。"""
    if not isinstance(zip_obj, Zip):
        return False
    if _timeline_has_successful_unzip_ancestor(timeline):
        return True
    if _is_nested_unzip_failure_context(zip_obj):
        return True
    if _under_work_root(zip_obj.path):
        return True
    return False


def _is_staging_unzip_path(path: str) -> bool:
    name = os.path.basename(os.path.normpath(path))
    return app_paths.is_staging_dir_name(name)


def _collect_protected_unzip_failure_roots(zip_obj: Zip | None) -> set[str]:
    """内层解压失败时不得删除的目录（外层作品夹 / 压缩包所在父目录）。"""
    protected: set[str] = set()
    if not isinstance(zip_obj, Zip) or not zip_obj.path:
        return protected
    archive_path = os.path.normpath(zip_obj.path)
    father = os.path.normpath(zip_obj.father)
    protected.add(os.path.normcase(father))
    work_root = _under_work_root(archive_path)
    if work_root:
        protected.add(os.path.normcase(work_root))
    for root in _work_roots:
        root_norm = os.path.normpath(root)
        if file_ops.is_path_under(root_norm, archive_path):
            protected.add(os.path.normcase(root_norm))
    return protected


def _is_nested_unzip_failure_context(zip_obj: Zip) -> bool:
    """压缩包位于已解压内容中（套娃内层），失败清理需保守处理。"""
    if _is_nested_archive(zip_obj):
        return True
    father = zip_obj.father
    if not father or not os.path.isdir(father):
        return False
    dedicated = os.path.normcase(os.path.normpath(
        os.path.join(father, zip_obj.filename),
    ))
    try:
        entries = os.listdir(father)
    except OSError:
        return False
    archive_name = zip_obj.name
    for name in entries:
        if name == archive_name or name.startswith('.pk_'):
            continue
        entry_case = os.path.normcase(os.path.normpath(os.path.join(father, name)))
        if entry_case == dedicated:
            continue
        return True
    return False


def _is_allowed_failure_cleanup_path(path: str, zip_obj: Zip) -> bool:
    """本次解压失败时允许删除的专属残留（暂存目录或 basename 子目录）。"""
    norm = os.path.normpath(path)
    if _is_staging_unzip_path(norm):
        return True
    dedicated = os.path.join(zip_obj.father, zip_obj.filename)
    return os.path.normcase(norm) == os.path.normcase(os.path.normpath(dedicated))


def _cleanup_failed_unzip_output(
    output_path: str,
    zip_obj: Zip | None = None,
    *,
    existed_before: bool = False,
    timeline: Timeline | None = None,
):
    """
    密码错误等原因导致解压失败时，移除本次尝试创建的输出/暂存目录。
    7-Zip 可能在失败前已创建空目录树或部分解压文件，均需清理。
    套娃内层失败时不得删除外层作品目录或解压前已存在的文件夹。
    """
    if not output_path:
        return
    norm = os.path.normpath(output_path)
    norm_case = os.path.normcase(norm)
    protected = _collect_protected_unzip_failure_roots(zip_obj)

    if isinstance(zip_obj, Zip) and zip_obj.path:
        if _is_staging_unzip_path(norm) and _staging_has_usable_partial_extract(norm):
            if logger:
                logger.info(
                    '外层部分解压成功，保留暂存目录："{}"'.format(norm),
                )
            return

        if norm_case in protected:
            if logger:
                logger.info(
                    '解压失败，保留作品目录："{}"'.format(norm),
                )
            return

        if existed_before and not _is_staging_unzip_path(norm):
            if logger:
                logger.info(
                    '解压失败，保留解压前已存在的目录："{}"'.format(norm),
                )
            return

        if _should_only_cleanup_staging_on_failure(zip_obj, timeline):
            if not _is_staging_unzip_path(norm):
                if logger:
                    logger.info(
                        '套娃内层解压失败，保留已解压内容，跳过清理："{}"'.format(norm),
                    )
                return

        elif _is_nested_unzip_failure_context(zip_obj):
            father_case = os.path.normcase(os.path.normpath(zip_obj.father))
            if norm_case == father_case:
                if logger:
                    logger.info(
                        '内层解压失败，保留压缩包所在目录："{}"'.format(norm),
                    )
                return
            if not _is_allowed_failure_cleanup_path(norm, zip_obj):
                if logger:
                    logger.info(
                        '内层解压失败，跳过非暂存残留目录："{}"'.format(norm),
                    )
                return

    if not os.path.exists(norm):
        _unregister_work_root(norm)
        return
    try:
        if os.path.isdir(norm):
            shutil.rmtree(norm, ignore_errors=True)
        else:
            os.remove(norm)
        _unregister_work_root(norm)
        if logger:
            logger.info('已清理解压失败残留："{}"'.format(norm))
    except OSError as err:
        if logger:
            logger.warning('清理解压失败残留失败 [{}]: {}'.format(norm, err))


def _restore_volume_original_names(zip_obj: Zip):
    if not isinstance(zip_obj, Zip) or not zip_obj.path:
        return
    try:
        from volume.rename import restore_renamed_volumes
        father = zip_obj.father or os.path.dirname(os.path.abspath(zip_obj.path))
        if zip_obj.volumes and len(zip_obj.volumes) > 1:
            restored = restore_renamed_volumes(list(zip_obj.volumes))
            zip_obj.volumes = restored
            zip_obj.path = restored[0]
        else:
            from volume.rename import restore_renames_in_directory
            restore_renames_in_directory(father)
        zip_obj.invalidate_namelist_scan()
    except Exception as err:
        if logger:
            logger.warning('还原分卷原名失败: {}'.format(err))


@Timeline_AOP
def unzip(timeline: Timeline):
    zip: Zip = timeline.get_current_record().output_file
    output_path, merge_mode = _resolve_unzip_output_path(zip)
    output_existed_before = os.path.exists(output_path)
    succeeded = False
    try:
        if not unzipper.unzip(zip, output_path, conf.thread_threshold_mb, conf.thread_compression_ratio):
            return
        succeeded = True
        password.hit_password(passwords, zip.pw_list[0])

        source_removed = False
        if merge_mode == 'same_name_staging':
            # 源压缩包保持原名与位置不动；仅当用户开启「解压后删除」且删除成功后，
            # 才将暂存目录提升为与压缩包同名的作品文件夹。
            work_root = _legacy_top_work_root(zip)
            if zip.del_after_unzip and os.path.isfile(zip.path):
                source_removed = delete_file(zip.path)
                if source_removed and not os.path.exists(work_root):
                    shutil.move(output_path, work_root)
                    _remap_work_root(output_path, work_root)
                    output_path = work_root
        elif merge_mode == 'top_staging':
            work_root = _intended_top_work_root(zip)
            base = work_root
            suffix = 1
            while os.path.exists(work_root):
                work_root = f'{base}_{suffix}'
                suffix += 1
            shutil.move(output_path, work_root)
            _remap_work_root(output_path, work_root)
            output_path = work_root
        elif merge_mode == 'volume_staging':
            stem = _volume_stem(zip) or zip.filename
            work_root = os.path.join(zip.father, stem)
            base = work_root
            suffix = 1
            while os.path.exists(work_root):
                work_root = f'{base}({suffix})'
                suffix += 1
            shutil.move(output_path, work_root)
            _remap_work_root(output_path, work_root)
            output_path = work_root
        elif merge_mode is True:
            _merge_directory_contents(output_path, zip.father)
            _unregister_work_root(output_path)
            output_path = zip.father
            # 内层压缩包位于已登记的外层作品根时，zip.father 只是内容子目录，
            # 不能再登记为更深的作品根，否则后续会只归档这个子目录。
            if _is_nested_archive(zip) and not _under_work_root(zip.path):
                _register_work_root(zip.father)

        if zip.del_after_unzip and not source_removed:
            for volume in (zip.volumes or [zip.path]):
                delete_file(volume)
        if zip.covered:
            file_ops.cleanup_covered_extract_junk(output_path)
        return output_path
    finally:
        if not succeeded:
            _cleanup_failed_unzip_output(
                output_path,
                zip,
                existed_before=output_existed_before,
                timeline=timeline,
            )
        _restore_volume_original_names(zip)


def archive_loop():
    prepare_archive_queue()
    flattened_roots: set[str] = set()
    for timeline in timelines:
        if _timeline_step_failed(timeline):
            continue
        root = _resolve_task_work_root(timeline.get_current_path())
        root_key = _work_root_key(root)
        if not root_key or root_key in flattened_roots:
            continue
        if _pending_unzip_under_work_root(root):
            continue
        flattened_roots.add(root_key)
        if file_ops.is_dir_path(root):
            _flatten_work_root(root)
    processed_roots: set[str] = set()
    for timeline in timelines:
        if _timeline_step_failed(timeline):
            continue
        target_path = _resolve_task_work_root(timeline.get_current_path())
        if not target_path:
            continue
        root_key = _work_root_key(target_path)
        if root_key in processed_roots:
            continue
        if _pending_unzip_under_work_root(target_path):
            if logger:
                logger.info(
                    '作品目录仍有未完成解压任务，推迟归档："{}"'.format(
                        os.path.normpath(target_path),
                    )
                )
            continue
        processed_roots.add(root_key)
        try:
            archive(timeline)
        except Exception as err:
            if logger:
                logger.error(
                    '归档异常，已跳过：{}: {}'.format(
                        timeline.get_current_path(), err,
                    )
                )
                logger.debug(traceback.format_exc())
        if progress_ui != 'not initialized':
            progress_ui.add2lis(timelines)


def insert_rj_loop():
    """兼容旧调用；等价于 archive_loop。"""
    archive_loop()


def _work_root_key_for_timeline(timeline: Timeline) -> str | None:
    """解析任务所属作品目录的稳定键（用于识别套娃影子任务）。"""
    path = timeline.get_current_path()
    root = _resolve_task_work_root(path)
    if not root and path:
        root = _under_work_root(path)
    if not root and path:
        root = _locate_relocated_work_root(path)
    return _work_root_key(root)


def _collect_completed_work_root_keys(step: str) -> set[str]:
    """收集本步骤已成功作品目录键，供清理同目录套娃影子任务使用。"""
    keys: set[str] = set()
    for timeline in timelines:
        if _timeline_step_failed(timeline):
            continue
        if not _timeline_step_succeeded(timeline, step):
            continue
        key = _work_root_key_for_timeline(timeline)
        if key:
            keys.add(key)
    if step == 'rename':
        keys.update(_last_rename_succeeded_roots)
    return keys


def _prune_companion_timelines(completed_roots: set[str]):
    """移除与已完成作品同目录、但停在中间步骤的套娃影子任务。"""
    if not completed_roots:
        return
    remaining = []
    for timeline in timelines:
        if _timeline_step_failed(timeline):
            remaining.append(timeline)
            continue
        key = _work_root_key_for_timeline(timeline)
        if key and key in completed_roots:
            continue
        remaining.append(timeline)
    if len(remaining) != len(timelines):
        timelines[:] = remaining
        if progress_ui != 'not initialized':
            progress_ui.add2lis(timelines)
    if not timelines:
        already_add.clear()
        archive_registry.clear()


def prune_after_step(step: str):
    """在当前工作流步骤全部跑完后，移除该步骤成功的任务。"""
    completed_roots = _collect_completed_work_root_keys(step)
    if step == 'rename':
        _prune_successful_timelines('rename', succeeded_roots=_last_rename_succeeded_roots)
    else:
        _prune_successful_timelines(step)
    _prune_companion_timelines(completed_roots)


_last_rename_succeeded_roots: set[str] = set()


def _work_root_path(path: str | None, *, allow_external: bool = False) -> str | None:
    """取 path 的顶层作品目录（供 insert_rj / rename / unnest 使用）。

    优先按音声库（output）边界解析出顶层作品目录；若 path 不在音声库内，
    则尝试匹配就地解压产生的工作目录（在识别到 RJ 移入音声库之前，作品位于此）。

    allow_external=True 时，还允许将音声库外的任意文件夹作为重命名扫描根目录。
    """
    if not path or not conf:
        return None
    output = os.path.normpath(conf.output_path)
    path_norm = os.path.normpath(path)
    if file_ops.is_path_under(output, path_norm):
        if os.path.normcase(path_norm) == os.path.normcase(output):
            if allow_external and file_ops.is_dir_path(path_norm):
                return path_norm
            if logger:
                logger.warning(f'不能对音声库根目录执行此操作："{output}"')
            return None
        try:
            rel = os.path.relpath(path_norm, output)
        except ValueError:
            return None
        parts = [part for part in re.split(r'[\\/]', rel) if part and part != '.']
        if not parts or parts[0] == '..':
            return None
        work_root = os.path.join(output, parts[0])
        if os.path.normcase(work_root) == os.path.normcase(output):
            return None
        return work_root

    # 音声库之外：命中就地解压的工作目录（尚未移入音声库的作品）
    in_place_root = _under_work_root(path_norm)
    if in_place_root:
        return in_place_root

    if allow_external:
        if file_ops.is_dir_path(path_norm):
            return path_norm
        if os.path.isfile(path_norm):
            contained = _resolve_work_root_containing(path_norm)
            if contained:
                return contained
            stem, ext = os.path.splitext(os.path.basename(path_norm))
            if ext.lower() in ('.7z', '.zip', '.rar', '.001', '.ha'):
                parent = os.path.dirname(path_norm)
                for folder_name in (stem + TOP_WORK_ROOT_SUFFIX, stem):
                    candidate = os.path.join(parent, folder_name)
                    if os.path.isdir(candidate):
                        return candidate
        parent = os.path.dirname(path_norm)
        if parent and file_ops.is_dir_path(parent):
            return parent
        return None

    return None


def _rename_root_path(path: str | None) -> str | None:
    """解析重命名扫描根目录；允许音声库外的任意文件夹。"""
    return _work_root_path(path, allow_external=True)


def _resolve_task_work_root(path: str | None) -> str | None:
    """解析任务所属作品目录（音声库 / 就地解压 / 库外重命名）。"""
    return _work_root_path(path) or _rename_root_path(path)


def _ensure_rj_prefix_in_place(work_path: str, timeline: Timeline) -> str | None:
    """在作品原位置插入 RJ 前缀，不移动至音声库/资源库。"""
    if not work_path or not file_ops.is_dir_path(work_path):
        return None
    target_path = _locate_relocated_work_root(work_path) or work_path
    if _is_container_or_library_root(target_path):
        narrowed = _narrow_rename_root(target_path, timeline.get_current_path())
        if not narrowed:
            if logger:
                logger.warning(
                    '跳过对上层容器目录插入 RJ（避免整夹改名）："{}"'.format(
                        os.path.normpath(target_path),
                    )
                )
            return None
        target_path = narrowed
    basename = _preferred_work_root_basename(target_path).rstrip(' .')
    basename_code = file_ops.parse_rj_code(basename, allow_bare=False)
    if basename_code and file_ops.rj_match_source(basename, basename_code) == 'prefixed':
        return target_path

    rj = _find_rj_for_timeline(timeline)
    if not rj:
        return target_path

    rj = rj.upper()
    dirname = os.path.dirname(target_path)
    new_basename = f'[{rj}]{basename}'
    new_path = os.path.join(dirname, new_basename)
    if os.path.normcase(target_path) == os.path.normcase(new_path):
        return target_path
    if file_ops.path_exists(new_path):
        logger.warning(
            f'RJ 目标文件夹已存在，跳过插入 RJ 重命名（保留原文件夹）：{new_path}'
        )
        return target_path
    if not file_ops.safe_rename_path(target_path, new_path):
        logger.error(f'插入 RJ 重命名失败: {target_path} -> {new_path}')
        return target_path
    logger.info(' 从内容中发现 RJ 号 [{}]，重命名文件夹（原位置）'.format(rj))
    return new_path


def _resource_library_path() -> str | None:
    if not conf:
        return None
    path = (getattr(conf, 'resource_path', None) or '').strip()
    return path or None


def _is_in_resource_library(path: str | None) -> bool:
    """判断路径是否已位于资源库内（未识别 RJ 时的最终去处）。"""
    if not path:
        return False
    resource_root = _resource_library_path()
    if not resource_root:
        return False
    return file_ops.is_path_under(os.path.normpath(resource_root), os.path.normpath(path))


def _move_into_library(work_path: str, dest_dir: str) -> str | None:
    """将作品文件夹移入目标库；若有登记的首选名称则使用该名称而非暂存目录名。"""
    if not work_path or not dest_dir or not file_ops.path_exists(work_path):
        return None
    dest_dir = os.path.normpath(dest_dir)
    preferred = _preferred_work_root_basename(work_path)
    actual = os.path.basename(os.path.normpath(work_path.rstrip(' \\')))
    if preferred == actual:
        return file_ops.move_into_directory(work_path, dest_dir)
    file_ops.mk_if_not_exit(dest_dir)
    dest = os.path.join(dest_dir, preferred)
    base_dest = dest
    suffix = 1
    while file_ops.path_exists(dest):
        dest = f'{base_dest}({suffix})'
        suffix += 1
    if (
        not file_ops.is_cross_drive_path(work_path, dest)
        and file_ops.safe_rename_path(work_path, dest)
    ):
        return dest
    try:
        shutil.move(work_path, dest)
        return dest
    except OSError as err:
        if logger:
            logger.error('移入目标库失败 [{}] -> [{}]: {}'.format(work_path, dest, err))
        return None


def _move_to_resource_library(work_path: str) -> str | None:
    """未识别 RJ 时，将作品文件夹从音声库移入资源库。"""
    resource_root = _resource_library_path()
    if not resource_root:
        return None
    resource_root = os.path.normpath(resource_root)
    work_norm = os.path.normpath(work_path)
    if os.path.normcase(resource_root) == os.path.normcase(conf.output_path):
        if logger:
            logger.error('资源库路径不能与音声库相同，已跳过移动')
        return None
    if file_ops.is_path_under(resource_root, work_norm):
        if logger:
            logger.info(f'已在资源库内，跳过移动："{work_norm}"')
        return work_path
    new_path = _move_into_library(work_path, resource_root)
    if new_path:
        if os.path.normcase(work_norm) != os.path.normcase(new_path):
            _remap_work_root(work_norm, new_path)
        if logger:
            logger.info(
                '未识别 RJ 号，已移入资源库："{}" -> "{}"'.format(
                    os.path.normpath(work_path), os.path.normpath(new_path),
                )
            )
        if file_ops.is_dir_path(new_path):
            try:
                new_path = _flatten_work_root(new_path)
            except Exception as err:
                logger.error(f'拍平套娃文件夹失败: {new_path}: {err}')
    elif logger:
        logger.error(f'移入资源库失败："{os.path.normpath(work_path)}"')
    return new_path


def _move_to_audio_library(work_path: str) -> str | None:
    """识别到 RJ 后，将就地解压的作品文件夹移入音声库（output）。

    若作品已在音声库内（套娃/复处理场景），原样返回。
    """
    if not work_path:
        return None
    if _is_under_output(work_path):
        return work_path
    output_root = os.path.normpath(conf.output_path)
    new_path = _move_into_library(work_path, output_root)
    if not new_path:
        if logger:
            logger.error(f'移入音声库失败："{os.path.normpath(work_path)}"')
        return None
    if os.path.normcase(os.path.normpath(work_path)) != os.path.normcase(os.path.normpath(new_path)):
        _remap_work_root(work_path, new_path)
    if logger:
        logger.info(
            '已识别 RJ，移入音声库："{}" -> "{}"'.format(
                os.path.normpath(work_path), os.path.normpath(new_path),
            )
        )
    if file_ops.is_dir_path(new_path):
        new_path = _flatten_work_root(new_path)
    return new_path


#  套娃文件夹
@Log_AOP
@Timeline_AOP
def unnest(timeline: Timeline):
    path = timeline.get_current_path()
    if not path or not file_ops.path_exists(path):
        return path

    work_root = _work_root_path(path)
    if not work_root or not file_ops.is_dir_path(work_root):
        return path

    mk_if_not_exit(work_root)
    _cleanup_staging_dirs(work_root)
    work_root = _flatten_work_root(work_root)
    return work_root


@Log_AOP
@Timeline_AOP
def insert_rj(timeline: Timeline):
    return _relocate_work_to_library(timeline)


@Log_AOP
@Timeline_AOP
def archive(timeline: Timeline):
    return _relocate_work_to_library(timeline)


def _relocate_work_to_library(timeline: Timeline):
    """识别 RJ 并将作品移入音声库；未识别则移入资源库。"""
    current_path = timeline.get_current_path()
    if not current_path:
        return

    target_path = _resolve_task_work_root(current_path)
    if not target_path:
        return None
    outer_work_root = _timeline_outer_work_root(timeline, current_path)
    if (
        outer_work_root
        and os.path.normcase(os.path.normpath(target_path))
        != os.path.normcase(outer_work_root)
    ):
        if logger:
            logger.warning(
                '归档目标落在外层作品根的子目录，已改用完整作品目录：'
                '"{}" -> "{}"'.format(
                    os.path.normpath(target_path), outer_work_root,
                )
            )
        target_path = outer_work_root
    target_path = _locate_relocated_work_root(target_path) or target_path
    if _is_container_or_library_root(target_path):
        narrowed = _narrow_rename_root(target_path, current_path)
        if not narrowed:
            if logger:
                logger.warning(
                    '跳过对上层容器目录归档（避免整夹移动）："{}"'.format(
                        os.path.normpath(target_path),
                    )
                )
            return None
        target_path = narrowed
    if not file_ops.is_dir_path(target_path):
        return None

    basename = _preferred_work_root_basename(target_path).rstrip(' .')
    basename_code = file_ops.parse_rj_code(basename, allow_bare=False)
    if basename_code and file_ops.rj_match_source(basename, basename_code) == 'prefixed':
        return _move_to_audio_library(target_path)

    rj = _find_rj_for_timeline(timeline)
    if not rj:
        logger.warning(f'未在解压内容中找到 RJ 号：{target_path}')
        return _move_to_resource_library(target_path)

    rj = rj.upper()
    dirname = os.path.dirname(target_path)
    new_basename = f'[{rj}]{basename}'
    new_path = os.path.join(dirname, new_basename)
    if os.path.normcase(target_path) == os.path.normcase(new_path):
        return _move_to_audio_library(target_path)
    if file_ops.path_exists(new_path):
        logger.warning(
            f'RJ 目标文件夹已存在，跳过插入 RJ 重命名（保留原文件夹）：{new_path}'
        )
        return _move_to_audio_library(target_path)
    if not file_ops.safe_rename_path(target_path, new_path):
        logger.error(f'插入 RJ 重命名失败: {target_path} -> {new_path}')
        return None
    logger.info(' 从内容中发现 RJ 号 [{}]，重命名文件夹'.format(rj))
    return _move_to_audio_library(new_path)


def _mark_rename_duplicate(timeline: Timeline, err: RenameDuplicateError):
    """将重复作品标记为 rename_duplicate，供运行状态栏与任务列表展示。"""
    record = timeline.get_current_record()
    input_archive = record.output_file or record.input_file
    current = Archive(timeline.get_current_path())
    if err.rjcode:
        current.RJ_code = err.rjcode
    existing_name = os.path.basename(err.existing_path)
    current.set_note(f'与库中重复：{existing_name}')
    timeline.add_record(Record(input_archive, 'rename_duplicate', current))


def _verify_rj_code(rjcode: str) -> bool:
    """向 DLsite 验证 RJ 号；无 renamer 时无法验证裸数字。"""
    if not rjcode:
        return False
    try:
        if renamer and renamer.renamer:
            return renamer.renamer.verify_rj_code(rjcode)
    except Exception as err:
        if logger:
            logger.debug(f'验证 RJ 号失败 [{rjcode}]: {err}')
    return False


def _select_verified_rj(
        candidates: list[tuple[str, str, int]], *, allow_bare: bool = True) -> str | None:
    """选取 RJ 号：先尝试全部带前缀候选，再验证裸数字候选。"""
    prefixed = [item for item in candidates if item[1] == 'prefixed']
    bare = [item for item in candidates if item[1] == 'bare']
    if prefixed:
        return prefixed[0][0]
    if not allow_bare:
        if bare and logger:
            logger.info('裸数字 RJ 候选已忽略：目录/压缩包内音频文件不足 2 个')
        return None
    for code, _source, _score in bare:
        if _verify_rj_code(code):
            return code
        if logger:
            logger.info(f'裸数字匹配 [{code}] 未通过 DLsite 验证，尝试其它候选')
    return None


def _find_rj_for_timeline(timeline: Timeline) -> str | None:
    """从时间线记录与解压目录中寻找 RJ 号。"""
    scores: dict[str, tuple[int, str]] = {}

    names: list[str] = []
    all_file_paths: list[str] = []
    for archive in timeline.get_all_input_archives() + timeline.get_all_output_archives():
        names.append(archive.name)
        all_file_paths.extend(getattr(archive, 'file_list', []))

    current_path = timeline.get_current_path()
    search_roots = []
    if current_path:
        search_roots.append(current_path)
    work_root = _resolve_task_work_root(current_path)
    if work_root and work_root not in search_roots:
        search_roots.append(work_root)

    allow_bare = file_ops.allow_bare_rj_digit_match(
        directory_roots=search_roots,
        file_paths=all_file_paths,
    )

    for archive in timeline.get_all_input_archives() + timeline.get_all_output_archives():
        rj = getattr(archive, 'RJ_code', None)
        if not rj:
            continue
        code = str(rj).upper()
        source = file_ops.rj_match_source(archive.name, code)
        note = getattr(archive, 'note', None)
        if note and file_ops.rj_match_source(note, code) == 'prefixed':
            source = 'prefixed'
        if source == 'bare' and not allow_bare:
            continue
        file_ops._score_rj_candidate(scores, code, source, 5 if source == 'prefixed' else 1)

    for code, source, score in file_ops.find_rj_candidates_in_names(names, allow_bare=allow_bare):
        file_ops._score_rj_candidate(scores, code, source, score)

    for root in search_roots:
        if root and file_ops.is_dir_path(root):
            for code, source, score in file_ops.find_rj_candidates_for_folder(
                    root, allow_bare=allow_bare):
                file_ops._score_rj_candidate(scores, code, source, score)

    return _select_verified_rj(file_ops._sorted_rj_candidates(scores), allow_bare=allow_bare)


def _work_root_has_prefixed_rj(path: str | None) -> bool:
    """作品目录/压缩包是否带有 RJ/BJ/VJ 前缀号（过滤仅适用于 DLsite 音声）。"""
    if not path:
        return False
    root = _resolve_task_work_root(path)
    if root:
        basename = os.path.basename(os.path.normpath(root)).rstrip(' .')
        if file_ops.has_prefixed_rj_in_text(basename):
            return True
    in_place = _under_work_root(path)
    if in_place:
        basename = os.path.basename(os.path.normpath(in_place)).rstrip(' .')
        if file_ops.has_prefixed_rj_in_text(basename):
            return True
    if conf and _is_under_output(path):
        path_norm = os.path.normpath(path)
        output = os.path.normpath(conf.output_path)
        try:
            rel = os.path.relpath(path_norm, output)
        except ValueError:
            return False
        parts = [part for part in re.split(r'[\\/]', rel) if part and part != '.']
        if parts and file_ops.has_prefixed_rj_in_text(parts[0]):
            return True
    return False


def _should_apply_content_filter(timeline: Timeline) -> bool:
    """过滤规则仅适用于已识别为 DLsite 音声（目录/压缩包名含 RJ 前缀）的作品。"""
    if _is_in_resource_library(timeline.get_current_path()):
        return False
    if _work_root_has_prefixed_rj(timeline.get_current_path()):
        return True
    record = timeline.get_current_record()
    for archive in (record.output_file, record.input_file):
        if not archive:
            continue
        name = getattr(archive, 'name', '') or ''
        note = getattr(archive, 'note', '') or ''
        if file_ops.has_prefixed_rj_in_text(name) or file_ops.has_prefixed_rj_in_text(note):
            return True
        if isinstance(archive, Zip):
            intended = _intended_top_work_root(archive)
            if _work_root_has_prefixed_rj(intended):
                return True
    return False


def _append_step_record(timeline: Timeline, ops: str):
    record = timeline.get_current_record()
    input_archive = record.output_file or record.input_file
    path = timeline.get_current_path()
    output = Archive(path) if path else input_archive
    if input_archive:
        extend(output, input_archive)
    timeline.add_record(Record(input_archive, ops, output))


def filter_loop():
    seen_roots: set[str] = set()
    for timeline in timelines:
        current_ops = timeline.get_current_record().ops
        if _timeline_step_failed(timeline) and current_ops != 'filter_failed':
            continue
        if _is_in_resource_library(timeline.get_current_path()):
            if logger:
                logger.info(
                    '资源库作品跳过过滤："{}"'.format(
                        os.path.normpath(timeline.get_current_path() or ''),
                    )
                )
            _append_step_record(timeline, 'post_filter_skip')
            continue

        # 与重命名一致：内层解压未完成时先推迟，避免在无 RJ 前缀时误跳过过滤
        work_root = (
            _work_root_path(timeline.get_current_path())
            or _rename_root_path(timeline.get_current_path())
            or timeline.get_current_path()
        )
        if work_root and _pending_unzip_under_work_root(work_root):
            if logger:
                logger.info(
                    '作品目录仍有未完成解压任务，推迟过滤："{}"'.format(
                        os.path.normpath(work_root),
                    )
                )
            continue

        # 过滤前补做 insert_rj，避免 insert_rj_loop 推迟后本步因无 RJ 前缀被跳过
        try:
            if work_root and (
                _is_under_output(work_root) or _under_work_root(work_root)
            ):
                relocated = insert_rj(timeline)
                if relocated:
                    work_root = relocated
            elif work_root:
                prefixed = _ensure_rj_prefix_in_place(work_root, timeline)
                if prefixed:
                    work_root = prefixed
                    record = timeline.get_current_record()
                    if record.output_file:
                        record.output_file.path = prefixed
        except Exception as err:
            if logger:
                logger.warning(
                    '过滤前插入 RJ 失败，继续尝试过滤：{}: {}'.format(
                        os.path.normpath(work_root or ''), err,
                    )
                )

        if not _should_apply_content_filter(timeline):
            if logger:
                logger.info(
                    '未识别为带 RJ 前缀的音声作品，已跳过过滤："{}"'.format(
                        os.path.normpath(timeline.get_current_path() or work_root or ''),
                    )
                )
            _append_step_record(timeline, 'post_filter_skip')
            continue

        root_key = _work_root_key(
            _work_root_path(timeline.get_current_path())
            or _rename_root_path(timeline.get_current_path())
            or timeline.get_current_path()
        )
        if root_key and root_key in seen_roots:
            # 同一作品多条时间线：只过滤一次，其余记为已完成以免卡住流水线
            _append_step_record(timeline, 'post_filter')
            continue
        if root_key:
            seen_roots.add(root_key)

        try:
            post_filter(timeline)
        except Exception as err:
            if logger:
                logger.error(
                    '过滤异常，已跳过：{}: {}'.format(
                        timeline.get_current_path(), err,
                    )
                )
                logger.debug(traceback.format_exc())
            if not _timeline_step_failed(timeline):
                _append_step_record(timeline, 'filter_failed')
        progress_ui.add2lis(timelines)


@Timeline_AOP
def post_filter(timeline: Timeline):
    input_path = (
        _work_root_path(timeline.get_current_path())
        or _rename_root_path(timeline.get_current_path())
        or timeline.get_current_path()
    )
    hit = filter.post_filter(input_path)
    if logger and not hit and input_path:
        logger.info(
            '过滤完成，未命中规则："{}"'.format(os.path.normpath(input_path))
        )
    return input_path


def rename_loop():
    global _last_rename_succeeded_roots
    seen_roots = set()
    succeeded_roots = set()
    relocated_roots: list[tuple[str, str]] = []
    for timeline in timelines:
        if _timeline_step_failed(timeline):
            continue
        # 未识别 RJ 已移入资源库的作品不参与重命名（不在音声库内，且应保持原样）
        if _is_in_resource_library(timeline.get_current_path()):
            continue
        rename_root = _rename_root_path(timeline.get_current_path())
        if not rename_root:
            continue
        narrowed = _narrow_rename_root(rename_root, timeline.get_current_path())
        if not narrowed:
            if logger and _is_container_or_library_root(rename_root):
                logger.warning(
                    '重命名扫描根过宽，已跳过（避免改名上层文件夹）："{}"'.format(
                        os.path.normpath(rename_root),
                    )
                )
            continue
        rename_root = narrowed
        root_key = os.path.normcase(rename_root)
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        if _pending_unzip_under_work_root(rename_root):
            if logger:
                logger.info(
                    '作品目录仍有未完成解压任务，推迟重命名："{}"'.format(
                        os.path.normpath(rename_root),
                    )
                )
            continue
        # 确保 work_root 文件夹名含 RJ，否则 dlrenamer Scanner 无法识别
        try:
            if _is_under_output(rename_root) or _under_work_root(rename_root):
                relocated = insert_rj(timeline)
                rename_root = _rename_root_path(relocated or timeline.get_current_path())
            else:
                prefixed = _ensure_rj_prefix_in_place(rename_root, timeline)
                if prefixed:
                    rename_root = prefixed
                    record = timeline.get_current_record()
                    if record.output_file:
                        record.output_file.path = prefixed
            if not rename_root or not file_ops.is_dir_path(rename_root):
                if logger:
                    skip_path = rename_root or timeline.get_current_path() or ''
                    logger.warning(
                        '重命名前未找到有效作品目录，已跳过："{}"'.format(
                            os.path.normpath(skip_path),
                        )
                    )
                continue
            new_path = rename(timeline)
        except Exception as err:
            if logger:
                logger.error(
                    '重命名异常，已跳过：{}: {}'.format(
                        os.path.normpath(rename_root), err,
                    )
                )
                logger.debug(traceback.format_exc())
            continue
        if new_path is not None:
            succeeded_roots.add(root_key)
            new_root = os.path.normpath(new_path)
            succeeded_roots.add(os.path.normcase(new_root))
            relocated_roots.append((rename_root, new_root))

    # dlrenamer 会直接移动作品目录；同步更新同作品的套娃影子时间线，
    # 否则后续音频步骤只处理新路径，而影子任务仍停在已不存在的旧路径，
    # 最终无法按作品根清理并被状态栏误报为“部分完成”。
    for old_root, new_root in relocated_roots:
        _remap_work_root(old_root, new_root)
    _last_rename_succeeded_roots = succeeded_roots
    progress_ui.add2lis(timelines)
    if hasattr(progress_ui, '_run_on_ui') and hasattr(progress_ui, '_refresh_run_status'):
        progress_ui._run_on_ui(progress_ui._refresh_run_status)


@Timeline_AOP
def rename(timeline: Timeline):
    path = timeline.get_current_path()
    father = _rename_root_path(path)
    if father:
        father = _narrow_rename_root(father, path)
    if not father:
        if logger and path:
            logger.warning(f'无法确定重命名范围，已跳过："{os.path.normpath(path)}"')
        return None
    if _is_container_or_library_root(father):
        if logger:
            logger.warning(
                '拒绝重命名上层容器目录："{}"'.format(os.path.normpath(father))
            )
        return None
    try:
        new_path = renamer.run_renamer(father)
    except RenameDuplicateError as err:
        _mark_rename_duplicate(timeline, err)
        return None
    if new_path is None:
        if timeline.get_current_record().ops != 'rename_duplicate':
            if logger:
                logger.warning(f'重命名未成功："{os.path.normpath(father)}"')
        return None
    return new_path


def _scraper_proxies() -> dict[str, str] | None:
    if not conf:
        return None
    proxy = (conf.renamer_config or {}).get('scraper_http_proxy')
    if proxy:
        return {'http': str(proxy), 'https': str(proxy)}
    return None


def _resolve_rj_for_timeline_root(root: str, timeline: Timeline) -> str | None:
    rj = audio_tagger.resolve_rj_for_folder(root)
    if rj:
        return rj
    record = timeline.get_current_record()
    for archive in (record.output_file, record.input_file):
        if not archive:
            continue
        for text in (getattr(archive, 'name', '') or '', getattr(archive, 'note', '') or ''):
            code = file_ops.parse_rj_code(text, allow_bare=False)
            if code:
                return code.upper()
    return None


def _iter_unique_audio_work_roots(retry_failed_op: str | None = None):
    seen_roots: set[str] = set()
    for timeline in timelines:
        current_ops = timeline.get_current_record().ops
        if _timeline_step_failed(timeline) and current_ops != retry_failed_op:
            continue
        if _is_in_resource_library(timeline.get_current_path()):
            continue
        root = _rename_root_path(timeline.get_current_path())
        if not root or not file_ops.is_dir_path(root):
            continue
        root_key = os.path.normcase(os.path.normpath(root))
        if root_key in seen_roots:
            continue
        seen_roots.add(root_key)
        yield timeline, root


def convert_audio_loop():
    monitor = _start_audio_disk_monitor()
    try:
        for timeline, root in _iter_unique_audio_work_roots('convert_audio_failed'):
            try:
                convert_audio(timeline)
            except Exception as err:
                if logger:
                    logger.error(
                        '转flac异常，已跳过：{}: {}'.format(
                            os.path.normpath(root), err,
                        )
                    )
                    logger.debug(traceback.format_exc())
                if not _timeline_step_failed(timeline):
                    _append_step_record(timeline, 'convert_audio_failed')
            progress_ui.add2lis(timelines)
    finally:
        _stop_disk_monitor(monitor)


@Log_AOP
@Timeline_AOP
def convert_audio(timeline: Timeline):
    root = _rename_root_path(timeline.get_current_path())
    if not root:
        return None
    cfg = conf.audio_convert_config
    extensions = tuple(cfg.get('source_extensions') or audio_convert.ConvertConfig.source_extensions)
    pending = audio_convert.find_convertible_files(root, extensions)
    flac_bin = audio_convert.resolve_flac(
        str(cfg.get('flac_path') or '')
    )
    ffmpeg_bin = audio_convert.resolve_ffmpeg_fallback(
        str(cfg.get('ffmpeg_fallback_path') or cfg.get('ffmpeg_path') or '')
    )
    if pending and not flac_bin:
        if logger:
            logger.error(
                '未找到 flac，无法转换 {} 个文件："{}"'.format(
                    len(pending), os.path.normpath(root),
                )
            )
        _append_step_record(timeline, 'convert_audio_failed')
        return None
    if pending and not ffmpeg_bin and any(
        audio_convert._needs_ffmpeg_for_source(path) for path in pending
    ):
        if logger:
            logger.error(
                '目录内存在 float WAV，但未找到 ffmpeg 回退工具（可自编译 ffmpeg-minimal/）："{}"'.format(
                    os.path.normpath(root),
                )
            )
        _append_step_record(timeline, 'convert_audio_failed')
        return None
    ok, total = audio_convert.convert_work_folder(
        root,
        cfg,
        log=logger.info if logger else None,
    )
    if total == 0:
        if logger:
            logger.info(
                '未发现待转换音频，已跳过："{}"'.format(os.path.normpath(root))
            )
    elif ok < total:
        if logger:
            logger.error(
                '转flac未全部成功："{}"（{}/{}）'.format(
                    os.path.normpath(root), ok, total,
                )
            )
        _append_step_record(timeline, 'convert_audio_failed')
        return None
    elif logger:
        logger.info(
            '转flac完成："{}"（{}/{}）'.format(
                os.path.normpath(root), ok, total,
            )
        )
    return root


def tag_audio_loop():
    monitor = _start_audio_disk_monitor()
    try:
        for timeline, root in _iter_unique_audio_work_roots('tag_audio_failed'):
            try:
                tag_audio(timeline)
            except Exception as err:
                if logger:
                    logger.error(
                        '写入元数据异常，已跳过：{}: {}'.format(
                            os.path.normpath(root), err,
                        )
                    )
                    logger.debug(traceback.format_exc())
                if not _timeline_step_failed(timeline):
                    _append_step_record(timeline, 'tag_audio_failed')
            progress_ui.add2lis(timelines)
    finally:
        _stop_disk_monitor(monitor)


@Log_AOP
@Timeline_AOP
def tag_audio(timeline: Timeline):
    root = _rename_root_path(timeline.get_current_path())
    if not root:
        return None
    rj = _resolve_rj_for_timeline_root(root, timeline)
    if not rj:
        if logger:
            logger.warning(
                '未找到 RJ 号，已跳过写入元数据："{}"'.format(os.path.normpath(root))
            )
        return None
    if not conf:
        return None

    scraper = get_shared_scraper(conf.renamer_config)
    metadata = scraper.scrape_metadata(rj)
    if not metadata or not metadata.get('work_name'):
        if logger:
            logger.warning('未获取到 DLsite 元数据，已跳过写入元数据：{}'.format(rj))
        return None

    cover_bytes = b''
    cover_url = metadata.get('cover_url') or ''
    if cover_url and conf.audio_tag_config.get('embed_cover', True):
        try:
            cover_bytes = audio_tagger._download_cover(
                cover_url,
                proxies=_scraper_proxies(),
            )
        except Exception as err:
            if logger:
                logger.warning(
                    '封面下载失败，将仅写入文本标签：{}：{}'.format(rj, err)
                )

    ok, total = audio_tagger.tag_work_folder(
        root,
        metadata,
        conf.audio_tag_config,
        cover_bytes=cover_bytes or None,
        log=logger.info if logger else None,
    )
    if total == 0:
        if logger:
            logger.info(
                '未发现可写入元数据的音频，已跳过："{}"'.format(os.path.normpath(root))
            )
    elif ok < total:
        if logger:
            logger.error(
                '写入元数据未全部成功："{}"（{}/{}）'.format(
                    os.path.normpath(root), ok, total,
                )
            )
        _append_step_record(timeline, 'tag_audio_failed')
        return None
    elif logger:
        logger.info(
            '写入元数据完成："{}"（{}/{}）'.format(
                os.path.normpath(root), ok, total,
            )
        )
    return root


def create_timeline(files):
    """拖入时仅加入工作区，不做扫描或解压。"""
    added = 0
    for file in files:
        if _is_path_queued(file):
            continue
        _prepare_user_rescan(file)
        archive = Archive(file)
        timeline = Timeline(archive, 'create_timeline', archive)
        timelines.append(timeline)
        added += 1
    if added:
        progress_ui.add2lis(timelines)
    return added


def delete_file(file_path) -> bool:  # 删除方法，若配置逻辑删除则丢进回收文件夹
    if not file_path or not os.path.exists(file_path):
        return True
    dest_item = ''
    try:
        file_ops.clear_shell_folder_attributes(file_path)
        if conf.logical_deletion:
            mk_if_not_exit(conf.recycle_path)
            dest = conf.recycle_path
            if file_ops.is_path_under(conf.output_path, file_path):
                rel_path = os.path.relpath(file_path, conf.output_path)
                rel_dir = os.path.dirname(rel_path)
                if rel_dir and rel_dir != '.':
                    dest = os.path.join(conf.recycle_path, rel_dir)
                    mk_if_not_exit(dest)
            dest_item = os.path.join(dest, os.path.basename(file_path.rstrip('\\')))
            if os.path.isdir(file_path):
                while os.path.exists(dest_item):
                    dest_item += '(1)'
                shutil.move(file_path, dest_item)
            else:
                while os.path.exists(dest_item):
                    base, ext = os.path.splitext(dest_item)
                    dest_item = base + '(1)' + ext
                shutil.move(file_path, dest_item)
        else:
            if os.path.isdir(file_path):
                def _rmtree_onerror(func, path, exc_info):
                    file_ops.clear_shell_folder_attributes(path)
                    func(path)

                shutil.rmtree(file_path, onerror=_rmtree_onerror)
            else:
                os.remove(file_path)
    except FileNotFoundError:
        # 父目录已整体移走时，子路径再删会找不到源，属预期，不记失败
        return not os.path.exists(file_path)
    except (shutil.Error, OSError) as err:
        if not os.path.exists(file_path):
            return True
        if logger:
            if conf.logical_deletion:
                logger.error(
                    '移入回收站失败，已保留原文件："{}" -> "{}": {}'.format(
                        os.path.normpath(file_path),
                        os.path.normpath(dest_item or conf.recycle_path),
                        err,
                    )
                )
            else:
                logger.error(
                    '永久删除失败，已保留原文件："{}": {}'.format(
                        os.path.normpath(file_path), err,
                    )
                )
        return False
    return not os.path.exists(file_path)


def clear():
    workflow_context.state.clear()
    archive_registry.clear()


def restore_rj_in_library(library_root: str | None = None) -> dict[str, list]:
    """批量恢复音声库中缺失 RJ 前缀的文件夹名。"""
    root = library_root or (conf.output_path if conf else '')
    rows = file_ops.restore_rj_in_library(root)
    restored: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []
    for old_path, new_path, error in rows:
        if new_path and os.path.normcase(old_path) != os.path.normcase(new_path):
            restored.append((old_path, new_path))
            if logger:
                logger.info(
                    '已恢复 RJ 前缀："{}" -> "{}"'.format(
                        os.path.normpath(old_path), os.path.normpath(new_path),
                    )
                )
        elif error:
            failed.append((old_path, error))
            if logger:
                logger.warning(
                    '恢复 RJ 失败："{}"：{}'.format(os.path.normpath(old_path), error)
                )
    return {'restored': restored, 'failed': failed}


def _discard_wrapper_dir(dir_path: str):
    """拍平套娃后，将空外壳移入内置回收站（逻辑删除）或永久删除。"""
    if not dir_path or not file_ops.path_exists(dir_path):
        return
    delete_file(dir_path)


def reload():
    new_conf = config.Config()
    file_ops.set_discard_dir_path_hook(_discard_wrapper_dir)
    file_ops.set_delete_path_hook(delete_file)
    new_passwords = password.read_password()
    new_filter = filter_module.Filter(
        new_conf.filter_kw, new_conf.filter_dir, logger,
    )
    new_renamer = dlrenamer.ez_client.ensure_client(new_conf.renamer_config)
    bind_runtime_services(
        configuration=new_conf,
        password_list=new_passwords,
        filter_service=new_filter,
        rename_service=new_renamer,
    )
    if unzipper is not None:
        unzipper.set_seven_z_mmt(new_conf.seven_z_mmt)


def reload_passwords(password_list: list | None = None):
    """重新加载密码库，并同步到待解压/解压失败任务的 pw_list。"""
    if password_list is None:
        new_passwords = password.read_password()
    else:
        new_passwords = password_list
    bind_runtime_services(password_list=new_passwords)
    str_passwords = password.get_str_passwords(password.sort_passwords(passwords))
    for timeline in timelines:
        record = timeline.get_current_record()
        if record.ops in ('find_zip', 'unzip_failed'):
            zip_obj = record.output_file
            if hasattr(zip_obj, 'pw_list'):
                _merge_zip_passwords(zip_obj, str_passwords)
        if record.ops == 'unzip_failed':
            requeue_unzip_failure(timeline)
