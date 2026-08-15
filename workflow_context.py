"""工作流运行时上下文。

集中持有状态与服务引用，供已拆分的编排器、扫描器及兼容入口共享。
"""

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WorkflowServices:
    logger: Any = None
    conf: Any = None
    passwords: Any = None
    unzipper: Any = None
    filter_service: Any = None
    renamer: Any = None
    progress_ui: Any = 'not initialized'


@dataclass(slots=True)
class WorkflowState:
    timelines: list[Any] = field(default_factory=list)
    already_add: list[str] = field(default_factory=list)
    work_roots: set[str] = field(default_factory=set)
    work_root_preferred_names: dict[str, str] = field(default_factory=dict)

    def is_work_root_registered(self, path: str | None) -> bool:
        if not path:
            return False
        key = os.path.normcase(os.path.normpath(path))
        return any(
            os.path.normcase(os.path.normpath(root)) == key
            for root in self.work_roots
        )

    def register_work_root(self, path: str) -> str:
        norm = os.path.normpath(path)
        self.work_roots.add(norm)
        return norm

    def set_preferred_work_root_name(self, work_root: str, preferred: str):
        self.work_root_preferred_names[os.path.normpath(work_root)] = preferred

    def preferred_work_root_name(self, work_root: str) -> str | None:
        return self.work_root_preferred_names.get(os.path.normpath(work_root))

    def unregister_work_root(self, path: str):
        norm = os.path.normpath(path)
        self.work_roots.discard(norm)
        self.work_root_preferred_names.pop(norm, None)

    def remap_work_root(self, old_root: str, new_root: str):
        old_norm = os.path.normpath(old_root)
        new_norm = os.path.normpath(new_root)
        if old_norm in self.work_roots:
            self.work_roots.discard(old_norm)
        self.work_roots.add(new_norm)
        if old_norm in self.work_root_preferred_names:
            self.work_root_preferred_names[new_norm] = (
                self.work_root_preferred_names.pop(old_norm)
            )

    def clear(self):
        self.timelines.clear()
        self.already_add.clear()
        self.work_roots.clear()
        self.work_root_preferred_names.clear()


@dataclass(slots=True)
class WorkflowContext:
    services: WorkflowServices = field(default_factory=WorkflowServices)
    state: WorkflowState = field(default_factory=WorkflowState)
