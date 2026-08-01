"""工作流步骤勾选与运行管线生成。"""

import unittest

from config import (
    DEFAULT_WORKFLOW_STEPS,
    build_run_pipeline,
    resolve_workflow_steps,
)


class TestWorkflowSteps(unittest.TestCase):
    def test_defaults(self):
        steps = resolve_workflow_steps(None)
        self.assertEqual(steps, DEFAULT_WORKFLOW_STEPS)
        self.assertTrue(steps['unzip'])
        self.assertFalse(steps['convert_audio'])

    def test_partial_override(self):
        steps = resolve_workflow_steps({'convert_audio': True, 'filter': False})
        self.assertTrue(steps['convert_audio'])
        self.assertFalse(steps['filter'])
        self.assertTrue(steps['rename'])

    def test_auto_next_off_runs_only_start(self):
        pipeline = build_run_pipeline(
            'unzip',
            auto_next=False,
            workflow_steps=DEFAULT_WORKFLOW_STEPS,
        )
        self.assertEqual(pipeline, ['unzip'])

    def test_default_core_pipeline_from_unzip(self):
        pipeline = build_run_pipeline(
            'unzip',
            auto_next=True,
            workflow_steps=DEFAULT_WORKFLOW_STEPS,
        )
        self.assertEqual(pipeline, ['unzip', 'archive', 'filter', 'rename'])

    def test_include_audio_when_enabled(self):
        steps = dict(DEFAULT_WORKFLOW_STEPS)
        steps['convert_audio'] = True
        steps['tag_audio'] = True
        pipeline = build_run_pipeline('unzip', auto_next=True, workflow_steps=steps)
        self.assertEqual(
            pipeline,
            ['unzip', 'archive', 'filter', 'rename', 'convert_audio', 'tag_audio'],
        )

    def test_skip_disabled_middle_step(self):
        steps = dict(DEFAULT_WORKFLOW_STEPS)
        steps['filter'] = False
        pipeline = build_run_pipeline('unzip', auto_next=True, workflow_steps=steps)
        self.assertEqual(pipeline, ['unzip', 'archive', 'rename'])

    def test_start_step_always_included_even_if_disabled(self):
        steps = dict(DEFAULT_WORKFLOW_STEPS)
        steps['filter'] = False
        pipeline = build_run_pipeline('filter', auto_next=True, workflow_steps=steps)
        self.assertEqual(pipeline, ['filter', 'rename'])

    def test_start_from_audio(self):
        steps = dict(DEFAULT_WORKFLOW_STEPS)
        steps['convert_audio'] = True
        steps['tag_audio'] = True
        pipeline = build_run_pipeline(
            'convert_audio',
            auto_next=True,
            workflow_steps=steps,
        )
        self.assertEqual(pipeline, ['convert_audio', 'tag_audio'])


if __name__ == '__main__':
    unittest.main()
