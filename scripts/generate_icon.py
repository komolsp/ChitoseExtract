"""从设计稿生成 assets/icon.ico 与 icon.png（补方、圆角、缩放）。"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image

from image_corners import round_image_corners

ASSETS = os.path.join(ROOT, 'assets')
DEFAULT_SOURCE = os.path.join(ASSETS, 'icon_source.png')
_ICON_SIZES = (256, 128, 64, 48, 32, 24, 16)


def _letterbox_square(image: Image.Image) -> Image.Image:
    """非正方形时补边成方图，不裁剪原图内容。"""
    rgba = image.convert('RGBA')
    width, height = rgba.size
    if width == height:
        return rgba
    side = max(width, height)
    canvas = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    ox = (side - width) // 2
    oy = (side - height) // 2
    canvas.paste(rgba, (ox, oy), rgba)
    return canvas


def _target_sizes(master: Image.Image) -> list[int]:
    """只向下缩放，不放大超过原图，避免任务栏选中模糊的大尺寸帧。"""
    native = max(master.size)
    sizes = [side for side in _ICON_SIZES if side <= native]
    if native not in sizes:
        sizes.append(native)
    return sorted(set(sizes), reverse=True)


def generate_icon(source_path: str = DEFAULT_SOURCE, assets_dir: str = ASSETS) -> str:
    os.makedirs(assets_dir, exist_ok=True)
    master = round_image_corners(_letterbox_square(Image.open(source_path)))
    sizes = _target_sizes(master)
    icons = [
        master.resize((side, side), Image.Resampling.LANCZOS)
        for side in sizes
    ]

    ico_path = os.path.join(assets_dir, 'icon.ico')
    png_path = os.path.join(assets_dir, 'icon.png')
    largest = icons[0]
    largest.save(
        ico_path,
        format='ICO',
        sizes=[(side, side) for side in sizes],
        append_images=icons[1:],
    )
    largest.save(png_path, format='PNG')
    return ico_path


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    source = argv[0] if argv else DEFAULT_SOURCE
    if not os.path.isfile(source):
        print(f'找不到设计稿: {source}', file=sys.stderr)
        return 1
    path = generate_icon(source)
    print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
