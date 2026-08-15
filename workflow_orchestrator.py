"""与 GUI 无关的工作流步骤调度器。"""

from dataclasses import dataclass
from typing import Any, Callable


class WorkflowStepError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowRunResult:
    start_step: str
    pipeline: tuple[str, ...]
    last_step: str | None

    @property
    def ran_full_from_unzip(self) -> bool:
        return self.start_step == 'unzip' and len(self.pipeline) > 1


class WorkflowOrchestrator:
    """按配置执行 runner 的 ``<step>_loop``，并统一完成步骤收尾。"""

    def __init__(self, runner: Any, pipeline_builder: Callable[..., list[str]]):
        self.runner = runner
        self.pipeline_builder = pipeline_builder

    def run(
        self,
        start_step: str,
        *,
        on_step_start: Callable[[str, int, tuple[str, ...]], None] | None = None,
        on_step_complete: Callable[[str, str | None], None] | None = None,
    ) -> WorkflowRunResult:
        self.runner.reload()
        conf = self.runner.conf
        pipeline = self.pipeline_builder(
            start_step,
            auto_next=bool(conf.auto_next),
            workflow_steps=getattr(conf, 'workflow_steps', None),
        )
        if not pipeline:
            pipeline = [start_step]
        steps = tuple(pipeline)

        last_step = None
        for index, step in enumerate(steps):
            handler = getattr(self.runner, f'{step}_loop', None)
            if not callable(handler):
                raise WorkflowStepError(f'未知工作流步骤：{step}')
            last_step = step
            if on_step_start:
                on_step_start(step, index, steps)
            handler()
            next_step = steps[index + 1] if index + 1 < len(steps) else None
            if on_step_complete:
                on_step_complete(step, next_step)

        if last_step:
            self.runner.prune_after_step(last_step)
        return WorkflowRunResult(start_step, steps, last_step)
