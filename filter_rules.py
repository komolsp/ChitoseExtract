"""过滤规则定义与 config 解析（无 task_runner 依赖，避免循环导入）。"""

import re
from typing import Any

# 预设规则：group 为 GUI 分组标题；label 为勾选项文案；patterns 为底层匹配逻辑
FILTER_GROUPS: tuple[str, ...] = (
    '无 SE 版本',
    '特殊音轨',
    '删除整类文件',
    '其他附件',
)

# GUI 布局：分组在设置面板中的位置与组内列数
FILTER_GROUP_LAYOUT: dict[str, dict[str, int]] = {
    '无 SE 版本': {'row': 0, 'col': 0, 'columns': 2},
    '特殊音轨': {'row': 0, 'col': 1, 'columns': 2},
    '删除整类文件': {'row': 1, 'col': 0, 'colspan': 2, 'columns': 4},
    '其他附件': {'row': 2, 'col': 0, 'colspan': 2, 'columns': 3},
}

# 无 SE / 无效果音常见写法（中日简繁、文件夹名与路径片段）
# 含「効果音カット / 效果音删减版 / 效果音CUT」等切除效果音命名
_NO_SE_CUT_WORD = r'カット|CUT|删减|削減|削除|去除|裁剪|删减版|削減版'
_NO_SE_PATH_MARKER = (
    r'无效果音|無效果音|'
    r'(?:效果音|効果音)(?:[な無无]し|なし|無し|CUT|カット|无|無|删减|削減|削除|去除)|'
    r'(?:NO|无|無)[ _\-]?(?:SE|效果音|効果音|音效)|'
    r'SE(?:[な無无]し|なし|無し|CUT|カット)|'
    r'(?:SE|音|音效)(?:[な無]し|CUT|カット)|'
    r'NOSE|'
    rf'(?:効果音|效果音|SE|音效).{{0,6}}(?:{_NO_SE_CUT_WORD})版?'
)

FILTER_RULES: tuple[dict[str, Any], ...] = (
    {
        'id': 'no_se_wav',
        'group': '无 SE 版本',
        'label': '无 SE 的 WAV',
        'default': True,
        'patterns': [
            (
                r'(?:SE|音|音效|效果音|効果音)(?:[な無无]し|なし|無し|CUT).*\.WAV$|'
                r'(?:NO|无|無)(?:SE|音效|效果音|効果音).*\.WAV$|'
                rf'(?:{_NO_SE_PATH_MARKER}).*\.WAV$'
            ),
        ],
    },
    {
        'id': 'no_se_folder',
        'group': '无 SE 版本',
        'label': '无 SE 文件夹（无 se、SEなし、无效果音 等）',
        'default': True,
        'patterns': [
            (
                r'WAV.*(?:SE|音|音效|效果音|効果音)(?:[な無]し|CUT)[^\\]*$|'
                r'(?:SE|音|音效|效果音|効果音)(?:[な無]し|CUT)[^\.]WAV[^\\]*$|'
                r'WAV.*(?:NO|无|無)(?:SE|音效|效果音|効果音)[^\\]*$|'
                r'(?:NO|无|無)(?:SE|音效|效果音|効果音)[^\.]*WAV[^\\]*$|'
                rf'WAV.*(?:{_NO_SE_PATH_MARKER})[^\\]*$|'
                rf'(?:{_NO_SE_PATH_MARKER})[^\\.]*WAV[^\\]*$'
            ),
            # 文件夹名可带序号前缀，如 03_効果音カット版
            rf'[\\/][^\\/]*(?:{_NO_SE_PATH_MARKER})[^\\/]*(?:[\\/]|$)',
        ],
    },
    {
        'id': 'no_se_all',
        'group': '无 SE 版本',
        'label': '所有无 SE 内容（更彻底，慎用）',
        'default': False,
        'patterns': [
            _NO_SE_PATH_MARKER,
        ],
    },
    {
        'id': 'full',
        'group': '特殊音轨',
        'label': 'FULL 整轨',
        'default': True,
        'patterns': ['FULL'],
    },
    {
        'id': 'reversed',
        'group': '特殊音轨',
        'label': '左右反转音轨',
        'default': True,
        'patterns': ['反転'],
    },
    {
        'id': 'wav',
        'group': '删除整类文件',
        'label': '所有 WAV',
        'default': False,
        'patterns': [r'\.WAV$'],
    },
    {
        'id': 'mp3',
        'group': '删除整类文件',
        'label': '所有 MP3',
        'default': False,
        'patterns': [r'\.MP3$'],
    },
    {
        'id': 'flac',
        'group': '删除整类文件',
        'label': '所有 FLAC',
        'default': False,
        'patterns': [r'\.FLAC$'],
    },
    {
        'id': 'ogg',
        'group': '删除整类文件',
        'label': '所有 OGG / Opus',
        'default': False,
        'patterns': [r'\.(OGG|OPUS)$'],
    },
    {
        'id': 'm4a',
        'group': '删除整类文件',
        'label': '所有 M4A / AAC',
        'default': False,
        'patterns': [r'\.(M4A|AAC)$'],
    },
    {
        'id': 'video',
        'group': '删除整类文件',
        'label': '所有视频',
        'default': False,
        'patterns': [r'\.(MP4|MKV|AVI|WMV|MOV|WEBM|FLV|M4V|TS)$'],
    },
    {
        'id': 'image',
        'group': '删除整类文件',
        'label': '所有图片',
        'default': False,
        'patterns': [r'\.(JPG|JPEG|PNG|GIF|BMP|WEBP|ICO|TIFF)$'],
    },
    {
        'id': 'subtitle',
        'group': '其他附件',
        'label': '字幕',
        'default': False,
        'patterns': [r'\.(SRT|ASS|SSA|VTT|LRC)$'],
    },
    {
        'id': 'text_doc',
        'group': '其他附件',
        'label': '说明文档',
        'default': False,
        'patterns': [r'\.(TXT|PDF|DOC|DOCX|RTF|MD)$'],
    },
    {
        'id': 'macos_junk',
        'group': '其他附件',
        'label': 'macOS 附带垃圾',
        'default': False,
        'patterns': [r'__MACOSX|\.DS_STORE'],
    },
)

FILTER_RULE_IDS: tuple[str, ...] = tuple(rule['id'] for rule in FILTER_RULES)

# 「删除整类文件 / 其他附件」文件按扩展名匹配；勾选 filter_dir 时另用文件夹名模式
EXTENSION_ONLY_RULE_IDS: frozenset[str] = frozenset({
    'wav', 'mp3', 'flac', 'ogg', 'm4a', 'video', 'image', 'subtitle', 'text_doc',
})

# filter_dir 开启时，整类规则可匹配的文件夹名片段（如 01_mp3、02_FLAC）
EXTENSION_ONLY_DIR_NAME_PATTERNS: dict[str, tuple[str, ...]] = {
    'wav': (r'WAV',),
    'mp3': (r'MP3',),
    'flac': (r'FLAC',),
    'ogg': (r'OGG', r'OPUS'),
    'm4a': (r'M4A', r'AAC'),
    'video': (r'(?:MP4|MKV|AVI|WMV|MOV|WEBM|FLV|M4V|TS)',),
    'image': (r'(?:JPG|JPEG|PNG|GIF|BMP|WEBP|ICO|TIFF)',),
}

DEFAULT_FILTER_RULES: dict[str, bool] = {
    rule['id']: bool(rule.get('default', True)) for rule in FILTER_RULES
}

_ALL_PRESET_PATTERNS: frozenset[str] = frozenset(
    pattern for rule in FILTER_RULES for pattern in rule['patterns']
)


def _rule_by_id(rule_id: str) -> dict[str, Any] | None:
    for rule in FILTER_RULES:
        if rule['id'] == rule_id:
            return rule
    return None


def _patterns_for_rule(rule_id: str) -> list[str]:
    rule = _rule_by_id(rule_id)
    return list(rule['patterns']) if rule else []


_LEGACY_PATTERN_RULES: dict[str, str] = {
    'MP3': 'mp3',
}


def _keyword_owned_by_rule(keyword: str) -> str | None:
    legacy = _LEGACY_PATTERN_RULES.get(keyword)
    if legacy:
        return legacy
    for rule in FILTER_RULES:
        if keyword in rule['patterns']:
            return rule['id']
    return None


def directory_name_patterns_for_rule(rule_id: str) -> tuple[str, ...]:
    return EXTENSION_ONLY_DIR_NAME_PATTERNS.get(rule_id, ())


def compile_directory_name_patterns(
    keyword_list: list,
    logger,
) -> list[tuple[Any, str]]:
    """从已启用的整类文件规则生成文件夹名匹配正则（仅 filter_dir 使用）。"""
    compiled: list[tuple[Any, str]] = []
    seen: set[str] = set()
    for key in keyword_list:
        if not key:
            continue
        owner = _keyword_owned_by_rule(key)
        if owner is None or owner not in EXTENSION_ONLY_RULE_IDS:
            continue
        for pattern in directory_name_patterns_for_rule(owner):
            if pattern in seen:
                continue
            try:
                compiled.append((re.compile(pattern, re.IGNORECASE), f'{owner}:dir'))
                seen.add(pattern)
            except re.error as err:
                if logger:
                    logger.warning(f'文件夹名过滤正则无效，已跳过：[ {pattern} ] {err}')
    return compiled


def pattern_allows_directory_match(pattern: str) -> bool:
    """整类扩展名的文件模式不参与路径匹配；无 SE 等路径规则仍可匹配文件夹。"""
    owner = _keyword_owned_by_rule(pattern)
    if owner is None:
        return True
    return owner not in EXTENSION_ONLY_RULE_IDS


def resolve_filter_rules(filter_section: dict | None) -> dict[str, bool]:
    """从 config.filter 解析各规则开关；兼容仅 keyword 列表的旧版配置。"""
    rules = dict(DEFAULT_FILTER_RULES)
    if not isinstance(filter_section, dict):
        return rules

    raw_rules = filter_section.get('rules')
    if isinstance(raw_rules, dict):
        for rule_id in FILTER_RULE_IDS:
            if rule_id in raw_rules:
                rules[rule_id] = bool(raw_rules[rule_id])
        return rules

    keywords = filter_section.get('keyword') or []
    if not isinstance(keywords, list):
        return rules

    for rule_id in FILTER_RULE_IDS:
        patterns = _patterns_for_rule(rule_id)
        rules[rule_id] = bool(patterns) and all(item in keywords for item in patterns)

    return rules


def extra_filter_keywords(filter_section: dict | None, rules: dict[str, bool] | None = None) -> list[str]:
    """keyword 列表中不属于任何预设规则、或对应预设已关闭的自定义正则。"""
    if not isinstance(filter_section, dict):
        return []
    keywords = filter_section.get('keyword') or []
    if not isinstance(keywords, list):
        return []
    resolved = rules if rules is not None else resolve_filter_rules(filter_section)
    extras: list[str] = []
    for keyword in keywords:
        if not isinstance(keyword, str) or not keyword:
            continue
        owner = _keyword_owned_by_rule(keyword)
        if owner is None:
            extras.append(keyword)
        elif not resolved.get(owner, False):
            extras.append(keyword)
    return extras


def build_filter_keywords(
    rules: dict[str, bool],
    extra_keywords: list[str] | None = None,
) -> list[str]:
    """根据勾选的预设规则与自定义 keyword 生成最终过滤正则列表。"""
    keywords: list[str] = []
    for rule in FILTER_RULES:
        if rules.get(rule['id'], False):
            for pattern in rule['patterns']:
                if pattern not in keywords:
                    keywords.append(pattern)
    if extra_keywords:
        for keyword in extra_keywords:
            if keyword and keyword not in keywords:
                keywords.append(keyword)
    return keywords
