"""窗口定位：多显示器下将对话框居中到父窗口所在屏幕。"""

from __future__ import annotations

import re
import sys
import tkinter as tk

_GEO_RE = re.compile(r'^(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?$')


def _parse_geometry(geo: str) -> tuple[int, int, int | None, int | None]:
    match = _GEO_RE.match(geo or '')
    if not match:
        return 800, 600, None, None
    width, height = int(match.group(1)), int(match.group(2))
    x = int(match.group(3)) if match.group(3) is not None else None
    y = int(match.group(4)) if match.group(4) is not None else None
    return width, height, x, y


def window_screen_bounds(widget: tk.Misc) -> tuple[int, int, int, int]:
    """返回窗口在虚拟桌面上的 (rootx, rooty, width, height)。"""
    widget.update_idletasks()
    rootx = widget.winfo_rootx()
    rooty = widget.winfo_rooty()
    width = widget.winfo_width()
    height = widget.winfo_height()
    if width <= 1 or height <= 1:
        geo_w, geo_h, geo_x, geo_y = _parse_geometry(widget.winfo_geometry())
        width, height = geo_w, geo_h
        if geo_x is not None and geo_y is not None:
            rootx, rooty = geo_x, geo_y
    return rootx, rooty, width, height


def _monitor_work_area(rootx: int, rooty: int, width: int, height: int) -> tuple[int, int, int, int]:
    """返回包含父窗口中心点的显示器工作区 (left, top, right, bottom)。"""
    if sys.platform != 'win32':
        return rootx, rooty, rootx + max(width, 1), rooty + max(height, 1)

    import ctypes
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ('left', ctypes.c_long),
            ('top', ctypes.c_long),
            ('right', ctypes.c_long),
            ('bottom', ctypes.c_long),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ('cbSize', ctypes.c_ulong),
            ('rcMonitor', RECT),
            ('rcWork', RECT),
            ('dwFlags', ctypes.c_ulong),
        ]

    cx = rootx + max(width, 1) // 2
    cy = rooty + max(height, 1) // 2
    point = wintypes.POINT(cx, cy)
    monitor = ctypes.windll.user32.MonitorFromPoint(point, 2)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        work = info.rcWork
        return work.left, work.top, work.right, work.bottom
    return rootx, rooty, rootx + max(width, 1), rooty + max(height, 1)


def place_toplevel_over_parent(
        toplevel: tk.Toplevel,
        parent: tk.Misc,
        *,
        min_width: int,
        min_height: int,
        max_width: int | None = None,
        max_height: int | None = None,
        margin_x: int = 40,
        margin_y: int = 30,
        prefer_width: int | None = None,
        prefer_height: int | None = None,
):
    """将 Toplevel 居中到 parent 所在显示器，并限制在工作区内。"""
    parent = parent.winfo_toplevel()
    toplevel.update_idletasks()
    parent.update_idletasks()

    px, py, pw, ph = window_screen_bounds(parent)
    if pw <= 1:
        pw = 1280
    if ph <= 1:
        ph = 720

    work_left, work_top, work_right, work_bottom = _monitor_work_area(px, py, pw, ph)
    work_w = max(work_right - work_left, min_width)
    work_h = max(work_bottom - work_top, min_height)

    req_w = max(toplevel.winfo_reqwidth(), min_width)
    req_h = max(toplevel.winfo_reqheight(), min_height)

    width = prefer_width if prefer_width is not None else req_w
    height = prefer_height if prefer_height is not None else req_h
    width = max(min_width, min(width, pw - margin_x, work_w - margin_x, max_width or work_w))
    height = max(min_height, min(height, ph - margin_y, work_h - margin_y, max_height or work_h))

    x = px + (pw - width) // 2
    y = py + (ph - height) // 2

    if x + width > work_right:
        x = work_right - width
    if y + height > work_bottom:
        y = work_bottom - height
    if x < work_left:
        x = work_left
    if y < work_top:
        y = work_top

    toplevel.geometry(f'{width}x{height}+{x}+{y}')
