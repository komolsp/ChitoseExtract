import json
import os
import copy
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

import pk_logger
from config_file import DEFAULT_CONFIG as RENAMER_DEFAULT_CONFIG
from filter_rules import build_filter_keywords, extra_filter_keywords, resolve_filter_rules

logger = pk_logger.Pk_logger('config_logger', 'log.txt').add_log_handler().get_logger()

CONFIG_PATH = 'config.yaml'
LEGACY_RENAMER_CONFIG_PATH = os.path.join('dlrenamer', 'config.json')

# 缺失时自动补全的非关键项默认值
_CONFIG_DEFAULTS: dict[str, Any] = {
    'logical_deletion': True,
    'del_after_unzip': False,
    'del_after_reunzip': True,
    'auto_next': True,
    'max_thread': 4,
    'thread_threshold_mb': 25.6,
    'thread_compression_ratio': 50,
    'seven_z_mmt': 0,
    'blacklist': [],
}
_STRICT_TOP_LEVEL_BOOLEAN_FIELDS = (
    'logical_deletion',
    'del_after_unzip',
    'del_after_reunzip',
    'auto_next',
)

_AUDIO_CONVERT_DEFAULTS: dict[str, Any] = {
    'source_extensions': ['.wav', '.aif', '.aiff'],
    'flac_compression': 5,
    'delete_source': True,
    'flac_path': '',
    'ffmpeg_fallback_path': '',
    'max_workers': 4,
}

_AUDIO_TAG_DEFAULTS: dict[str, Any] = {
    'extensions': ['.flac', '.mp3', '.wav'],
    'embed_cover': True,
    'save_cover_jpg': True,
    'force_retag': False,
    'cv_max_count': 4,
    'max_workers': 4,
}

# 流水线步骤（顺序固定）；默认仅常规四步参与 auto_next
WORKFLOW_STEP_OPTIONS: tuple[tuple[str, str], ...] = (
    ('unzip', '解压'),
    ('archive', '归档'),
    ('filter', '过滤'),
    ('rename', '重命名'),
    ('convert_audio', '转flac'),
    ('tag_audio', '写入元数据'),
)
WORKFLOW_STEP_IDS: tuple[str, ...] = tuple(step_id for step_id, _ in WORKFLOW_STEP_OPTIONS)
DEFAULT_WORKFLOW_STEPS: dict[str, bool] = {
    'unzip': True,
    'archive': True,
    'filter': True,
    'rename': True,
    'convert_audio': False,
    'tag_audio': False,
}


class ConfigError(Exception):
    """配置文件缺少关键项或格式不正确。"""

    def __init__(self, message: str, details: list[str] | None = None):
        self.details = list(details or [])
        detail_text = '\n'.join(f'  • {item}' for item in self.details)
        if detail_text:
            message = f'{message.rstrip()}\n\n{detail_text}'
        super().__init__(message)


def _require_yaml_bool(value: Any, field_path: str) -> bool:
    """只接受 YAML 原生布尔值，防止非空字符串 ``"false"`` 被当作真。"""
    if type(value) is not bool:
        raise ConfigError(
            '配置文件中的布尔值格式错误：',
            [
                f'{field_path} — 必须写为 true 或 false（不要加引号；'
                f'当前类型：{type(value).__name__}）',
            ],
        )
    return value


def resolve_workflow_steps(raw: Any) -> dict[str, bool]:
    """从 config.workflow_steps 解析各步骤开关。"""
    steps = dict(DEFAULT_WORKFLOW_STEPS)
    if raw is None:
        return steps
    if not isinstance(raw, dict):
        raise ConfigError(
            '配置文件格式错误：',
            ['workflow_steps — 必须是 YAML 映射（键值对）'],
        )
    # 兼容旧版 insert_rj 配置
    if 'archive' not in raw and 'insert_rj' in raw:
        steps['archive'] = _require_yaml_bool(
            raw['insert_rj'], 'workflow_steps.insert_rj',
        )
    for step_id in WORKFLOW_STEP_IDS:
        if step_id in raw:
            steps[step_id] = _require_yaml_bool(
                raw[step_id], f'workflow_steps.{step_id}',
            )
    return steps


def build_run_pipeline(
    start_process: str,
    *,
    auto_next: bool,
    workflow_steps: dict[str, bool] | None = None,
) -> list[str]:
    """按起始步骤与勾选结果生成本次实际执行的步骤列表。

    - 起始步骤始终执行（即使用户在设置里关掉了它）
    - auto_next=False 时只跑起始一步
    - auto_next=True 时从起始步骤起，按固定顺序追加已勾选的后续步骤
    """
    full = list(WORKFLOW_STEP_IDS)
    if start_process not in full:
        return [start_process]
    enabled = resolve_workflow_steps(workflow_steps)
    start_idx = full.index(start_process)
    if not auto_next:
        return [start_process]
    pipeline: list[str] = []
    for index, step_id in enumerate(full):
        if index < start_idx:
            continue
        if index == start_idx or enabled.get(step_id, False):
            pipeline.append(step_id)
    return pipeline


_yaml: YAML | None = None


def _normalize_config_path(path: str) -> str:
    """保存到 YAML 前将 Windows 路径转为正斜杠，避免双引号内 \\ 转义问题。"""
    return str(path or '').strip().replace('\\', '/')


def _get_yaml() -> YAML:
    global _yaml
    if _yaml is None:
        _yaml = YAML()
        _yaml.preserve_quotes = True
        _yaml.default_flow_style = False
        _yaml.width = 4096
        _yaml.indent(mapping=2, sequence=4, offset=2)
    return _yaml


def _to_plain_dict(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_to_plain_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: _to_plain_dict(item) for key, item in value.items()}
    if hasattr(value, 'items'):
        return {key: _to_plain_dict(item) for key, item in value.items()}
    return value


def _load_yaml(path: str):
    with open(path, 'r', encoding='utf-8') as conf:
        data = _get_yaml().load(conf)
    if data is None:
        return CommentedMap()
    return data


def _save_yaml(path: str, data):
    with open(path, 'w', encoding='utf-8') as conf:
        _get_yaml().dump(data, conf)


def _append_bool_field_error(
        errors: list[str], section: dict, key: str, field_path: str) -> None:
    if key not in section or type(section[key]) is bool:
        return
    errors.append(
        f'{field_path} — 必须写为 true 或 false（不要加引号；'
        f'当前类型：{type(section[key]).__name__}）'
    )


def _config_section(
        raw: dict, key: str, errors: list[str], field_path: str | None = None,
        ) -> dict | None:
    field_path = field_path or key
    section = raw.get(key)
    if section is None:
        return None
    if not isinstance(section, dict):
        errors.append(f'{field_path} — 必须是 YAML 映射（键值对）')
        return None
    return section


def _validate_config_boolean_types(raw: dict) -> list[str]:
    """集中校验会控制删除、过滤或自动流程的布尔配置。"""
    errors: list[str] = []
    for key in _STRICT_TOP_LEVEL_BOOLEAN_FIELDS:
        _append_bool_field_error(errors, raw, key, key)

    workflow = _config_section(raw, 'workflow_steps', errors)
    if workflow is not None:
        for key in (*WORKFLOW_STEP_IDS, 'insert_rj'):
            _append_bool_field_error(errors, workflow, key, f'workflow_steps.{key}')

    filter_section = _config_section(raw, 'filter', errors)
    if filter_section is not None:
        for key in ('filter_dir', 'filte_dir'):
            _append_bool_field_error(errors, filter_section, key, f'filter.{key}')
        rules = _config_section(filter_section, 'rules', errors, 'filter.rules')
        if rules is not None:
            for key in rules:
                _append_bool_field_error(errors, rules, key, f'filter.rules.{key}')

    audio_convert = _config_section(raw, 'audio_convert', errors)
    if audio_convert is not None:
        _append_bool_field_error(
            errors, audio_convert, 'delete_source', 'audio_convert.delete_source',
        )

    audio_tag = _config_section(raw, 'audio_tag', errors)
    if audio_tag is not None:
        for key in ('embed_cover', 'save_cover_jpg', 'force_retag'):
            _append_bool_field_error(errors, audio_tag, key, f'audio_tag.{key}')
    return errors


def _prepare_config(raw: dict | None) -> dict:
    if raw is None:
        raise ConfigError(
            f'无法读取配置文件：{os.path.normpath(CONFIG_PATH)}',
            ['请确认 config.yaml 存在且 YAML 格式正确。'],
        )
    if not isinstance(raw, dict):
        raise ConfigError(
            f'配置文件格式错误：{os.path.normpath(CONFIG_PATH)}',
            ['根节点必须是 YAML 映射（键值对），不能是空文件或纯列表。'],
        )

    merged = dict(raw)
    applied_defaults: list[str] = []
    for key, default in _CONFIG_DEFAULTS.items():
        if key not in merged or (
                merged[key] is None and key not in _STRICT_TOP_LEVEL_BOOLEAN_FIELDS):
            merged[key] = default
            applied_defaults.append(key)

    path_section = merged.get('path')
    if not isinstance(path_section, dict):
        path_section = {}
        merged['path'] = path_section

    critical_errors: list[str] = []
    if not str(path_section.get('output') or '').strip():
        critical_errors.append('path.output — 音声库输出路径（必填）')
    if not str(path_section.get('recycle') or '').strip():
        critical_errors.append('path.recycle — 逻辑删除回收站路径（必填）')
    if critical_errors:
        raise ConfigError(
            '配置文件缺少关键项，请打开 config.yaml 补全后重试：',
            critical_errors,
        )

    boolean_errors = _validate_config_boolean_types(merged)
    if boolean_errors:
        raise ConfigError(
            '配置文件中的布尔值格式错误，请修正后重试：',
            boolean_errors,
        )

    if applied_defaults:
        logger.info(
            '配置项缺失，已使用默认值：{}'.format(', '.join(applied_defaults)),
        )
    return merged


class Config:
    def __init__(self):
        raw = get_config(CONFIG_PATH)
        self._apply_config(_prepare_config(raw))

    def load_config(self):
        raw = get_config(CONFIG_PATH)
        if raw is None:
            return
        self._apply_config(_prepare_config(raw))

    def _apply_config(self, raw: dict):
        self._raw = raw
        path_section = raw['path']
        self.output_path = str(path_section['output']).strip()
        self.resource_path = (path_section.get('resource') or '').strip()
        self.recycle_path = str(path_section['recycle']).strip()
        self.logical_deletion = raw['logical_deletion']
        self.del_after_unzip = raw['del_after_unzip']
        filter_section = raw.get('filter') or {}
        self.filter_rules = resolve_filter_rules(filter_section)
        self.filter_extra_kw = extra_filter_keywords(filter_section, self.filter_rules)
        self.filter_kw = build_filter_keywords(self.filter_rules, self.filter_extra_kw)
        self.filter_dir = filter_section.get('filter_dir', filter_section.get('filte_dir', True))
        self.del_after_reunzip = raw['del_after_reunzip']
        self.auto_next = raw['auto_next']
        self.workflow_steps = resolve_workflow_steps(raw.get('workflow_steps'))
        self.max_thread = int(raw['max_thread'])
        self.thread_threshold_mb = float(raw['thread_threshold_mb'])
        self.thread_compression_ratio = float(raw['thread_compression_ratio'])
        self.seven_z_mmt = int(raw.get('seven_z_mmt', 0) or 0)
        self.blacklist = raw.get('blacklist') or []
        self.renamer_config = raw.get('renamer') or dict(RENAMER_DEFAULT_CONFIG)
        self.audio_convert_config = _merge_section_defaults(
            raw.get('audio_convert'),
            _AUDIO_CONVERT_DEFAULTS,
        )
        self.audio_tag_config = _merge_section_defaults(
            raw.get('audio_tag'),
            _AUDIO_TAG_DEFAULTS,
        )


def _merge_section_defaults(section: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    if isinstance(section, dict):
        merged.update(section)
    return merged


def get_config(path):
    try:
        document = _load_yaml(path)
        document = _ensure_renamer_section(document, path)
        document, _ = _normalize_config_keys(document, path)
        return _to_plain_dict(document)
    except Exception as err:
        if isinstance(err, FileNotFoundError):
            logger.error(f'配置文件加载失败："{os.path.normpath(path)}"')
            logger.error(f'FileNotFoundError: {err.strerror}')
        else:
            logger.error(f'配置文件解析失败："{os.path.normpath(path)}"')
            logger.error(f'{type(err).__name__}: {err}')
        return None


def _normalize_config_keys(config, yaml_path: str):
    """将旧版配置键名迁移为统一命名；必要时写回 yaml（保留注释）。"""
    changed = False

    filter_section = config.get('filter')
    if isinstance(filter_section, dict):
        if 'filter_dir' not in filter_section and 'filte_dir' in filter_section:
            filter_section['filter_dir'] = filter_section.pop('filte_dir')
            changed = True

    renamer_section = config.get('renamer')
    if isinstance(renamer_section, dict):
        if 'scanner_max_depth' not in renamer_section and 'scaner_max_depth' in renamer_section:
            renamer_section['scanner_max_depth'] = renamer_section.pop('scaner_max_depth')
            changed = True

    if changed:
        logger.info(f'已迁移旧版配置键名："{os.path.normpath(yaml_path)}"')
        _save_yaml(yaml_path, config)

    return config, changed


def save_settings(updates: dict) -> tuple[bool, str | None]:
    """将设置对话框中的字段写回 config.yaml，尽量保留原有注释。"""
    try:
        document = _load_yaml(CONFIG_PATH)
        document, _ = _normalize_config_keys(document, CONFIG_PATH)
        document = _ensure_renamer_section(document, CONFIG_PATH)

        path_section = document.setdefault('path', CommentedMap())
        if 'output_path' in updates:
            path_section['output'] = _normalize_config_path(updates['output_path'])
        if 'resource_path' in updates:
            path_section['resource'] = _normalize_config_path(updates['resource_path'])
        if 'recycle_path' in updates:
            path_section['recycle'] = _normalize_config_path(updates['recycle_path'])

        for key in ('logical_deletion', 'del_after_unzip', 'del_after_reunzip', 'auto_next',
                    'max_thread'):
            if key in updates:
                document[key] = updates[key]

        if 'workflow_steps' in updates:
            steps_map = CommentedMap()
            resolved = resolve_workflow_steps(updates['workflow_steps'])
            for step_id in WORKFLOW_STEP_IDS:
                steps_map[step_id] = bool(resolved.get(step_id, False))
            document['workflow_steps'] = steps_map

        if 'filter_dir' in updates or 'filter_rules' in updates:
            filter_section = document.setdefault('filter', CommentedMap())
            if 'filter_dir' in updates:
                filter_section['filter_dir'] = updates['filter_dir']
            if 'filter_rules' in updates:
                rules_map = CommentedMap()
                for rule_id, enabled in updates['filter_rules'].items():
                    rules_map[rule_id] = bool(enabled)
                filter_section['rules'] = rules_map
                extras = updates.get('filter_extra_kw') or []
                filter_section['keyword'] = build_filter_keywords(
                    updates['filter_rules'], extras,
                )

        renamer_section = document.setdefault('renamer', CommentedMap())
        renamer_keys = {
            'scraper_locale': 'scraper_locale',
            'scraper_http_proxy': 'scraper_http_proxy',
            'renamer_template': 'renamer_template',
            'renamer_cv_list_left': 'renamer_cv_list_left',
            'renamer_cv_list_right': 'renamer_cv_list_right',
            'renamer_tags_list_left': 'renamer_tags_list_left',
            'renamer_tags_list_right': 'renamer_tags_list_right',
            'renamer_age_cat_left': 'renamer_age_cat_left',
            'renamer_age_cat_right': 'renamer_age_cat_right',
            'renamer_rjcode_display_locales': 'renamer_rjcode_display_locales',
        }
        for update_key, yaml_key in renamer_keys.items():
            if update_key in updates:
                value = updates[update_key]
                if update_key == 'renamer_rjcode_display_locales':
                    from scraper.rjcode_locales import normalize_display_locales
                    renamer_section[yaml_key] = normalize_display_locales(value)
                else:
                    renamer_section[yaml_key] = value

        if 'flac_compression' in updates or 'flac_max_workers' in updates:
            audio_section = document.setdefault('audio_convert', CommentedMap())
            if 'flac_compression' in updates:
                try:
                    level = int(updates['flac_compression'])
                except (TypeError, ValueError):
                    level = 5
                audio_section['flac_compression'] = max(0, min(12, level))
            if 'flac_max_workers' in updates:
                try:
                    workers = int(updates['flac_max_workers'])
                except (TypeError, ValueError):
                    workers = 4
                audio_section['max_workers'] = max(1, min(32, workers))

        if 'tag_max_workers' in updates or 'tag_embed_cover' in updates or 'tag_save_cover_jpg' in updates:
            tag_section = document.setdefault('audio_tag', CommentedMap())
            if 'tag_max_workers' in updates:
                try:
                    workers = int(updates['tag_max_workers'])
                except (TypeError, ValueError):
                    workers = 4
                tag_section['max_workers'] = max(1, min(32, workers))
            if 'tag_embed_cover' in updates:
                tag_section['embed_cover'] = bool(updates['tag_embed_cover'])
            if 'tag_save_cover_jpg' in updates:
                tag_section['save_cover_jpg'] = bool(updates['tag_save_cover_jpg'])

        _save_yaml(CONFIG_PATH, document)
        return True, None
    except Exception as err:
        logger.error(f'保存配置失败："{os.path.normpath(CONFIG_PATH)}"')
        logger.error(f'{type(err).__name__}: {err}')
        return False, str(err)


def _ensure_renamer_section(config, yaml_path: str):
    """确保 config 含有 renamer 段；缺失时从旧版 config.json 迁移。"""
    if config.get('renamer'):
        return config

    if os.path.isfile(LEGACY_RENAMER_CONFIG_PATH):
        with open(LEGACY_RENAMER_CONFIG_PATH, encoding='utf-8') as f:
            legacy = json.load(f)
        if not legacy.get('_deprecated'):
            config['renamer'] = CommentedMap(legacy)
            logger.info(f'已从 {LEGACY_RENAMER_CONFIG_PATH} 迁移重命名配置到 {yaml_path}')
            _save_yaml(yaml_path, config)
        else:
            config['renamer'] = CommentedMap(copy.deepcopy(RENAMER_DEFAULT_CONFIG))
    else:
        config['renamer'] = CommentedMap(copy.deepcopy(RENAMER_DEFAULT_CONFIG))
    return config
