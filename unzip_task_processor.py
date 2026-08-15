"""单个解压时间线的决策与结果状态转换。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

import file_ops
from timeline import Record, Timeline
from zip import Zip


TimelineAction = Callable[[Timeline], Any]
ZipCheck = Callable[[Zip], bool]


@dataclass(slots=True)
class UnzipTaskDependencies:
    """单任务处理所需策略；文件 I/O 仍由现有业务函数实现。"""

    archive_registry: Any
    is_nested_archive: ZipCheck
    requests_reextract: Callable[[Timeline, Zip], bool]
    resolve_extracted_work_root: Callable[[Zip], str | None]
    has_extracted_content: Callable[[str | None], bool]
    should_resume_nested_only: ZipCheck
    register_work_root: Callable[[str], Any]
    flatten_work_root: Callable[[str], str]
    enqueue_nested_archives: Callable[..., None]
    timeline_targets_outer_zip: Callable[[Timeline], bool]
    promote_outer_timeline_to_inner: Callable[[Timeline], bool]
    advance_past_outer_layer: Callable[[Timeline, Zip, str | None], bool]
    timeline_has_unzipped_ancestor: Callable[[Timeline, Zip], bool]
    unnest: Callable[[Timeline], str | None]
    pre_filter: TimelineAction
    pending_zip: Callable[[Timeline], Zip | None]
    refresh_zip_volumes: Callable[[Zip, str | None], None]
    prepare_zip_for_unzip: Callable[[Zip], None]
    resolve_work_root_containing: Callable[[str | None], str | None]
    snapshot_scan_tree: Callable[[str | None], dict]
    skip_duplicate_volume_unzip: Callable[[Timeline], bool]
    unzip: Callable[[Timeline], str | None]
    recover_outer_with_pending_inner: Callable[[Timeline, Zip], bool]
    remember_unzipped_archive: Callable[[Zip], None]
    dismiss_volume_sibling_failures: Callable[[Zip, str | None], int]
    incremental_scan_roots: Callable[[str, dict | None], list[str] | None]
    logger: Any = None


class UnzipTaskProcessor:
    """执行单条时间线的解压决策，不直接持有运行时全局状态。"""

    def process(
        self,
        timeline: Timeline,
        dependencies: UnzipTaskDependencies,
    ) -> None:
        record = timeline.get_current_record()
        zip_obj = record.output_file
        parent_zip = zip_obj if isinstance(zip_obj, Zip) else None

        if isinstance(zip_obj, Zip) and not dependencies.is_nested_archive(zip_obj):
            reextract = dependencies.requests_reextract(timeline, zip_obj)
            work_root = dependencies.resolve_extracted_work_root(zip_obj)
            if (
                not reextract
                and work_root
                and dependencies.has_extracted_content(work_root)
                and (
                    dependencies.should_resume_nested_only(zip_obj)
                    or dependencies.archive_registry.is_unzipped(
                        zip_obj.path,
                        zip_obj.volumes,
                    )
                )
            ):
                dependencies.archive_registry.mark_unzipped(
                    zip_obj.path,
                    zip_obj.volumes,
                )
                if dependencies.logger:
                    dependencies.logger.info(
                        '外层已解压，跳过重复解压并处理内层："{}"'.format(
                            os.path.normpath(zip_obj.path or ''),
                        ),
                    )
                new_path = work_root
                if file_ops.is_dir_path(new_path):
                    dependencies.register_work_root(new_path)
                    new_path = dependencies.flatten_work_root(new_path)
                dependencies.enqueue_nested_archives(
                    timeline,
                    new_path,
                    zip_obj,
                )
                if dependencies.timeline_targets_outer_zip(timeline):
                    if not dependencies.promote_outer_timeline_to_inner(timeline):
                        dependencies.advance_past_outer_layer(
                            timeline,
                            zip_obj,
                            work_root,
                        )
                return

        if (
            isinstance(zip_obj, Zip)
            and dependencies.is_nested_archive(zip_obj)
            and dependencies.timeline_has_unzipped_ancestor(timeline, zip_obj)
            and dependencies.logger
        ):
            dependencies.logger.info(
                '套娃内层重试，跳过外层："{}"'.format(
                    os.path.normpath(zip_obj.path or ''),
                ),
            )

        if (
            not dependencies.requests_reextract(timeline, zip_obj)
            and isinstance(zip_obj, Zip)
            and dependencies.archive_registry.is_unzipped(
                zip_obj.path,
                zip_obj.volumes,
            )
        ):
            if dependencies.logger:
                dependencies.logger.info(
                    '压缩包已解压过，跳过重复解压："{}"'.format(
                        os.path.normpath(zip_obj.path or ''),
                    ),
                )
            new_path = dependencies.unnest(timeline) or timeline.get_current_path()
            dependencies.enqueue_nested_archives(
                timeline,
                new_path,
                parent_zip,
            )
            return

        dependencies.pre_filter(timeline)
        # pre_filter/unzip 可能替换当前记录，必须提前保留实际 Zip 引用。
        active_zip = dependencies.pending_zip(timeline)
        parent_zip = timeline.get_current_record().output_file
        incremental_base = None
        incremental_before = None
        if isinstance(parent_zip, Zip):
            source = timeline.records[0].input_file.path if timeline.records else None
            dependencies.refresh_zip_volumes(parent_zip, source)
            dependencies.prepare_zip_for_unzip(parent_zip)
            if dependencies.is_nested_archive(parent_zip):
                incremental_base = dependencies.resolve_work_root_containing(
                    parent_zip.path,
                )
                if incremental_base and os.path.isdir(incremental_base):
                    incremental_before = dependencies.snapshot_scan_tree(
                        incremental_base,
                    )

        if dependencies.skip_duplicate_volume_unzip(timeline):
            output_path = timeline.get_current_record().output_file.path
        else:
            output_path = dependencies.unzip(timeline)

        if not output_path:
            failed_zip = (
                active_zip
                if isinstance(active_zip, Zip)
                else timeline.get_current_record().output_file
            )
            if (
                isinstance(failed_zip, Zip)
                and dependencies.recover_outer_with_pending_inner(
                    timeline,
                    failed_zip,
                )
            ):
                return
            if isinstance(failed_zip, Zip):
                timeline.add_record(
                    Record(failed_zip, 'unzip_failed', failed_zip),
                )
                if dependencies.logger:
                    label = (
                        '内层'
                        if dependencies.is_nested_archive(failed_zip)
                        else '压缩包'
                    )
                    dependencies.logger.error(
                        '{}解压失败，跳过套娃继续: "{}"'.format(
                            label,
                            os.path.normpath(failed_zip.path or ''),
                        ),
                    )
            return

        if isinstance(parent_zip, Zip):
            dependencies.remember_unzipped_archive(parent_zip)
            source = timeline.records[0].input_file.path if timeline.records else None
            dependencies.dismiss_volume_sibling_failures(parent_zip, source)

        new_path = dependencies.unnest(timeline)
        if not new_path:
            new_path = timeline.get_current_path()
        if new_path and file_ops.is_dir_path(new_path):
            dependencies.register_work_root(new_path)

        incremental_roots = None
        if (
            incremental_base
            and new_path
            and os.path.normcase(os.path.normpath(incremental_base))
            == os.path.normcase(os.path.normpath(new_path))
        ):
            incremental_roots = dependencies.incremental_scan_roots(
                incremental_base,
                incremental_before,
            )
            if dependencies.logger and incremental_roots is not None:
                dependencies.logger.debug(
                    '套娃增量扫描：{} 个新增根 [{}]'.format(
                        len(incremental_roots),
                        '],['.join(incremental_roots),
                    ),
                )

        dependencies.enqueue_nested_archives(
            timeline,
            new_path,
            parent_zip,
            incremental_roots=incremental_roots,
        )
