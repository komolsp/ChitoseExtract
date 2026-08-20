"""安全相关 YAML 开关必须使用原生 true/false，不能接受真值字符串。"""

import unittest
from io import StringIO

from ruamel.yaml import YAML

import config


def _base_config() -> dict:
    return {
        'path': {
            'output': 'D:/audio',
            'recycle': 'D:/recycle',
        },
    }


class ConfigBooleanValidationTests(unittest.TestCase):
    def test_unquoted_false_remains_disabled(self):
        raw = _base_config()
        raw['del_after_unzip'] = False

        prepared = config._prepare_config(raw)
        loaded = config.Config.__new__(config.Config)
        loaded._apply_config(prepared)

        self.assertIs(loaded.del_after_unzip, False)

    def test_quoted_false_from_yaml_is_rejected(self):
        raw = YAML().load(StringIO(
            'path:\n'
            '  output: D:/audio\n'
            '  recycle: D:/recycle\n'
            'del_after_unzip: "false"\n'
        ))

        with self.assertRaises(config.ConfigError) as raised:
            config._prepare_config(raw)

        message = str(raised.exception)
        self.assertIn('del_after_unzip', message)
        self.assertIn('不要加引号', message)

    def test_all_destructive_boolean_sections_reject_strings(self):
        cases = (
            ('del_after_reunzip', {'del_after_reunzip': 'false'}),
            ('logical_deletion', {'logical_deletion': None}),
            ('auto_next', {'auto_next': 'off'}),
            ('workflow_steps.filter', {'workflow_steps': {'filter': 'false'}}),
            ('filter.filter_dir', {'filter': {'filter_dir': '0'}}),
            ('filter.rules.mp3', {'filter': {'rules': {'mp3': 'false'}}}),
            (
                'audio_convert.delete_source',
                {'audio_convert': {'delete_source': 'false'}},
            ),
            (
                'audio_tag.force_retag',
                {'audio_tag': {'force_retag': 'false'}},
            ),
        )

        for field_path, overrides in cases:
            with self.subTest(field_path=field_path):
                raw = _base_config()
                raw.update(overrides)
                with self.assertRaises(config.ConfigError) as raised:
                    config._prepare_config(raw)
                self.assertIn(field_path, str(raised.exception))

    def test_reports_all_invalid_boolean_fields_together(self):
        raw = _base_config()
        raw.update({
            'logical_deletion': 'false',
            'del_after_unzip': 'false',
            'workflow_steps': {'filter': 'false'},
        })

        with self.assertRaises(config.ConfigError) as raised:
            config._prepare_config(raw)

        message = str(raised.exception)
        self.assertIn('logical_deletion', message)
        self.assertIn('del_after_unzip', message)
        self.assertIn('workflow_steps.filter', message)

    def test_workflow_resolver_rejects_non_mapping_and_string_values(self):
        for raw in ('false', {'filter': 'false'}):
            with self.subTest(raw=raw):
                with self.assertRaises(config.ConfigError):
                    config.resolve_workflow_steps(raw)


if __name__ == '__main__':
    unittest.main()
