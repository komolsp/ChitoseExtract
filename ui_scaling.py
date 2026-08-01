"""Tk 界面逻辑尺寸 → 物理像素（高分屏清晰渲染）。"""

from __future__ import annotations

import tkinter as tk


def tk_scale_factor(widget: tk.Misc) -> float:
    """当前窗口的 Tk DPI 缩放倍数（125%/150% 屏上通常 > 1）。"""
    try:
        widget.update_idletasks()
        return float(widget.tk.call('tk', 'scaling'))
    except tk.TclError:
        return 1.0


def logical_to_pixels(widget: tk.Misc, logical_size: int) -> int:
    """把布局用的逻辑像素换算为 PhotoImage 应用渲染的物理像素。"""
    if logical_size <= 0:
        return logical_size
    scale = tk_scale_factor(widget)
    return max(logical_size, int(round(logical_size * scale)))
