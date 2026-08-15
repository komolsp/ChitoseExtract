"""解压密码库管理对话框。"""

from __future__ import annotations

import datetime
import os
import tkinter as tk
from tkinter import messagebox, ttk

import password
import task_runner
from gui import COLORS, FONT_SMALL, FONT_UI, _set_window_icon
from gui_edit_bindings import bind_edit_shortcuts
from gui_window import place_toplevel_over_parent

_TREE_COLUMNS = ('password', 'add_date', 'hit_count', 'last_hit_date')
_COLUMN_LABELS = {
    'password': '密码',
    'add_date': '添加日期',
    'hit_count': '命中次数',
    'last_hit_date': '最后命中',
}


class PasswordDialog(tk.Toplevel):
    """内置解压密码库编辑器。"""

    _instance: PasswordDialog | None = None

    def __init__(self, parent: tk.Misc):
        if PasswordDialog._instance is not None and PasswordDialog._instance.winfo_exists():
            PasswordDialog._instance._center_over(PasswordDialog._instance._parent)
            PasswordDialog._instance.lift()
            PasswordDialog._instance.focus_force()
            return

        super().__init__(parent)
        self.withdraw()
        PasswordDialog._instance = self
        self._parent = parent.winfo_toplevel()
        self._passwords: list[password.Password] = []
        self._dirty = False

        self.title('解压密码库')
        self.configure(bg=COLORS['bg'])
        self.transient(self._parent)
        self.resizable(True, True)
        self.minsize(560, 420)
        _set_window_icon(self)

        self._build_ui()
        self._bind_global_shortcuts()
        self._load_passwords()
        self._center_over(self._parent)

        self.protocol('WM_DELETE_WINDOW', self._close)
        self.deiconify()
        self.grab_set()
        self.focus_force()

    def _center_over(self, parent: tk.Misc):
        place_toplevel_over_parent(
            self,
            parent,
            min_width=560,
            min_height=420,
            max_width=760,
            max_height=560,
            margin_x=120,
            margin_y=120,
        )

    def _build_ui(self):
        outer = ttk.Frame(self, padding=(16, 14))
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        hint_frame = ttk.Frame(outer)
        hint_frame.grid(row=0, column=0, sticky='ew', pady=(0, 10))
        ttk.Label(
            hint_frame,
            text='匹配时优先尝试最近添加的密码；同日添加时，命中次数较高者优先。',
            style='Muted.TLabel',
        ).pack(anchor='w')
        ttk.Label(
            hint_frame,
            text='在窗口任意位置右键粘贴或按 Ctrl+V 可粘贴并添加密码。',
            style='Muted.TLabel',
        ).pack(anchor='w')

        tree_frame = ttk.Frame(outer)
        tree_frame.grid(row=1, column=0, sticky='nsew')
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(
            tree_frame,
            columns=_TREE_COLUMNS,
            show='headings',
            selectmode='extended',
        )
        self._tree.heading('password', text=_COLUMN_LABELS['password'])
        self._tree.heading('add_date', text=_COLUMN_LABELS['add_date'])
        self._tree.heading('hit_count', text=_COLUMN_LABELS['hit_count'])
        self._tree.heading('last_hit_date', text=_COLUMN_LABELS['last_hit_date'])
        self._tree.column('password', width=260, minwidth=160, stretch=True)
        self._tree.column('add_date', width=100, minwidth=90, stretch=False)
        self._tree.column('hit_count', width=72, minwidth=72, stretch=False, anchor='center')
        self._tree.column('last_hit_date', width=100, minwidth=90, stretch=False)

        scroll = ttk.Scrollbar(tree_frame, orient='vertical', command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        self._tree.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')

        self._tree.bind('<Double-1>', self._edit_selected)
        self._tree.bind('<Delete>', lambda _e: self._delete_selected())
        self._bind_tree_context_menu()

        add_frame = ttk.Frame(outer, style='Surface.TFrame', padding=(0, 10, 0, 0))
        add_frame.grid(row=2, column=0, sticky='ew', pady=(10, 0))
        add_frame.columnconfigure(1, weight=1)

        ttk.Label(add_frame, text='新密码', style='Field.TLabel').grid(
            row=0, column=0, sticky='w', padx=(0, 8),
        )
        self._new_entry = ttk.Entry(add_frame)
        self._new_entry.grid(row=0, column=1, sticky='ew', padx=(0, 8))
        bind_edit_shortcuts(self._new_entry)
        self._new_entry.bind('<Return>', lambda _e: self._add_from_entry())

        ttk.Button(add_frame, text='添加', command=self._add_from_entry).grid(
            row=0, column=2, sticky='e',
        )

        btn_row = ttk.Frame(outer)
        btn_row.grid(row=3, column=0, sticky='ew', pady=(14, 0))
        btn_row.columnconfigure(0, weight=1)

        left_btns = ttk.Frame(btn_row)
        left_btns.grid(row=0, column=0, sticky='w')
        for col, (label, command) in enumerate((
            ('编辑', self._edit_selected),
            ('删除', self._delete_selected),
            ('上移', lambda: self._move_selected(-1)),
            ('下移', lambda: self._move_selected(1)),
        )):
            ttk.Button(left_btns, text=label, command=command, width=8).grid(
                row=0, column=col, padx=(0, 6),
            )

        right_btns = ttk.Frame(btn_row)
        right_btns.grid(row=0, column=1, sticky='e')
        ttk.Button(
            right_btns, text='打开password.txt', style='Ghost.TButton', command=self._open_external,
        ).pack(side='left', padx=(0, 8))
        ttk.Button(right_btns, text='取消', command=self._close).pack(side='left', padx=(0, 8))
        ttk.Button(right_btns, text='保存', style='Accent.TButton', command=self._save).pack(side='left')

        style = ttk.Style(self)
        style.configure(
            'Field.TLabel',
            background=COLORS['bg'],
            foreground=COLORS['text'],
            font=FONT_UI,
        )
        style.configure(
            'Muted.TLabel',
            background=COLORS['bg'],
            foreground=COLORS['text_muted'],
            font=FONT_SMALL,
        )

    def _bind_tree_context_menu(self):
        menu = tk.Menu(self._tree, tearoff=0)
        menu.add_command(label='复制密码', command=self._copy_selected)
        menu.add_command(label='粘贴并添加', command=self._paste_as_new)
        menu.add_separator()
        menu.add_command(label='编辑', command=self._edit_selected)
        menu.add_command(label='删除', command=self._delete_selected)
        menu.add_separator()
        menu.add_command(label='全选', command=self._select_all)

        def _show_menu(event):
            row = self._tree.identify_row(event.y)
            if row:
                if row not in self._tree.selection():
                    self._tree.selection_set(row)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        self._tree.bind('<Button-3>', _show_menu)

    def _widget_is_descendant(self, widget: tk.Misc | None) -> bool:
        while widget is not None:
            if widget is self:
                return True
            widget = widget.master
        return False

    def _focus_in_editable_entry(self) -> bool:
        """焦点在可编辑输入框内时，保留系统默认粘贴行为。"""
        focus = self.focus_get()
        if not isinstance(focus, (tk.Entry, ttk.Entry)):
            return False
        if not self._widget_is_descendant(focus):
            return False
        try:
            return str(focus.cget('state')) != 'readonly'
        except tk.TclError:
            return True

    def _bind_global_shortcuts(self):
        self._paste_sequences = (
            '<Control-v>', '<Control-V>', '<Shift-Insert>',
        )
        for sequence in self._paste_sequences:
            self.bind_all(sequence, self._on_global_paste, add='+')

    def _unbind_global_shortcuts(self):
        for sequence in getattr(self, '_paste_sequences', ()):
            self.unbind_all(sequence)

    def _on_global_paste(self, event=None):
        widget = getattr(event, 'widget', None) if event else None
        if widget is not None and not self._widget_is_descendant(widget):
            return
        if self._focus_in_editable_entry():
            return
        self._paste_as_new()
        return 'break'

    def _load_passwords(self):
        self._passwords = password.read_password()
        self._refresh_tree()

    def _refresh_tree(self):
        for item in self._tree.get_children():
            self._tree.delete(item)
        for index, item in enumerate(self._passwords):
            self._tree.insert(
                '',
                tk.END,
                iid=str(index),
                values=(
                    item.password,
                    item.add_date,
                    item.hit_count,
                    item.last_hit_date,
                ),
            )

    def _selected_indices(self) -> list[int]:
        indices = []
        for iid in self._tree.selection():
            try:
                indices.append(int(iid))
            except ValueError:
                continue
        return sorted(indices)

    def _select_all(self):
        self._tree.selection_set(*[str(i) for i in range(len(self._passwords))])

    def _copy_selected(self):
        indices = self._selected_indices()
        if not indices:
            return
        lines = [self._passwords[i].password for i in indices]
        self.clipboard_clear()
        self.clipboard_append('\n'.join(lines))

    def _find_password_index(self, pw_text: str) -> int | None:
        for index, item in enumerate(self._passwords):
            if item.password == pw_text:
                return index
        return None

    def _highlight_indices(self, indices: list[int]):
        """选中并滚动到指定行，短暂高亮提示。"""
        iids = [str(i) for i in indices if 0 <= i < len(self._passwords)]
        if not iids:
            return
        self._tree.tag_configure('duplicate_hint', background=COLORS['select'])
        for iid in iids:
            self._tree.item(iid, tags=('duplicate_hint',))
        self._tree.selection_set(iids[0])
        self._tree.focus(iids[0])
        self._tree.see(iids[0])

        def _clear_hint():
            for iid in iids:
                if self._tree.exists(iid):
                    self._tree.item(iid, tags=())

        self.after(2500, _clear_hint)

    def _scroll_tree_to_bottom(self):
        children = self._tree.get_children()
        if children:
            self._tree.see(children[-1])

    def _paste_as_new(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        added = False
        duplicate_indices: list[int] = []
        for line in text.splitlines():
            pw_text = line.split('\t')[0].strip()
            if not pw_text:
                continue
            index = self._find_password_index(pw_text)
            if index is not None:
                if index not in duplicate_indices:
                    duplicate_indices.append(index)
                continue
            if self._append_password(pw_text):
                added = True
        if added:
            self._refresh_tree()
            self._scroll_tree_to_bottom()
            self._dirty = True
        if duplicate_indices:
            self._highlight_indices(duplicate_indices)

    def _append_password(self, pw_text: str) -> bool:
        if self._find_password_index(pw_text) is not None:
            return False
        today = str(datetime.datetime.now().date())
        self._passwords.append(password.Password(pw_text, today, 0, ''))
        return True

    def _add_from_entry(self):
        pw_text = self._new_entry.get().strip()
        if not pw_text:
            return
        index = self._find_password_index(pw_text)
        if index is not None:
            self._highlight_indices([index])
            return
        if not self._append_password(pw_text):
            return
        self._new_entry.delete(0, tk.END)
        self._refresh_tree()
        self._scroll_tree_to_bottom()
        self._dirty = True

    def _edit_selected(self, _event=None):
        indices = self._selected_indices()
        if len(indices) != 1:
            if indices:
                messagebox.showinfo('编辑', '请只选择一条密码进行编辑。', parent=self)
            return
        index = indices[0]
        current = self._passwords[index]

        dialog = tk.Toplevel(self)
        dialog.title('编辑密码')
        dialog.configure(bg=COLORS['bg'])
        dialog.transient(self)
        dialog.resizable(False, False)
        _set_window_icon(dialog)

        body = ttk.Frame(dialog, padding=(16, 14))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text='密码内容：', style='Field.TLabel').grid(row=0, column=0, sticky='w')
        entry = ttk.Entry(body, width=48)
        entry.grid(row=1, column=0, sticky='ew', pady=(6, 12))
        entry.insert(0, current.password)
        bind_edit_shortcuts(entry)
        body.columnconfigure(0, weight=1)

        result: dict[str, str | None] = {'value': None}

        def _confirm():
            result['value'] = entry.get()
            dialog.destroy()

        def _cancel():
            dialog.destroy()

        btn_row = ttk.Frame(body)
        btn_row.grid(row=2, column=0, sticky='e')
        ttk.Button(btn_row, text='取消', command=_cancel).pack(side='left', padx=(0, 8))
        ttk.Button(btn_row, text='确定', style='Accent.TButton', command=_confirm).pack(side='left')
        entry.bind('<Return>', lambda _e: _confirm())
        entry.bind('<Escape>', lambda _e: _cancel())
        dialog.protocol('WM_DELETE_WINDOW', _cancel)

        place_toplevel_over_parent(
            dialog,
            self,
            min_width=420,
            min_height=140,
            max_width=520,
            max_height=180,
            margin_x=40,
            margin_y=40,
        )
        dialog.grab_set()
        entry.focus_set()
        entry.select_range(0, tk.END)
        self.wait_window(dialog)

        new_text = result['value']
        if new_text is None:
            return
        new_text = new_text.strip()
        if not new_text:
            messagebox.showwarning('无效密码', '密码不能为空。', parent=self)
            return
        if new_text != current.password:
            dup_index = self._find_password_index(new_text)
            if dup_index is not None:
                self._highlight_indices([dup_index])
                return
        current.password = new_text
        self._refresh_tree()
        self._dirty = True

    def _delete_selected(self):
        indices = self._selected_indices()
        if not indices:
            return
        if not messagebox.askyesno(
                '确认删除',
                f'确定删除选中的 {len(indices)} 条密码吗？',
                parent=self,
        ):
            return
        for index in reversed(indices):
            if 0 <= index < len(self._passwords):
                del self._passwords[index]
        self._refresh_tree()
        self._dirty = True

    def _move_selected(self, delta: int):
        indices = self._selected_indices()
        if len(indices) != 1:
            messagebox.showinfo('移动', '请选择单条密码后再上移/下移。', parent=self)
            return
        index = indices[0]
        new_index = index + delta
        if new_index < 0 or new_index >= len(self._passwords):
            return
        item = self._passwords.pop(index)
        self._passwords.insert(new_index, item)
        self._refresh_tree()
        self._tree.selection_set(str(new_index))
        self._tree.see(str(new_index))
        self._dirty = True

    def _save(self):
        cleaned: list[password.Password] = []
        seen: set[str] = set()
        for item in self._passwords:
            pw_text = item.password.strip()
            if not pw_text or pw_text in seen:
                continue
            seen.add(pw_text)
            item.password = pw_text
            cleaned.append(item)
        self._passwords = cleaned
        try:
            password.write_password(self._passwords)
        except OSError as err:
            messagebox.showerror('保存失败', f'无法写入 password.txt：\n{err}', parent=self)
            return
        task_runner.reload_passwords(self._passwords)
        self._dirty = False
        self._refresh_tree()
        self._close()

    def _open_external(self):
        path = os.path.abspath(password.PASSWORD_PATH)
        if os.path.isfile(path):
            os.startfile(path)
        else:
            messagebox.showwarning('文件不存在', f'未找到 {path}', parent=self)

    def _close(self):
        if self._dirty:
            if not messagebox.askyesno(
                    '未保存的更改',
                    '密码库有未保存的修改，确定要关闭吗？',
                    parent=self,
            ):
                return
        PasswordDialog._instance = None
        self._unbind_global_shortcuts()
        self.grab_release()
        self.destroy()


def open_password_dialog(parent: tk.Misc):
    PasswordDialog(parent)
