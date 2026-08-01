import os
import queue
import re
import sys
import threading
import time

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

import windnd

import app_paths
import config
import disk_io_monitor
import gif_player
import pk_logger
from ui_scaling import logical_to_pixels

import task_runner
from zip import Zip

UI = None
Window = None
output = ''
_drop_queue = queue.Queue()
_bring_to_front_pending = False
_ICON_PATH = app_paths.bundle_path('assets', 'icon.ico')

# Light theme: pink · white · gold/platinum
COLORS = {
    'bg': '#fffdfb',
    'surface': '#ffffff',
    'surface_alt': '#faf6f1',
    'border': '#e8dcc8',
    'text': '#4a3d42',
    'text_muted': '#9a858f',
    'accent': '#e88aad',
    'accent_hover': '#f0a0bd',
    'accent_dim': '#c97294',
    'gold': '#c9a227',
    'gold_light': '#e8d5a8',
    'gold_dim': '#a8864a',
    'log_bg': '#fffefb',
    'log_fg': '#4a4048',
    'header': '#f8f4ef',
    'drop_zone': '#fdf8fa',
    'tree_odd': '#ffffff',
    'tree_even': '#fdf6f9',
    'select': '#fce8f0',
}

FONT_UI = ('Segoe UI', 10)
FONT_UI_BOLD = ('Segoe UI', 10, 'bold')
FONT_TITLE = ('Segoe UI', 13, 'bold')
FONT_LOG = ('Consolas', 10)
FONT_SMALL = ('Segoe UI', 9)
FONT_DISK = ('Consolas', 9)
DISK_SPEED_LABEL_WIDTH = 14
STATUS_GIF_SIZE = 40
STATUS_GIF_GAP = 10
STATUS_TOP_ROW_HEIGHT = STATUS_GIF_SIZE + 8
STATUS_BANNER_ROW_HEIGHT = 22
STATUS_BANNER_INLINE_MAX = 36

STEP_OPTIONS = [
    ('unzip', '解压'),
    ('archive', '归档'),
    ('filter', '过滤'),
    ('rename', '重命名'),
]

# 音频后处理：默认不勾选进 auto_next，可在设置「工作流」中启用
AUDIO_STEP_OPTIONS = [
    ('convert_audio', '转flac'),
    ('tag_audio', '写入元数据'),
]

CORE_PIPELINE = [value for value, _ in STEP_OPTIONS]
AUDIO_PIPELINE = [value for value, _ in AUDIO_STEP_OPTIONS]
ALL_STEP_OPTIONS = STEP_OPTIONS + AUDIO_STEP_OPTIONS
ALL_PIPELINE = [value for value, _ in ALL_STEP_OPTIONS]


def _format_elapsed(seconds: float) -> str:
    """将秒数格式化为可读的用时文案。"""
    if seconds < 0:
        seconds = 0
    total = int(seconds)
    if total < 60:
        if seconds < 10:
            return f'用时 {seconds:.1f} 秒'
        return f'用时 {total} 秒'
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f'用时 {hours} 小时 {minutes} 分 {secs} 秒'
    return f'用时 {minutes} 分 {secs} 秒'


def _set_window_icon(window: tk.Tk):
    if not os.path.isfile(_ICON_PATH):
        return
    try:
        window.iconbitmap(default=_ICON_PATH)
    except tk.TclError:
        try:
            window.iconbitmap(_ICON_PATH)
        except tk.TclError:
            pass
    try:
        from PIL import Image, ImageTk
        png_path = app_paths.bundle_path('assets', 'icon.png')
        if os.path.isfile(png_path):
            window.update_idletasks()
            # iconphoto 会参与任务栏/标题栏显示；此前硬编码 64px 在 125%/150% 缩放下
            # 会被系统再次放大而发糊。按 DPI 取足够大的物理像素，且不超过源图。
            side = logical_to_pixels(window, 48)
            image = Image.open(png_path).convert('RGBA')
            native = max(image.size)
            side = min(native, max(side, 48), 256)
            if native != side:
                image = image.resize((side, side), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            window.iconphoto(True, photo)
            window._app_icon_photo = photo
    except Exception:
        pass


def setup_theme(root: tk.Tk):
    root.configure(bg=COLORS['bg'])
    style = ttk.Style(root)
    style.theme_use('clam')

    style.configure('.', background=COLORS['bg'], foreground=COLORS['text'], font=FONT_UI)
    style.configure('TFrame', background=COLORS['bg'])
    style.configure('Surface.TFrame', background=COLORS['surface'])
    style.configure('Header.TFrame', background=COLORS['header'])
    style.configure('Card.TLabelframe', background=COLORS['surface'], foreground=COLORS['text'],
                    bordercolor=COLORS['border'], relief='flat')
    style.configure('Card.TLabelframe.Label', background=COLORS['surface'], foreground=COLORS['text_muted'],
                    font=FONT_UI_BOLD)
    style.configure('TLabel', background=COLORS['bg'], foreground=COLORS['text'])
    style.configure('Muted.TLabel', background=COLORS['bg'], foreground=COLORS['text_muted'], font=FONT_SMALL)
    style.configure('Header.TLabel', background=COLORS['header'], foreground=COLORS['accent'], font=FONT_TITLE)
    style.configure('Status.TLabel', background=COLORS['header'], foreground=COLORS['text_muted'], font=FONT_UI)
    style.configure('Count.TLabel', background=COLORS['header'], foreground=COLORS['gold'], font=FONT_UI_BOLD)
    style.configure('Disk.TLabel', background=COLORS['header'], foreground=COLORS['gold_dim'], font=FONT_SMALL)
    style.configure('Strip.Status.TLabel', background=COLORS['surface'], foreground=COLORS['text_muted'], font=FONT_UI)
    style.configure('Strip.Count.TLabel', background=COLORS['surface'], foreground=COLORS['gold'], font=FONT_UI_BOLD)
    style.configure('Strip.Banner.TLabel', background=COLORS['surface'], foreground=COLORS['gold_dim'],
                    font=FONT_SMALL, anchor='center')
    style.configure('Strip.Disk.TLabel', background=COLORS['surface'], foreground=COLORS['gold_dim'], font=FONT_SMALL)

    style.configure('TRadiobutton', background=COLORS['surface'], foreground=COLORS['text'],
                    indicatorcolor=COLORS['surface_alt'], font=FONT_UI, padding=(8, 6))
    style.map('TRadiobutton',
              background=[('active', COLORS['surface_alt']), ('selected', COLORS['select'])],
              foreground=[('selected', COLORS['accent_dim'])])

    style.configure('TCheckbutton', background=COLORS['surface'], foreground=COLORS['text'],
                    font=FONT_UI, focuscolor=COLORS['select'], indicatormargin=4)
    style.map('TCheckbutton',
              background=[('active', COLORS['surface_alt'])],
              foreground=[('disabled', COLORS['text_muted'])],
              indicatorcolor=[
                  ('selected', COLORS['accent']),
                  ('!selected', COLORS['surface']),
                  ('disabled', COLORS['surface_alt']),
              ])

    style.configure('TButton', background=COLORS['surface_alt'], foreground=COLORS['text'],
                    bordercolor=COLORS['border'], focusthickness=0, padding=(12, 8), font=FONT_UI)
    style.map('TButton',
              background=[('active', COLORS['gold_light']), ('disabled', COLORS['surface'])],
              foreground=[('disabled', COLORS['text_muted'])])

    style.configure('Accent.TButton', background=COLORS['accent'], foreground='#ffffff',
                    bordercolor=COLORS['accent'], padding=(16, 10), font=FONT_UI_BOLD)
    style.map('Accent.TButton',
              background=[('active', COLORS['accent_hover']), ('disabled', COLORS['accent_dim'])],
              foreground=[('disabled', '#f5e8ee')])

    style.configure('Ghost.TButton', background=COLORS['surface'], foreground=COLORS['text_muted'],
                    bordercolor=COLORS['border'], padding=(10, 6))
    style.map('Ghost.TButton',
              background=[('active', COLORS['select'])],
              foreground=[('active', COLORS['accent_dim'])])

    style.configure('Treeview', background=COLORS['surface'], fieldbackground=COLORS['surface'],
                    foreground=COLORS['text'], bordercolor=COLORS['border'], rowheight=28, font=FONT_UI)
    style.configure('Treeview.Heading', background=COLORS['surface_alt'], foreground=COLORS['gold_dim'],
                    bordercolor=COLORS['border'], font=FONT_UI_BOLD, padding=(8, 6))
    style.map('Treeview',
              background=[('selected', COLORS['select'])],
              foreground=[('selected', COLORS['accent_dim'])])


STEP_STATUS = {
    'unzip': '解压中…',
    'archive': '归档中…',
    'filter': '过滤中…',
    'rename': '重命名中…',
    'convert_audio': '转flac中…',
    'tag_audio': '写入元数据中…',
}

# 任务列表里每条任务当前状态（record.ops）的中文可读标签
OPS_LABEL = {
    'create_timeline': '待处理',
    'find_zip': '待解压',
    'pre_filter': '预过滤',
    'unzip': '解压中',
    'unnest': '已解压',
    'insert_rj': '已识别RJ',
    'archive': '已归档',
    'post_filter': '已过滤',
    'post_filter_skip': '已跳过过滤',
    'rename': '已完成',
    'rename_duplicate': '库中有重复',
    'convert_audio': '已转换',
    'tag_audio': '已写入元数据',
    'unzip_failed': '解压失败',
}


def _ops_label(ops: str) -> str:
    return OPS_LABEL.get(ops, ops)


def _timeline_step_label(timeline) -> str:
    """任务队列「步骤」列的可读标签（结合路径做细化）。"""
    record = timeline.get_current_record()
    ops = record.ops
    current_path = timeline.get_current_path()
    if ops == 'unzip_failed':
        pending = task_runner._timeline_pending_zip(timeline)
        if pending and task_runner._timeline_manual_7z_waiting(timeline):
            return '特殊7z待填密码'
        if pending and task_runner._is_nested_archive(pending):
            return '内层解压失败'
    if ops in ('insert_rj', 'archive'):
        if task_runner._is_in_resource_library(current_path):
            return '已移入资源库'
        if task_runner._is_under_output(current_path):
            return '已移入音声库'
    if ops == 'rename':
        return '已完成'
    if ops in ('convert_audio', 'tag_audio'):
        return _ops_label(ops)
    if ops == 'rename_duplicate':
        return '库中有重复'
    return _ops_label(ops)


def _timeline_output_label(timeline) -> str:
    """任务队列「输出」列的可读标签。"""
    record = timeline.get_current_record()
    pending = task_runner._timeline_pending_zip(timeline)
    if pending and record.ops in ('find_zip', 'unzip_failed', 'pre_filter'):
        if task_runner._timeline_manual_7z_waiting(timeline):
            return f'{pending.name}（双击填密码）'
        if (
            isinstance(pending, Zip)
            and pending.requires_manual_password()
            and pending.note
        ):
            return f'{pending.name}（已填密码）'
        return pending.name
    if not record.output_file:
        return ''
    if record.ops == 'rename_duplicate':
        note = getattr(record.output_file, 'note', None)
        if note:
            return note
    return record.output_file.name


def _format_run_status_summary(
    timelines,
    *,
    interrupted: bool = False,
    last_step: str | None = None,
) -> tuple[str, str, str]:
    """根据任务队列生成运行状态栏 (主文案, 行内补充, 底栏长文案)。"""
    if not timelines:
        return '已完成', '', ''

    counts: dict[str, int] = {}
    manual_7z_details: list[str] = []
    for timeline in timelines:
        label = _timeline_step_label(timeline)
        counts[label] = counts.get(label, 0) + 1
        if task_runner._timeline_manual_7z_waiting(timeline):
            detail = task_runner._timeline_manual_7z_status_detail(timeline)
            if detail and detail not in manual_7z_details:
                manual_7z_details.append(detail)

    parts = [f'{label} {n}' for label, n in counts.items()]
    detail = '，'.join(parts)

    manual_7z = counts.get('特殊7z待填密码', 0)
    pending_unzip = counts.get('待解压', 0)
    pending_queue = counts.get('待处理', 0)
    if manual_7z and not pending_unzip and not pending_queue:
        inline = f'共 {manual_7z} 个须双击填密码' if manual_7z > 1 else ''
        if manual_7z == 1 and manual_7z_details:
            return '特殊7z：待填密码', inline, manual_7z_details[0]
        if manual_7z_details:
            return '特殊7z：待填密码', inline, '；'.join(manual_7z_details[:3])
        return '特殊7z：待填密码', inline, ''

    if interrupted and last_step == 'unzip':
        return '解压已中断', detail, ''

    failed = counts.get('解压失败', 0)
    if failed and failed == len(timelines):
        suffix = f'{failed} 个任务待重试' if failed > 1 else '1 个任务待重试'
        return '解压失败', suffix, ''

    if failed:
        return '部分完成', detail, ''

    duplicate = counts.get('库中有重复', 0)
    if duplicate:
        return '库中有重复内容', detail, ''

    if len(counts) == 1 and '已完成' in counts:
        return '已完成', detail, ''
    if pending_queue and len(counts) == 1:
        return '待处理', detail, ''
    if pending_unzip and len(counts) == 1:
        return '待解压', detail, ''
    return '部分完成', detail, ''


class Console(ttk.Frame):

    def __init__(self, master, *args, **kwargs):
        super().__init__(master, *args, **kwargs)

        self.val = tk.StringVar(value='unzip')
        self.val2 = tk.StringVar(value='待机')
        self.val3 = tk.StringVar(value='')
        self.val4 = tk.StringVar(value='')
        auto_next_default = True
        try:
            if task_runner.conf is not None:
                auto_next_default = bool(task_runner.conf.auto_next)
        except Exception:
            pass
        self.auto_next_var = tk.BooleanVar(value=auto_next_default)

        self._disk_speed_vars: dict[str, tuple[tk.StringVar, tk.StringVar]] = {}
        self._last_task_elapsed: float | None = None
        self._log_queue = None
        self._worker_busy = False
        self._worker_interrupted = False
        self._worker_last_step = None
        self._build_layout()
        self._task_buttons = self._collect_task_buttons()

    def _collect_task_buttons(self):
        buttons = [self.btn_run, self.btn_clear, self.btn_settings, self.btn_output,
                   self.btn_resource, self.btn_password, self.btn_recycle]
        buttons.extend(self.step_radios)
        if getattr(self, 'chk_auto_next', None) is not None:
            buttons.append(self.chk_auto_next)
        return tuple(buttons)

    def _build_layout(self):
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=248)
        self.rowconfigure(1, weight=0)
        self.rowconfigure(3, weight=1)

        self._build_header()
        self._build_main_panel()
        self._build_sidebar()
        self._build_status_strip()
        self._build_log_panel()

    def _build_header(self):
        header = ttk.Frame(self, style='Header.TFrame', padding=(16, 12))
        header.grid(row=0, column=0, columnspan=2, sticky='ew')

        ttk.Label(header, text=app_paths.APP_NAME, style='Header.TLabel').pack(side='left')
        ttk.Label(header, text=app_paths.APP_VERSION, style='Status.TLabel').pack(
            side='left', padx=(12, 0))

    def _build_status_strip(self):
        strip = ttk.LabelFrame(self, text='运行状态', style='Card.TLabelframe', padding=(10, 6))
        strip.grid(row=2, column=0, sticky='ew', padx=(16, 8), pady=(0, 8))
        strip.columnconfigure(0, weight=1)

        self._status_top = tk.Frame(
            strip, bg=COLORS['surface'], height=STATUS_TOP_ROW_HEIGHT,
        )
        self._status_top.grid(row=0, column=0, sticky='ew')
        self._status_top.grid_propagate(False)

        gif_half = STATUS_GIF_SIZE // 2 + STATUS_GIF_GAP

        self._status_left = ttk.Frame(self._status_top, style='Surface.TFrame')
        self._status_left.place(relx=0.5, rely=0.5, anchor='e', x=-gif_half)
        ttk.Label(self._status_left, textvariable=self.val2, style='Strip.Status.TLabel').pack(side='left')
        ttk.Label(self._status_left, textvariable=self.val3, style='Strip.Count.TLabel').pack(
            side='left', padx=(10, 0))

        self._unzip_gif = gif_player.AnimatedGifLabel(
            self._status_top,
            gif_player.AnimatedGifLabel.default_unzip_gif_path(),
            size=STATUS_GIF_SIZE,
            bg=COLORS['surface'],
        )
        self._unzip_gif.label.place(relx=0.5, rely=0.5, anchor='center')

        self._disk_panel = ttk.Frame(self._status_top, style='Surface.TFrame')
        self._disk_panel.place(relx=0.5, rely=0.5, anchor='w', x=gif_half)

        self._status_banner = ttk.Label(
            strip, textvariable=self.val4, style='Strip.Banner.TLabel',
            anchor='center', justify='center', wraplength=720,
        )
        self._status_banner.grid(row=1, column=0, sticky='ew', pady=(2, 0))
        self._status_banner.grid_remove()

        # 兼容旧代码中对 _status_inner 的引用（磁盘面板刷新等）
        self._status_inner = self._status_top

    def _build_sidebar(self):
        sidebar = ttk.Frame(self, style='Surface.TFrame', padding=(14, 16))
        sidebar.grid(row=1, column=1, rowspan=2, sticky='ns', padx=(1, 0))
        sidebar.configure(width=248)

        ttk.Label(sidebar, text='工作流', background=COLORS['surface'],
                  foreground=COLORS['gold_dim'], font=FONT_UI_BOLD).pack(anchor='w', pady=(0, 8))

        steps_host = ttk.Frame(sidebar, style='Surface.TFrame')
        steps_host.pack(fill='x', pady=(0, 16))
        steps_host.columnconfigure(0, weight=1)
        steps_host.columnconfigure(1, weight=1)

        core_col = ttk.Frame(steps_host, style='Surface.TFrame')
        core_col.grid(row=0, column=0, sticky='nw', padx=(0, 10))
        ttk.Label(
            core_col, text='常规', background=COLORS['surface'],
            foreground=COLORS['text_muted'], font=FONT_SMALL,
        ).pack(anchor='w', pady=(0, 4))

        audio_col = ttk.Frame(steps_host, style='Surface.TFrame')
        audio_col.grid(row=0, column=1, sticky='nw')
        ttk.Label(
            audio_col, text='音频', background=COLORS['surface'],
            foreground=COLORS['text_muted'], font=FONT_SMALL,
        ).pack(anchor='w', pady=(0, 4))

        self.step_radios = []
        for value, label in STEP_OPTIONS:
            rb = ttk.Radiobutton(core_col, text=label, variable=self.val, value=value)
            rb.pack(anchor='w', fill='x')
            self.step_radios.append(rb)
        for value, label in AUDIO_STEP_OPTIONS:
            rb = ttk.Radiobutton(audio_col, text=label, variable=self.val, value=value)
            rb.pack(anchor='w', fill='x')
            self.step_radios.append(rb)

        self.chk_auto_next = tk.Checkbutton(
            sidebar,
            text='自动执行后续步骤',
            variable=self.auto_next_var,
            command=self._on_auto_next_toggled,
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
        self.chk_auto_next.pack(anchor='w', fill='x', pady=(8, 4))

        self.btn_run = ttk.Button(sidebar, text='启动', style='Accent.TButton', command=self.dash)
        self.btn_run.pack(fill='x', pady=(4, 16))

        ttk.Separator(sidebar, orient='horizontal').pack(fill='x', pady=(0, 12))

        btn_row1 = ttk.Frame(sidebar, style='Surface.TFrame')
        btn_row1.pack(fill='x', pady=(0, 6))
        self.btn_recycle = ttk.Button(btn_row1, text='回收站', style='Ghost.TButton',
                                      command=self.open_recycle)
        self.btn_recycle.pack(side='left', expand=True, fill='x', padx=(0, 4))
        self.btn_settings = ttk.Button(btn_row1, text='设置', style='Ghost.TButton',
                                       command=self.open_settings)
        self.btn_settings.pack(side='left', expand=True, fill='x')

        btn_row2 = ttk.Frame(sidebar, style='Surface.TFrame')
        btn_row2.pack(fill='x', pady=(0, 6))
        self.btn_output = ttk.Button(btn_row2, text='音声库', style='Ghost.TButton',
                                     command=self.open_output)
        self.btn_output.pack(side='left', expand=True, fill='x', padx=(0, 4))
        self.btn_password = ttk.Button(btn_row2, text='密码', style='Ghost.TButton',
                                       command=self.open_passwords)
        self.btn_password.pack(side='left', expand=True, fill='x')

        btn_row3 = ttk.Frame(sidebar, style='Surface.TFrame')
        btn_row3.pack(fill='x')
        self.btn_resource = ttk.Button(btn_row3, text='资源库', style='Ghost.TButton',
                                       command=self.open_resource)
        self.btn_resource.pack(side='left', expand=True, fill='x', padx=(0, 4))
        self.btn_clear = ttk.Button(btn_row3, text='清除队列', style='Ghost.TButton',
                                    command=self.clear)
        self.btn_clear.pack(side='left', expand=True, fill='x')

    def _build_main_panel(self):
        main = ttk.Frame(self, padding=(16, 16, 16, 8))
        main.grid(row=1, column=0, sticky='new')
        main.columnconfigure(0, weight=1)

        drop_frame = tk.Frame(main, bg=COLORS['drop_zone'], highlightbackground=COLORS['border'],
                              highlightthickness=1, bd=0)
        drop_frame.grid(row=0, column=0, sticky='ew', pady=(0, 12))
        tk.Label(drop_frame, text='将文件或文件夹拖放到此窗口任意位置',
                 bg=COLORS['drop_zone'], fg=COLORS['text_muted'], font=FONT_UI).pack(padx=16, pady=10)
        tk.Label(drop_frame, text='双击任务行可添加备注（RJ 号 / 一次性密码）',
                 bg=COLORS['drop_zone'], fg=COLORS['gold_dim'], font=FONT_SMALL).pack(padx=16, pady=(0, 10))

        task_card = ttk.LabelFrame(main, text='任务队列', style='Card.TLabelframe', padding=(8, 8))
        task_card.grid(row=1, column=0, sticky='ew')
        task_card.columnconfigure(0, weight=1)

        columns = ('input', 'step', 'output')
        self.task_tree = ttk.Treeview(task_card, columns=columns, show='headings',
                                      selectmode='browse', height=4)
        self.task_tree.heading('input', text='输入')
        self.task_tree.heading('step', text='步骤')
        self.task_tree.heading('output', text='输出')
        self.task_tree.column('input', width=320, minwidth=120, stretch=True)
        self.task_tree.column('step', width=148, minwidth=100, stretch=False)
        self.task_tree.column('output', width=320, minwidth=120, stretch=True)
        self.task_tree.grid(row=0, column=0, sticky='ew')

        scroll = ttk.Scrollbar(task_card, orient='vertical', command=self.task_tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.task_tree.configure(yscrollcommand=scroll.set)
        self.task_tree.bind('<Double-1>', self.note)

        for tag, bg in (('odd', COLORS['tree_odd']), ('even', COLORS['tree_even'])):
            self.task_tree.tag_configure(tag, background=bg)
        self.task_tree.tag_configure('failed', background='#fde8ec')

    def _build_log_panel(self):
        log_card = ttk.LabelFrame(self, text='运行日志', style='Card.TLabelframe', padding=(8, 8))
        log_card.grid(row=3, column=0, columnspan=2, sticky='nsew', padx=16, pady=(0, 16))
        log_card.columnconfigure(0, weight=1)
        log_card.rowconfigure(0, weight=1)

        self.text = tk.Text(log_card, wrap='word', font=FONT_LOG,
                            bg=COLORS['log_bg'], fg=COLORS['log_fg'],
                            insertbackground=COLORS['accent'], relief='flat',
                            highlightthickness=1, highlightbackground=COLORS['border'],
                            highlightcolor=COLORS['border'], padx=10, pady=8)
        self.text.grid(row=0, column=0, sticky='nsew')

        log_scroll = ttk.Scrollbar(log_card, orient='vertical', command=self.text.yview)
        log_scroll.grid(row=0, column=1, sticky='ns')
        self.text.configure(yscrollcommand=log_scroll.set)

    def _run_on_ui(self, func):
        self.after(0, func)

    def _set_ui_busy(self, busy: bool):
        self._worker_busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for widget in self._task_buttons:
            widget.configure(state=state)
        self.task_tree.configure(selectmode='none' if busy else 'browse')
        if not busy:
            self._run_on_ui(self._refresh_run_status)

    def _set_status_banner(self, text: str):
        if text:
            self.val4.set(text)
            self._status_banner.grid()
        else:
            self.val4.set('')
            self._status_banner.grid_remove()

    def _apply_run_status(self, main: str, inline: str = '', banner: str = ''):
        self.val2.set(main)
        if banner:
            self.val3.set(inline)
            self._set_status_banner(banner)
            return
        combined = inline or ''
        if len(combined) > STATUS_BANNER_INLINE_MAX:
            self.val3.set('')
            self._set_status_banner(combined)
            return
        self.val3.set(combined)
        self._set_status_banner('')

    def _clear_run_status_banner(self):
        self._set_status_banner('')

    def _reset_status(self):
        self._apply_run_status('待机')
        self._unzip_gif.stop()
        self._last_task_elapsed = None
        self.clear_disk_speed_panel()

    def _refresh_run_status(self):
        """任务结束后，根据队列剩余任务更新运行状态栏文案。"""
        if self._worker_busy:
            return
        main, inline, banner = _format_run_status_summary(
            task_runner.timelines,
            interrupted=self._worker_interrupted,
            last_step=self._worker_last_step,
        )
        self._apply_run_status(main, inline, banner)
        self._unzip_gif.stop()
        if self._last_task_elapsed is not None:
            self.show_task_elapsed_time(self._last_task_elapsed)
            self._last_task_elapsed = None
        else:
            self.clear_disk_speed_panel()
        if task_runner.timelines:
            self.add2lis(task_runner.timelines)

    def show_run_status(self, main: str, detail: str = '', *, banner: str = ''):
        """更新状态栏；任务进行中时不展示特殊包提示。"""
        def _do():
            if self._worker_busy and main.startswith('特殊7z'):
                return
            self._apply_run_status(main, detail, banner)
        self._run_on_ui(_do)

    def set_step_status(self, step: str):
        def _do():
            self._clear_run_status_banner()
            self.val2.set(STEP_STATUS.get(step, step))
            self.val3.set('')
            # 解压 / 转flac / 写入元数据为磁盘密集步骤，播放状态 GIF
            if step in ('unzip', 'convert_audio', 'tag_audio') and self._unzip_gif.available:
                self._unzip_gif.start()
            else:
                self._unzip_gif.stop()
        self._run_on_ui(_do)

    def _clear_task_tree(self):
        for item in self.task_tree.get_children():
            self.task_tree.delete(item)

    def _insert_task_row(self, input_text, step_text, output_text, ops: str | None = None):
        count = len(self.task_tree.get_children())
        tag = 'failed' if ops in ('unzip_failed', 'rename_duplicate') else ('odd' if count % 2 else 'even')
        self.task_tree.insert('', 'end', values=(input_text, step_text, output_text), tags=(tag,))

    def write(self, info):
        def _do():
            self.text.insert('end', info)
            self.text.see(tk.END)
        self._run_on_ui(_do)

    def clear(self):
        if self._worker_busy:
            return
        task_runner.clear()
        self._worker_interrupted = False
        self._worker_last_step = None
        self._last_task_elapsed = None
        self.add2lisbox(queue.Queue())
        self._run_on_ui(self._reset_status)

    def open_output(self):
        if self._worker_busy:
            return
        global output
        path = (task_runner.conf.output_path if task_runner.conf else None) or output
        if not path:
            messagebox.showwarning('未配置路径', '请先在设置中填写音声库路径。', parent=self.winfo_toplevel())
            return
        from file_ops import mk_if_not_exit
        mk_if_not_exit(path)
        os.startfile(path)

    def open_recycle(self):
        if self._worker_busy or not task_runner.conf:
            return
        from file_ops import mk_if_not_exit
        recycle_path = task_runner.conf.recycle_path
        mk_if_not_exit(recycle_path)
        os.startfile(recycle_path)

    def open_resource(self):
        if self._worker_busy or not task_runner.conf:
            return
        resource_path = (getattr(task_runner.conf, 'resource_path', None) or '').strip()
        if not resource_path:
            messagebox.showwarning(
                '未配置资源库',
                '请先在设置中填写资源库路径（用于存放未识别 RJ 号的作品）。',
                parent=self.winfo_toplevel(),
            )
            return
        from file_ops import mk_if_not_exit
        mk_if_not_exit(resource_path)
        os.startfile(resource_path)

    def _sync_auto_next_from_config(self):
        """从当前配置同步主界面「自动后续」开关。"""
        try:
            enabled = bool(task_runner.conf.auto_next) if task_runner.conf else True
        except Exception:
            enabled = True
        self.auto_next_var.set(enabled)

    def _on_auto_next_toggled(self):
        """主界面切换自动后续，立即写回配置。"""
        enabled = bool(self.auto_next_var.get())
        ok, err = config.save_settings({'auto_next': enabled})
        if not ok:
            messagebox.showerror('保存失败', err or '无法写入 auto_next', parent=self)
            self._sync_auto_next_from_config()
            return
        if task_runner.conf is not None:
            task_runner.conf.auto_next = enabled

    def open_settings(self):
        from settings_dialog import open_settings_dialog

        def _on_saved(new_output_path: str):
            global output
            output = new_output_path
            self._sync_auto_next_from_config()

        open_settings_dialog(self, on_saved=_on_saved)

    def open_passwords(self):
        from password_dialog import open_password_dialog

        open_password_dialog(self)

    def add2lisbox(self, q: queue.Queue):
        def _do():
            self._clear_task_tree()
            qlist = list(q.queue)
            for item in qlist:
                record = item.get_current_record()
                self._insert_task_row(
                    record.input_file.path,
                    _timeline_step_label(item),
                    '',
                    record.ops,
                )
        self._run_on_ui(_do)

    def add2lis(self, item_list):
        def _do():
            self._clear_task_tree()
            for item in item_list:
                record = item.get_current_record()
                self._insert_task_row(
                    task_runner._timeline_input_label(item),
                    _timeline_step_label(item),
                    _timeline_output_label(item),
                    record.ops,
                )
        self._run_on_ui(_do)

    def _clear_disk_panel_widgets(self):
        for widget in self._disk_panel.winfo_children():
            widget.destroy()
        self._disk_speed_vars = {}
        if hasattr(self, '_status_inner'):
            self._status_inner.update_idletasks()

    def setup_disk_speed_panel(self, drives: list[str]):
        if not drives:
            return
        ready = threading.Event()

        def _do():
            try:
                self._clear_disk_panel_widgets()
                # 每个磁盘占一排（双排/多排堆叠），避免多盘时横向排开超出状态栏
                for i, drive in enumerate(sorted(drives)):
                    ttk.Label(
                        self._disk_panel, text=f'{drive}:', style='Strip.Disk.TLabel', width=3,
                    ).grid(row=i, column=0, sticky='w', pady=0)

                    read_var = tk.StringVar(
                        value=f'读 {disk_io_monitor.format_speed_display(0.0)}')
                    write_var = tk.StringVar(
                        value=f'写 {disk_io_monitor.format_speed_display(0.0)}')
                    tk.Label(
                        self._disk_panel, textvariable=read_var, font=FONT_DISK,
                        bg=COLORS['surface'], fg=COLORS['gold_dim'],
                        width=DISK_SPEED_LABEL_WIDTH, anchor='e',
                    ).grid(row=i, column=1, padx=(2, 6), pady=0)
                    tk.Label(
                        self._disk_panel, textvariable=write_var, font=FONT_DISK,
                        bg=COLORS['surface'], fg=COLORS['gold_dim'],
                        width=DISK_SPEED_LABEL_WIDTH, anchor='e',
                    ).grid(row=i, column=2, pady=0)

                    self._disk_speed_vars[drive] = (read_var, write_var)
            finally:
                ready.set()

        self._run_on_ui(_do)
        ready.wait(timeout=2)

    def update_disk_speed_stats(self, stats: dict[str, tuple[float, float]]):
        def _do():
            for drive, (read_var, write_var) in self._disk_speed_vars.items():
                read_bps, write_bps = stats.get(drive, (0.0, 0.0))
                read_var.set(f'读 {disk_io_monitor.format_speed_display(read_bps)}')
                write_var.set(f'写 {disk_io_monitor.format_speed_display(write_bps)}')
        self._run_on_ui(_do)

    def show_task_elapsed_time(self, elapsed_seconds: float):
        """在磁盘速度面板位置显示本次任务流程总用时。"""
        text = _format_elapsed(elapsed_seconds)

        def _do():
            self._clear_disk_panel_widgets()
            ttk.Label(self._disk_panel, text=text, style='Strip.Disk.TLabel').grid(
                row=0, column=0, sticky='w',
            )
            if hasattr(self, '_status_inner'):
                self._status_inner.update_idletasks()

        self._run_on_ui(_do)

    def clear_disk_speed_panel(self, *, wait: bool = True):
        ready = threading.Event()

        def _do():
            try:
                self._clear_disk_panel_widgets()
            finally:
                ready.set()

        if wait and threading.current_thread() is threading.main_thread():
            _do()
            return
        self._run_on_ui(_do)
        if wait:
            ready.wait(timeout=2)

    def update_progress(self, value, maximum, message: str | None = None):
        def _do():
            self._clear_run_status_banner()
            text = message or f'解压中 {value}/{maximum}'
            self.val2.set(text)
            self.val3.set('')
        self._run_on_ui(_do)

    def dash(self):
        if self._worker_busy:
            return
        start_process = str(self.val.get())
        threading.Thread(target=self._run_dash_worker, args=(start_process,), daemon=True).start()

    def _run_dash_worker(self, start_process):
        def _begin():
            self._last_task_elapsed = None
            self._set_ui_busy(True)
            self._clear_run_status_banner()
            self.clear_disk_speed_panel()

        self._run_on_ui(_begin)
        task_started_at = time.perf_counter()
        ran_full_from_unzip = False
        last_process = None
        self._worker_interrupted = False
        self._worker_last_step = None
        try:
            task_runner.reload()
            pipeline = config.build_run_pipeline(
                start_process,
                auto_next=bool(task_runner.conf.auto_next),
                workflow_steps=getattr(task_runner.conf, 'workflow_steps', None),
            )
            if not pipeline:
                pipeline = [start_process]
            for index, process in enumerate(pipeline):
                last_process = process
                self._worker_last_step = process
                self.set_step_status(process)
                getattr(task_runner, f'{process}_loop')()
                if index < len(pipeline) - 1:
                    next_process = pipeline[index + 1]
                    self._run_on_ui(lambda p=next_process: self.val.set(p))
            ran_full_from_unzip = (
                start_process == 'unzip'
                and bool(task_runner.conf.auto_next)
                and len(pipeline) > 1
            )
            if last_process:
                task_runner.prune_after_step(last_process)
        except Exception:
            self._worker_interrupted = True
            app_paths.append_startup_error_log('dash worker crash')
        finally:
            if not self._worker_interrupted:
                self._worker_last_step = last_process
            elapsed = time.perf_counter() - task_started_at

            def _finish():
                self._last_task_elapsed = elapsed
                self._set_ui_busy(False)
                if ran_full_from_unzip:
                    self.val.set('unzip')

            self._run_on_ui(_finish)

    def note(self, event):
        if self._worker_busy:
            return
        selection = self.task_tree.selection()
        if not selection:
            return
        i = self.task_tree.index(selection[0])
        new_text = simpledialog.askstring('备注', '备注 RJ / 密码', parent=self.winfo_toplevel())
        if new_text:
            timeline = task_runner.timelines[i]
            pending = task_runner._timeline_pending_zip(timeline)
            if pending is not None:
                pending.set_note(new_text)
            else:
                timeline.get_current_record().output_file.set_note(new_text)
            # 若该任务此前因密码错误滞留，立即解除卡住状态，下次「启动」直接重试
            task_runner.requeue_unzip_failure(timeline)
            self.add2lis(task_runner.timelines)
            if hasattr(self, '_refresh_run_status'):
                self._refresh_run_status()

    def bind_log_queue(self, log_queue):
        self._log_queue = log_queue

    def flush_progress_once(self):
        if self._log_queue is None:
            return
        while not self._log_queue.empty():
            item = self._log_queue.get(block=False)
            if item is None:
                return
            list_id, msg = item
            value, maximum = extract_fraction(msg)
            if value is not None and maximum is not None:
                self.update_progress(value, maximum, msg)
            else:
                self._clear_run_status_banner()
                self.val2.set(msg)

    def flush_progress(self, log_queue):
        self.bind_log_queue(log_queue)
        self.flush_progress_once()
        self.after(50, self.flush_progress, log_queue)


def extract_fraction(fraction_string):
    match = re.search(r'(\d+)/(\d+)', fraction_string)
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def _decode_dropped_path(item: str | bytes) -> str:
    if isinstance(item, bytes):
        try:
            return item.decode('utf-8')
        except UnicodeDecodeError:
            return item.decode('gbk')
    return item


def _bring_window_to_front():
    """拖放等场景下将主窗口提到最前。"""
    global Window
    if Window is None:
        return
    try:
        Window.deiconify()
        Window.lift()
        Window.attributes('-topmost', True)
        Window.after(200, lambda: Window.attributes('-topmost', False))
        Window.focus_force()
    except tk.TclError:
        return

    if sys.platform == 'win32':
        try:
            import ctypes
            Window.update_idletasks()
            hwnd = Window.winfo_id()
            if hwnd:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass


def _process_dropped_files(files):
    if UI is None:
        return False
    if UI._worker_busy:
        return False
    _bring_window_to_front()
    try:
        task_runner.reload()
        if not files:
            return True

        if not task_runner.timelines:
            task_runner.clear()

        decoded_files = [_decode_dropped_path(item) for item in files]
        added = task_runner.create_timeline(decoded_files)
        if added == 0:
            UI._run_on_ui(lambda: UI.val2.set('文件已在工作区'))
        elif hasattr(UI, '_refresh_run_status'):
            UI._run_on_ui(UI._refresh_run_status)
    except Exception:
        app_paths.append_startup_error_log('drop handler crash')
    return True


def on_drop(files):
    """windnd 在 Windows 消息线程回调，只能入队，不可触碰 Tk。"""
    global _bring_to_front_pending
    _drop_queue.put(list(files))
    _bring_to_front_pending = True


def _poll_drop_queue():
    global _bring_to_front_pending
    if _bring_to_front_pending:
        _bring_to_front_pending = False
        _bring_window_to_front()
    pending_retry = []
    try:
        while True:
            files = _drop_queue.get_nowait()
            if not _process_dropped_files(files):
                pending_retry.append(files)
    except queue.Empty:
        pass
    for files in pending_retry:
        _drop_queue.put(files)
    if Window is not None:
        Window.after(50, _poll_drop_queue)


def _center_window(window: tk.Tk, width: int, height: int):
    window.update_idletasks()
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()
    pos_x = max((screen_w - width) // 2, 0)
    pos_y = max((screen_h - height) // 2, 0)
    window.geometry(f'{width}x{height}+{pos_x}+{pos_y}')


def init_ui(log_queue):
    window = tk.Tk()
    window.withdraw()
    window.title(app_paths.APP_TITLE)
    _set_window_icon(window)
    setup_theme(window)
    window.minsize(960, 560)

    console = Console(window, padding=(0, 0))
    console.pack(fill=tk.BOTH, expand=True)

    pk_logger.gui = console
    global UI
    UI = console
    global Window
    Window = window
    task_runner.progress_ui = console
    if task_runner.unzipper is not None:
        task_runner.unzipper.progress_ui = console
    console.bind_log_queue(log_queue)
    windnd.hook_dropfiles(window, func=on_drop, force_unicode=True)
    window.after(50, _poll_drop_queue)
    console.after(50, console.flush_progress, log_queue)

    _center_window(window, 1280, 720)
    window.deiconify()


def mainloop_ui():
    global Window
    Window.mainloop()
