"""ChitoseExtract 设置对话框（常用配置项）。"""

from __future__ import annotations

import copy
import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import config
import task_runner
from config import DEFAULT_WORKFLOW_STEPS, WORKFLOW_STEP_OPTIONS
from filter_rules import FILTER_GROUP_LAYOUT, FILTER_GROUPS, FILTER_RULES
from dlrenamer.runner import create_renamer_from_dict
from gui import COLORS, FONT_SMALL, FONT_UI, FONT_UI_BOLD, _set_window_icon
from gui_edit_bindings import bind_edit_shortcuts_recursive
from gui_window import place_toplevel_over_parent
from scraper.work_metadata import WorkMetadata
from template_field_list import (
    TemplateFieldList,
    build_template_string,
    finalize_folder_name,
    parse_bracket_styles_from_config,
    parse_template_items,
    resolve_rename_template,
)

LOCALE_OPTIONS = [
    ('zh_cn', '简体中文'),
    ('ja_jp', '日本語'),
    ('en_us', 'English'),
    ('ko_kr', '한국어'),
    ('zh_tw', '繁體中文'),
]

ERROR_FG = '#c0392b'
ERROR_ENTRY_BG = '#fff5f5'
ERROR_BORDER = '#e57373'
PROXY_EXAMPLE = 'http://127.0.0.1:7890'
PROXY_SCHEME_PREFIX = 'http://'

# config.yaml 注释中的示例作品 RJ363096（桃色CODE / 道草屋）
SAMPLE_PREVIEW_METADATA: WorkMetadata = {
    'rjcode': 'RJ363096',
    'work_name': '道草屋 なつな2...',
    'maker_id': 'RG56961',
    'maker_name': '桃色CODE',
    'release_date': '2024-03-15',
    'series_id': 'SR00001234',
    'series_name': '道草屋',
    'age_category': 'R18',
    'tags': ['舔耳', 'ASMR', '姐姐', '治愈', '耳舐め'],
    'cvs': ['なつな'],
    'cover_url': '',
    'rjcodes_by_locale': {'ja_jp': 'RJ363095', 'zh_cn': 'RJ363096'},
}


def _load_preview_metadata() -> WorkMetadata:
    """优先使用本地刮削缓存中的真实元数据，否则回退到示例作品。"""
    sample = copy.deepcopy(SAMPLE_PREVIEW_METADATA)
    try:
        from scraper.db import WorkMetadataCache, db

        db.connect(reuse_if_open=True)
        for rjcode in ('RJ363096',):
            row = WorkMetadataCache.get_or_none(WorkMetadataCache.rjcode == rjcode)
            if row:
                cached = json.loads(row.metadata)
                return _merge_preview_metadata(sample, cached)
        row = WorkMetadataCache.select().order_by(WorkMetadataCache.rjcode).first()
        if row:
            cached = json.loads(row.metadata)
            return _merge_preview_metadata(sample, cached)
    except Exception:
        pass
    return sample


def _merge_preview_metadata(sample: WorkMetadata, cached: WorkMetadata) -> WorkMetadata:
    """用示例作品补全缓存中缺失的预览字段（如 series_name）。"""
    merged = copy.deepcopy(cached)
    for key, value in sample.items():
        current = merged.get(key)
        if current is None or current == '':
            merged[key] = value
    return merged


def _compile_name_preview(template: str, renamer_overrides: dict | None = None) -> tuple[str, WorkMetadata]:
    metadata = _load_preview_metadata()
    if not template:
        return '', metadata

    exec_template, _strip_rj = resolve_rename_template(template)
    conf = task_runner.conf
    renamer_cfg = copy.deepcopy(conf.renamer_config if conf else {})
    renamer_cfg['renamer_template'] = exec_template
    if renamer_overrides:
        renamer_cfg.update(renamer_overrides)
    # 预览时始终展示 age_cat，便于用户在模板中查看年龄分级效果
    renamer_cfg['renamer_age_cat_ignore_r18'] = False
    renamer, _errors = create_renamer_from_dict(renamer_cfg)
    if not renamer:
        return '', metadata
    try:
        compiled = renamer.preview_folder_name(metadata, exec_template)
        return finalize_folder_name(template, compiled), metadata
    except Exception:
        return '', metadata


def _settings_checkbutton(parent, text: str, variable: tk.Variable) -> tk.Checkbutton:
    """使用原生复选框，避免 ttk/clam 在 Windows 上选中态显示为叉。"""
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        bg=COLORS['surface'],
        fg=COLORS['text'],
        activebackground=COLORS['surface_alt'],
        activeforeground=COLORS['text'],
        selectcolor=COLORS['surface'],
        highlightthickness=0,
        bd=0,
        anchor='w',
        font=FONT_UI,
        cursor='hand2',
    )


class SettingsDialog(tk.Toplevel):
    """常用设置项；高级选项仍可通过 config.yaml 编辑。"""

    _instance: SettingsDialog | None = None

    def __init__(self, parent: tk.Misc, *, on_saved=None):
        if SettingsDialog._instance is not None and SettingsDialog._instance.winfo_exists():
            SettingsDialog._instance._center_over(SettingsDialog._instance._parent)
            SettingsDialog._instance.lift()
            SettingsDialog._instance.focus_force()
            return

        super().__init__(parent)
        self.withdraw()
        SettingsDialog._instance = self
        self._on_saved = on_saved
        self._busy = getattr(parent, '_worker_busy', False)
        self._parent = parent.winfo_toplevel()
        self._template_list: TemplateFieldList | None = None
        self._field_refs: dict[str, dict] = {}

        self.title('设置')
        self.configure(bg=COLORS['bg'])
        self.transient(self._parent)
        self.resizable(True, True)
        self.minsize(920, 540)
        _set_window_icon(self)

        self._vars = self._build_vars()
        self._setup_validation_styles()
        self._build_ui()
        bind_edit_shortcuts_recursive(self)
        self._wire_validation_traces()
        self._load_from_config()
        self._center_over(self._parent)

        self.protocol('WM_DELETE_WINDOW', self._close)
        self._bind_mousewheel_global()
        self.deiconify()
        self.grab_set()
        self.focus_force()

    def _bind_mousewheel_global(self):
        self.bind_all('<MouseWheel>', self._on_mousewheel, add='+')
        self.bind_all('<Button-4>', self._on_mousewheel, add='+')
        self.bind_all('<Button-5>', self._on_mousewheel, add='+')

    def _unbind_mousewheel_global(self):
        self.unbind_all('<MouseWheel>')
        self.unbind_all('<Button-4>')
        self.unbind_all('<Button-5>')

    def _center_over(self, parent: tk.Misc):
        parent_win = parent.winfo_toplevel()
        parent_win.update_idletasks()
        pw = max(parent_win.winfo_width(), 960)
        ph = max(parent_win.winfo_height(), 560)
        # 仅比主窗口略小，避免遮住标题栏/边框观感
        prefer_w = max(920, pw - 40)
        prefer_h = max(540, ph - 40)
        place_toplevel_over_parent(
            self,
            parent_win,
            min_width=920,
            min_height=540,
            prefer_width=prefer_w,
            prefer_height=prefer_h,
            margin_x=24,
            margin_y=24,
        )

    def _on_mousewheel(self, event):
        if not hasattr(self, '_scroll_canvas'):
            return
        canvas = self._scroll_canvas
        if getattr(event, 'num', None) == 5:
            canvas.yview_scroll(1, 'units')
        elif getattr(event, 'num', None) == 4:
            canvas.yview_scroll(-1, 'units')
        elif event.delta:
            canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta < 0 else 1), 'units')

    def _setup_validation_styles(self):
        style = ttk.Style(self)
        style.configure(
            'RequiredError.TLabel',
            background=COLORS['surface'],
            foreground=ERROR_FG,
            font=FONT_UI,
        )
        style.configure(
            'Field.TLabel',
            background=COLORS['surface'],
            foreground=COLORS['text'],
            font=FONT_UI,
        )
        style.configure(
            'RequiredError.TEntry',
            fieldbackground=ERROR_ENTRY_BG,
            bordercolor=ERROR_BORDER,
            lightcolor=ERROR_BORDER,
            darkcolor=ERROR_BORDER,
        )
        style.configure(
            'RequiredError.TSpinbox',
            fieldbackground=ERROR_ENTRY_BG,
            bordercolor=ERROR_BORDER,
            lightcolor=ERROR_BORDER,
            darkcolor=ERROR_BORDER,
        )

    def _register_field(self, field_id: str, label: ttk.Label, widget=None, *, frame=None):
        self._field_refs[field_id] = {
            'label': label,
            'widget': widget,
            'frame': frame,
        }

    def _wire_validation_traces(self):
        for key in ('output_path', 'recycle_path', 'max_thread', 'flac_compression', 'flac_max_workers', 'tag_max_workers'):
            self._vars[key].trace_add('write', lambda *_: self._refresh_validation())

    def _field_errors(self) -> dict[str, bool]:
        errors = {
            'output_path': not self._vars['output_path'].get().strip(),
            'recycle_path': not self._vars['recycle_path'].get().strip(),
            'renamer_template': (
                not self._template_list or not self._template_list.get_enabled_keys()
            ),
        }
        try:
            max_thread = int(self._vars['max_thread'].get())
            errors['max_thread'] = max_thread < 1 or max_thread > 32
        except (tk.TclError, ValueError):
            errors['max_thread'] = True
        try:
            level = int(self._vars['flac_compression'].get())
            errors['flac_compression'] = level < 0 or level > 12
        except (tk.TclError, ValueError):
            errors['flac_compression'] = True
        try:
            workers = int(self._vars['flac_max_workers'].get())
            errors['flac_max_workers'] = workers < 1 or workers > 32
        except (tk.TclError, ValueError):
            errors['flac_max_workers'] = True
        try:
            workers = int(self._vars['tag_max_workers'].get())
            errors['tag_max_workers'] = workers < 1 or workers > 32
        except (tk.TclError, ValueError):
            errors['tag_max_workers'] = True
        return errors

    def _refresh_validation(self):
        if not self._field_refs:
            return
        errors = self._field_errors()
        for field_id, refs in self._field_refs.items():
            invalid = errors.get(field_id, False)
            label = refs['label']
            label.configure(style='RequiredError.TLabel' if invalid else 'Field.TLabel')
            widget = refs.get('widget')
            if widget is not None:
                if field_id in ('max_thread', 'flac_compression', 'flac_max_workers', 'tag_max_workers'):
                    widget.configure(style='RequiredError.TSpinbox' if invalid else 'TSpinbox')
                elif isinstance(widget, ttk.Entry):
                    widget.configure(style='RequiredError.TEntry' if invalid else 'TEntry')
            frame = refs.get('frame')
            if frame is not None:
                if invalid:
                    frame.configure(highlightbackground=ERROR_BORDER, highlightthickness=1)
                else:
                    frame.configure(highlightbackground=COLORS['border'], highlightthickness=1)

    def _on_template_changed(self):
        self._update_template_preview()
        self._refresh_validation()

    def _build_vars(self) -> dict[str, tk.Variable]:
        vars_dict = {
            'output_path': tk.StringVar(),
            'resource_path': tk.StringVar(),
            'recycle_path': tk.StringVar(),
            'logical_deletion': tk.BooleanVar(value=True),
            'del_after_unzip': tk.BooleanVar(value=False),
            'del_after_reunzip': tk.BooleanVar(value=True),
            'auto_next': tk.BooleanVar(value=True),
            'max_thread': tk.IntVar(value=6),
            'flac_compression': tk.IntVar(value=5),
            'flac_max_workers': tk.IntVar(value=4),
            'tag_max_workers': tk.IntVar(value=4),
            'tag_embed_cover': tk.BooleanVar(value=True),
            'tag_save_cover_jpg': tk.BooleanVar(value=True),
            'scraper_locale': tk.StringVar(value='zh_cn'),
            'scraper_http_proxy': tk.StringVar(),
            'filter_dir': tk.BooleanVar(value=True),
        }
        for rule in FILTER_RULES:
            vars_dict[f'filter_rule_{rule["id"]}'] = tk.BooleanVar(
                value=bool(rule.get('default', True)),
            )
        for step_id, _ in WORKFLOW_STEP_OPTIONS:
            vars_dict[f'workflow_step_{step_id}'] = tk.BooleanVar(
                value=bool(DEFAULT_WORKFLOW_STEPS.get(step_id, False)),
            )
        return vars_dict

    def _build_ui(self):
        outer = ttk.Frame(self, padding=(20, 16, 20, 12))
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text='设置', font=FONT_UI_BOLD, foreground=COLORS['accent']).pack(anchor='w')
        ttk.Label(
            header,
            text='常用选项在此修改；高级命名细节等仍可编辑 config.yaml。',
            style='Muted.TLabel',
        ).pack(anchor='w', pady=(4, 0))

        scroll_host = ttk.Frame(outer)
        scroll_host.pack(fill=tk.BOTH, expand=True)
        scroll_host.columnconfigure(0, weight=1)
        scroll_host.rowconfigure(0, weight=1)

        canvas = tk.Canvas(scroll_host, bg=COLORS['bg'], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(scroll_host, orient='vertical', command=canvas.yview)
        body = ttk.Frame(canvas)
        self._scroll_canvas = canvas
        body.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        window_id = canvas.create_window((0, 0), window=body, anchor='nw')
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')

        def _resize_body(event):
            canvas.itemconfigure(window_id, width=event.width)

        canvas.bind('<Configure>', _resize_body)

        self._add_path_section(body)
        self._add_unzip_section(body)
        self._add_workflow_section(body)
        self._add_filter_section(body)
        self._add_renamer_section(body)

        ttk.Separator(outer, orient='horizontal').pack(fill=tk.X, pady=(12, 10))

        footer = ttk.Frame(outer)
        footer.pack(fill=tk.X)
        footer.columnconfigure(0, weight=1)
        footer.columnconfigure(1, weight=1)

        aux_row = ttk.Frame(footer)
        aux_row.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        ttk.Button(
            aux_row, text='打开 config.yaml', style='Ghost.TButton', command=self._open_yaml,
        ).pack(side=tk.LEFT)
        if self._busy:
            ttk.Label(
                aux_row,
                text='任务运行中，请等待完成后再保存设置。',
                style='Muted.TLabel',
            ).pack(side=tk.LEFT, padx=(12, 0))

        self._save_btn = ttk.Button(
            footer, text='保存', style='Accent.TButton', command=self._save,
        )
        ttk.Button(footer, text='取消', command=self._close).grid(row=1, column=0, sticky='ew', padx=(0, 6))
        self._save_btn.grid(row=1, column=1, sticky='ew', padx=(6, 0))

        if self._busy:
            self._save_btn.configure(state=tk.DISABLED)

    def _add_section(self, parent, title: str) -> ttk.LabelFrame:
        section = ttk.LabelFrame(parent, text=title, style='Card.TLabelframe', padding=(14, 12))
        section.pack(fill=tk.X, pady=(0, 12))
        section.columnconfigure(1, weight=1)
        return section

    def _add_labeled_row(
        self, parent, row: int, label: str, widget, hint: str = '',
        *, field_id: str | None = None, input_widget=None, frame=None,
    ):
        label_widget = ttk.Label(parent, text=label, style='Field.TLabel', background=COLORS['surface'])
        label_widget.grid(row=row, column=0, sticky='nw', padx=(0, 10), pady=(0, 8))
        widget.grid(row=row, column=1, sticky='ew', pady=(0, 8 if not hint else 2))
        if hint:
            ttk.Label(parent, text=hint, style='Muted.TLabel', background=COLORS['surface']).grid(
                row=row + 1, column=1, sticky='w', pady=(0, 8))
        if field_id:
            self._register_field(field_id, label_widget, input_widget, frame=frame)
        return label_widget

    def _path_row(self, parent, row: int, label: str, var: tk.StringVar, field_id: str, hint: str = ''):
        frame = ttk.Frame(parent, style='Surface.TFrame')
        entry = ttk.Entry(frame, textvariable=var)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(
            frame, text='浏览…', style='Ghost.TButton',
            command=lambda: self._browse_directory(var),
        ).pack(side=tk.LEFT, padx=(8, 0))
        self._add_labeled_row(parent, row, label, frame, hint, field_id=field_id, input_widget=entry)
        return row + (2 if hint else 1)

    def _add_path_section(self, parent):
        section = self._add_section(parent, '路径')
        row = 0
        row = self._path_row(
            section, row, '音声库',
            self._vars['output_path'],
            'output_path',
            '解压后的作品输出目录。',
        )
        row = self._path_row(
            section, row, '资源库',
            self._vars['resource_path'],
            'resource_path',
            '解压后未识别到 RJ 号的作品会移入此目录；留空则保留在音声库。',
        )
        self._path_row(
            section, row, '回收站',
            self._vars['recycle_path'],
            'recycle_path',
            '逻辑删除时，被过滤的文件会移到这里。',
        )

    def _add_unzip_section(self, parent):
        section = self._add_section(parent, '解压与删除')
        checks = tk.Frame(section, bg=COLORS['surface'])
        checks.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 8))

        for text, key in (
            ('逻辑删除（移到回收站，而非永久删除）', 'logical_deletion'),
            ('首次解压后删除原始压缩包', 'del_after_unzip'),
            ('套娃解压后删除中间层压缩包', 'del_after_reunzip'),
        ):
            _settings_checkbutton(checks, text, self._vars[key]).pack(anchor='w', pady=2)

        thread_frame = ttk.Frame(section, style='Surface.TFrame')
        ttk.Label(
            thread_frame, text='并行解压进程数', background=COLORS['surface'], foreground=COLORS['text_muted'],
            font=FONT_SMALL,
        ).pack(side=tk.LEFT)
        spin = ttk.Spinbox(
            thread_frame, from_=1, to=32, width=6, textvariable=self._vars['max_thread'],
        )
        spin.pack(side=tk.LEFT, padx=(10, 0))
        self._add_labeled_row(
            section, 1, '性能', thread_frame,
            'SSD 建议 6–10；过高可能导致磁盘争用。',
            field_id='max_thread', input_widget=spin,
        )

    def _fill_filter_group(self, parent, rules: list, columns: int = 1):
        inner = tk.Frame(parent, bg=COLORS['surface'])
        inner.pack(fill='both', expand=True, anchor='nw')
        columns = max(1, columns)
        for col in range(columns):
            inner.columnconfigure(col, weight=1)
        for index, rule in enumerate(rules):
            row, col = divmod(index, columns)
            _settings_checkbutton(
                inner,
                rule['label'],
                self._vars[f'filter_rule_{rule["id"]}'],
            ).grid(row=row, column=col, sticky='w', pady=2, padx=(0, 12))

    def _add_filter_section(self, parent):
        section = self._add_section(parent, '过滤规则')
        ttk.Label(
            section,
            text='解压后按勾选内容删除不需要的文件/文件夹。',
            style='Muted.TLabel',
            background=COLORS['surface'],
        ).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 4))
        ttk.Label(
            section,
            text='作品若只有 MP3、没有 WAV，会自动保留 MP3，避免误删音源。',
            style='Muted.TLabel',
            background=COLORS['surface'],
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 8))

        groups_host = tk.Frame(section, bg=COLORS['surface'])
        groups_host.grid(row=2, column=0, columnspan=2, sticky='ew')
        groups_host.columnconfigure(0, weight=1)
        groups_host.columnconfigure(1, weight=1)

        rules_by_group: dict[str, list] = {name: [] for name in FILTER_GROUPS}
        for rule in FILTER_RULES:
            rules_by_group.setdefault(rule.get('group', '其他附件'), []).append(rule)

        for group_name in FILTER_GROUPS:
            group_rules = rules_by_group.get(group_name) or []
            if not group_rules:
                continue
            layout = FILTER_GROUP_LAYOUT.get(group_name, {'row': 0, 'col': 0, 'columns': 2})
            group_frame = ttk.LabelFrame(
                groups_host,
                text=group_name,
                style='Card.TLabelframe',
                padding=(10, 6),
            )
            group_frame.grid(
                row=layout.get('row', 0),
                column=layout.get('col', 0),
                columnspan=layout.get('colspan', 1),
                sticky='nsew',
                padx=(0, 8) if layout.get('col', 0) == 0 else 0,
                pady=(0, 8),
            )
            self._fill_filter_group(group_frame, group_rules, layout.get('columns', 2))

        _settings_checkbutton(
            section,
            '同时删除匹配的文件夹（关闭时只删文件）',
            self._vars['filter_dir'],
        ).grid(row=3, column=0, columnspan=2, sticky='w', pady=(4, 0))

    def _add_workflow_section(self, parent):
        section = self._add_section(parent, '工作流')
        _settings_checkbutton(
            section,
            '启动后自动执行后续已勾选步骤（从侧栏选中的步骤开始）',
            self._vars['auto_next'],
        ).grid(row=0, column=0, columnspan=2, sticky='w')

        ttk.Label(
            section,
            text='流水线步骤（勾选后才会被「自动后续」执行；侧栏仍可单独启动任一步）',
            background=COLORS['surface'],
            foreground=COLORS['text_muted'],
            font=FONT_SMALL,
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(10, 4))

        steps_frame = ttk.Frame(section, style='Surface.TFrame')
        steps_frame.grid(row=2, column=0, columnspan=2, sticky='ew')
        for col in range(3):
            steps_frame.columnconfigure(col, weight=1)
        for index, (step_id, label) in enumerate(WORKFLOW_STEP_OPTIONS):
            row, col = divmod(index, 3)
            _settings_checkbutton(
                steps_frame,
                label,
                self._vars[f'workflow_step_{step_id}'],
            ).grid(row=row, column=col, sticky='w', padx=(0, 12), pady=2)

        level_frame = ttk.Frame(section, style='Surface.TFrame')
        ttk.Label(
            level_frame,
            text='压缩等级',
            background=COLORS['surface'],
            foreground=COLORS['text_muted'],
            font=FONT_SMALL,
        ).pack(side=tk.LEFT)
        spin = ttk.Spinbox(
            level_frame,
            from_=0,
            to=12,
            width=6,
            textvariable=self._vars['flac_compression'],
        )
        spin.pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(
            level_frame,
            text='  （0 最快 / 5 默认 / 12 最小）',
            background=COLORS['surface'],
            foreground=COLORS['text_muted'],
            font=FONT_SMALL,
        ).pack(side=tk.LEFT)
        self._add_labeled_row(
            section,
            3,
            'FLAC 压缩',
            level_frame,
            '转flac 步骤使用；等级越高文件越小、耗时越长。',
            field_id='flac_compression',
            input_widget=spin,
        )

        workers_frame = ttk.Frame(section, style='Surface.TFrame')
        ttk.Label(
            workers_frame,
            text='并行线程',
            background=COLORS['surface'],
            foreground=COLORS['text_muted'],
            font=FONT_SMALL,
        ).pack(side=tk.LEFT)
        workers_spin = ttk.Spinbox(
            workers_frame,
            from_=1,
            to=32,
            width=6,
            textvariable=self._vars['flac_max_workers'],
        )
        workers_spin.pack(side=tk.LEFT, padx=(10, 0))
        self._add_labeled_row(
            section,
            5,
            '转flac 并行',
            workers_frame,
            '同时转换的文件数；SSD 可适当提高，过高可能磁盘争用。',
            field_id='flac_max_workers',
            input_widget=workers_spin,
        )

        tag_workers_frame = ttk.Frame(section, style='Surface.TFrame')
        ttk.Label(
            tag_workers_frame,
            text='并行线程',
            background=COLORS['surface'],
            foreground=COLORS['text_muted'],
            font=FONT_SMALL,
        ).pack(side=tk.LEFT)
        tag_workers_spin = ttk.Spinbox(
            tag_workers_frame,
            from_=1,
            to=32,
            width=6,
            textvariable=self._vars['tag_max_workers'],
        )
        tag_workers_spin.pack(side=tk.LEFT, padx=(10, 0))
        self._add_labeled_row(
            section,
            7,
            '写入元数据并行',
            tag_workers_frame,
            '同时写入标签的文件数；嵌入封面时磁盘写入较多。',
            field_id='tag_max_workers',
            input_widget=tag_workers_spin,
        )

        _settings_checkbutton(
            section,
            '嵌入封面到音频文件（关闭后仅写文本标签，速度更快）',
            self._vars['tag_embed_cover'],
        ).grid(row=9, column=0, columnspan=2, sticky='w', pady=(4, 0))

        _settings_checkbutton(
            section,
            '在含音频的子目录保存 cover.jpg',
            self._vars['tag_save_cover_jpg'],
        ).grid(row=10, column=0, columnspan=2, sticky='w', pady=(4, 0))

    def _add_renamer_section(self, parent):
        section = self._add_section(parent, '重命名')
        locale_frame = ttk.Frame(section, style='Surface.TFrame')
        values = [code for code, _ in LOCALE_OPTIONS]
        combo = ttk.Combobox(
            locale_frame, textvariable=self._vars['scraper_locale'],
            values=values, state='readonly', width=12,
        )
        combo.pack(side=tk.LEFT)
        ttk.Label(
            locale_frame, text='  修改语言后重新刮削时会自动刷新该作品的缓存',
            style='Muted.TLabel', background=COLORS['surface'],
        ).pack(side=tk.LEFT, padx=(8, 0))
        self._add_labeled_row(section, 0, '元数据语言', locale_frame)

        proxy_frame = ttk.Frame(section, style='Surface.TFrame')
        self._proxy_entry = ttk.Entry(proxy_frame, textvariable=self._vars['scraper_http_proxy'])
        self._proxy_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(
            proxy_frame,
            text=f'  示例：{PROXY_EXAMPLE}',
            style='Muted.TLabel',
            background=COLORS['surface'],
        ).pack(side=tk.LEFT, padx=(8, 0))
        self._add_labeled_row(
            section, 1, 'HTTP 代理', proxy_frame,
            '留空则使用系统代理。',
        )

        template_frame = tk.Frame(
            section, bg=COLORS['surface'],
            highlightbackground=COLORS['border'], highlightthickness=1, bd=0,
        )
        template_frame.columnconfigure(0, weight=1)

        ttk.Label(
            template_frame, text='拖动 ☰ 调整顺序，勾选启用字段',
            background=COLORS['surface'], foreground=COLORS['text_muted'], font=FONT_SMALL,
        ).grid(row=0, column=0, sticky='w', pady=(0, 6))

        self._template_list = TemplateFieldList(
            template_frame, on_change=self._on_template_changed,
        )
        self._template_list.grid(row=1, column=0, sticky='ew')

        preview_box = tk.Frame(
            template_frame, bg=COLORS['surface_alt'],
            highlightbackground=COLORS['border'], highlightthickness=1, bd=0,
        )
        preview_box.grid(row=2, column=0, sticky='ew', pady=(10, 0))
        preview_box.columnconfigure(0, weight=1)

        ttk.Label(
            preview_box, text='预览', background=COLORS['surface_alt'],
            foreground=COLORS['text_muted'], font=FONT_SMALL,
        ).grid(row=0, column=0, sticky='w', padx=10, pady=(8, 2))

        self._preview_var = tk.StringVar()
        self._preview_entry = ttk.Entry(
            preview_box, textvariable=self._preview_var,
            state='readonly', font=FONT_UI,
        )
        self._preview_entry.grid(row=1, column=0, sticky='ew', padx=10, pady=(0, 8))

        self._add_labeled_row(
            section, 2, '命名模板', template_frame,
            '拖动排序决定先后；未启用 RJ 号时，重命名完成后会自动从文件夹名中移除 RJ。',
            field_id='renamer_template', frame=template_frame,
        )

    def _current_template_string(self) -> str:
        if not self._template_list:
            return ''
        return build_template_string(
            self._template_list.get_items(),
            self._template_list.get_bracket_styles(),
        )

    def _set_preview_text(self, text: str):
        entry = self._preview_entry
        entry.configure(state='normal')
        self._preview_var.set(text)
        entry.configure(state='readonly')
        entry.xview_moveto(0)

    def _update_template_preview(self, *_args):
        if not hasattr(self, '_preview_entry') or not self._template_list:
            return

        enabled = self._template_list.get_enabled_keys()
        if not enabled:
            self._set_preview_text('请至少启用一个命名字段')
            return

        template = self._current_template_string()
        preview_overrides = self._template_list.get_renamer_wrapper_overrides()
        preview_overrides['renamer_rjcode_display_locales'] = self._template_list.get_rjcode_display_locales()
        compiled, _metadata = _compile_name_preview(template, preview_overrides)
        if compiled:
            self._set_preview_text(compiled)
        else:
            self._set_preview_text(f'（预览生成失败，模板：{template}）')
        if hasattr(self, '_scroll_canvas'):
            self._scroll_canvas.configure(scrollregion=self._scroll_canvas.bbox('all'))

    def _browse_directory(self, var: tk.StringVar):
        initial = var.get().strip() or os.path.expanduser('~')
        if not os.path.isdir(initial):
            initial = os.path.dirname(initial) if os.path.isfile(initial) else os.path.expanduser('~')
        chosen = filedialog.askdirectory(parent=self, title='选择文件夹', initialdir=initial)
        if chosen:
            var.set(chosen)

    def _load_from_config(self):
        conf = task_runner.conf
        if conf is None:
            conf = config.Config()
        renamer = conf.renamer_config or {}
        proxy = renamer.get('scraper_http_proxy')
        self._vars['output_path'].set(conf.output_path)
        self._vars['resource_path'].set(getattr(conf, 'resource_path', '') or '')
        self._vars['recycle_path'].set(conf.recycle_path)
        self._vars['logical_deletion'].set(conf.logical_deletion)
        self._vars['del_after_unzip'].set(conf.del_after_unzip)
        self._vars['del_after_reunzip'].set(conf.del_after_reunzip)
        self._vars['auto_next'].set(conf.auto_next)
        workflow_steps = getattr(conf, 'workflow_steps', None) or dict(DEFAULT_WORKFLOW_STEPS)
        for step_id, _ in WORKFLOW_STEP_OPTIONS:
            self._vars[f'workflow_step_{step_id}'].set(
                bool(workflow_steps.get(step_id, DEFAULT_WORKFLOW_STEPS.get(step_id, False))),
            )
        self._vars['filter_dir'].set(conf.filter_dir)
        for rule in FILTER_RULES:
            self._vars[f'filter_rule_{rule["id"]}'].set(
                conf.filter_rules.get(rule['id'], True),
            )
        self._vars['max_thread'].set(conf.max_thread)
        audio_convert = getattr(conf, 'audio_convert_config', None) or {}
        try:
            flac_level = int(audio_convert.get('flac_compression', 5))
        except (TypeError, ValueError):
            flac_level = 5
        self._vars['flac_compression'].set(max(0, min(12, flac_level)))
        try:
            flac_workers = int(audio_convert.get('max_workers', 4))
        except (TypeError, ValueError):
            flac_workers = 4
        self._vars['flac_max_workers'].set(max(1, min(32, flac_workers)))
        audio_tag = getattr(conf, 'audio_tag_config', None) or {}
        try:
            tag_workers = int(audio_tag.get('max_workers', 4))
        except (TypeError, ValueError):
            tag_workers = 4
        self._vars['tag_max_workers'].set(max(1, min(32, tag_workers)))
        self._vars['tag_embed_cover'].set(bool(audio_tag.get('embed_cover', True)))
        self._vars['tag_save_cover_jpg'].set(bool(audio_tag.get('save_cover_jpg', True)))
        self._vars['scraper_locale'].set(str(renamer.get('scraper_locale', 'zh_cn')))
        proxy_text = '' if proxy in (None, 'null') else str(proxy)
        self._vars['scraper_http_proxy'].set(proxy_text or PROXY_SCHEME_PREFIX)
        if hasattr(self, '_proxy_entry'):
            self._proxy_entry.icursor('end')
        if self._template_list:
            template = str(renamer.get('renamer_template', ''))
            items = parse_template_items(template)
            self._template_list.set_items(items)
            self._template_list.set_bracket_styles(
                parse_bracket_styles_from_config(renamer, template),
            )
            self._template_list.set_rjcode_display_locales(
                renamer.get('renamer_rjcode_display_locales'),
            )
        self._update_template_preview()
        self._refresh_validation()

    def _validate(self) -> str | None:
        output = self._vars['output_path'].get().strip()
        recycle = self._vars['recycle_path'].get().strip()
        resource = self._vars['resource_path'].get().strip()
        if not output:
            return '请填写音声库路径。'
        if not recycle:
            return '请填写回收站路径。'
        if resource and os.path.normcase(resource) == os.path.normcase(output):
            return '资源库路径不能与音声库相同。'
        if not self._template_list or not self._template_list.get_enabled_keys():
            return '请至少启用一个命名字段。'
        template = self._current_template_string()
        if not template:
            return '请至少启用一个命名字段。'
        try:
            max_thread = int(self._vars['max_thread'].get())
        except (tk.TclError, ValueError):
            return '并行解压进程数必须是整数。'
        if max_thread < 1 or max_thread > 32:
            return '并行解压进程数应在 1–32 之间。'
        try:
            flac_level = int(self._vars['flac_compression'].get())
        except (tk.TclError, ValueError):
            return 'FLAC 压缩等级必须是整数。'
        if flac_level < 0 or flac_level > 12:
            return 'FLAC 压缩等级应在 0–12 之间。'
        try:
            flac_workers = int(self._vars['flac_max_workers'].get())
        except (tk.TclError, ValueError):
            return '转flac 并行线程数必须是整数。'
        if flac_workers < 1 or flac_workers > 32:
            return '转flac 并行线程数应在 1–32 之间。'
        try:
            tag_workers = int(self._vars['tag_max_workers'].get())
        except (tk.TclError, ValueError):
            return '写入元数据并行线程数必须是整数。'
        if tag_workers < 1 or tag_workers > 32:
            return '写入元数据并行线程数应在 1–32 之间。'
        return None

    def _collect_updates(self) -> dict:
        proxy = self._vars['scraper_http_proxy'].get().strip()
        if proxy.rstrip('/') in ('http:', 'https:'):
            # 用户没有在预填的 http:// 后面补充地址，视为未填写
            proxy = ''
        wrapper_overrides = {}
        if self._template_list:
            wrapper_overrides = self._template_list.get_renamer_wrapper_overrides()
        filter_rules = {
            rule['id']: bool(self._vars[f'filter_rule_{rule["id"]}'].get())
            for rule in FILTER_RULES
        }
        filter_extra_kw = []
        if task_runner.conf:
            filter_extra_kw = list(getattr(task_runner.conf, 'filter_extra_kw', []) or [])
        return {
            'output_path': self._vars['output_path'].get().strip(),
            'resource_path': self._vars['resource_path'].get().strip(),
            'recycle_path': self._vars['recycle_path'].get().strip(),
            'logical_deletion': bool(self._vars['logical_deletion'].get()),
            'del_after_unzip': bool(self._vars['del_after_unzip'].get()),
            'del_after_reunzip': bool(self._vars['del_after_reunzip'].get()),
            'auto_next': bool(self._vars['auto_next'].get()),
            'workflow_steps': {
                step_id: bool(self._vars[f'workflow_step_{step_id}'].get())
                for step_id, _ in WORKFLOW_STEP_OPTIONS
            },
            'filter_dir': bool(self._vars['filter_dir'].get()),
            'filter_rules': filter_rules,
            'filter_extra_kw': filter_extra_kw,
            'max_thread': int(self._vars['max_thread'].get()),
            'flac_compression': int(self._vars['flac_compression'].get()),
            'flac_max_workers': int(self._vars['flac_max_workers'].get()),
            'tag_max_workers': int(self._vars['tag_max_workers'].get()),
            'tag_embed_cover': bool(self._vars['tag_embed_cover'].get()),
            'tag_save_cover_jpg': bool(self._vars['tag_save_cover_jpg'].get()),
            'scraper_locale': self._vars['scraper_locale'].get().strip(),
            'scraper_http_proxy': proxy if proxy else None,
            'renamer_template': self._current_template_string(),
            'renamer_rjcode_display_locales': (
                self._template_list.get_rjcode_display_locales() if self._template_list else []
            ),
            **wrapper_overrides,
        }

    def _save(self):
        if self._busy:
            return
        error = self._validate()
        self._refresh_validation()
        if error:
            messagebox.showerror('无法保存', error, parent=self)
            return

        self.focus_set()
        self.update_idletasks()

        ok, err = config.save_settings(self._collect_updates())
        if not ok:
            messagebox.showerror('保存失败', err or '未知错误', parent=self)
            return

        task_runner.reload()
        if self._on_saved:
            self._on_saved(task_runner.conf.output_path)
        self._close()

    def _open_yaml(self):
        yaml_path = os.path.abspath(config.CONFIG_PATH)
        if os.path.isfile(yaml_path):
            os.startfile(yaml_path)
        else:
            messagebox.showwarning('文件不存在', f'未找到 {yaml_path}', parent=self)

    def _close(self):
        SettingsDialog._instance = None
        self._unbind_mousewheel_global()
        self.grab_release()
        self.destroy()


def open_settings_dialog(parent: tk.Misc, *, on_saved=None):
    SettingsDialog(parent, on_saved=on_saved)
