"""Entry / Text / Spinbox 通用编辑快捷键与右键菜单。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def _is_readonly(widget: tk.Misc) -> bool:
    if not isinstance(widget, (tk.Entry, ttk.Entry, tk.Text)):
        return False
    try:
        return str(widget.cget('state')) == 'readonly'
    except tk.TclError:
        return False


def _select_all(widget: tk.Misc):
    if isinstance(widget, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox)):
        widget.select_range(0, tk.END)
        widget.icursor(tk.END)
    elif isinstance(widget, tk.Text):
        widget.tag_add(tk.SEL, '1.0', tk.END)
        widget.mark_set(tk.INSERT, '1.0')
        widget.see(tk.INSERT)


def bind_edit_shortcuts(widget: tk.Misc):
    """绑定 Ctrl+C/V/X/A 及 Windows 常用组合键，并提供右键编辑菜单。"""
    if _is_readonly(widget):
        return

    def _cut(_event=None):
        widget.event_generate('<<Cut>>')
        return 'break'

    def _copy(_event=None):
        widget.event_generate('<<Copy>>')
        return 'break'

    def _paste(_event=None):
        widget.event_generate('<<Paste>>')
        return 'break'

    def _select_all_event(_event=None):
        _select_all(widget)
        return 'break'

    for sequence, handler in (
        ('<Control-c>', _copy),
        ('<Control-C>', _copy),
        ('<Control-v>', _paste),
        ('<Control-V>', _paste),
        ('<Control-x>', _cut),
        ('<Control-X>', _cut),
        ('<Control-a>', _select_all_event),
        ('<Control-A>', _select_all_event),
        ('<Control-Insert>', _copy),
        ('<Shift-Insert>', _paste),
    ):
        widget.bind(sequence, handler, add='+')

    menu = tk.Menu(widget, tearoff=0)
    menu.add_command(label='剪切', command=_cut)
    menu.add_command(label='复制', command=_copy)
    menu.add_command(label='粘贴', command=_paste)
    menu.add_separator()
    menu.add_command(label='全选', command=_select_all_event)

    def _show_menu(event):
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    widget.bind('<Button-3>', _show_menu, add='+')


def bind_edit_shortcuts_recursive(root: tk.Misc):
    """为窗口下所有可编辑 Entry / Spinbox / Text 绑定快捷键。"""
    for widget in root.winfo_children():
        if isinstance(widget, (tk.Entry, ttk.Entry, tk.Spinbox, ttk.Spinbox, tk.Text)):
            bind_edit_shortcuts(widget)
        bind_edit_shortcuts_recursive(widget)
