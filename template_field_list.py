"""命名模板字段：可勾选、可拖动排序的列表。"""

from __future__ import annotations

import re
import tkinter as tk

# 避免 template_field_list → gui → task_runner → ez_client 循环导入
_COLORS = {
    'surface': '#ffffff',
    'text': '#3d3530',
    'select': '#fff0f6',
    'surface_alt': '#faf6f1',
    'text_muted': '#8a7f75',
    'border': '#e8dcc8',
    'accent': '#d4567a',
}
_FONT_UI = ('Segoe UI', 10)
_FONT_SMALL = ('Segoe UI', 9)

# (占位符, 中文名)
TEMPLATE_FIELDS = [
    ('rjcode', 'RJ 号'),
    ('work_name', '作品名'),
    ('maker_id', '社团 RG 号'),
    ('maker_name', '社团名'),
    ('series_name', '系列名'),
    ('release_date', '发售日'),
    ('cv_list_str', '声优列表'),
    ('tags_list_str', '标签列表'),
    ('age_cat', '年龄分级'),
]

_BRACKET_FIELDS = frozenset({'rjcode', 'maker_id', 'maker_name', 'series_name'})
# 可在设置界面选择 [] / () 包裹的字段
BRACKET_OPTION_FIELDS = frozenset({
    'rjcode', 'maker_id', 'maker_name', 'series_name',
    'cv_list_str', 'tags_list_str', 'age_cat',
})
_FIELD_LABELS = {key: label for key, label in TEMPLATE_FIELDS}
_FIELD_KEYS = [key for key, _ in TEMPLATE_FIELDS]
_DEFAULT_TEMPLATE = '[rjcode][maker_name] work_name cv_list_str'

BRACKET_STYLE_NONE = 'none'
BRACKET_STYLE_SQUARE = 'square'
BRACKET_STYLE_ROUND = 'round'

CV_ROUND_LEFT = '(CV '
CV_ROUND_RIGHT = ')'

from scraper.rjcode_locales import RJCODE_DISPLAY_LOCALES, RJCODE_LOCALE_LABELS, normalize_display_locales


def parse_bracket_style_from_wrappers(left: str, right: str, *, field: str) -> str:
    left = str(left or '')
    right = str(right or '')
    if not left.strip() and not right.strip():
        return BRACKET_STYLE_NONE
    if left == '[' and right == ']':
        return BRACKET_STYLE_SQUARE
    if field == 'cv_list_str' and left == CV_ROUND_LEFT and right == CV_ROUND_RIGHT:
        return BRACKET_STYLE_ROUND
    if field in {'tags_list_str', 'age_cat'} and left == '(' and right == ')':
        return BRACKET_STYLE_ROUND
    if left or right:
        return BRACKET_STYLE_ROUND
    return BRACKET_STYLE_NONE


def wrapper_values_for_field(field: str, style: str) -> tuple[str, str]:
    if style == BRACKET_STYLE_SQUARE:
        return '[', ']'
    if style == BRACKET_STYLE_ROUND:
        if field == 'cv_list_str':
            return CV_ROUND_LEFT, CV_ROUND_RIGHT
        return '(', ')'
    return '', ''


def parse_bracket_styles_from_template(template: str) -> dict[str, str]:
    """从 renamer_template 解析 _BRACKET_FIELDS 各字段的包裹样式。"""
    styles: dict[str, str] = {}
    for key in _BRACKET_FIELDS:
        if re.search(rf'\[{re.escape(key)}\]', template or ''):
            styles[key] = BRACKET_STYLE_SQUARE
        elif re.search(rf'\({re.escape(key)}\)', template or ''):
            styles[key] = BRACKET_STYLE_ROUND
        elif re.search(rf'(?<![a-z_]){re.escape(key)}(?![a-z_])', template or ''):
            styles[key] = BRACKET_STYLE_NONE
    return styles


def parse_bracket_styles_from_config(renamer_config: dict | None, template: str = '') -> dict[str, str]:
    """合并模板与 config 中的括号设置，供设置界面初始化。"""
    renamer = renamer_config or {}
    styles = parse_bracket_styles_from_template(template)
    styles['cv_list_str'] = parse_bracket_style_from_wrappers(
        renamer.get('renamer_cv_list_left', CV_ROUND_LEFT),
        renamer.get('renamer_cv_list_right', CV_ROUND_RIGHT),
        field='cv_list_str',
    )
    if styles['cv_list_str'] == BRACKET_STYLE_NONE and not renamer:
        styles['cv_list_str'] = BRACKET_STYLE_ROUND
    styles['tags_list_str'] = parse_bracket_style_from_wrappers(
        renamer.get('renamer_tags_list_left', ''),
        renamer.get('renamer_tags_list_right', ''),
        field='tags_list_str',
    )
    styles['age_cat'] = parse_bracket_style_from_wrappers(
        renamer.get('renamer_age_cat_left', '('),
        renamer.get('renamer_age_cat_right', ')'),
        field='age_cat',
    )
    if styles['age_cat'] == BRACKET_STYLE_NONE and renamer.get('renamer_age_cat_left', '('):
        styles['age_cat'] = BRACKET_STYLE_ROUND
    return styles


def build_renamer_wrapper_overrides(bracket_styles: dict[str, str]) -> dict[str, str]:
    """将 UI 括号选择转为 renamer 配置键（cv / tags / age_cat）。"""
    overrides: dict[str, str] = {}
    for field, config_prefix in (
        ('cv_list_str', 'renamer_cv_list'),
        ('tags_list_str', 'renamer_tags_list'),
        ('age_cat', 'renamer_age_cat'),
    ):
        left, right = wrapper_values_for_field(field, bracket_styles.get(field, BRACKET_STYLE_NONE))
        overrides[f'{config_prefix}_left'] = left
        overrides[f'{config_prefix}_right'] = right
    return overrides


_TEMPLATE_TOKEN_RE = re.compile(
    r'\[(rjcode|maker_id|maker_name|series_name)\]|'
    r'\((rjcode|maker_id|maker_name|series_name)\)|'
    r'(?<![a-z_])(rjcode|maker_id|maker_name|series_name|work_name|release_date|cv_list_str|tags_list_str|age_cat)(?![a-z_])'
)


def parse_template_items(template: str) -> list[tuple[str, bool]]:
    """解析 renamer_template → [(key, enabled), ...]，顺序与模板一致。"""
    seen: set[str] = set()
    items: list[tuple[str, bool]] = []

    for match in _TEMPLATE_TOKEN_RE.finditer(template or ''):
        key = match.group(1) or match.group(2) or match.group(3)
        if key in seen:
            continue
        seen.add(key)
        items.append((key, True))

    if not items:
        return parse_template_items(_DEFAULT_TEMPLATE)

    for key in _FIELD_KEYS:
        if key not in seen:
            items.append((key, False))
    return items


def build_template_string(
        items: list[tuple[str, bool]],
        bracket_styles: dict[str, str] | None = None) -> str:
    """按列表顺序与勾选状态生成 renamer_template。"""
    styles = bracket_styles or {}
    parts: list[str] = []
    for key, enabled in items:
        if not enabled:
            continue
        if key in _BRACKET_FIELDS:
            style = styles.get(key, BRACKET_STYLE_NONE)
            if style == BRACKET_STYLE_ROUND:
                token = f'({key})'
            elif style == BRACKET_STYLE_SQUARE:
                token = f'[{key}]'
            else:
                token = key
        else:
            token = key
        if not parts:
            parts.append(token)
            continue
        if token.startswith(('[', '(')):
            if parts[-1].endswith((']', ')')):
                parts[-1] += token
            else:
                parts.append(' ' + token)
        else:
            parts.append(' ' + token)
    return ''.join(parts)


def template_includes_rjcode(template: str) -> bool:
    return any(key == 'rjcode' and enabled for key, enabled in parse_template_items(template))


def _resolve_bracket_styles(
        user_template: str,
        bracket_styles: dict[str, str] | None = None) -> dict[str, str]:
    """合并模板内已有括号样式与 UI 显式传入的样式。"""
    styles = parse_bracket_styles_from_template(user_template)
    if bracket_styles:
        styles.update(bracket_styles)
    return styles


def build_rename_template(template: str, bracket_styles: dict[str, str] | None = None) -> str:
    """重命名执行用模板：用户未启用 RJ 号时临时 prepend [rjcode]。"""
    items = parse_template_items(template)
    styles = _resolve_bracket_styles(template, bracket_styles)
    if template_includes_rjcode(template):
        return build_template_string(items, styles)
    enabled_items = [(key, enabled) for key, enabled in items if enabled]
    if 'rjcode' not in styles:
        styles['rjcode'] = BRACKET_STYLE_SQUARE
    return build_template_string([('rjcode', True)] + enabled_items, styles)


def resolve_rename_template(
        user_template: str,
        bracket_styles: dict[str, str] | None = None) -> tuple[str, bool]:
    """返回 (执行用模板, 重命名后是否移除 RJ 号)。"""
    styles = _resolve_bracket_styles(user_template, bracket_styles)
    if template_includes_rjcode(user_template):
        return build_template_string(parse_template_items(user_template), styles), False
    return build_rename_template(user_template, styles), True


def finalize_folder_name(user_template: str, compiled_name: str) -> str:
    """按用户对 RJ 号的启用状态，得到最终展示的文件夹名。"""
    if template_includes_rjcode(user_template):
        return compiled_name
    from file_ops import strip_rj_from_basename
    return strip_rj_from_basename(compiled_name)


def _settings_checkbutton(parent, text: str, variable: tk.Variable) -> tk.Checkbutton:
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        bg=_COLORS['surface'],
        fg=_COLORS['text'],
        activebackground=_COLORS['select'],
        activeforeground=_COLORS['text'],
        selectcolor=_COLORS['surface'],
        highlightthickness=0,
        bd=0,
        anchor='w',
        font=_FONT_UI,
        cursor='hand2',
    )


class TemplateFieldList(tk.Frame):
    """命名模板字段列表：拖动 ☰ 把手排序，勾选启用。"""

    def __init__(self, parent, *, on_change=None, **kwargs):
        kwargs.setdefault('bg', _COLORS['surface'])
        super().__init__(parent, **kwargs)
        self._on_change = on_change
        self._rows: list[dict] = []
        self._drag_key: str | None = None
        self._drop_index: int | None = None
        self._bracket_vars: dict[str, dict[str, tk.BooleanVar]] = {}
        self._bracket_sync_guard = False
        self._bracket_last_toggle: dict[str, str] = {}
        self._rjcode_locale_vars: dict[str, tk.BooleanVar] = {}

        self.columnconfigure(0, weight=1)

    def _ensure_rjcode_locale_var(self, locale: str) -> tk.BooleanVar:
        if locale not in self._rjcode_locale_vars:
            var = tk.BooleanVar(value=False)
            var.trace_add('write', lambda *_: self._notify_change())
            self._rjcode_locale_vars[locale] = var
        return self._rjcode_locale_vars[locale]

    def set_rjcode_display_locales(self, locales: list[str] | tuple[str, ...] | None):
        selected = set(normalize_display_locales(locales))
        for locale in RJCODE_DISPLAY_LOCALES:
            self._ensure_rjcode_locale_var(locale).set(locale in selected)

    def get_rjcode_display_locales(self) -> list[str]:
        return normalize_display_locales([
            locale
            for locale in RJCODE_DISPLAY_LOCALES
            if self._rjcode_locale_vars.get(locale) and self._rjcode_locale_vars[locale].get()
        ])

    def _ensure_bracket_vars(self, key: str) -> dict[str, tk.BooleanVar]:
        if key not in self._bracket_vars:
            square = tk.BooleanVar(value=False)
            round_ = tk.BooleanVar(value=False)

            def _apply_style(*_args, _key=key, _square=square, _round=round_):
                if _square.get() and _round.get():
                    if getattr(self, '_bracket_sync_guard', False):
                        return
                    self._bracket_sync_guard = True
                    try:
                        if self._bracket_last_toggle.get(_key) == 'round':
                            _square.set(False)
                        else:
                            _round.set(False)
                    finally:
                        self._bracket_sync_guard = False
                if _square.get():
                    self._bracket_last_toggle[_key] = 'square'
                elif _round.get():
                    self._bracket_last_toggle[_key] = 'round'
                self._notify_change()

            self._bracket_last_toggle.setdefault(key, 'square')
            square.trace_add('write', _apply_style)
            round_.trace_add('write', _apply_style)
            self._bracket_vars[key] = {'square': square, 'round': round_}
        return self._bracket_vars[key]

    def set_bracket_styles(self, styles: dict[str, str]):
        for key in BRACKET_OPTION_FIELDS:
            vars_ = self._ensure_bracket_vars(key)
            style = styles.get(key, BRACKET_STYLE_NONE)
            vars_['square'].set(style == BRACKET_STYLE_SQUARE)
            vars_['round'].set(style == BRACKET_STYLE_ROUND)

    def get_bracket_styles(self) -> dict[str, str]:
        styles: dict[str, str] = {}
        for key in BRACKET_OPTION_FIELDS:
            vars_ = self._bracket_vars.get(key)
            if not vars_:
                styles[key] = BRACKET_STYLE_NONE
                continue
            if vars_['square'].get():
                styles[key] = BRACKET_STYLE_SQUARE
            elif vars_['round'].get():
                styles[key] = BRACKET_STYLE_ROUND
            else:
                styles[key] = BRACKET_STYLE_NONE
        return styles

    def get_renamer_wrapper_overrides(self) -> dict[str, str]:
        return build_renamer_wrapper_overrides(self.get_bracket_styles())

    def set_items(self, items: list[tuple[str, bool]]):
        for row in self._rows:
            row['frame'].destroy()
        self._rows.clear()

        for key, enabled in items:
            if key not in _FIELD_LABELS:
                continue
            self._append_row(key, enabled)
        self._relayout()

    def get_items(self) -> list[tuple[str, bool]]:
        return [(row['key'], bool(row['enabled'].get())) for row in self._rows]

    def get_enabled_keys(self) -> set[str]:
        return {key for key, enabled in self.get_items() if enabled}

    def _append_row(self, key: str, enabled: bool):
        frame = tk.Frame(
            self, bg=_COLORS['surface'],
            highlightbackground=_COLORS['border'], highlightthickness=1,
        )

        grip = tk.Label(
            frame, text='☰', bg=_COLORS['surface_alt'], fg=_COLORS['text_muted'],
            font=_FONT_UI, width=2, cursor='fleur',
        )
        grip.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))

        enabled_var = tk.BooleanVar(value=enabled)
        _settings_checkbutton(frame, _FIELD_LABELS[key], enabled_var).pack(
            side=tk.LEFT, padx=(0, 8), pady=4,
        )

        if key in BRACKET_OPTION_FIELDS:
            vars_ = self._ensure_bracket_vars(key)
            _settings_checkbutton(frame, '显示[]', vars_['square']).pack(
                side=tk.LEFT, padx=(0, 8), pady=4,
            )
            _settings_checkbutton(frame, '显示()', vars_['round']).pack(
                side=tk.LEFT, padx=(0, 8), pady=4,
            )
        if key == 'rjcode':
            for locale in RJCODE_DISPLAY_LOCALES:
                _settings_checkbutton(
                    frame,
                    RJCODE_LOCALE_LABELS[locale],
                    self._ensure_rjcode_locale_var(locale),
                ).pack(side=tk.LEFT, padx=(0, 8), pady=4)

        tk.Label(
            frame, text=key, bg=_COLORS['surface'], fg=_COLORS['text_muted'],
            font=_FONT_SMALL,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        enabled_var.trace_add('write', lambda *_: self._notify_change())

        grip.bind('<Button-1>', lambda e, k=key: self._start_drag(k, e))
        grip.bind('<Double-Button-1>', lambda e: 'break')

        self._rows.append({
            'key': key,
            'enabled': enabled_var,
            'frame': frame,
            'grip': grip,
        })

    def _relayout(self):
        self._clear_drop_marks()
        for index, row in enumerate(self._rows):
            row['frame'].grid(row=index, column=0, sticky='ew', pady=2)
        self.columnconfigure(0, weight=1)

    def _clear_drop_marks(self):
        for row in self._rows:
            row['frame'].configure(
                highlightbackground=_COLORS['border'], highlightthickness=1,
            )

    def _row_index(self, key: str) -> int:
        for index, row in enumerate(self._rows):
            if row['key'] == key:
                return index
        return -1

    def _index_at_y(self, y_root: int) -> int:
        for index, row in enumerate(self._rows):
            frame = row['frame']
            top = frame.winfo_rooty()
            bottom = top + frame.winfo_height()
            if y_root < top + (bottom - top) // 2:
                return index
        return len(self._rows) - 1

    def _start_drag(self, key: str, _event):
        self._drag_key = key
        self._drop_index = self._row_index(key)
        self._highlight(key, True)
        top = self.winfo_toplevel()
        top.bind('<B1-Motion>', self._on_drag, add='+')
        top.bind('<ButtonRelease-1>', self._end_drag, add='+')

    def _on_drag(self, event):
        if self._drag_key is None:
            return
        target = self._index_at_y(event.y_root)
        if target < 0:
            return
        if target != self._drop_index:
            self._clear_drop_marks()
            self._highlight(self._drag_key, True)
            self._rows[target]['frame'].configure(
                highlightbackground=_COLORS['accent'], highlightthickness=2,
            )
            self._drop_index = target

    def _end_drag(self, _event):
        top = self.winfo_toplevel()
        top.unbind('<B1-Motion>')
        top.unbind('<ButtonRelease-1>')

        if self._drag_key is not None and self._drop_index is not None:
            from_index = self._row_index(self._drag_key)
            to_index = self._drop_index
            if from_index >= 0 and to_index >= 0 and from_index != to_index:
                row = self._rows.pop(from_index)
                self._rows.insert(to_index, row)
                self._relayout()

        if self._drag_key is not None:
            self._highlight(self._drag_key, False)
        self._drag_key = None
        self._drop_index = None
        self._clear_drop_marks()
        self._notify_change()

    def _highlight(self, key: str, active: bool):
        for row in self._rows:
            if row['key'] == key:
                bg = _COLORS['select'] if active else _COLORS['surface']
                row['frame'].configure(bg=bg)
                row['grip'].configure(bg=_COLORS['select'] if active else _COLORS['surface_alt'])
                break

    def _notify_change(self):
        if self._on_change:
            self._on_change()
