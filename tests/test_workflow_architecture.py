import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import task_runner
from workflow_context import WorkflowContext
from workflow_orchestrator import WorkflowOrchestrator, WorkflowStepError


class WorkflowContextTests(unittest.TestCase):
    def test_contexts_do_not_share_mutable_state(self):
        first = WorkflowContext()
        second = WorkflowContext()

        first.state.timelines.append('task')
        first.state.already_add.append('archive.zip')

        self.assertEqual(second.state.timelines, [])
        self.assertEqual(second.state.already_add, [])

    def test_work_root_state_moves_and_clears_as_one_unit(self):
        context = WorkflowContext()
        with tempfile.TemporaryDirectory() as tmp:
            old_root = os.path.join(tmp, 'old')
            new_root = os.path.join(tmp, 'new')
            context.state.register_work_root(old_root)
            context.state.set_preferred_work_root_name(old_root, 'preferred')
            context.state.remap_work_root(old_root, new_root)

            self.assertFalse(context.state.is_work_root_registered(old_root))
            self.assertTrue(context.state.is_work_root_registered(new_root))
            self.assertEqual(
                context.state.preferred_work_root_name(new_root), 'preferred',
            )

            context.state.clear()
            self.assertEqual(context.state.work_roots, set())
            self.assertEqual(context.state.work_root_preferred_names, {})

    def test_task_runner_compatibility_aliases_share_context_state(self):
        self.assertIs(task_runner.timelines, task_runner.workflow_context.state.timelines)
        self.assertIs(task_runner.already_add, task_runner.workflow_context.state.already_add)
        self.assertIs(task_runner._work_roots, task_runner.workflow_context.state.work_roots)
        self.assertIs(
            task_runner._work_root_preferred_names,
            task_runner.workflow_context.state.work_root_preferred_names,
        )

    def test_runtime_binding_updates_compatibility_fields_and_context(self):
        previous = {
            'logger_service': task_runner.logger,
            'configuration': task_runner.conf,
            'password_list': task_runner.passwords,
            'unzip_service': task_runner.unzipper,
            'filter_service': task_runner.filter,
            'rename_service': task_runner.renamer,
            'progress_service': task_runner.progress_ui,
        }
        services = {name: object() for name in previous}
        try:
            context = task_runner.bind_runtime_services(**services)
            self.assertIs(task_runner.logger, services['logger_service'])
            self.assertIs(task_runner.conf, services['configuration'])
            self.assertIs(task_runner.progress_ui, services['progress_service'])
            self.assertIs(context.services.unzipper, services['unzip_service'])
            self.assertIs(context.services.filter_service, services['filter_service'])
            self.assertIs(context.services.renamer, services['rename_service'])
        finally:
            task_runner.bind_runtime_services(**previous)


class _FakeRunner:
    def __init__(self):
        self.conf = SimpleNamespace(auto_next=True, workflow_steps=['unzip', 'archive'])
        self.calls = []

    def reload(self):
        self.calls.append('reload')

    def unzip_loop(self):
        self.calls.append('unzip')

    def archive_loop(self):
        self.calls.append('archive')

    def prune_after_step(self, step):
        self.calls.append(('prune', step))


class WorkflowOrchestratorTests(unittest.TestCase):
    def test_orchestrator_runs_pipeline_and_prunes_last_step(self):
        runner = _FakeRunner()
        builder = mock.Mock(return_value=['unzip', 'archive'])
        starts = []
        completions = []
        orchestrator = WorkflowOrchestrator(runner, builder)

        result = orchestrator.run(
            'unzip',
            on_step_start=lambda step, index, steps: starts.append(
                (step, index, steps)
            ),
            on_step_complete=lambda step, next_step: completions.append(
                (step, next_step)
            ),
        )

        builder.assert_called_once_with(
            'unzip', auto_next=True, workflow_steps=['unzip', 'archive'],
        )
        self.assertEqual(
            runner.calls,
            ['reload', 'unzip', 'archive', ('prune', 'archive')],
        )
        self.assertEqual(starts[0], ('unzip', 0, ('unzip', 'archive')))
        self.assertEqual(
            completions, [('unzip', 'archive'), ('archive', None)],
        )
        self.assertEqual(result.last_step, 'archive')
        self.assertTrue(result.ran_full_from_unzip)

    def test_orchestrator_rejects_unknown_step_before_pruning(self):
        runner = _FakeRunner()
        orchestrator = WorkflowOrchestrator(
            runner, mock.Mock(return_value=['missing']),
        )

        with self.assertRaisesRegex(WorkflowStepError, 'missing'):
            orchestrator.run('unzip')

        self.assertEqual(runner.calls, ['reload'])


if __name__ == '__main__':
    unittest.main()
