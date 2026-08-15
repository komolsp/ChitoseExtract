"""解压任务的轮次调度与失败隔离。"""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from timeline import Record, Timeline
from workflow_context import WorkflowState
from zip import Zip


ProcessTimeline = Callable[[Timeline], None]
PendingZip = Callable[[Timeline], Zip | None]
RecoverTimeline = Callable[[Timeline, Zip], bool]
TaskPriority = Callable[[Timeline], tuple[int, str]]
QueueChanged = Callable[[list], None]


@dataclass(slots=True)
class UnzipExecutionDependencies:
    """解压轮次所需策略；实际解压和恢复逻辑由调用方提供。"""

    process_timeline: ProcessTimeline
    pending_zip: PendingZip
    recover_timeline: RecoverTimeline
    task_priority: TaskPriority
    logger: Any = None
    on_round_complete: QueueChanged | None = None


@dataclass(frozen=True, slots=True)
class UnzipExecutionResult:
    rounds: int
    processed: int
    stopped_at_limit: bool


def unzip_task_priority(timeline: Timeline) -> tuple[int, str]:
    """完整分卷组优先；残缺组靠后，等待其它任务可能释出首卷。"""
    record = timeline.get_current_record()
    zip_obj = record.output_file
    path_key = getattr(zip_obj, 'path', '') or ''
    if isinstance(zip_obj, Zip) and zip_obj.volumes and len(zip_obj.volumes) > 1:
        from volume.resolver import is_complete_volume_group

        if is_complete_volume_group(zip_obj.volumes):
            return (0, path_key)
        return (2, path_key)
    return (1, path_key)


class UnzipExecutor:
    """在共享工作流状态上执行待解压任务，不持有具体 I/O 服务。"""

    def __init__(self, state: WorkflowState):
        self.state = state

    def requeue_failures(self, requeue: Callable[[Timeline], bool]) -> int:
        requeued = 0
        for timeline in self.state.timelines:
            if requeue(timeline):
                requeued += 1
        return requeued

    def run(
        self,
        *,
        max_rounds: int,
        dependencies: UnzipExecutionDependencies,
    ) -> UnzipExecutionResult:
        """逐轮消费 ``find_zip`` 任务，并将单任务异常隔离为失败记录。"""
        rounds = 0
        round_index = 0
        processed = 0
        stopped_at_limit = False

        while True:
            round_index += 1
            if round_index > max_rounds:
                stopped_at_limit = True
                if dependencies.logger:
                    dependencies.logger.error(
                        '套娃解压轮次超过 {} 次，已中止以防重复解压；'
                        '请检查是否有压缩包反复被识别或密码错误任务未清除'.format(
                            max_rounds,
                        ),
                    )
                break

            pending = [
                timeline for timeline in self.state.timelines
                if timeline.records[-1].ops == 'find_zip'
            ]
            if not pending:
                break
            rounds += 1
            pending.sort(key=dependencies.task_priority)

            for timeline in pending:
                active_zip = dependencies.pending_zip(timeline)
                processed += 1
                try:
                    dependencies.process_timeline(timeline)
                except Exception as err:
                    if (
                        isinstance(active_zip, Zip)
                        and dependencies.recover_timeline(timeline, active_zip)
                    ):
                        if dependencies.logger:
                            dependencies.logger.info(
                                '外层解压遇内层加密项报错，已转入内层："{}"'.format(
                                    os.path.normpath(active_zip.path or ''),
                                ),
                            )
                        continue

                    failed_zip = (
                        active_zip
                        if isinstance(active_zip, Zip)
                        else timeline.get_current_record().output_file
                    )
                    if isinstance(failed_zip, Zip):
                        timeline.add_record(
                            Record(failed_zip, 'unzip_failed', failed_zip),
                        )
                    else:
                        record = timeline.get_current_record()
                        timeline.add_record(
                            Record(
                                record.output_file,
                                'unzip_failed',
                                record.output_file,
                            ),
                        )
                    if dependencies.logger:
                        dependencies.logger.error(
                            '处理解压任务异常，已标记失败并继续其余任务: {}: {}'.format(
                                getattr(
                                    failed_zip,
                                    'path',
                                    timeline.get_current_path(),
                                ),
                                err,
                            ),
                        )
                        dependencies.logger.debug(traceback.format_exc())

            if dependencies.on_round_complete:
                dependencies.on_round_complete(self.state.timelines)

        return UnzipExecutionResult(
            rounds=rounds,
            processed=processed,
            stopped_at_limit=stopped_at_limit,
        )
