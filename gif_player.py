"""Tkinter GIF 动画播放器（基于 Pillow）。"""

from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageSequence, ImageTk

import app_paths
from ui_scaling import logical_to_pixels


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip('#')
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


class AnimatedGifLabel:
    """在 Label 上播放 GIF；待机显示首帧，start/stop 控制是否循环。"""

    def __init__(
        self,
        parent: tk.Misc,
        gif_path: str,
        *,
        size: int = 40,
        bg: str = '#ffffff',
    ):
        self._parent = parent
        self._size = size
        self._bg_rgb = _hex_to_rgb(bg)
        self._frames: list[ImageTk.PhotoImage] = []
        self._delays_ms: list[int] = []
        self._index = 0
        self._job: str | None = None
        self._playing = False

        self.label = tk.Label(parent, bg=bg, bd=0, highlightthickness=0)
        self._pixel_size = logical_to_pixels(parent, size)
        self._load_frames(gif_path)
        self.show_static()

    @staticmethod
    def default_unzip_gif_path() -> str:
        return app_paths.bundle_path('assets', 'unzip_running.gif')

    def _load_frames(self, gif_path: str):
        if not gif_path:
            return
        try:
            image = Image.open(gif_path)
        except OSError:
            return

        for frame in ImageSequence.Iterator(image):
            rgba = frame.convert('RGBA')
            px = self._pixel_size
            rgba = rgba.resize((px, px), Image.Resampling.LANCZOS)
            background = Image.new('RGBA', (px, px), self._bg_rgb + (255,))
            composed = Image.alpha_composite(background, rgba)
            photo = ImageTk.PhotoImage(composed)
            self._frames.append(photo)
            delay = frame.info.get('duration', image.info.get('duration', 100))
            self._delays_ms.append(max(int(delay), 20))

    @property
    def available(self) -> bool:
        return bool(self._frames)

    def show_static(self):
        self._playing = False
        if self._job is not None:
            self.label.after_cancel(self._job)
            self._job = None
        if not self._frames:
            return
        photo = self._frames[0]
        self.label.configure(image=photo)
        self.label.image = photo

    def start(self):
        if not self._frames or self._playing:
            return
        self._playing = True
        self._index = 0
        self._tick()

    def stop(self):
        self.show_static()

    def _tick(self):
        if not self._playing or not self._frames:
            return
        photo = self._frames[self._index]
        self.label.configure(image=photo)
        self.label.image = photo
        delay = self._delays_ms[self._index]
        self._index = (self._index + 1) % len(self._frames)
        self._job = self.label.after(delay, self._tick)
