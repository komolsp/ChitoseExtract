"""工作流扫描队列与分卷任务归组。

该模块只负责把 ``create_timeline`` / ``scan_failed`` 项转换为解压任务；
解压恢复、已解压判断等策略仍由调用方注入。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from timeline import Record, Timeline
from workflow_context import WorkflowState
from zip import Zip


FindZip = Callable[..., None]
PrepareRescan = Callable[[str], None]
FilterDiscovered = Callable[..., list]
ForgetArchive = Callable[[str, list[str] | None], None]
QueueChanged = Callable[[list], None]


@dataclass(slots=True)
class QueueScanDependencies:
    """一次扫描所需的运行时服务与可替换业务策略。"""

    find_zip: FindZip
    prepare_rescan: PrepareRescan
    filter_discovered: FilterDiscovered
    forget_archive: ForgetArchive
    logger: Any = None
    on_queue_changed: QueueChanged | None = None


def volume_group_key(volumes: list[str]) -> tuple[str, ...]:
    return tuple(sorted(os.path.normcase(path) for path in volumes))


def volume_task_identity(
    zip_obj: Zip,
    source_path: str | None = None,
) -> tuple | None:
    from volume import collect as volume_collect

    if source_path:
        identity = volume_collect.volume_group_identity_for_anchor(source_path)
        if identity:
            return identity
    if isinstance(zip_obj, Zip) and zip_obj.volumes and len(zip_obj.volumes) > 1:
        return volume_collect.volume_group_identity(zip_obj.volumes)
    return None


def zip_volume_identities(
    zip_obj: Zip,
    source_path: str | None = None,
) -> set[tuple]:
    identities: set[tuple] = set()
    identity = volume_task_identity(zip_obj, source_path)
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


def filter_volume_sibling_unresolved(
    discovered: list,
    unresolved: list,
) -> list:
    """首卷已被识别时，剔除同组分卷生成的失败占位项。"""
    claimed_paths: set[str] = set()
    claimed_identities: set[tuple] = set()
    for zip_obj in discovered:
        if not isinstance(zip_obj, Zip):
            continue
        if zip_obj.volumes:
            claimed_paths.update(os.path.normcase(path) for path in zip_obj.volumes)
        claimed_identities.update(zip_volume_identities(zip_obj))

    filtered: list = []
    for zip_obj in unresolved:
        if not isinstance(zip_obj, Zip):
            filtered.append(zip_obj)
            continue
        path_norm = os.path.normcase(zip_obj.path or '')
        if path_norm and path_norm in claimed_paths:
            continue
        if zip_obj.volumes and any(
            os.path.normcase(path) in claimed_paths for path in zip_obj.volumes
        ):
            continue
        if zip_volume_identities(zip_obj) & claimed_identities:
            continue
        filtered.append(zip_obj)
    return filtered


class QueueScanner:
    """拥有扫描队列状态转换的组件，不持有 GUI 或解压器全局变量。"""

    def __init__(self, state: WorkflowState):
        self.state = state

    def is_source_path_queued(self, path: str) -> bool:
        norm = os.path.normcase(os.path.normpath(path))
        for timeline in self.state.timelines:
            if not timeline.records:
                continue
            first = timeline.records[0].input_file
            first_path = getattr(first, 'path', None)
            if first_path and os.path.normcase(os.path.normpath(first_path)) == norm:
                return True
        return False

    def is_archive_path_queued(self, path: str | None) -> bool:
        if not path:
            return False
        norm = os.path.normcase(os.path.normpath(path))
        for timeline in self.state.timelines:
            record = timeline.get_current_record()
            zip_obj = record.output_file
            # unzip_failed 仍需重试，不能视为已入队。
            if record.ops != 'find_zip' or not isinstance(zip_obj, Zip):
                continue
            if os.path.normcase(os.path.normpath(zip_obj.path)) == norm:
                return True
        return False

    def collect_already_add(self) -> list[str]:
        """从已有解压任务回填扫描去重列表。"""
        paths: list[str] = []
        for timeline in self.state.timelines:
            record = timeline.get_current_record()
            if record.ops not in ('find_zip', 'unzip_failed'):
                continue
            zip_obj = record.output_file
            if isinstance(zip_obj, Zip) and zip_obj.volumes:
                paths.extend(zip_obj.volumes)
            else:
                path = getattr(zip_obj, 'path', None)
                if path:
                    paths.append(path)
        return paths

    @staticmethod
    def _apply_queued_note(queued_archive, zip_obj) -> None:
        if getattr(queued_archive, 'note', None):
            zip_obj.set_note(queued_archive.note)
        elif queued_archive.RJ_code and hasattr(zip_obj, 'pw_list'):
            if queued_archive.RJ_code not in zip_obj.pw_list:
                zip_obj.pw_list.insert(0, queued_archive.RJ_code)
                zip_obj.invalidate_namelist_scan()

    def scan(
        self,
        *,
        passwords: list[str],
        delete_after_unzip: bool,
        dependencies: QueueScanDependencies,
    ) -> int:
        """发现压缩包并以新时间线原子替换用户拖入项。"""
        timelines = self.state.timelines
        queued = [
            timeline for timeline in timelines
            if timeline.get_current_record().ops in ('create_timeline', 'scan_failed')
        ]
        if not queued:
            return 0

        scan_already_add = self.collect_already_add()
        new_timelines: list[Timeline] = []
        removed: list[Timeline] = []
        claimed_volume_groups: set[tuple[str, ...]] = set()
        claimed_volume_identities: set[tuple] = set()

        for timeline in queued:
            source = timeline.records[0].input_file.path
            queued_archive = timeline.records[0].input_file
            dependencies.prepare_rescan(source)
            discovered: list = []
            unresolved: list = []
            dependencies.find_zip(
                source,
                passwords,
                delete_after_unzip,
                scan_already_add,
                discovered,
                unresolved_list=unresolved,
                collect_unresolved=True,
            )
            discovered[:] = dependencies.filter_discovered(
                discovered,
                passwords,
                scan_already_add,
                allow_reextract=True,
            )
            unresolved[:] = filter_volume_sibling_unresolved(discovered, unresolved)

            if not discovered and not unresolved:
                if dependencies.logger:
                    dependencies.logger.warning(
                        '工作区项未发现可处理压缩文件："{}"'.format(
                            os.path.normpath(source),
                        ),
                    )
                # 保留失败时间线，避免空队列被 UI 误报为“已完成”。
                if timeline.get_current_record().ops != 'scan_failed':
                    timeline.add_record(
                        Record(queued_archive, 'scan_failed', queued_archive),
                    )
                continue

            for zip_obj in discovered:
                if zip_obj.volumes and len(zip_obj.volumes) > 1:
                    group_key = volume_group_key(zip_obj.volumes)
                    identity = volume_task_identity(zip_obj, source)
                    if group_key in claimed_volume_groups:
                        continue
                    if identity and identity in claimed_volume_identities:
                        continue
                    claimed_volume_groups.add(group_key)
                    if identity:
                        claimed_volume_identities.add(identity)
                dependencies.forget_archive(zip_obj.path, zip_obj.volumes)
                self._apply_queued_note(queued_archive, zip_obj)
                # 复用源 Archive，避免同一目录的每个结果再次递归扫描文件树。
                new_timelines.append(Timeline(queued_archive, 'find_zip', zip_obj))
            for zip_obj in unresolved:
                self._apply_queued_note(queued_archive, zip_obj)
                new_timelines.append(
                    Timeline(queued_archive, 'unzip_failed', zip_obj),
                )
            removed.append(timeline)

        for timeline in removed:
            timelines.remove(timeline)
        timelines.extend(new_timelines)
        if dependencies.on_queue_changed:
            dependencies.on_queue_changed(timelines)
        return len(new_timelines)
