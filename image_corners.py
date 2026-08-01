"""图标圆角裁切（Pillow）。"""

from __future__ import annotations

from PIL import Image, ImageDraw


def round_image_corners(image: Image.Image, *, radius_ratio: float = 0.35) -> Image.Image:
    """为方形图标应用圆角透明蒙版。"""
    rgba = image.convert('RGBA')
    width, height = rgba.size
    radius = max(2, int(round(min(width, height) * radius_ratio)))
    mask = Image.new('L', (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    rounded = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    rounded.paste(rgba, mask=mask)
    return rounded
